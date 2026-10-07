"""Disposable native fixture refuses resources when its token bootstrap fails."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def load_fixture():
    spec = importlib.util.spec_from_file_location(
        "u02_fixture", Path(__file__).parent / "fixtures/u02_native_browser_server.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    return fixture


@pytest.mark.skipif(os.name != "nt", reason="Windows process token prerequisite")
@pytest.mark.parametrize("worker", [False, True])
def test_owner_failure_precedes_parent_paths_and_worker_start(monkeypatch, capsys, tmp_path, worker):
    fixture = load_fixture()

    def unavailable():
        raise RuntimeError("synthetic-private-owner-payload")

    # Inject the boundary even before the fixture uses the shared prerequisite.
    monkeypatch.setattr(fixture, "prepare_windows_fixture_owner", unavailable, raising=False)
    monkeypatch.setattr(sys, "argv", ["fixture", "--worker" if worker else "bundle", "lf", str(tmp_path)])

    def resource_before_owner(*args, **kwargs):
        raise AssertionError("resource_created_before_owner")

    monkeypatch.setattr(Path, "mkdir", resource_before_owner)
    from tg_assistant.desktop import worker as worker_module

    monkeypatch.setattr(worker_module, "serve_worker", resource_before_owner)
    with pytest.raises(SystemExit) as stopped:
        fixture.main()
    assert stopped.value.code == 2
    assert json.loads(capsys.readouterr().out) == {
        "type": "failure", "code": "windows_fixture_owner_unavailable"
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows native worker diagnostic")
@pytest.mark.parametrize("payload,code", [
    ("storage_access_denied", "storage_access_denied"),
    ("synthetic-private-worker-payload", "native_worker_failed"),
])
def test_worker_exception_records_only_fixed_code(monkeypatch, tmp_path, payload, code):
    fixture = load_fixture()
    monkeypatch.setattr(fixture, "prepare_windows_fixture_owner", lambda: True)
    monkeypatch.setattr(sys, "argv", ["fixture", "--worker", str(tmp_path / "profile"), str(tmp_path / "sid-control")])
    from tg_assistant.desktop import worker

    def broken(*args, **kwargs):
        raise OSError(payload)

    monkeypatch.setattr(worker, "serve_worker", broken)
    with pytest.raises(SystemExit) as stopped:
        fixture.main()
    assert stopped.value.code == 2
    assert json.loads((tmp_path / "worker-diagnostic.json").read_text()) == {
        "code": code, "owner_matches_before": True, "owner_matches_after": True
    }


@pytest.mark.parametrize("phase,exit_code,unexpected", [
    ("stopped", 0, False), ("starting", 2, True), ("error", 2, True),
])
def test_resume_transition_distinguishes_old_clean_exit_from_failed_replacement(tmp_path, phase, exit_code, unexpected):
    from tg_assistant.config import Settings
    from tg_assistant.desktop.runtime_controller import RuntimeController, RuntimeSnapshot

    fixture = load_fixture()
    runtime = RuntimeController(Settings(_env_file=None, data_dir=tmp_path / "profile"))
    try:
        # The real old process remains referenced while resume clears the handoff
        # flag and creates replacement paths/Popen; its snapshot is still stopped.
        with subprocess.Popen(
            [sys.executable, "-c", f"raise SystemExit({exit_code})"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ) as process:
            process.wait(timeout=10)
            runtime.process = process
            runtime.handoff_active = False
            runtime.snapshot = RuntimeSnapshot(phase, "fixture_transition")
            assert fixture.unexpected_worker_exit(runtime, stopping=False) is unexpected
    finally:
        runtime.close()
