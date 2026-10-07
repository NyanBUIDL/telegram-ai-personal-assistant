"""Real stop-file publication and ownership, using disposable profile state."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import psutil
import pytest
from desktop.test_launcher import settings as settings

from tg_assistant.config import Settings
from tg_assistant.desktop.instance import process_matches_owner
from tg_assistant.desktop.runtime_controller import RuntimeController


@pytest.fixture
def ready_controller(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("TG_ASSISTANT_"):
            monkeypatch.delenv(name)
    settings = Settings(_env_file=None, data_dir=tmp_path / "profile")
    settings.data_dir.joinpath("config").mkdir(parents=True)
    state = {
        "profile_id": settings.profile_id,
        "pid": os.getpid(),
        "process_started_at": psutil.Process().create_time(),
        "port": 0,
        "run_id": uuid4().hex,
        "mode": "setup",
        "owner_id": None,
    }

    class Readiness(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("X-TG-Runtime-ID", state["run_id"])
            self.end_headers()
            self.wfile.write(json.dumps({
                "service": "runtime", "state": "ready", "code": "runtime_ready",
                "checked_at": datetime.now(UTC).isoformat(), "capabilities": ["setup"],
                "message": "Synthetic local readiness", "next_action": None,
            }).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Readiness)
    state["port"] = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runtime = RuntimeController(settings)
    runtime.state_file.write_text(json.dumps(state), encoding="utf-8")
    try:
        assert runtime._read_state() == state
        assert process_matches_owner(state["pid"], state["process_started_at"])
        assert runtime._refresh().phase == "ready"
        yield runtime, state
    finally:
        runtime.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_acknowledged_stop_is_not_recreated_by_readiness_poll(ready_controller):
    runtime, state = ready_controller
    marker = runtime.settings.data_dir / "stop.request"
    runtime.stop()
    assert marker.read_text(encoding="ascii") == state["run_id"]
    marker.unlink()  # Worker acknowledged the stop; readiness can still be live.
    runtime._refresh()
    runtime.stop()
    assert not marker.exists(), "Polling recreated an acknowledged stop request"


def test_start_on_same_live_run_cannot_cancel_acknowledged_stop(ready_controller):
    runtime, state = ready_controller
    marker = runtime.settings.data_dir / "stop.request"
    runtime.stop()
    marker.unlink()
    runtime.start().result(timeout=2)
    runtime._refresh()
    runtime.stop()
    assert not marker.exists(), "A no-op start reset shutdown of the same live run"


@pytest.mark.parametrize("failed_start", [False, True])
def test_unbound_stop_before_explicit_fresh_start_does_not_stop_new_worker(
    settings, tmp_path, failed_start,
):
    from desktop.test_launcher import controller, ready, stop

    runtime = controller(settings, tmp_path)
    command = runtime.worker_command
    try:
        if failed_start:
            runtime.worker_command = [str(tmp_path / "missing-worker.exe")]
            runtime.start().result(timeout=2)
            assert runtime.snapshot.phase == "error"
            runtime.worker_command = command
        runtime.stop()
        runtime.start().result(timeout=2)
        assert ready(runtime).phase == "ready"
        assert not (settings.data_dir / "stop.request").exists()
    finally:
        stop(runtime)


@pytest.mark.skipif(os.name != "nt", reason="Windows delete sharing regression")
def test_other_controller_publication_does_not_block_worker_marker_cleanup(
    ready_controller, monkeypatch,
):
    runtime, state = ready_controller
    marker = runtime.settings.data_dir / "stop.request"
    runtime.stop()
    other = RuntimeController(runtime.settings)
    assert other._refresh().phase == "ready"
    original_open = Path.open
    cleanup_errors = []

    @contextmanager
    def cleanup_during_write(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        with original_open(path, *args, **kwargs) as stream:
            if path.parent == marker.parent and any(flag in mode for flag in "wx"):
                try:
                    marker.unlink()
                except OSError as error:
                    cleanup_errors.append(error)
            yield stream

    monkeypatch.setattr(Path, "open", cleanup_during_write)
    try:
        other.stop()
    finally:
        other.close()
    assert not cleanup_errors, "Controller publication held a handle blocking worker cleanup"
    assert not list(marker.parent.glob(".stop-*.tmp"))


def test_failed_stop_publication_can_retry_same_run(ready_controller, monkeypatch):
    runtime, state = ready_controller
    marker = runtime.settings.data_dir / "stop.request"
    original_open = Path.open

    def fail_write(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if path.parent == marker.parent and any(flag in mode for flag in "wx"):
            raise PermissionError("synthetic publication denied")
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", fail_write)
        runtime.stop()
    assert not marker.exists()
    runtime._refresh()
    assert marker.read_text(encoding="ascii") == state["run_id"]


def test_failed_stop_never_follows_replacement_incarnation(ready_controller, monkeypatch):
    runtime, state = ready_controller
    marker = runtime.settings.data_dir / "stop.request"
    original_open = Path.open

    def fail_write(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if path.parent == marker.parent and any(flag in mode for flag in "wx"):
            raise PermissionError("synthetic publication denied")
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", fail_write)
        runtime.stop()
    state["run_id"] = uuid4().hex
    runtime.state_file.write_text(json.dumps(state), encoding="utf-8")
    runtime._refresh()
    runtime.stop()
    assert not marker.exists(), "A pending stop followed a replacement run"


@pytest.mark.parametrize("field,value", [
    ("profile_id", "foreign-profile"),
    ("process_started_at", 1.0),
])
def test_stop_revalidates_profile_and_process_ownership(ready_controller, field, value):
    runtime, state = ready_controller
    state[field] = value
    runtime.state_file.write_text(json.dumps(state), encoding="utf-8")
    runtime.stop()
    assert not (runtime.settings.data_dir / "stop.request").exists()


def test_completed_stop_stays_stopped_and_explicit_start_can_attach_new_run(ready_controller):
    runtime, state = ready_controller
    process = subprocess.Popen([sys._base_executable, "-c", "import time; time.sleep(30)"])
    try:
        state.update(pid=process.pid, process_started_at=psutil.Process(process.pid).create_time())
        runtime.state_file.write_text(json.dumps(state), encoding="utf-8")
        assert runtime._refresh().phase == "ready"
        runtime.stop()
        process.terminate()  # Original disposable Popen handle only.
        assert process.wait(timeout=5) is not None
        assert runtime._refresh().phase == "stopped"
        runtime.stop()
        assert runtime.snapshot.phase == "stopped"
        marker = runtime.settings.data_dir / "stop.request"
        marker.unlink()
        state.update(pid=os.getpid(), process_started_at=psutil.Process().create_time(), run_id=uuid4().hex)
        runtime.state_file.write_text(json.dumps(state), encoding="utf-8")
        runtime.start().result(timeout=2)
        assert runtime._refresh().phase == "ready"
        assert runtime.process is None  # Explicit start attached the independently verified run.
        runtime.stop()
        assert marker.read_text(encoding="ascii") == state["run_id"]
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
