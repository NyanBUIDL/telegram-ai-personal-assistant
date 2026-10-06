"""Native completion uses explicit choices and observations before SQL writes."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from desktop.test_setup_context import migrated_settings as migrated_settings

from tg_assistant.contracts import ConnectionStatus
from tg_assistant.desktop.setup_context import open_setup_context
from tg_assistant.services.onboarding import StageVerification


def test_explicit_native_skip_advances_only_verified_prefix(migrated_settings):
    settings, storage = migrated_settings
    with open_setup_context(settings, secret_store_factory=lambda: storage.store) as context:
        context.begin()
        status = context.skip_ai()
        assert status.profile.setup_stage == "ai_configured"
        assert status.profile.owner_id is None
        assert context.coordinator.options()["ai_mode"] == "skip"
        assert {"chat_ai", "embeddings", "management", "first_answer"} <= set(
            status.disabled_capabilities
        )
        assert "telegram_verified" not in status.stage_evidence_ids


def test_refresh_observes_outside_transaction_and_preserves_telegram_age(
    migrated_settings, monkeypatch
):
    settings, storage = migrated_settings
    with open_setup_context(settings, secret_store_factory=lambda: storage.store) as context:
        events = []
        checked = datetime.now(UTC)
        monkeypatch.setattr(
            context.provider, "refresh_saved_health", lambda **_: events.append("ai")
        )
        telegram = SimpleNamespace(
            refresh=lambda: events.append("telegram"),
            verification=lambda: StageVerification(owner_id="123", fingerprint="a" * 64),
            connection_status=lambda: ConnectionStatus(
                service="telegram_account",
                state="ready",
                checked_at=checked,
                code="telegram_verified",
                message="Đã kiểm tra.",
                next_action=None,
                capabilities=["authenticated"],
            ),
            close=lambda: events.append("close"),
        )
        context.install_telegram(telegram)
        context.begin()
        context.skip_ai()
        events.clear()
        original = context.coordinator.resume

        def resume():
            assert events == ["ai", "telegram"], "SDK checks must precede O01 SQL mutation"
            return original()

        monkeypatch.setattr(context.coordinator, "resume", resume)
        status = context.refresh()
        assert status.profile.setup_stage == "telegram_verified"
        measured = next(row for row in status.connections if row.service == "telegram_account")
        assert measured.checked_at == checked
        assert status.profile.owner_id == 123
        assert "management" in status.disabled_capabilities
        assert "bot_verified" not in status.stage_evidence_ids


def test_failed_telegram_drain_retains_storage_and_context(migrated_settings):
    settings, storage = migrated_settings
    context = open_setup_context(settings, secret_store_factory=lambda: storage.store)

    class Pending:
        def close(self):
            raise RuntimeError("telegram_shutdown_pending")

        def verification(self):
            return None

        def connection_status(self):
            return ConnectionStatus(
                service="telegram_account",
                state="unknown",
                checked_at=None,
                code="telegram_check_required",
                message="Cần kiểm tra.",
                next_action=None,
                capabilities=[],
            )

    context.install_telegram(Pending())
    try:
        with pytest.raises(RuntimeError, match="telegram_shutdown_pending"):
            context.detach_telegram()
        assert context.telegram is not None
        assert context.coordinator.status().profile.owner_id is None
    finally:
        context.telegram = None
        context.close()


def test_native_user_can_choose_limited_setup_without_ai(qt_application, migrated_settings):
    from desktop.test_setup_ui import wait_until
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from tg_assistant.desktop.setup_ui import NativeSetupController, SetupDialog

    settings, storage = migrated_settings
    context = open_setup_context(settings, secret_store_factory=lambda: storage.store)
    control = NativeSetupController(
        lambda: context.coordinator,
        begin_handler=context.begin,
        skip_handler=context.skip_ai,
        close_handler=context.close,
    )
    dialog = SetupDialog(control)
    try:
        dialog.show()
        wait_until(lambda: dialog.pending is None)
        assert not dialog.skip_ai_button.isEnabled(), "Welcome must be acknowledged first"
        QTest.mouseClick(dialog.begin_button, Qt.LeftButton)
        wait_until(lambda: dialog.pending is None)
        assert dialog.skip_ai_button.isEnabled()
        QTest.mouseClick(dialog.skip_ai_button, Qt.LeftButton)
        wait_until(lambda: dialog.pending is None)
        assert context.coordinator.status().profile.setup_stage == "ai_configured"
        assert "Tài khoản Telegram" in dialog.next_action.text()
        assert "chưa bật ai" in dialog.limitations.text().lower()
        assert not dialog.skip_ai_button.isEnabled()
    finally:
        dialog.close()
        control.close().result(timeout=5)


def test_later_native_cleanup_resumes_only_after_successful_guard_drain(
    migrated_settings, monkeypatch
):
    from concurrent.futures import Future

    from tg_assistant.desktop import setup_context
    from tg_assistant.desktop.setup_ui import NativeSetupController

    settings, storage = migrated_settings
    current = open_setup_context(settings, secret_store_factory=lambda: storage.store)
    events = []

    class Pending:
        def __init__(self):
            self.calls = 0

        def close(self):
            self.calls += 1
            events.append("drain")
            if self.calls == 1:
                raise RuntimeError("telegram_shutdown_pending")

        def verification(self):
            return None

        def connection_status(self):
            return ConnectionStatus(
                service="telegram_account",
                state="unknown",
                checked_at=None,
                code="telegram_check_required",
                message="Cần kiểm tra.",
                next_action=None,
                capabilities=[],
            )

        def refresh(self):
            return None

    current.install_telegram(Pending())
    monkeypatch.setattr(setup_context, "open_setup_context", lambda *_args, **_kwargs: current)

    class Runtime:
        handoff_active = True

        def resume_after_handoff(self):
            assert current.telegram is None, "Actual context drain must precede resume"
            events.append("resume")
            self.handoff_active = False
            future = Future()
            future.set_result(None)
            return future

        def setup_status(self):
            future = Future()
            future.set_result(None)
            return future

    runtime = Runtime()
    control = NativeSetupController.for_settings(settings, runtime_controller=runtime)
    control.begin().result(timeout=5)
    with pytest.raises(RuntimeError, match="telegram_shutdown_pending"):
        current.detach_telegram()
    assert runtime.handoff_active and events == ["drain"]
    control.close().result(timeout=5)
    assert events == ["drain", "drain", "resume"]
    assert not runtime.handoff_active
