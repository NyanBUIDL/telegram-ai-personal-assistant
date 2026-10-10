"""Qt behavior with a real borrowed loop and a synthetic bot service boundary.

No Telegram account/token/network health is established by these UI tests.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import importlib.util
from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit, QWidget

from tg_assistant.contracts import ConnectionStatus
from tg_assistant.desktop.telegram_context import NativeTelegramRunner
from tg_assistant.services.onboarding import StageVerification

PAYLOAD = "pair_" + base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")


class DialogService:
    def __init__(self):
        self.ready = self.paired = False
        self.username = "synthetic_bot"
        self.connected = Event()
        self.gate = None
        self.failure = False
        self.pair_on_poll = False
        self.invitation = None
        self.code = "bot_check_required"

    async def connect(self, token):
        if token != "native-input-only":
            raise ValueError("synthetic-private-input-canary")
        self.connected.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.failure:
            raise ValueError("synthetic-private-exception-canary")
        self.ready, self.code = True, "bot_verified"

    async def resume(self):
        return self.connection_status()

    async def issue_pairing(self):
        self.invitation = SimpleNamespace(
            payload=PAYLOAD,
            manual_code="ABCD-EFGH-IJKL-MNOP",
            generation="a" * 32,
            expires_at=datetime.now(UTC) + timedelta(seconds=299),
        )
        return self.invitation

    async def poll_once(self):
        if self.pair_on_poll:
            self.paired, self.code = True, "owner_paired"
        return self.connection_status()

    async def cancel_pairing(self):
        self.invitation = None

    async def close(self):
        raise AssertionError("The dialog must not close the borrowed service/runner")

    def connection_status(self):
        return ConnectionStatus(
            service="control_bot",
            state="ready" if self.ready else "unknown",
            checked_at=datetime.now(UTC),
            code=self.code,
            message="synthetic-private-status-canary",
            next_action=None,
            capabilities=["owner_paired"],
        )

    def verification(self):
        return StageVerification(owner_id=17, fingerprint="a" * 64) if self.ready else None

    def pairing_verification(self):
        return StageVerification(owner_id=17, fingerprint="b" * 64) if self.paired else None

    def pairing_username(self):
        return self.username if self.ready else None


@pytest.fixture
def native_context():
    service = DialogService()
    runner = NativeTelegramRunner()

    async def refresh_async():
        return await service.resume()

    context = SimpleNamespace(service=service, runner=runner, refresh_async=refresh_async)
    try:
        yield context
    finally:
        if service.gate is not None:
            runner.loop.call_soon_threadsafe(service.gate.set)
        runner.stop()


def open_dialog(context, qt_application, parent=None):
    assert importlib.util.find_spec("tg_assistant.desktop.dialogs.bot"), (
        "The bot setup flow must provide its native dialog"
    )
    module = importlib.import_module("tg_assistant.desktop.dialogs.bot")
    dialog = module.NativeBotDialog(context, parent=parent)
    dialog.show()
    qt_application.processEvents()
    return dialog


def finish_action(dialog, application):
    for _ in range(100):
        application.processEvents()
        if dialog._future is None:
            return
        QTest.qWait(10)
    pytest.fail("Native dialog did not resolve its bounded async operation")


def connect(dialog, context, application):
    finish_action(dialog, application)
    dialog.token.setText("native-input-only")
    dialog.connect_button.click()
    assert dialog.token.text() == ""
    finish_action(dialog, application)
    assert context.service.verification() is not None


def test_blank_password_input_plain_status_and_borrowed_loop_survive_escape(
    native_context, qt_application
):
    parent = QWidget()
    focus = QLineEdit(parent)
    parent.show()
    focus.setFocus()
    qt_application.processEvents()
    dialog = open_dialog(native_context, qt_application, parent)
    assert dialog.token.text() == ""
    assert dialog.token.echoMode() == QLineEdit.EchoMode.Password
    assert dialog.token.minimumHeight() >= 44
    assert dialog.status_label.textFormat() == Qt.TextFormat.PlainText
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    qt_application.processEvents()
    assert dialog._closed and not dialog._timer.isActive()
    assert focus.hasFocus()
    assert native_context.runner.thread.is_alive()
    assert dialog.token.text() == "" and dialog.manual_code.text() == ""
    parent.close()


def test_connect_clears_native_secret_while_async_wait_remains_responsive(
    native_context, qt_application
):
    dialog = open_dialog(native_context, qt_application)
    finish_action(dialog, qt_application)
    native_context.service.gate = asyncio.Event()
    dialog.token.setText("native-input-only")
    dialog.connect_button.click()
    assert dialog.token.text() == ""
    assert native_context.service.connected.wait(1)
    assert not dialog.connect_button.isEnabled()
    assert dialog.close_button.isEnabled()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert dialog._closed and native_context.runner.thread.is_alive()


def test_async_failure_uses_fixed_message_and_allows_retry(native_context, qt_application):
    dialog = open_dialog(native_context, qt_application)
    finish_action(dialog, qt_application)
    native_context.service.failure = True
    dialog.token.setText("native-input-only")
    dialog.connect_button.click()
    finish_action(dialog, qt_application)
    assert "canary" not in dialog.status_label.text()
    assert "native-input-only" not in dialog.status_label.text()
    assert dialog.connect_button.isEnabled() and dialog.token.text() == ""
    native_context.service.failure = False
    connect(dialog, native_context, qt_application)
    dialog.reject()


def test_native_link_and_qr_use_only_verified_username_and_exact_payload(
    native_context, qt_application, monkeypatch
):
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    dialog = open_dialog(native_context, qt_application)
    dialog.help_button.click()
    connect(dialog, native_context, qt_application)
    dialog.pair_button.click()
    finish_action(dialog, qt_application)
    assert dialog.manual_code.text() == "ABCD-EFGH-IJKL-MNOP"
    assert not dialog.qr_label.pixmap().isNull()
    dialog.open_button.click()
    assert opened == ["https://t.me/BotFather", f"https://t.me/synthetic_bot?start={PAYLOAD}"]
    dialog.reject()
    assert dialog.manual_code.text() == "" and dialog.qr_label.pixmap().isNull()


@pytest.mark.parametrize("username", ["evil/path", "<b>spoof</b>", "", "x" * 33])
def test_invalid_username_never_opens_link_or_renders_native_invitation(
    native_context, qt_application, username
):
    dialog = open_dialog(native_context, qt_application)
    connect(dialog, native_context, qt_application)
    native_context.service.username = username
    dialog.pair_button.click()
    finish_action(dialog, qt_application)
    assert not dialog.open_button.isEnabled()
    assert dialog.manual_code.text() == ""
    assert username == "" or username not in dialog.status_label.text()
    dialog.reject()


def test_only_private_pairing_measurement_can_render_paired_state(native_context, qt_application):
    dialog = open_dialog(native_context, qt_application)
    connect(dialog, native_context, qt_application)
    assert "Đã ghép" not in dialog.status_label.text()
    native_context.service.pair_on_poll = True
    for _ in range(150):
        qt_application.processEvents()
        if "Đã ghép" in dialog.status_label.text():
            break
        QTest.qWait(10)
    assert "Đã ghép" in dialog.status_label.text()
    assert dialog.manual_code.text() == "" and not dialog.open_button.isEnabled()
    dialog.reject()


@pytest.mark.parametrize("mutation", ["payload", "noncanonical", "manual", "expired", "future"])
def test_invalid_or_expired_invitation_never_renders_link_or_manual_code(
    native_context, qt_application, mutation
):
    dialog = open_dialog(native_context, qt_application)
    connect(dialog, native_context, qt_application)
    original = native_context.service.issue_pairing

    async def invalid_invitation():
        value = await original()
        if mutation == "payload":
            value.payload = "https://evil.invalid/private-canary"
        elif mutation == "noncanonical":
            value.payload = "pair_" + "A" * 42 + "B"
        elif mutation == "manual":
            value.manual_code = "<script>private-canary</script>"
        else:
            value.expires_at = datetime.now(UTC) + timedelta(
                seconds=-1 if mutation == "expired" else 301
            )
        return value

    native_context.service.issue_pairing = invalid_invitation
    dialog.pair_button.click()
    finish_action(dialog, qt_application)
    assert not dialog.open_button.isEnabled() and dialog.manual_code.text() == ""
    assert "canary" not in dialog.status_label.text()
    dialog.reject()


def test_expiry_clears_native_invitation_before_another_open(native_context, qt_application):
    dialog = open_dialog(native_context, qt_application)
    connect(dialog, native_context, qt_application)
    dialog.pair_button.click()
    finish_action(dialog, qt_application)
    dialog._invitation_deadline = monotonic() - 1
    dialog._poll()
    assert dialog.manual_code.text() == "" and not dialog.open_button.isEnabled()
    assert dialog._invitation is None
    dialog.reject()


def test_reopening_link_cannot_extend_original_monotonic_deadline(
    native_context, qt_application, monkeypatch
):
    module = importlib.import_module("tg_assistant.desktop.dialogs.bot")
    current = [100.0]
    monkeypatch.setattr(module, "monotonic", lambda: current[0])
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda _: True)
    dialog = open_dialog(native_context, qt_application)
    connect(dialog, native_context, qt_application)
    dialog.pair_button.click()
    finish_action(dialog, qt_application)
    original = dialog._invitation_deadline
    current[0] += 100
    dialog.open_button.click()
    assert dialog._invitation_deadline <= original
    dialog.reject()


def test_closed_dialog_rejects_late_pairing_result_and_new_submissions(
    native_context, qt_application
):
    dialog = open_dialog(native_context, qt_application)
    connect(dialog, native_context, qt_application)
    gate = asyncio.Event()
    entered = Event()
    original = native_context.service.issue_pairing

    async def delayed_pairing():
        entered.set()
        await gate.wait()
        return await original()

    native_context.service.issue_pairing = delayed_pairing
    dialog.pair_button.click()
    assert entered.wait(1)
    dialog.reject()
    native_context.runner.loop.call_soon_threadsafe(gate.set)
    QTest.qWait(100)
    dialog._poll()
    dialog.pair_button.click()
    assert dialog._closed and dialog._invitation is None and dialog.manual_code.text() == ""
    assert native_context.runner.thread.is_alive()


def test_periodic_owning_account_refresh_withdraws_native_link_and_pair_capability(
    native_context, qt_application, monkeypatch
):
    module = importlib.import_module("tg_assistant.desktop.dialogs.bot")
    current = [100.0]
    monkeypatch.setattr(module, "monotonic", lambda: current[0])
    dialog = open_dialog(native_context, qt_application)
    connect(dialog, native_context, qt_application)
    dialog.pair_button.click()
    finish_action(dialog, qt_application)
    assert dialog.open_button.isEnabled()

    async def account_lost():
        native_context.service.ready = False
        native_context.service.code = "bot_account_required"

    native_context.refresh_async = account_lost
    current[0] += 20.1
    dialog._poll()
    finish_action(dialog, qt_application)
    assert not dialog.pair_button.isEnabled() and not dialog.open_button.isEnabled()
    assert dialog.manual_code.text() == "" and dialog._invitation is None
    dialog.reject()
