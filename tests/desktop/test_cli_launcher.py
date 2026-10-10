"""Real CLI/process/socket boundaries, with disposable profile and SID controls."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from desktop.test_launcher import (
    controller,
    fixture_bootstrap_diagnostics,
    fixture_readiness_diagnostics,
    stop,
)
from typer.testing import CliRunner

from tg_assistant import cli, config
from tg_assistant.config import Settings
from tg_assistant.desktop import instance
from tg_assistant.paths import ensure_runtime_dirs
from tg_assistant.services.maintenance import FileLock


@pytest.fixture
def native_cli(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("TG_ASSISTANT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    settings = Settings(_env_file=None, data_dir=tmp_path / "profile")
    control = tmp_path / "sid-control"
    monkeypatch.setattr(instance, "native_control_directory", lambda: control)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(config, "get_settings", lambda: settings)

    def forbidden():
        pytest.fail("Native CLI reached interactive credentials or foreground setup")

    monkeypatch.setattr(cli, "_ensure_ready", forbidden)
    monkeypatch.setattr(cli, "SecretStore", forbidden)
    runtimes = []

    def make(command=None):
        runtime = controller(settings, tmp_path, instance_directory=control)
        if command is not None:
            runtime.worker_command = command
            runtime.fixture_worker_command = tuple(command)
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(cli, "_native_controller", make, raising=False)
    yield settings, make, CliRunner()
    for runtime in runtimes:
        stop(runtime)


def await_ready(runtime):
    runtime.start().result(timeout=5)
    began = time.monotonic()
    deadline = began + 15
    while time.monotonic() < deadline:
        state = runtime.refresh().result(timeout=2)
        if state.phase == "ready":
            return state
        assert state.phase != "error", state.code
        time.sleep(0.05)
    runtime.fixture_failed_readiness = True
    diagnostics = json.dumps(fixture_readiness_diagnostics(runtime, began), sort_keys=True)
    pytest.fail("Synthetic worker never became ready; diagnostics=" + diagnostics)


def test_native_readiness_timeout_identifies_bootstrap_without_private_values(native_cli, tmp_path, monkeypatch):
    from tg_assistant.paths import current_user_sid

    settings, make, _ = native_cli
    runtime = make()
    state_release = tmp_path / "state-release"
    runtime.worker_command.append(str(state_release))
    runtime.fixture_worker_command = tuple(runtime.worker_command)
    try:
        runtime.start().result(timeout=5)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            facts = fixture_bootstrap_diagnostics(runtime)
            if any(step["phase"] == "state_write_started" for step in facts.get("bootstrap_steps", ())):
                break
            time.sleep(0.01)
        else:
            pytest.fail("Fixture did not reach identity publication")
        assert not runtime.state_file.exists()
        began = time.monotonic()
        ticks = iter((began, began + 16))
        with monkeypatch.context() as patch:
            patch.setattr(sys.modules[__name__], "time", SimpleNamespace(monotonic=lambda: next(ticks)))
            with pytest.raises(pytest.fail.Exception) as caught:
                await_ready(runtime)
        message = str(caught.value)
        assert "state_write_started" in message and '"http": "not_attempted"' in message
        for private in (str(settings.data_dir), runtime.launch_id, current_user_sid()):
            assert private not in message
    finally:
        state_release.touch()


@pytest.mark.skipif(os.name != "nt", reason="Normal Windows native CLI adapter")
def test_native_start_first_setup_uses_measured_fallback_url(native_cli):
    settings, _, runner = native_cli
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        settings.admin_api_port = occupied.getsockname()[1]
        result = runner.invoke(cli.app, ["start"])
    assert result.exit_code == 0, result.output
    state = json.loads((settings.data_dir / "config/desktop-runtime.json").read_text())
    assert state["port"] != settings.admin_api_port
    assert f"http://127.0.0.1:{state['port']}" in result.output
    assert f"http://127.0.0.1:{settings.admin_api_port}" not in result.output
    assert (settings.data_dir / "db/assistant.sqlite3").exists()


@pytest.mark.skipif(os.name != "nt", reason="Normal Windows native CLI adapter")
def test_native_status_and_graceful_stop_adopt_existing_worker(native_cli):
    _, make, runner = native_cli
    runtime = make()
    state = await_ready(runtime)
    runtime.close()
    status = runner.invoke(cli.app, ["status"])
    assert status.exit_code == 0 and "ready" in status.output
    assert state.url in status.output
    stopped = runner.invoke(cli.app, ["stop"])
    assert stopped.exit_code == 0, stopped.output
    runtime.process.wait(timeout=2)
    assert runtime.process.returncode == 0
    assert "Đã dừng an toàn" in stopped.output


@pytest.mark.skipif(os.name != "nt", reason="Normal Windows native CLI adapter")
def test_native_spawn_failure_is_sanitized_without_success(native_cli, tmp_path, monkeypatch):
    _, make, runner = native_cli
    private = "PRIVATE_EXECUTABLE_MARKER"
    monkeypatch.setattr(cli, "_native_controller", lambda: make([str(tmp_path / private)]))
    result = runner.invoke(cli.app, ["start"])
    assert result.exit_code == 1
    assert "runtime_start_failed" in result.output
    assert private not in result.output and "http://" not in result.output


@pytest.mark.skipif(os.name != "nt", reason="Normal Windows native CLI adapter")
def test_native_queued_start_timeout_never_reports_success(native_cli, monkeypatch):
    _, make, runner = native_cli
    monkeypatch.setattr(
        cli,
        "_native_controller",
        lambda: make([sys.executable, "-c", "import time; time.sleep(1)"]),
    )
    monkeypatch.setattr(cli, "NATIVE_START_TIMEOUT", 0.15, raising=False)
    result = runner.invoke(cli.app, ["start"])
    assert result.exit_code == 1
    assert "chưa xác nhận" in result.output.lower()
    assert "http://" not in result.output and "Đã chạy nền" not in result.output
    assert "runtime_start_failed" not in result.output


@pytest.mark.skipif(os.name != "nt", reason="Normal Windows native CLI adapter")
def test_native_stale_state_stop_refuses_without_stop_request(native_cli):
    settings, _, runner = native_cli
    paths = ensure_runtime_dirs(settings.data_dir, profile_id=settings.profile_id)
    (paths["config"] / "desktop-runtime.json").write_text(
        json.dumps(
            {
                "profile_id": settings.profile_id,
                "pid": 999999999,
                "process_started_at": 1.0,
                "port": 8765,
                "run_id": "1" * 32,
                "mode": "setup",
                "owner_id": None,
            }
        )
    )
    status = runner.invoke(cli.app, ["status"])
    assert "unknown" in status.output and "http://" not in status.output
    stop = runner.invoke(cli.app, ["stop"])
    assert stop.exit_code == 1 and "runtime_readiness_unavailable" in stop.output
    assert not (paths["data"] / "stop.request").exists()


def test_legacy_foreground_refuses_other_sid_instance_before_setup(native_cli):
    settings, _, _ = native_cli
    ensure_runtime_dirs(settings.data_dir, profile_id=settings.profile_id)
    with instance.InstanceGuard():
        with pytest.raises(RuntimeError, match="instance"):
            cli.run()


def test_legacy_lock_reuses_owned_byte_zero_with_existing_history(native_cli, tmp_path):
    path = tmp_path / "profile-history.lock"
    path.write_bytes(b"existing-history-at-nonzero-offset")
    with FileLock(path, exclusive=True):
        with pytest.raises(RuntimeError, match="instance"):
            with cli.InstanceLock(path):
                pytest.fail("Legacy append offset bypassed the shared owned lock")


@pytest.mark.skipif(os.name != "nt", reason="Normal Windows native CLI adapter")
@pytest.mark.parametrize("command", ["start", "stop", "status"])
def test_foreign_profile_cli_refusal_is_sanitized_and_preserves_marker(native_cli, command):
    settings, _, runner = native_cli
    ensure_runtime_dirs(settings.data_dir, profile_id="foreign-profile")
    marker = settings.data_dir / ".tg-assistant-data"
    original = marker.read_bytes()
    result = runner.invoke(cli.app, [command])
    assert result.exit_code == 1
    assert "runtime_configuration_invalid" in result.output
    assert "foreign-profile" not in result.output and "http://" not in result.output
    assert marker.read_bytes() == original
    assert not (settings.data_dir / "db/assistant.sqlite3").exists()


@pytest.mark.skipif(os.name != "nt", reason="Normal Windows native CLI adapter")
def test_native_status_stop_ignore_malformed_legacy_pid(native_cli):
    settings, make, runner = native_cli
    runtime = make()
    state = await_ready(runtime)
    legacy_pid = settings.data_dir / "assistant.pid"
    legacy_pid.write_text("private-invalid-pid", encoding="ascii")
    status = runner.invoke(cli.app, ["status"])
    assert status.exit_code == 0 and state.url in status.output
    assert "private-invalid-pid" not in status.output
    stopped = runner.invoke(cli.app, ["stop"])
    assert stopped.exit_code == 0, stopped.output
    runtime.process.wait(timeout=2)
    assert runtime.process.returncode == 0


@pytest.mark.skipif(os.name != "nt", reason="Normal Windows native CLI adapter")
def test_native_restart_stopped_app_reaches_measured_readiness(native_cli):
    settings, _, runner = native_cli
    result = runner.invoke(cli.app, ["restart"])
    assert result.exit_code == 0, result.output
    state = json.loads((settings.data_dir / "config/desktop-runtime.json").read_text())
    assert f"http://127.0.0.1:{state['port']}" in result.output


@pytest.mark.skipif(os.name != "nt", reason="Normal Windows native CLI adapter")
def test_native_restart_ready_app_replaces_completed_incarnation(native_cli):
    settings, make, runner = native_cli
    original = make()
    first = await_ready(original)
    result = runner.invoke(cli.app, ["restart"])
    assert result.exit_code == 0, result.output
    original.process.wait(timeout=2)
    assert original.process.returncode == 0
    current = json.loads((settings.data_dir / "config/desktop-runtime.json").read_text())
    assert current["run_id"] != first.run_id and current["pid"] != first.pid
    assert f"http://127.0.0.1:{current['port']}" in result.output


@pytest.mark.skipif(os.name != "nt", reason="Normal Windows native CLI adapter")
def test_native_restart_unconfirmed_live_worker_refuses_replacement(native_cli, monkeypatch):
    settings, make, runner = native_cli
    original = make()
    first = await_ready(original)
    state_file = settings.data_dir / "config/desktop-runtime.json"
    saved = state_file.read_text()
    state_file.write_text(json.dumps({**json.loads(saved), "run_id": "2" * 32}))
    launched = []
    real_popen = subprocess.Popen

    def observe_launch(*args, **kwargs):
        launched.append(args)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", observe_launch)
    try:
        result = runner.invoke(cli.app, ["restart"])
        assert result.exit_code == 1 and "http://" not in result.output
        assert original.process.poll() is None
        assert not (settings.data_dir / "stop.request").exists()
        assert json.loads(state_file.read_text())["pid"] == first.pid
        assert not launched
    finally:
        state_file.write_text(saved)
