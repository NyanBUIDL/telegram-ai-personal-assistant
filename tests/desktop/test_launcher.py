"""Real Qt/process/socket lifecycle with disposable profiles; no account secrets."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import time
import types
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import nullcontext
from pathlib import Path
from threading import Lock

import pytest
from PySide6.QtTest import QTest

from tg_assistant.config import Settings


def module(name):
    if importlib.util.find_spec(name) is None:
        pytest.fail("Windows launcher/lifecycle is not implemented")
    return importlib.import_module(name)


@pytest.fixture
def app(qt_application):
    return qt_application


@pytest.fixture
def settings(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("TG_ASSISTANT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    return Settings(_env_file=None, data_dir=tmp_path / "profile")


def track_fixture_starts(runtime):
    runtime.fixture_starts = []
    runtime.fixture_closing = False
    runtime.fixture_producer_lock = Lock()

    def tracked(producer):
        def start():
            with runtime.fixture_producer_lock:
                if runtime.fixture_closing:
                    future = Future()
                    future.cancel()
                    return future
                future = producer()
                runtime.fixture_starts.append(future)
                return future

        return start

    runtime.start = tracked(runtime.start)
    runtime.resume_after_handoff = tracked(runtime.resume_after_handoff)
    return runtime


def observe_fixture_readiness(runtime):
    """Observe original calls only; each refresh owns an independent fact set."""
    original_refresh = runtime._refresh.__func__
    original_globals = original_refresh.__globals__
    original_response = original_globals["_readiness_response"]
    protocol_error = original_globals["HTTPException"]
    original_owner = original_globals["process_matches_owner"]
    original_status = original_globals["ConnectionStatus"]
    original_datetime = original_globals["datetime"]
    runtime.fixture_diagnostics = {"state": "absent", "http": "not_attempted"}

    def refresh(self):
        facts = {"state": "absent", "http": "not_attempted"}
        self.fixture_diagnostics = facts
        saved, parsed = {}, {}

        def read():
            try:
                state = self._read_state()
            except OSError:
                facts["state"] = "unavailable"
                raise
            except ValueError:
                facts["state"] = "invalid"
                raise
            saved.update(state)
            facts.update(
                state="valid",
                profile_matches=state["profile_id"] == self.settings.profile_id,
                run_matches=state["run_id"] == self.launch_id,
                port_is_preferred=state["port"] == self.settings.admin_api_port,
            )
            return state

        def owner(*args):
            try:
                result = original_owner(*args)
            except Exception:
                facts["owner_check"] = "error"
                raise
            facts["owner_matches"] = result is True
            return result

        def validate(value):
            try:
                measured = original_status.model_validate(value)
            except Exception:
                facts["body"] = "validation_error"
                raise
            parsed["value"] = measured
            facts.update(
                body="valid",
                service_matches=measured.service.value == "runtime",
                measured_ready=measured.state.value == "ready",
                capabilities_match=measured.capabilities
                == (["setup"] if saved.get("mode") == "setup" else ["management"]),
            )
            if measured.checked_at is None:
                facts["age"] = "absent"
            return measured

        def now(*args):
            measured_now = original_datetime.now(*args)
            measured = parsed["value"]
            age = (measured_now - measured.checked_at).total_seconds()
            facts["age"] = "future" if age < 0 else "fresh" if age < 5 else "stale"
            return measured_now

        def response(*args, **kwargs):
            began = time.monotonic()
            facts["http"] = "pending"
            try:
                header, body = original_response(*args, **kwargs)
            except TimeoutError:
                facts["http"] = "timeout"
                raise
            except protocol_error:
                facts["http"] = "protocol_error"
                raise
            except OSError:
                facts["http"] = "transport_error"
                raise
            except ValueError as error:
                facts["http"] = {
                    "runtime_readiness_status_rejected": "status_rejected",
                    "runtime_readiness_body_oversized": "body_oversized",
                    "runtime_readiness_port_invalid": "port_invalid",
                }.get(str(error), "body_invalid")
                raise
            finally:
                facts["http_elapsed_ms"] = round((time.monotonic() - began) * 1000)
            facts["http"] = "response"
            facts["header_matches"] = header == saved.get("run_id")
            return header, body

        class Probe:
            # Only the cloned refresh receives this self proxy. Stop and all
            # other methods retain the real self and original state reader.
            def __getattr__(self, name):
                return read if name == "_read_state" else getattr(runtime, name)

            def __setattr__(self, name, value):
                setattr(runtime, name, value)

        globals_copy = dict(original_globals)
        globals_copy.update(
            _readiness_response=response,
            process_matches_owner=owner,
            ConnectionStatus=types.SimpleNamespace(model_validate=validate),
            datetime=types.SimpleNamespace(now=now),
        )
        probe = types.FunctionType(
            original_refresh.__code__,
            globals_copy,
            original_refresh.__name__,
            original_refresh.__defaults__,
            original_refresh.__closure__,
        )
        return probe(Probe())

    runtime._refresh = types.MethodType(refresh, runtime)
    return runtime


def fixture_readiness_diagnostics(runtime, began):
    """Only fixed codes/booleans/durations; never state, paths or error strings."""
    diagnostics = dict(getattr(runtime, "fixture_diagnostics", {}))
    starts = getattr(runtime, "fixture_starts", ())
    error_types = {"OSError", "ValueError", "RuntimeError", "TimeoutError"}
    errors = []
    for future in starts:
        if future.done() and not future.cancelled():
            error = future.exception()
            if error is not None:
                errors.append(
                    type(error).__name__ if type(error).__name__ in error_types else "other"
                )
    diagnostics.update(
        elapsed_ms=round((time.monotonic() - began) * 1000),
        start_done=all(future.done() for future in starts),
        start_cancelled=any(future.cancelled() for future in starts),
        start_errors=errors,
        owned_process_exists=runtime.process is not None,
        owned_process_alive=runtime.process is not None and runtime.process.poll() is None,
    )
    try:
        diagnostics["state_file_exists"] = runtime.state_file.exists()
        if diagnostics["state_file_exists"]:
            diagnostics["state_file_bounded"] = runtime.state_file.stat().st_size <= 8192
    except OSError:
        diagnostics["state_file_available"] = False
    if hasattr(runtime, "fixture_collision_occupied"):
        diagnostics["collision_occupied"] = runtime.fixture_collision_occupied()
    diagnostics.update(fixture_bootstrap_diagnostics(runtime))
    return diagnostics


def fixture_bootstrap_diagnostics(runtime):
    """Project only fixed stages/durations bound to this fixture launch."""
    path = getattr(runtime, "fixture_phase_file", None)
    if path is None or not path.exists():
        return {"bootstrap": "unavailable"}
    phases = {
        "imports_started", "imports_ready", "directories_started", "directories_ready",
        "security_started", "security_ready", "storage_open_started", "storage_open_ready",
        "migration_started", "migration_ready", "context_started", "context_ready",
        "server_starting", "health_started", "health_ready", "resume_started", "resume_ready",
        "state_write_started", "state_published",
    }
    try:
        if path.is_symlink() or path.stat().st_size > 8192:
            return {"bootstrap": "invalid"}
        with open_fixture_diagnostic(path) as stream:
            value = json.loads(stream.read(8193))
        expected = hashlib.sha256((runtime.launch_id or "").encode()).hexdigest()
        if not isinstance(value, dict) or value.get("run_fingerprint") != expected:
            return {"bootstrap": "stale", "bootstrap_run_matches": False}
        steps = value.get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
            return {"bootstrap": "invalid"}
        projected = []
        for step in steps:
            if (
                not isinstance(step, dict) or step.get("phase") not in phases
                or type(step.get("elapsed_ms")) is not int
                or not 0 <= step["elapsed_ms"] <= 86_400_000
            ):
                return {"bootstrap": "invalid"}
            projected.append({"phase": step["phase"], "elapsed_ms": step["elapsed_ms"]})
        terminal = value.get("terminal")
        if terminal not in {"running", "stopped", "worker_bootstrap_failed"}:
            return {"bootstrap": "invalid"}
        return {
            "bootstrap": "observed", "bootstrap_run_matches": True,
            "bootstrap_steps": projected, "bootstrap_terminal": terminal,
        }
    except (OSError, ValueError, TypeError):
        return {"bootstrap": "unavailable"}


def open_fixture_diagnostic(path):
    if os.name != "nt":
        return path.open("rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80000000, 7, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError("fixture_diagnostic_unavailable")
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        kernel.CloseHandle(handle)
        raise
    return os.fdopen(descriptor, "rb")


def controller(settings, tmp_path, *, instance_directory=None):
    lifecycle = module("tg_assistant.desktop.runtime_controller")
    runtime = observe_fixture_readiness(
        track_fixture_starts(
            lifecycle.RuntimeController(
                settings,
                worker_command=[
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "fixtures/launcher_worker.py"),
                    str(settings.data_dir),
                    str(instance_directory if instance_directory is not None else tmp_path / "sid-instance"),
                    str(settings.admin_api_port),
                ],
            )
        )
    )
    from tg_assistant.paths import current_user_sid

    runtime.fixture_worker_command = tuple(runtime.worker_command)
    runtime.fixture_owner_sid = current_user_sid()
    runtime.fixture_phase_file = settings.data_dir.parent / "synthetic-worker-phases.json"
    return runtime


def ready(runtime, *, timeout=30):
    began = time.monotonic()
    deadline = began + timeout
    while time.monotonic() < deadline:
        future = runtime.refresh()
        runtime.fixture_ready_future = future
        try:
            state = future.result(timeout=max(0, deadline - time.monotonic()))
        except FutureTimeoutError:
            future.cancel()
            break
        if state.phase == "ready":
            return state
        if state.phase == "error":
            runtime.fixture_failed_readiness = True
            pytest.fail(f"Synthetic worker failed: {state.code}")
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))
    diagnostics = json.dumps(fixture_readiness_diagnostics(runtime, began), sort_keys=True)
    runtime.fixture_failed_readiness = True
    pytest.fail(
        f"Worker did not reach readiness: {runtime.snapshot.code}; diagnostics={diagnostics}"
    )


def force_fixture_worker_exit(runtime, *, timeout=3):
    """Force only the original fixture Popen and its verified recorded worker."""
    import psutil

    from tg_assistant.desktop.instance import process_matches_owner
    from tg_assistant.paths import current_user_sid

    process = runtime.process
    expected = getattr(runtime, "fixture_worker_command", ())
    allowed = {Path(sys.executable).resolve(), Path(sys._base_executable).resolve()}
    if (
        not isinstance(process, subprocess.Popen)
        or not expected
        or Path(expected[0]).resolve() not in allowed
        or tuple(process.args) != expected
        or getattr(runtime, "fixture_owner_sid", None) != current_user_sid()
    ):
        return ["fixture_parent_not_owned"]
    deadline = time.monotonic() + timeout
    errors = []
    child = None
    recorded = getattr(runtime, "attached_process", None)
    if recorded is None and getattr(runtime, "_stop_sent", False) is True:
        target = getattr(runtime, "_stop_target", None)
        if (
            isinstance(target, tuple) and len(target) == 3
            and isinstance(target[0], str) and target[0] == runtime.launch_id
            and type(target[1]) is int and target[1] > 0
            and type(target[2]) in {int, float} and target[2] > 0
            and math.isfinite(target[2])
        ):
            # stop() already verified this exact incarnation before atomic
            # publication; its sent fast path may precede an attaching refresh.
            # The child still passes every live SID/exe/command/parent check below.
            recorded = target[1:]
    if recorded is None:
        errors.append("fixture_worker_identity_unavailable")
    elif recorded[0] != process.pid:
        try:
            child = psutil.Process(recorded[0])
            if (
                not process_matches_owner(*recorded)
                or abs(child.create_time() - recorded[1]) >= 0.001
                or Path(child.exe()).resolve() not in allowed
                or child.cmdline()[1:] != list(expected[1:])
                or child.ppid() != process.pid
            ):
                errors.append("fixture_child_not_owned")
                child = None
            else:
                # Same captured Process retains psutil's incarnation checks.
                child.kill()
        except psutil.NoSuchProcess:
            child = None
        except psutil.Error:
            errors.append("fixture_child_cleanup_failed")
            child = None
    try:
        if process.poll() is None:
            process.kill()  # Windows uses the original Popen kernel handle.
        process.wait(timeout=max(0, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired):
        errors.append("fixture_parent_cleanup_failed")
    if child is not None:
        try:
            child.wait(timeout=max(0, deadline - time.monotonic()))
        except psutil.NoSuchProcess:
            pass
        except psutil.Error:
            errors.append("fixture_child_exit_unconfirmed")
    return errors


def stop(runtime, *, timeout=15):
    deadline = time.monotonic() + timeout
    phase = "producer"
    failures = []
    with getattr(runtime, "fixture_producer_lock", nullcontext()):
        runtime.fixture_closing = True
        producers = tuple(getattr(runtime, "fixture_starts", ()))
        pending_start = any(not future.done() for future in producers) or (
            bool(producers) and runtime.process is not None
            and runtime.process.poll() is None and runtime.attached_process is None
            and getattr(runtime, "_stop_sent", False) is not True
        )
    try:
        runtime.stop()
        stop_published_at = (
            time.monotonic() if getattr(runtime, "_stop_sent", False) is True else None
        )
        # A running start can assign Popen after stop(). Wait for fixture-owned
        # starts; cancel queued ones before claiming cleanup is complete.
        for future in producers:
            if not future.cancel():
                try:
                    future.result(timeout=max(0, deadline - time.monotonic()))
                    if time.monotonic() >= deadline:
                        failures.append("Fixture producer exceeded deadline")
                except Exception as error:
                    failures.append(
                        type(error).__name__ if type(error).__name__ in {
                            "OSError", "ValueError", "RuntimeError", "TimeoutError",
                        } else "fixture_start_failed"
                    )
                    # A running producer cannot be cancelled. Do not return
                    # while it can still assign a Popen after cleanup.
                    runtime.executor.shutdown(wait=True, cancel_futures=True)
        if pending_start and runtime.process is not None and runtime.process.poll() is None:
            # Popen creation does not include cold imports/migrations. Acquire a
            # real bound stop separately; neither phase renews on a failed poll.
            if getattr(runtime, "_stop_sent", False) is True:
                phase = "drain"
                if stop_published_at is not None:
                    deadline = stop_published_at + timeout
                # An unmeasured publication during producer drain retains the
                # earlier deadline instead of inventing a later publication.
            else:
                deadline = time.monotonic() + timeout
                phase = "acquisition"
        runtime.stop()
        # Check publication timing even if the worker exited inside stop().
        while runtime.process is not None:
            # Refresh may record a venv-launcher's actual worker child after
            # a slow producer completes. Only that verified identity may be forced.
            runtime._refresh()
            runtime.stop()
            if phase == "acquisition" and getattr(runtime, "_stop_sent", False) is True:
                published_at = time.monotonic()
                if published_at >= deadline:
                    failures.append("Bound stop acquisition expired")
                    failures.extend(force_fixture_worker_exit(runtime))
                    break
                # The real controller has atomically published an identity-bound
                # stop. Only now does the original graceful-drain budget begin.
                deadline = published_at + timeout
                phase = "drain"
            if runtime.process.poll() is not None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failures.append("Owned synthetic worker did not stop")
                failures.extend(force_fixture_worker_exit(runtime))
                break
            # The original executor may already be closed after launcher reopen.
            # Real refresh verifies this profile/SID/incarnation before shutdown.
            try:
                runtime.process.wait(timeout=min(0.1, remaining))
            except subprocess.TimeoutExpired:
                pass
    finally:
        runtime.close()
        if getattr(runtime, "fixture_failed_readiness", False):
            late = getattr(runtime, "fixture_ready_future", None)
            terminal = fixture_bootstrap_diagnostics(runtime)
            terminal.update(
                late_refresh_done=late is not None and late.done(),
                late_refresh_cancelled=late is not None and late.cancelled(),
                owned_process_alive=runtime.process is not None and runtime.process.poll() is None,
                cleanup_failed=bool(failures),
            )
            if late is not None and late.done() and not late.cancelled():
                try:
                    measured = late.result()
                    terminal["late_refresh_phase"] = (
                        measured.phase if measured.phase in {
                            "unknown", "starting", "ready", "stopping", "stopped", "error",
                        } else "other"
                    )
                except BaseException:
                    terminal["late_refresh_phase"] = "failed"
            print("Fixture terminal: " + json.dumps(terminal, sort_keys=True))
    if failures:
        cleanup = pytest.fail.Exception("Fixture cleanup failed: " + "; ".join(failures))
        primary = sys.exception()
        if primary is not None:
            raise BaseExceptionGroup("Fixture body and cleanup failed", [primary, cleanup])
        raise cleanup


def held_start(settings, tmp_path, monkeypatch):
    import threading

    runtime = controller(settings, tmp_path)
    entered, release = threading.Event(), threading.Event()
    original_start = runtime._start

    def start():
        entered.set()
        assert release.wait(10), "Synthetic held startup was not released"
        original_start()

    monkeypatch.setattr(runtime, "_start", start)
    startup = runtime.start()
    assert entered.wait(2)
    assert runtime.process is None
    return runtime, startup, release


def test_ready_waits_for_slow_start_within_overall_deadline(settings, tmp_path, monkeypatch):
    import threading

    runtime, startup, release = held_start(settings, tmp_path, monkeypatch)
    timer = threading.Timer(2.2, release.set)
    timer.start()
    try:
        assert ready(runtime).phase == "ready"
    finally:
        release.set()
        stop(runtime)
        timer.join()
    assert startup.done() and runtime.process.returncode == 0


def test_ready_deadline_failure_survives_pending_start_cleanup(settings, tmp_path, monkeypatch):
    runtime, startup, release = held_start(settings, tmp_path, monkeypatch)
    began = time.monotonic()
    try:
        with pytest.raises(pytest.fail.Exception, match="Worker did not reach readiness"):
            ready(runtime, timeout=0.1)
        assert time.monotonic() - began < 0.5
    finally:
        release.set()
        stop(runtime)
    assert startup.done() and runtime.process.returncode == 0


@pytest.mark.parametrize("bootstrap_floor, cleanup_fails, exit_during_stop", [
    (8, False, False), (16, True, False), (16, True, True),
])
def test_cleanup_pending_start_keeps_acquisition_and_drain_deadlines_separate(
    settings, tmp_path, monkeypatch, bootstrap_floor, cleanup_fails, exit_during_stop,
):
    import threading

    from tg_assistant.desktop.instance import process_incarnation_exists

    real_time = time

    class Clock:
        elapsed = 0.0

        def monotonic(self):
            return real_time.monotonic() + self.elapsed

        def sleep(self, seconds):
            real_time.sleep(seconds)

        def require_elapsed(self, began, minimum):
            # A slow runner's real elapsed time already counts. Never add the
            # simulated minimum on top of actual cold-bootstrap duration.
            self.elapsed += max(0, began + minimum - self.monotonic())

    clock = Clock()
    runtime, startup, release = held_start(settings, tmp_path, monkeypatch)
    original_result, original_stop = startup.result, runtime.stop
    producer_charged = False
    producer_began = clock.monotonic()
    acquisition_began = None
    forced = []
    original_force = force_fixture_worker_exit
    state_release = tmp_path / "state-release"
    original_refresh = runtime._refresh
    if exit_during_stop:
        runtime.worker_command.append(str(state_release))
        runtime.fixture_worker_command = tuple(runtime.worker_command)

    def refresh():
        if exit_during_stop and producer_charged:
            state_release.touch()
        return original_refresh()

    def producer_result(*args, **kwargs):
        nonlocal producer_charged, acquisition_began
        result = original_result(*args, **kwargs)
        # Charge only the helper's clock for actual producer completion.
        # Product clocks and the real worker's identity/health are untouched.
        if not producer_charged:
            clock.require_elapsed(producer_began, 8)
            acquisition_began = clock.monotonic()
            producer_charged = True
        return result

    def publish_stop():
        was_sent = runtime._stop_sent
        original_stop()
        if not was_sent and runtime._stop_sent:
            assert runtime._stop_target[0] == runtime.launch_id
            # The real marker was bound to the actual worker before this charge.
            if exit_during_stop:
                runtime.process.wait(timeout=15)
            assert acquisition_began is not None
            clock.require_elapsed(acquisition_began, bootstrap_floor)

    def force(*args, **kwargs):
        forced.append(True)
        return original_force(*args, **kwargs)

    monkeypatch.setattr(startup, "result", producer_result)
    monkeypatch.setattr(runtime, "stop", publish_stop)
    monkeypatch.setattr(runtime, "_refresh", refresh)
    monkeypatch.setattr(sys.modules[__name__], "time", clock)
    monkeypatch.setattr(sys.modules[__name__], "force_fixture_worker_exit", force)
    timer = threading.Timer(0.2, release.set)
    timer.start()
    try:
        if cleanup_fails:
            with pytest.raises(pytest.fail.Exception, match="Bound stop acquisition expired"):
                stop(runtime)
            assert forced, "Expired acquisition must retain independent cleanup failure"
        else:
            stop(runtime)
            assert runtime.process.returncode == 0
            assert not forced, "Emergency forced exit cannot prove graceful cleanup"
        assert startup.done() and runtime._stop_sent
        assert runtime.process.returncode is not None
        recorded = runtime.attached_process or runtime._stop_target[1:]
        assert not process_incarnation_exists(*recorded)
    finally:
        release.set()
        state_release.touch()
        # Real time remains available for independent leak cleanup if RED fails.
        monkeypatch.setattr(sys.modules[__name__], "time", real_time)
        stop(runtime)
        timer.join()


def test_cleanup_running_start_proves_exact_owned_worker_exit(settings, tmp_path, monkeypatch):
    import threading

    from tg_assistant.desktop.instance import process_incarnation_exists

    runtime, startup, release = held_start(settings, tmp_path, monkeypatch)
    timer = threading.Timer(0.2, release.set)
    timer.start()
    try:
        stop(runtime)
        assert startup.done(), "Cleanup returned while fixture startup was running"
        assert runtime.process is not None and runtime.process.returncode == 0
        assert runtime.attached_process is not None
        assert not process_incarnation_exists(*runtime.attached_process)
    finally:
        release.set()
        stop(runtime)
        timer.join()


def test_force_cleanup_uses_bound_stop_identity_before_attaching_refresh(settings, tmp_path):
    from tg_assistant.desktop.instance import process_incarnation_exists

    runtime = controller(settings, tmp_path)
    target = None
    try:
        startup = runtime.start()
        startup.result(timeout=15)
        deadline = time.monotonic() + 15
        # Deliberately do not refresh: stop itself reads and validates the real
        # profile/owner/run/PID incarnation before it publishes its marker.
        while not runtime._stop_sent and time.monotonic() < deadline:
            runtime.stop()
            time.sleep(0.01)
        assert runtime._stop_sent and runtime.attached_process is None
        target = runtime._stop_target
        assert target[0] == runtime.launch_id
        errors = force_fixture_worker_exit(runtime)
        assert not errors
        assert runtime.process.returncode is not None
        assert not process_incarnation_exists(target[1], target[2])
    finally:
        if target is not None:
            # Independent leak cleanup of the actual already-verified target if
            # the original helper fails to follow the Windows launcher child.
            runtime.attached_process = (target[1], target[2])
        stop(runtime)


def test_cleanup_failure_preserves_primary_failure_and_owned_exit(settings, tmp_path, monkeypatch):
    from tg_assistant.desktop.instance import process_incarnation_exists

    runtime = controller(settings, tmp_path)
    primary = pytest.fail.Exception("fixture_primary_failure")
    try:
        runtime.start()
        ready(runtime)
        with monkeypatch.context() as patch:
            # Real owned child stays live until cleanup reaches its forced-exit
            # fallback; simulate the independently failing stop publication.
            patch.setattr(runtime, "stop", lambda: None)
            with pytest.raises(BaseExceptionGroup) as caught:
                try:
                    raise primary
                finally:
                    stop(runtime, timeout=0.01)
        assert caught.value.exceptions[0] is primary
        assert "Fixture cleanup failed" in str(caught.value.exceptions[1])
        assert runtime.process.returncode is not None
        assert not process_incarnation_exists(*runtime.attached_process)
    finally:
        stop(runtime)


def test_first_launch_setup(app, settings, tmp_path):
    desktop = module("tg_assistant.desktop.app")
    runtime = controller(settings, tmp_path)
    window = desktop.LauncherWindow(runtime)
    try:
        window.show()
        QTest.qWait(50)
        assert window.isVisible()
        state = ready(runtime)
        assert state.mode == "setup" and state.owner_id is None
        assert state.url.startswith("http://127.0.0.1:")
        assert (settings.data_dir / "db/assistant.sqlite3").exists()
        assert window.title.text() == "Telegram AI"
    finally:
        window.close()
        stop(runtime)


def test_existing_profile_resume(settings, tmp_path):
    from tg_assistant.contracts import PublicProfile
    from tg_assistant.services.storage import StorageService

    service = StorageService(settings)
    db = service.open(
        PublicProfile(
            profile_id="default",
            owner_id=None,
            storage_backend="sqlite",
            setup_stage="welcome",
            version=1,
        )
    )
    service.migrate()
    import asyncio
    import sqlite3

    asyncio.run(db.close())
    location = settings.data_dir / "db/assistant.sqlite3"
    with sqlite3.connect(location) as connection:
        connection.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            ("desktop_resume_fixture", json.dumps("Giữ dữ liệu")),
        )
    runtime = controller(settings, tmp_path)
    try:
        runtime.start()
        state = ready(runtime)
        assert state.mode == "setup"
        with sqlite3.connect(location) as connection:
            assert (
                json.loads(
                    connection.execute(
                        "SELECT value FROM app_settings WHERE key='desktop_resume_fixture'"
                    ).fetchone()[0]
                )
                == "Giữ dữ liệu"
            )
    finally:
        stop(runtime)


def test_singleton_same_sid(tmp_path):
    instance = module("tg_assistant.desktop.instance")
    root = tmp_path / "sid-lock"
    code = (
        "from pathlib import Path; import sys; "
        "from tg_assistant.desktop.instance import InstanceGuard, AlreadyRunning; "
        "guard=InstanceGuard(Path(sys.argv[1])); "
        "\ntry: guard.acquire()\nexcept AlreadyRunning: sys.exit(17)\n"
        "else: guard.close()"
    )
    with instance.InstanceGuard(root):
        result = subprocess.run(
            [sys.executable, "-c", code, str(root)], capture_output=True, timeout=10, check=False
        )
        assert result.returncode == 17
    result = subprocess.run(
        [sys.executable, "-c", code, str(root)], capture_output=True, timeout=10, check=False
    )
    assert result.returncode == 0


def test_stop_restart(settings, tmp_path):
    runtime = controller(settings, tmp_path)
    try:
        runtime.start()
        first = ready(runtime)
        runtime.stop()
        runtime.process.wait(timeout=15)
        assert runtime.refresh().result(timeout=2).phase == "stopped"
        runtime.start()
        second = ready(runtime)
        assert second.run_id != first.run_id
        assert second.pid != first.pid
    finally:
        stop(runtime)


def test_port_collision(settings, tmp_path):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        preferred = occupied.getsockname()[1]
        settings = settings.model_copy(update={"admin_api_port": preferred})
        runtime = controller(settings, tmp_path)
        runtime.fixture_collision_occupied = lambda: occupied.fileno() >= 0
        try:
            runtime.start()
            state = ready(runtime)
            assert state.port != preferred
            assert state.url == f"http://127.0.0.1:{state.port}"
            assert state.mode == "setup"
        finally:
            stop(runtime)


def test_no_console_required(app, settings, tmp_path):
    desktop = module("tg_assistant.desktop.app")
    runtime = controller(settings, tmp_path)
    window = desktop.LauncherWindow(runtime)
    try:
        window.show()
        QTest.qWait(50)
        ready(runtime)
        assert runtime.spawn_options["stdin"] == subprocess.DEVNULL
        if os.name == "nt":
            assert runtime.spawn_options["creationflags"] & subprocess.CREATE_NO_WINDOW
        window.close()
        assert runtime.process.poll() is None
    finally:
        stop(runtime)


def test_readiness_failure_does_not_stay_ready(settings, tmp_path):
    runtime = controller(settings, tmp_path)
    try:
        runtime.start()
        state = ready(runtime)
        runtime.stop()
        runtime.process.wait(timeout=15)
        # Reopening a launcher must not display an old successful measurement.
        state_path = runtime.state_file
        state_path.write_text(
            json.dumps(
                {
                    "profile_id": settings.profile_id,
                    "pid": state.pid,
                    "port": state.port,
                    "run_id": state.run_id,
                    "mode": "setup",
                    "owner_id": None,
                }
            ),
            encoding="utf-8",
        )
        detached = module("tg_assistant.desktop.runtime_controller").RuntimeController(settings)
        detached.snapshot = state
        try:
            measured = detached.refresh().result(timeout=2)
            assert measured.phase != "ready" and measured.url is None
        finally:
            detached.close()
    finally:
        stop(runtime)


def test_stop_during_queued_start_is_not_lost(settings, tmp_path):
    import threading

    runtime = controller(settings, tmp_path)
    held = threading.Event()
    queued = runtime.executor.submit(held.wait, 5)
    try:
        started = runtime.start()
        runtime.stop()
        assert runtime.stop_requested is True
        assert runtime._stop_sent is False
        held.set()
        queued.result(timeout=2)
        started.result(timeout=2)
        assert runtime.stop_requested is True
        began = time.monotonic()
        deadline = began + 15
        while time.monotonic() < deadline:
            state = runtime.refresh().result(timeout=2)
            if state.phase == "stopped":
                break
            time.sleep(0.05)
        assert state.phase == "stopped", queued_stop_diagnostics(runtime, began, state)
    finally:
        held.set()
        stop(runtime)


def queued_stop_diagnostics(runtime, began, state):
    """Passive fixed facts; cleanup cannot replace the original stop result."""
    facts = fixture_readiness_diagnostics(runtime, began)
    target = runtime._stop_target
    facts.update(
        original_stop_intent=runtime.stop_requested is True,
        bound_stop_published=runtime._stop_sent is True,
        stop_target_present=target is not None,
        stop_target_launch_matches=target is not None and target[0] == runtime.launch_id,
        attached_identity_present=runtime.attached_process is not None,
        observed_phase=state.phase if state.phase in {
            "unknown", "starting", "ready", "stopping", "stopped", "error",
        } else "other",
        observed_code=state.code if state.code in {
            "runtime_starting", "runtime_readiness_unavailable", "runtime_readiness_stale",
            "runtime_not_ready", "runtime_ready", "runtime_stopping", "runtime_stopped",
            "runtime_start_failed",
        } else "other",
    )
    return "Original queued stop did not finish; diagnostics=" + json.dumps(facts, sort_keys=True)


def test_gateway_checks_exact_host_and_origin(settings, tmp_path):
    import httpx

    runtime = controller(settings, tmp_path)
    try:
        runtime.start()
        state = ready(runtime)
        url = state.url + "/api/v1/runtime/readiness"
        with httpx.Client(trust_env=False, timeout=2) as client:
            assert client.get(url).status_code == 200
            assert client.get(url, headers={"Host": "127.0.0.1:1"}).status_code == 403
            assert client.get(url, headers={"Origin": "https://example.invalid"}).status_code == 403
            assert client.get(url, headers={"Host": "example.invalid"}).status_code == 403
    finally:
        stop(runtime)


def test_native_tray_exit_options(app, settings, tmp_path):
    from PySide6.QtCore import Qt

    tray_module = module("tg_assistant.desktop.tray")
    desktop = module("tg_assistant.desktop.app")
    runtime = controller(settings, tmp_path)
    window = desktop.LauncherWindow(runtime)
    try:
        window.show()
        QTest.qWait(50)
        ready(runtime)
        for choice in ("background", "stop", "cancel"):
            dialog = tray_module.ExitDialog(window)
            dialog.show()
            QTest.qWait(10)
            QTest.mouseClick(dialog.buttons[choice], Qt.LeftButton)
            assert dialog.choice == choice
            assert not dialog.isVisible()
        tray = tray_module.TrayController(window, runtime, quit_application=lambda: None)
        try:
            tray.finish_exit("cancel")
            assert runtime.process.poll() is None
            tray.finish_exit("background")
            assert runtime.process.poll() is None
            tray.finish_exit("stop")
            deadline = time.monotonic() + 15
            while runtime.process.poll() is None and time.monotonic() < deadline:
                QTest.qWait(50)
            assert runtime.process.poll() == 0
        finally:
            tray.close()
    finally:
        window.close()
        stop(runtime)


def test_start_failure_is_sanitized_and_retryable(settings, tmp_path):
    lifecycle = module("tg_assistant.desktop.runtime_controller")
    private_marker = "PRIVATE_DIAGNOSTIC_DO_NOT_DISPLAY"
    runtime = lifecycle.RuntimeController(settings, worker_command=[str(tmp_path / private_marker)])
    try:
        runtime.start().result(timeout=2)
        state = runtime.refresh().result(timeout=2)
        assert state.phase == "error" and state.code == "runtime_start_failed"
        assert private_marker not in repr(state)
        valid = controller(settings, tmp_path)
        runtime.worker_command = valid.worker_command
        valid.close()
        runtime.start().result(timeout=2)
        assert ready(runtime).phase == "ready"
    finally:
        stop(runtime)


def test_foreign_profile_not_overwritten(settings, tmp_path):
    from tg_assistant.paths import ensure_runtime_dirs

    ensure_runtime_dirs(settings.data_dir, profile_id="other-profile")
    marker = settings.data_dir / ".tg-assistant-data"
    original = marker.read_bytes()
    runtime = controller(settings, tmp_path)
    try:
        runtime.start().result(timeout=2)
        state = runtime.refresh().result(timeout=2)
        assert state.phase == "error" and state.code == "runtime_start_failed"
        assert marker.read_bytes() == original
        assert not (settings.data_dir / "db/assistant.sqlite3").exists()
    finally:
        stop(runtime)


def _assert_private_acl(path, *, protected):
    """Inspect the actual OS descriptor/ACEs, independently of SDDL writing."""
    import ctypes
    from ctypes import wintypes

    from tg_assistant.paths import current_user_sid

    class AclSizeInformation(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    class AceHeader(ctypes.Structure):
        _fields_ = [
            ("AceType", wintypes.BYTE),
            ("AceFlags", wintypes.BYTE),
            ("AceSize", wintypes.WORD),
        ]

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.POINTER(ctypes.c_void_p)
    api.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        pointer, pointer, pointer, pointer, pointer,
    ]
    api.GetNamedSecurityInfoW.restype = wintypes.DWORD
    api.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD),
    ]
    api.GetSecurityDescriptorControl.restype = wintypes.BOOL
    api.IsValidAcl.argtypes = [ctypes.c_void_p]
    api.IsValidAcl.restype = wintypes.BOOL
    api.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int]
    api.GetAclInformation.restype = wintypes.BOOL
    api.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, pointer]
    api.GetAce.restype = wintypes.BOOL
    api.IsValidSid.argtypes = [ctypes.c_void_p]
    api.IsValidSid.restype = wintypes.BOOL
    api.GetLengthSid.argtypes = [ctypes.c_void_p]
    api.GetLengthSid.restype = wintypes.DWORD
    api.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    api.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p

    def sid_text(sid):
        text = wintypes.LPWSTR()
        try:
            assert api.IsValidSid(sid), "acl_sid_invalid"
            assert api.ConvertSidToStringSidW(sid, ctypes.byref(text)), "acl_sid_read"
            return text.value
        finally:
            if text:
                kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))

    owner, acl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    result = api.GetNamedSecurityInfoW(
        str(path), 1, 1 | 4, ctypes.byref(owner), None, ctypes.byref(acl), None,
        ctypes.byref(descriptor),
    )
    try:
        assert result == 0 and descriptor and owner and acl, "acl_descriptor_read"
        control, revision = wintypes.WORD(), wintypes.DWORD()
        assert api.GetSecurityDescriptorControl(
            descriptor, ctypes.byref(control), ctypes.byref(revision),
        ), "acl_control_read"
        assert bool(control.value & 0x1000) is protected, "acl_protection"
        expected_sid = current_user_sid()
        assert sid_text(owner) == expected_sid, "acl_owner"
        assert api.IsValidAcl(acl), "acl_invalid"
        info = AclSizeInformation()
        assert api.GetAclInformation(
            acl, ctypes.byref(info), ctypes.sizeof(info), 2,
        ), "acl_information_read"
        assert 8 <= info.AclBytesInUse <= 65535, "acl_bounds"
        assert 0 < info.AceCount <= (info.AclBytesInUse - 8) // 4, "acl_bounds"
        entries = []
        for index in range(info.AceCount):
            ace = ctypes.c_void_p()
            assert api.GetAce(acl, index, ctypes.byref(ace)) and ace, "acl_ace_read"
            assert acl.value + 8 <= ace.value <= acl.value + info.AclBytesInUse - 4, "acl_bounds"
            header = AceHeader.from_address(ace.value)
            assert 16 <= header.AceSize <= acl.value + info.AclBytesInUse - ace.value, "acl_bounds"
            assert header.AceType == 0, "acl_ace_type"  # ACCESS_ALLOWED_ACE only.
            mask = wintypes.DWORD.from_address(ace.value + 4).value
            sid = ace.value + 8
            sid_size = 8 + 4 * wintypes.BYTE.from_address(sid + 1).value
            assert sid_size <= header.AceSize - 8, "acl_bounds"
            assert api.IsValidSid(sid) and api.GetLengthSid(sid) == sid_size, "acl_sid_invalid"
            entries.append((sid_text(sid), mask, header.AceFlags))
        assert len(entries) == 2 and {entry[0] for entry in entries} == {
            expected_sid, "S-1-5-18",
        }, "acl_principals"
        for _sid, mask, flags in entries:
            assert mask == 0x001F01FF, "acl_rights"  # FILE_ALL_ACCESS.
            # Root grants apply here and to children; file grants are inherited.
            assert flags == (0x03 if path.is_dir() else 0x10), "acl_inheritance"
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)


def _replace_test_dacl(path, sddl):
    """Alter only a disposable negative fixture; never call a product writer."""
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
    ]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    api.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    api.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    api.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p,
    ]
    api.SetNamedSecurityInfoW.restype = wintypes.DWORD
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    assert api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None,
    ), "negative_acl_descriptor"
    try:
        present, defaulted, acl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        assert api.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted),
        ) and present and acl, "negative_acl_dacl"
        assert api.SetNamedSecurityInfoW(
            str(path), 1, 4 | 0x80000000, None, None, acl, None,
        ) == 0, "negative_acl_write"
    finally:
        kernel.LocalFree(descriptor)


@pytest.mark.skipif(os.name != "nt", reason="Windows NTFS protected DACL evidence")
def test_owned_directory_acl_and_inherited_file(tmp_path, monkeypatch):
    def refuse_shell(*_args, **_kwargs):
        pytest.fail("acl_reader_must_not_spawn_shell")

    monkeypatch.setattr(subprocess, "run", refuse_shell)
    instance = module("tg_assistant.desktop.instance")
    root = tmp_path / "private"
    instance.secure_directory(root)
    sample = root / "sample"
    sample.write_text("synthetic", encoding="utf-8")
    for path, protected in ((root, True), (sample, False)):
        _assert_private_acl(path, protected=protected)


@pytest.mark.skipif(os.name != "nt", reason="Windows real ACL reader negative evidence")
@pytest.mark.parametrize("changed", ["rights", "inheritance", "principals"])
def test_acl_reader_rejects_actual_weakened_directory(tmp_path, changed):
    from tg_assistant.paths import current_user_sid

    instance = module("tg_assistant.desktop.instance")
    root = tmp_path / "negative-private"
    instance.secure_directory(root)
    sid = current_user_sid()
    rights = "FRWDSD" if changed == "rights" else "FA"
    inheritance = "" if changed == "inheritance" else "OICI"
    extra = "(A;OICI;FR;;;WD)" if changed == "principals" else ""
    try:
        _replace_test_dacl(
            root, f"D:P(A;{inheritance};{rights};;;{sid})(A;{inheritance};{rights};;;SY){extra}",
        )
        with pytest.raises(AssertionError, match=f"acl_{changed}"):
            _assert_private_acl(root, protected=True)
    finally:
        # Restore cleanup rights using the original, unchanged production writer.
        instance.secure_directory(root)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction refusal")
def test_directory_junction_does_not_change_target_acl(tmp_path):
    instance = module("tg_assistant.desktop.instance")
    target, junction = tmp_path / "outside", tmp_path / "link"
    target.mkdir()
    # Only fixed cmd syntax and generated fixture paths; no filesystem deletion.
    created = subprocess.run(
        [shutil.which("cmd.exe"), "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert created.returncode == 0 and junction.is_junction()
    with pytest.raises(OSError, match="storage_access_denied"):
        instance.secure_directory(junction)


def test_reused_port_cannot_adopt_dead_incarnation(settings, tmp_path):
    import http.server
    import threading
    from datetime import UTC, datetime

    from tg_assistant.contracts import ConnectionStatus
    from tg_assistant.paths import ensure_runtime_dirs

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-TG-Runtime-ID", "1" * 32)
            self.end_headers()
            payload = ConnectionStatus(
                service="runtime",
                state="ready",
                checked_at=datetime.now(UTC),
                code="runtime_ready",
                message="Synthetic",
                next_action=None,
                capabilities=["setup"],
            )
            self.wfile.write(payload.model_dump_json().encode())

        def log_message(self, *args):
            pass

    ensure_runtime_dirs(settings.data_dir, profile_id=settings.profile_id)
    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runtime = module("tg_assistant.desktop.runtime_controller").RuntimeController(settings)
    runtime.state_file.write_text(
        json.dumps(
            {
                "profile_id": settings.profile_id,
                "pid": 999999999,
                "process_started_at": 1.0,
                "port": server.server_port,
                "run_id": "1" * 32,
                "mode": "assistant",
                "owner_id": "123",
            }
        ),
        encoding="utf-8",
    )
    try:
        measured = runtime.refresh().result(timeout=2)
        assert measured.phase != "ready" and measured.owner_id is None
    finally:
        runtime.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_reopened_launcher_tray_stop_finishes(app, settings, tmp_path):
    runtime = controller(settings, tmp_path)
    runtime.start()
    try:
        first = ready(runtime)
        # Closing and reopening the launcher retains the owned worker.
        runtime.close()
        detached = track_fixture_starts(
            module("tg_assistant.desktop.runtime_controller").RuntimeController(settings)
        )
        window = module("tg_assistant.desktop.app").LauncherWindow(detached)
        window.show()
        QTest.qWait(50)
        exited = []
        tray = module("tg_assistant.desktop.tray").TrayController(
            window,
            detached,
            quit_application=lambda: exited.append(True),
        )
        try:
            assert ready(detached).run_id == first.run_id
            assert detached.process is None
            tray.finish_exit("stop")
            deadline = time.monotonic() + 15
            while not exited and time.monotonic() < deadline:
                QTest.qWait(50)
            assert exited == [True]
            runtime.process.wait(timeout=2)
            assert runtime.process.returncode == 0
        finally:
            tray.close()
            window.close()
            stop(detached)
    finally:
        stop(runtime)


def test_live_worker_identity_and_capability_mismatch_refused(settings, tmp_path):
    runtime = controller(settings, tmp_path)
    detached = module("tg_assistant.desktop.runtime_controller").RuntimeController(settings)
    try:
        runtime.start()
        ready(runtime)
        original = runtime.state_file.read_text(encoding="utf-8")
        state = json.loads(original)
        for change in ({"run_id": "2" * 32}, {"mode": "assistant", "owner_id": "123"}):
            runtime.state_file.write_text(json.dumps({**state, **change}), encoding="utf-8")
            measured = detached.refresh().result(timeout=2)
            assert measured.phase != "ready" and measured.owner_id is None
        runtime.state_file.write_text(original, encoding="utf-8")
        assert ready(detached).run_id == state["run_id"]
    finally:
        detached.close()
        stop(runtime)


def test_malformed_saved_state_is_sanitized_and_start_recovers(settings, tmp_path):
    from tg_assistant.paths import ensure_runtime_dirs

    ensure_runtime_dirs(settings.data_dir, profile_id=settings.profile_id)
    runtime = controller(settings, tmp_path)
    valid_shape = {
        "profile_id": settings.profile_id,
        "pid": os.getpid(),
        "process_started_at": 1.0,
        "port": 8765,
        "run_id": "1" * 32,
        "mode": "setup",
        "owner_id": None,
    }
    try:
        for broken in (123, True, [], None, "malformed-private-value"):
            runtime.state_file.write_text(
                json.dumps({**valid_shape, "run_id": broken}), encoding="utf-8"
            )
            state = runtime.refresh().result(timeout=2)
            assert state.phase != "ready" and state.url is None
            assert "malformed-private-value" not in repr(state)
        for change in ({"mode": []}, {"process_started_at": 10**1000}):
            runtime.state_file.write_text(json.dumps({**valid_shape, **change}), encoding="utf-8")
            state = runtime.refresh().result(timeout=2)
            assert state.phase != "ready"
            runtime.snapshot = module("tg_assistant.desktop.runtime_controller").RuntimeSnapshot(
                "starting", "runtime_starting"
            )
            runtime.stop()
        runtime.state_file.write_text("[" * 3000 + "0" + "]" * 3000, encoding="utf-8")
        assert runtime.refresh().result(timeout=2).phase != "ready"
        runtime.snapshot = module("tg_assistant.desktop.runtime_controller").RuntimeSnapshot(
            "starting", "runtime_starting"
        )
        runtime.stop()
        runtime.start().result(timeout=2)
        assert ready(runtime).phase == "ready"
    finally:
        stop(runtime)


@pytest.mark.skipif(os.name != "nt", reason="Windows legacy protected file ACL")
def test_private_tree_secures_existing_protected_file_without_content_changes(tmp_path):
    from tg_assistant.paths import current_user_sid

    instance = module("tg_assistant.desktop.instance")
    root = tmp_path / "owned"
    root.mkdir()
    sample = root / "legacy-file"
    sample.write_bytes(b"preserve synthetic bytes")
    literal = str(sample).replace("'", "''")
    command = (
        "$a=[System.IO.File]::GetAccessControl('" + literal + "');"
        "$a.SetAccessRuleProtection($true,$false);"
        "$s=[System.Security.Principal.SecurityIdentifier]::new('S-1-1-0');"
        "$r=[System.Security.AccessControl.FileSystemAccessRule]::new($s,'Read','Allow');"
        "$a.AddAccessRule($r); [System.IO.File]::SetAccessControl('" + literal + "',$a)"
    )
    created = subprocess.run(
        [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        timeout=30,
    )
    assert created.returncode == 0, created.stderr.decode(errors="replace")
    assert hasattr(instance, "secure_tree"), "Existing protected file ACLs are not repaired"
    instance.secure_tree(root)
    command = (
        "$a=[System.IO.File]::GetAccessControl('" + literal + "');"
        "@{protected=$a.AreAccessRulesProtected;sids=@($a.Access|ForEach-Object {$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value})}|ConvertTo-Json -Compress"
    )
    measured = subprocess.run(
        [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        # Hosted Windows ACL inspection exceeded ten seconds before returning a descriptor.
        timeout=30,
    )
    assert measured.returncode == 0, measured.stderr.decode(errors="replace")
    descriptor = json.loads(measured.stdout)
    assert descriptor["protected"] is True
    assert set(descriptor["sids"]) == {current_user_sid(), "S-1-5-18"}
    assert sample.read_bytes() == b"preserve synthetic bytes"


@pytest.mark.skipif(os.name != "nt", reason="Windows hardlink ACL propagation")
def test_tree_hardlink_refusal_preserves_outside_descriptor(tmp_path):
    instance = module("tg_assistant.desktop.instance")
    root = tmp_path / "owned"
    root.mkdir()
    outside = tmp_path / "outside-file"
    outside.write_bytes(b"outside synthetic content")
    os.link(outside, root / "linked-file")
    literal = str(outside).replace("'", "''")
    command = (
        "[System.IO.File]::GetAccessControl('"
        + literal
        + "').GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::All)"
    )

    def descriptor():
        result = subprocess.run(
            [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        return result.stdout

    before = descriptor()
    with pytest.raises(OSError, match="storage_access_denied"):
        instance.secure_tree(root)
    assert descriptor() == before
    assert outside.read_bytes() == b"outside synthetic content"
