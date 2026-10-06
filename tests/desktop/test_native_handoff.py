"""A login dialog cannot overlap a running worker's SID/session ownership."""

from __future__ import annotations

import json

from desktop.test_launcher import settings as settings

from tg_assistant.desktop.instance import process_incarnation_exists


def test_handoff_waits_for_actual_worker_and_blocks_restart(settings, tmp_path):
    from desktop.test_launcher import controller, ready, stop

    runtime = controller(settings, tmp_path)
    try:
        runtime.start()
        state = ready(runtime)
        saved = json.loads(runtime.state_file.read_text(encoding="utf-8"))
        assert state.phase == "ready"
        assert runtime.quiesce().result(timeout=25) is True
        assert not process_incarnation_exists(saved["pid"], saved["process_started_at"])
        assert not runtime.state_file.exists()
        runtime.start().result(timeout=3)
        assert runtime.process.poll() is not None, (
            "A launcher refresh must not restart during login"
        )
        runtime.resume_after_handoff().result(timeout=25)
        replacement = ready(runtime)
        assert replacement.phase == "ready" and replacement.run_id != state.run_id
    finally:
        # Teardown must run even if resume raised; it must not spawn a new
        # worker solely for cleanup. The body already verifies explicit resume.
        stop(runtime)


def test_unverified_worker_is_not_stopped_or_claimed(settings, tmp_path):
    from desktop.test_launcher import controller

    runtime = controller(settings, tmp_path)
    try:
        assert runtime.quiesce().result(timeout=3) is False
        assert not (settings.data_dir / "stop.request").exists()
        assert runtime.process is None
    finally:
        runtime.close()


def test_timed_out_handoff_recovers_only_after_actual_worker_exit(settings, tmp_path):
    import time

    from desktop.test_launcher import controller, ready, stop

    runtime = controller(settings, tmp_path)
    try:
        runtime.start()
        ready(runtime)
        assert runtime.quiesce(timeout=0.001).result(timeout=3) is False
        assert runtime.handoff_active
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            runtime.refresh().result(timeout=2)
            if not runtime.handoff_active:
                break
            time.sleep(0.02)
        assert runtime.process.poll() is not None
        assert not runtime.handoff_active, "A confirmed completed stop must allow an explicit retry"
    finally:
        stop(runtime)


def test_real_launcher_opens_native_telegram_only_after_worker_exit(
    qt_application, settings, tmp_path, monkeypatch
):
    from desktop.test_launcher import controller, ready, stop
    from desktop.test_setup_ui import wait_until
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest

    from tg_assistant.desktop import instance
    from tg_assistant.desktop.app import LauncherWindow
    from tg_assistant.desktop.dialogs.telegram import TelegramDialog
    from tg_assistant.desktop.setup_ui import NativeSetupController
    from tg_assistant.services import telegram_publication

    runtime = controller(settings, tmp_path)
    directory = tmp_path / "native-control"
    monkeypatch.setattr(instance, "native_control_directory", lambda: directory)
    monkeypatch.setattr(telegram_publication, "native_control_directory", lambda: directory)

    class Store:
        def get(self, _name):
            return None

    original = NativeSetupController.for_settings
    monkeypatch.setattr(
        NativeSetupController,
        "for_settings",
        lambda current, **kwargs: original(
            current,
            **kwargs,
            context_options={"secret_store_factory": Store},
        ),
    )
    window = LauncherWindow(runtime)
    observed, failures = [], []
    poll, limit = QTimer(), QTimer()
    old_run = None

    def inspect():
        modal = qt_application.activeModalWidget()
        if isinstance(modal, TelegramDialog):
            try:
                assert runtime.handoff_active
                assert runtime.process.poll() is not None
                assert not runtime.state_file.exists()
                assert modal.api_id.text() == "" and modal.api_hash.text() == ""
                assert not modal._owns_runner
                observed.append(True)
            except BaseException as error:
                failures.append(error)
            modal.reject()
        elif observed and window.setup_dialog and window.setup_dialog.pending is None:
            window.setup_dialog.reject()

    try:
        window.show()
        QTest.qWait(50)
        old_run = ready(runtime).run_id
        wait_until(window.setup_button.isEnabled, timeout=8000)
        poll.timeout.connect(inspect)
        poll.start(20)
        limit.setSingleShot(True)
        limit.timeout.connect(window.finish_setup)
        limit.start(20000)
        window.open_setup(requested_dialog="open_telegram_login")
        assert observed == [True] and not failures
        assert not runtime.handoff_active
        assert ready(runtime).run_id != old_run
    finally:
        poll.stop()
        limit.stop()
        window.close()
        stop(runtime)
