"""Real desktop worker setup status follows native persisted progress."""

from __future__ import annotations

import os

import httpx

from tg_assistant.config import Settings
from tg_assistant.contracts import OnboardingStatus
from tg_assistant.desktop.setup_context import open_setup_context


def test_real_worker_exposes_readonly_setup_and_observes_native_progress(tmp_path, monkeypatch):
    from desktop.test_launcher import controller, ready, stop

    for name in list(os.environ):
        if name.startswith("TG_ASSISTANT_"):
            monkeypatch.delenv(name)
    selected = Settings(_env_file=None, data_dir=tmp_path / "profile")
    runtime = controller(selected, tmp_path)
    try:
        runtime.start()
        state = ready(runtime)
        with httpx.Client(base_url=state.url, trust_env=False, timeout=3) as client:
            response = client.get("/api/v1/setup/status")
            assert response.status_code == 200
            status = OnboardingStatus.model_validate(response.json())
            assert status.profile.owner_id is None and status.profile.setup_stage == "welcome"
            assert not status.stage_evidence_ids
            assert "management" in status.disabled_capabilities
            connections = client.get("/api/v1/connections")
            assert connections.status_code == 200
            assert {item["service"]: item["state"] for item in connections.json()}[
                "storage"
            ] == "ready"
            assert (
                client.get("/api/v1/setup/status", headers={"Host": "evil.invalid"}).status_code
                == 403
            )
            assert (
                client.get(
                    "/api/v1/connections", headers={"Origin": "https://evil.invalid"}
                ).status_code
                == 403
            )
            assert (
                client.post(
                    "/api/v1/setup/status", json={"stage": "ready"}, headers={"Origin": state.url}
                ).status_code
                == 405
            )
            with open_setup_context(selected) as native:
                assert native.begin().profile.setup_stage == "storage_ready"
            measured = OnboardingStatus.model_validate(client.get("/api/v1/setup/status").json())
            assert measured.profile.setup_stage == "storage_ready"
            assert (
                measured.profile.owner_id is None and "management" in measured.disabled_capabilities
            )
            assert client.get("/api/v1/overview").status_code in (401, 403, 404)
            assert "secret" not in response.text.lower()
    finally:
        stop(runtime)


def test_launcher_setup_button_opens_actual_native_context(qt_application, tmp_path, monkeypatch):
    from desktop.test_launcher import controller, ready, stop
    from PySide6.QtCore import QEventLoop, Qt, QTimer
    from PySide6.QtTest import QTest

    from tg_assistant.desktop.app import LauncherWindow

    for name in list(os.environ):
        if name.startswith("TG_ASSISTANT_"):
            monkeypatch.delenv(name)
    selected = Settings(_env_file=None, data_dir=tmp_path / "profile")
    runtime = controller(selected, tmp_path)
    window = LauncherWindow(runtime)
    try:
        window.show()
        QTest.qWait(50)
        ready(runtime)
        assert hasattr(window, "setup_button"), "Launcher has no native setup entry"
        loop = QEventLoop()
        limit = QTimer()
        limit.setSingleShot(True)
        limit.timeout.connect(loop.quit)
        limit.start(3000)
        poll = QTimer()
        poll.timeout.connect(lambda: loop.quit() if window.setup_button.isEnabled() else None)
        poll.start(10)
        loop.exec()
        poll.stop()
        limit.stop()
        assert window.setup_button.isEnabled()
        observed = []
        acknowledged = []
        check = QTimer()

        def inspect_dialog():
            dialog = window.setup_dialog
            if dialog is not None and dialog.pending is None:
                if not acknowledged:
                    assert dialog.begin_button.isEnabled()
                    QTest.mouseClick(dialog.begin_button, Qt.LeftButton)
                    acknowledged.append(True)
                    return
                observed.append(dialog)
                dialog.reject()

        check.timeout.connect(inspect_dialog)
        check.start(10)
        bailout = QTimer()
        bailout.setSingleShot(True)
        bailout.timeout.connect(window.finish_setup)
        bailout.start(5000)
        try:
            QTest.mouseClick(window.setup_button, Qt.LeftButton)
        finally:
            check.stop()
            bailout.stop()
        assert len(observed) == 1
        assert not observed[0].begin_button.isEnabled(), (
            "Completed native acknowledgment must not offer a no-op"
        )
        assert "Đã kiểm tra" in observed[0].connections["storage"].text()
        assert observed[0].owner.text() == "Chưa xác minh tài khoản Telegram"
        assert window.setup_dialog is None
        with open_setup_context(selected) as saved:
            assert saved.coordinator.status().profile.setup_stage == "storage_ready"
        from desktop.test_setup_ui import wait_until

        wait_until(window.setup_button.hasFocus)
        observed[0].controller.cleanup_future.result(timeout=3)
    finally:
        window.close()
        stop(runtime)
