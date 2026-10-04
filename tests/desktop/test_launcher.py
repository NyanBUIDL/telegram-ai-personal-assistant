"""Real Qt/process/socket lifecycle with disposable profiles; no account secrets."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time

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


def controller(settings, tmp_path):
    lifecycle = module("tg_assistant.desktop.runtime_controller")
    code = (
        "from pathlib import Path; import sys, traceback; "
        "from tg_assistant.config import Settings; "
        "from tg_assistant.desktop.worker import serve_worker; "
        "\ntry: serve_worker(Settings(_env_file=None,data_dir=Path(sys.argv[1]),"
        "admin_api_port=int(sys.argv[3])),instance_directory=Path(sys.argv[2]))"
        "\nexcept Exception:"
        "\n with (Path(sys.argv[1]).parent/'synthetic-worker-error.log').open('w',encoding='utf-8') as log: traceback.print_exc(file=log)"
        "\n raise"
    )
    return lifecycle.RuntimeController(
        settings,
        worker_command=[
            sys.executable,
            "-c",
            code,
            str(settings.data_dir),
            str(tmp_path / "sid-instance"),
            str(settings.admin_api_port),
        ],
    )


def ready(runtime, *, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = runtime.refresh().result(timeout=2)
        if state.phase == "ready":
            return state
        if state.phase == "error":
            pytest.fail(f"Synthetic worker failed: {state.code}")
        time.sleep(0.05)
    pytest.fail("Worker did not reach readiness")


def stop(runtime):
    runtime.stop()
    if runtime.process:
        runtime.process.wait(timeout=15)
    runtime.close()


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
        held.set()
        queued.result(timeout=2)
        started.result(timeout=2)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = runtime.refresh().result(timeout=2)
            if state.phase == "stopped":
                break
            time.sleep(0.05)
        assert state.phase == "stopped"
    finally:
        held.set()
        stop(runtime)


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


@pytest.mark.skipif(os.name != "nt", reason="Windows NTFS protected DACL evidence")
def test_owned_directory_acl_and_inherited_file(tmp_path):
    from tg_assistant.paths import current_user_sid

    instance = module("tg_assistant.desktop.instance")
    root = tmp_path / "private"
    instance.secure_directory(root)
    sample = root / "sample"
    sample.write_text("synthetic", encoding="utf-8")
    for path, protected in ((root, True), (sample, False)):
        # Read the real OS descriptor through .NET, independently of the writer.
        literal = str(path).replace("'", "''")
        kind = "Directory" if path.is_dir() else "File"
        command = (
            "$a=[System.IO." + kind + "]::GetAccessControl('" + literal + "'); "
            "@{protected=$a.AreAccessRulesProtected; owner=$a.GetOwner([System.Security.Principal.SecurityIdentifier]).Value; "
            "sids=@($a.Access | ForEach-Object {$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value})} | ConvertTo-Json -Compress"
        )
        response = subprocess.run(
            [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert response.returncode == 0, response.stderr.decode(errors="replace")
        descriptor = json.loads(response.stdout)
        assert descriptor["protected"] is protected
        assert descriptor["owner"] == current_user_sid()
        assert set(descriptor["sids"]) == {current_user_sid(), "S-1-5-18"}


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
        detached = module("tg_assistant.desktop.runtime_controller").RuntimeController(settings)
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
            detached.stop()
            runtime.process.wait(timeout=15)
            detached.close()
    finally:
        # The original executor is already shut down, but its exact Popen is
        # still available solely for this fixture's completion proof.
        if runtime.process.poll() is None:
            state = json.loads(runtime.state_file.read_text(encoding="utf-8"))
            (settings.data_dir / "stop.request").write_text(state["run_id"], encoding="ascii")
            runtime.process.wait(timeout=15)


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
        timeout=10,
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
        timeout=10,
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
            timeout=10,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        return result.stdout

    before = descriptor()
    with pytest.raises(OSError, match="storage_access_denied"):
        instance.secure_tree(root)
    assert descriptor() == before
    assert outside.read_bytes() == b"outside synthetic content"
