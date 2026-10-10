"""Synthetic external transport; real service/fence/Qt behavior, no live auth."""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import MemorySession

from tg_assistant.paths import APP_NAME
from tg_assistant.services.maintenance import MaintenanceService
from tg_assistant.services.telegram_login import TelegramLoginService

API = {"api_id": 12345, "api_hash": "a" * 32}


class SyntheticQR:
    def __init__(self, transport):
        self.transport = transport
        self.url = "tg://login?token=synthetic-native-only"
        self.expires = datetime.now(UTC) + timedelta(seconds=60)
        self.event = asyncio.Event()

    async def wait(self, timeout=None):  # noqa: ASYNC109 - Telethon transport signature
        await asyncio.wait_for(self.event.wait(), timeout)
        if self.transport.qr_fail:
            raise self.transport.qr_fail
        self.transport.authorized = True
        return self.transport.me


class SyntheticTransport:
    def __init__(self):
        self.authorized = False
        self.me = SimpleNamespace(id=9007199254740993, bot=False, deleted=False)
        self.fail = None
        self.qr_fail = None
        self.code_calls = 0
        self.password_calls = 0
        self.phone_requests = 0
        self.disconnect_calls = 0
        self.connected = False
        self.session = MemorySession()
        self.qrs = []
        self.connect_gate = None

    async def connect(self):
        if self.connect_gate:
            await self.connect_gate.wait()
        self.connected = True

    async def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False

    async def qr_login(self):
        qr = SyntheticQR(self)
        self.qrs.append(qr)
        return qr

    async def send_code_request(self, phone):
        self.phone_requests += 1
        if self.fail:
            raise self.fail
        return SimpleNamespace(phone_code_hash="synthetic-phone-code-hash")

    async def sign_in(self, phone=None, code=None, *, password=None, phone_code_hash=None):
        if code:
            assert phone == "+84900000000"
            assert phone_code_hash == "synthetic-phone-code-hash"
            self.code_calls += 1
        if password is not None:
            self.password_calls += 1
        if self.fail:
            raise self.fail
        self.authorized = True
        return self.me

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        return self.me if self.authorized else None


class SyntheticPublication:
    """Fake atomic writer/secret store; only auth API credentials may persist."""
    def __init__(self):
        self.credentials = {}
        self.owner = None
        self.before_commit = lambda: None
        self.active = True

    def publish(self, session, *, api_id, api_hash, owner_id, check_binding):
        self.before_commit()
        check_binding()
        self.credentials = {"telegram_api_id": str(api_id), "telegram_api_hash": api_hash}
        self.owner = owner_id
        return True

    def is_current(self, session, owner_id):
        return self.active and self.owner == owner_id


@pytest.fixture
def system(tmp_path):
    sid = ["synthetic-sid"]
    root = tmp_path / "profile"
    root.mkdir()
    (root / ".tg-assistant-data").write_text(json.dumps({
        "version": 1, "app": APP_NAME, "profile_id": "personal", "sid": sid[0],
    }))
    fence = MaintenanceService(root / "config", profile_id="personal")
    transport = SyntheticTransport()
    publication = SyntheticPublication()
    current_owner = [None]
    def factory(session, api_id, api_hash):
        assert isinstance(session, MemorySession)
        assert api_id == 12345 and api_hash == "a" * 32
        transport.session = session
        return transport
    service = TelegramLoginService(
        profile_root=root, profile_id="personal", maintenance=fence,
        sid_provider=lambda: sid[0], client_factory=factory, publication=publication,
        existing_owner=lambda: current_owner[0], request_timeout=0.2, qr_timeout=0.2,
    )
    return SimpleNamespace(service=service, transport=transport, publication=publication,
                           sid=sid, root=root, fence=fence, current_owner=current_owner)


async def phone(system):
    result = await system.service.submit_phone("+84900000000", **API)
    assert result.state == "code"


async def settle():
    for _ in range(12):
        await asyncio.sleep(0)


async def test_qr_expiry_refresh(system):
    service = system.service
    assert (await service.begin_qr(**API)).state == "qr"
    first = service.native_qr()
    assert first is not None
    await asyncio.sleep(0.23)
    assert service.status().code == "qr_expired"
    assert service.native_qr() is None
    assert (await service.begin_qr()).state == "qr"
    assert len(system.transport.qrs) == 2
    await service.cancel()


async def test_otp_invalid_retry_bounded(system):
    await phone(system)
    system.transport.fail = PhoneCodeInvalidError(request=None)
    for remaining in (2, 1, 0):
        status = await system.service.submit_code("12345")
        assert status.attempts_remaining == remaining
    assert status.code == "retry_limit"
    await system.service.submit_code("12345")
    assert system.transport.code_calls == 3
    assert not system.publication.credentials


async def test_2fa_cancel_no_persist(system):
    await phone(system)
    system.transport.fail = SessionPasswordNeededError(request=None)
    assert (await system.service.submit_code("12345")).state == "password"
    assert (await system.service.cancel()).state == "cancelled"
    assert not system.publication.credentials
    assert system.service.native_qr() is None
    assert (await system.service.submit_password("secret-canary")).code == "auth_state_invalid"


async def test_floodwait_countdown(system):
    await phone(system)
    system.transport.fail = FloodWaitError(request=None, capture=2)
    result = await system.service.submit_code("12345")
    assert result.code == "flood_wait" and result.retry_after == 2
    before = system.transport.code_calls
    assert (await system.service.submit_code("12345")).retry_after > 0
    assert system.transport.code_calls == before
    await system.service.cancel()


async def test_owner_from_verified_account(system):
    await phone(system)
    status = await system.service.submit_code("12345")
    assert status.state == "active" and status.owner_id == 9007199254740993
    assert system.publication.owner == 9007199254740993
    assert set(system.publication.credentials) == {"telegram_api_id", "telegram_api_hash"}
    await system.service.cancel()


async def test_revoke_session_reconnect(system):
    await phone(system)
    assert (await system.service.submit_code("12345")).state == "active"
    system.transport.authorized = False
    status = await system.service.check_session()
    assert status.state == "reconnect" and status.owner_id is None
    assert system.publication.owner == 9007199254740993


@pytest.mark.parametrize("identity", [None, 0, -1, True, "42"])
async def test_invalid_owner_never_published(system, identity):
    await phone(system)
    system.transport.me.id = identity
    assert (await system.service.submit_code("12345")).code == "owner_invalid"
    assert not system.publication.credentials


@pytest.mark.parametrize("flag", ["bot", "deleted"])
async def test_bot_or_deleted_owner_rejected(system, flag):
    await phone(system)
    setattr(system.transport.me, flag, True)
    assert (await system.service.submit_code("12345")).code == "owner_invalid"
    assert system.publication.owner is None


async def test_existing_owner_cannot_silently_change(system):
    system.current_owner[0] = 17
    await phone(system)
    assert (await system.service.submit_code("12345")).code == "owner_mismatch"
    assert not system.publication.credentials


async def test_sid_rechecked_immediately_before_writer_commit(system):
    await phone(system)
    system.publication.before_commit = lambda: system.sid.__setitem__(0, "other-sid")
    assert (await system.service.submit_code("12345")).code == "profile_binding_invalid"
    assert not system.publication.credentials


async def test_maintenance_blocks_publication_preserves_previous_session(system):
    (system.root / "account.session.enc").write_bytes(b"previous-ciphertext")
    await phone(system)
    lease = system.fence.acquire("synthetic-maintenance", timeout=0.2)
    try:
        assert (await system.service.submit_code("12345")).code == "maintenance_busy"
    finally:
        system.fence.release(lease)
    assert (system.root / "account.session.enc").read_bytes() == b"previous-ciphertext"
    assert not system.publication.credentials


async def test_cancel_interrupts_blocked_connect(system):
    system.transport.connect_gate = asyncio.Event()
    task = asyncio.create_task(system.service.begin_qr(**API))
    await settle()
    assert (await asyncio.wait_for(system.service.cancel(), 0.1)).state == "cancelled"
    await task
    assert not system.transport.connected
    assert not system.publication.credentials


async def test_unknown_transport_exception_never_exposes_native_secret(system):
    system.transport.fail = RuntimeError("+84900000000 password=secret-canary token=qr-canary")
    result = await system.service.submit_phone("+84900000000", **API)
    assert result.code == "auth_failed"
    assert "canary" not in repr(result)
    assert "+849" not in json.dumps(asdict(result))
    assert not system.publication.credentials


async def test_api_credentials_required_even_for_qr(system):
    result = await system.service.begin_qr()
    assert result.code == "api_credentials_required"
    assert not system.transport.connected


async def test_missing_publication_is_unavailable(system):
    system.service._publication = None
    await phone(system)
    assert (await system.service.submit_code("12345")).code == "publication_unavailable"
    assert system.service.status().state != "active"
    assert not system.publication.credentials


async def test_qr_fallback_cancels_waiter_before_phone_login(system):
    assert (await system.service.begin_qr(**API)).state == "qr"
    old = system.transport.qrs[0]
    await phone(system)
    old.event.set()
    await settle()
    assert system.service.status().state == "code"
    assert system.service.native_qr() is None
    assert not system.publication.credentials
    await system.service.cancel()


async def test_request_timeout_disconnects_candidate(system):
    system.transport.connect_gate = asyncio.Event()
    assert (await system.service.begin_qr(**API)).code == "auth_timeout"
    assert not system.transport.connected
    assert not system.publication.credentials


async def test_qr_2fa_and_owner_use_authenticated_get_me(system):
    assert (await system.service.begin_qr(**API)).state == "qr"
    system.transport.qrs[0].event.set()
    await asyncio.sleep(0.02)
    assert system.service.status().state == "active"
    assert system.service.status().owner_id == 9007199254740993
    assert "synthetic-native-only" not in repr(system.service.status())
    assert system.service.native_qr() is None
    await system.service.cancel()


async def test_restart_session_must_recheck_authorization_not_reuse_otp(system):
    await phone(system)
    system.publication.owner = 9007199254740993
    system.current_owner[0] = 9007199254740993
    system.transport.authorized = False
    result = await system.service.resume_existing(MemorySession(), **API)
    assert result.state == "reconnect"
    assert not system.publication.credentials
    assert system.transport.code_calls == 0


async def test_verified_restart_uses_current_runtime_session(system):
    system.publication.owner = 9007199254740993
    system.current_owner[0] = 9007199254740993
    system.transport.authorized = True
    assert (await system.service.resume_existing(MemorySession(), **API)).state == "active"
    assert not system.publication.credentials
    await system.service.cancel()


def test_native_dialog_blank_secret_fields_and_qr_targets(system, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton

    from tg_assistant.desktop.dialogs.telegram import TelegramDialog
    app = QApplication.instance() or QApplication([])
    dialog = TelegramDialog(system.service)
    for name in ("api_hash", "phone", "otp", "password"):
        field = dialog.findChild(QLineEdit, name)
        assert field is not None, f"native field missing: {name}"
        assert field.text() == "" and field.echoMode() == QLineEdit.EchoMode.Password
    for button in dialog.findChildren(QPushButton):
        assert button.minimumHeight() >= 44
    dialog.reject()
    app.processEvents()


def test_native_dialog_esc_cancels_and_restores_focus(system, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QVBoxLayout, QWidget

    from tg_assistant.desktop.dialogs.telegram import TelegramDialog
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    layout = QVBoxLayout(parent)
    control = QPushButton("Open native login", parent)
    layout.addWidget(control)
    parent.show()
    control.setFocus()
    app.processEvents()
    dialog = TelegramDialog(system.service, parent=parent)
    dialog.show()
    app.processEvents()
    password = dialog.findChild(QLineEdit, "password")
    assert password is not None, "native password fallback missing"
    password.setText("native-secret-canary")
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    app.processEvents()
    assert not dialog.isVisible()
    assert dialog.findChild(QLineEdit, "password").text() == ""
    assert parent.focusWidget() is control
    parent.close()


async def test_default_transport_suppresses_child_logs_and_uses_memory_session(system, caplog):
    from tg_assistant.services.telegram_login import _real_client
    client = _real_client(MemorySession(), **API)
    with caplog.at_level("DEBUG"):
        client._log["telethon.client.auth"].warning("native-secret-canary")
    assert not any("native-secret-canary" in record.message for record in caplog.records)
    assert isinstance(client.session, MemorySession)
    assert client.flood_sleep_threshold == 0
    await client.disconnect()


async def test_qr_2fa_fallback_clears_qr_then_bounded_password_retry(system):
    system.transport.qr_fail = SessionPasswordNeededError(request=None)
    assert (await system.service.begin_qr(**API)).state == "qr"
    system.transport.qrs[0].event.set()
    await asyncio.sleep(0.02)
    assert system.service.status().state == "password"
    assert system.service.native_qr() is None
    system.transport.fail = PasswordHashInvalidError(request=None)
    for remaining in (2, 1, 0):
        result = await system.service.submit_password("native-secret-canary")
        assert result.attempts_remaining == remaining
    assert result.code == "retry_limit"
    assert not system.publication.credentials


async def test_queued_begin_after_cancel_cannot_restart_or_publish(system):
    await system.service.cancel()
    result = await system.service.begin_qr(**API)
    assert result.code == "login_cancelled"
    assert not system.transport.connected
    assert not system.publication.credentials


async def test_parallel_code_submissions_publish_once(system):
    await phone(system)
    one, two = await asyncio.gather(system.service.submit_code("12345"),
                                    system.service.submit_code("12345"))
    assert one.state == "active" and two.code == "auth_state_invalid"
    assert system.transport.code_calls == 1
    assert system.publication.owner == 9007199254740993
    assert (await system.service.check_session()).state == "active"
    await system.service.cancel()


def test_native_dialog_qr_and_fallback_async_io(system, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from tg_assistant.desktop.dialogs.telegram import TelegramDialog
    app = QApplication.instance() or QApplication([])
    dialog = TelegramDialog(system.service)
    dialog.show()
    dialog.api_id.setText("12345")
    dialog.api_hash.setText("a" * 32)
    dialog.qr_button.click()
    assert dialog.api_id.text() == ""
    assert dialog.api_hash.text() == ""
    assert dialog.close_button.isEnabled()
    for _ in range(50):
        app.processEvents()
        if system.service.status().state == "qr" and dialog._future is None:
            break
        QTest.qWait(10)
    app.processEvents()
    assert system.service.status().state == "qr"
    assert not dialog.qr_label.pixmap().isNull()
    assert "tg://" not in dialog.status_label.text()
    dialog.phone.setText("+84900000000")
    dialog.phone_button.click()
    assert dialog.phone.text() == ""
    for _ in range(50):
        app.processEvents()
        if system.service.status().state == "code" and dialog._future is None:
            break
        QTest.qWait(10)
    assert system.service.status().state == "code"
    assert dialog.otp.isEnabled()
    assert dialog.qr_label.pixmap().isNull()
    dialog.reject()
    dialog._worker.thread.join(1)
    assert not dialog._worker.thread.is_alive()


def test_native_dialog_art_focus_and_logical_geometry(system, qt_application):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel, QPushButton, QScrollArea

    from tg_assistant.desktop.dialogs.telegram import TelegramDialog
    from tg_assistant.desktop.theme import ArtPanel
    dialog = TelegramDialog(system.service)
    dialog.show()
    QTest.qWait(50)
    scroll = dialog.findChild(QScrollArea)
    assert scroll.horizontalScrollBar().maximum() == 0
    assert qt_application.font().family().startswith("Darley")
    for button in dialog.findChildren(QPushButton):
        assert button.height() >= 44 and button.width() >= 44
        assert button.font().family().startswith("Darley")
    panel = dialog.findChild(ArtPanel)
    pixels = panel.grab().toImage()
    assert pixels.pixelColor(0, 0).name() == "#090909"
    inside = round(5 * pixels.devicePixelRatio())
    assert pixels.pixelColor(inside, inside).name() == "#fffdf5"
    title = [label for label in dialog.findChildren(QLabel)
             if label.property("artRole") == "title"][0]
    assert title.font().family() == "LNTH-Peter Obscure"
    for _ in range(15):
        QTest.keyClick(qt_application.focusWidget(), Qt.Key.Key_Tab)
        assert dialog.isAncestorOf(qt_application.focusWidget())
    # Capture only idle native dialog: no QR, phone, OTP, API hash or password.
    if os.environ.get("O03_ART_EVIDENCE_DIR"):
        destination = Path(os.environ["O03_ART_EVIDENCE_DIR"])
        destination.mkdir(parents=True, exist_ok=True)
        scale = os.environ.get("QT_SCALE_FACTOR", "1")
        assert dialog.grab().save(str(destination / f"telegram-idle-scale-{scale}.png"))
    dialog.reject()
    dialog._worker.thread.join(1)
    assert not dialog._worker.thread.is_alive()


@pytest.mark.parametrize("invalid", ["", "12", "native-input-canary", "12345678901", None])
async def test_local_invalid_otp_can_be_corrected_without_resend(system, invalid):
    await phone(system)
    candidate = system.transport.session
    result = await system.service.submit_code(invalid)
    assert result.state == "code" and result.code == "code_input_invalid"
    assert result.attempts_remaining == 3
    assert system.transport.connected and system.transport.session is candidate
    assert system.transport.code_calls == system.transport.disconnect_calls == 0
    assert system.transport.phone_requests == 1
    assert not system.publication.credentials
    assert "native-input-canary" not in repr(result)
    assert (await system.service.submit_code("12345")).state == "active"
    assert system.transport.phone_requests == 1 and system.transport.code_calls == 1
    assert set(system.publication.credentials) == {"telegram_api_id", "telegram_api_hash"}
    await system.service.cancel()


@pytest.mark.parametrize("invalid", ["", None, "x" * 1025])
async def test_local_invalid_2fa_can_be_corrected_without_disposal(system, invalid):
    await phone(system)
    system.transport.fail = SessionPasswordNeededError(request=None)
    assert (await system.service.submit_code("12345")).state == "password"
    system.transport.fail = None
    result = await system.service.submit_password(invalid)
    assert result.state == "password" and result.code == "password_input_invalid"
    assert result.attempts_remaining == 3
    assert system.transport.connected
    assert system.transport.password_calls == system.transport.disconnect_calls == 0
    assert system.transport.phone_requests == 1 and system.transport.code_calls == 1
    assert not system.publication.credentials
    assert (await system.service.submit_password("synthetic-correct-password")).state == "active"
    assert system.transport.phone_requests == 1 and system.transport.password_calls == 1
    assert set(system.publication.credentials) == {"telegram_api_id", "telegram_api_hash"}
    await system.service.cancel()


async def test_local_validation_does_not_reset_server_retry_budget(system):
    await phone(system)
    system.transport.fail = PhoneCodeInvalidError(request=None)
    assert (await system.service.submit_code("12345")).attempts_remaining == 2
    result = await system.service.submit_code("12")
    assert result.state == "code" and result.attempts_remaining == 2
    assert system.transport.code_calls == 1
    assert (await system.service.submit_code("12345")).attempts_remaining == 1
    assert (await system.service.submit_code("")).attempts_remaining == 1
    assert (await system.service.submit_code("12345")).code == "retry_limit"
    assert not system.transport.connected and system.transport.code_calls == 3
    assert not system.publication.credentials


@pytest.mark.parametrize("outcome", ["expired", "flood", "cancelled", "binding"])
async def test_local_validation_preserves_terminal_and_flood_rules(system, outcome):
    await phone(system)
    assert (await system.service.submit_code("12")).state == "code"
    if outcome == "expired":
        system.transport.fail = PhoneCodeExpiredError(request=None)
        assert (await system.service.submit_code("12345")).code == "code_expired"
        assert not system.transport.connected
    elif outcome == "flood":
        system.transport.fail = FloodWaitError(request=None, capture=2)
        assert (await system.service.submit_code("12345")).retry_after == 2
        before = system.transport.code_calls
        assert (await system.service.submit_code("12")).retry_after > 0
        assert system.transport.code_calls == before
        await system.service.cancel()
    elif outcome == "cancelled":
        assert (await system.service.cancel()).state == "cancelled"
        assert (await system.service.submit_code("12345")).code == "auth_state_invalid"
        assert not system.transport.connected
    else:
        system.sid[0] = "another-sid"
        assert (await system.service.submit_code("")).code == "profile_binding_invalid"
        assert not system.transport.connected
    assert not system.publication.credentials


@pytest.mark.parametrize("mode", ["code", "password"])
def test_native_local_validation_keeps_challenge_correctable(system, qt_application, mode):
    from PySide6.QtTest import QTest

    from tg_assistant.desktop.dialogs.telegram import TelegramDialog
    dialog = TelegramDialog(system.service)
    dialog.show()
    dialog.api_id.setText("12345")
    dialog.api_hash.setText("a" * 32)
    dialog.phone.setText("+84900000000")
    dialog.phone_button.click()
    def finish_step():
        for _ in range(50):
            qt_application.processEvents()
            if dialog._future is None:
                return
            QTest.qWait(10)
        pytest.fail("native synthetic IO did not settle")
    try:
        finish_step()
        if mode == "password":
            system.transport.fail = SessionPasswordNeededError(request=None)
            dialog.otp.setText("12345")
            dialog.code_button.click()
            finish_step()
            system.transport.fail = None
            field, button, expected = dialog.password, dialog.password_button, "password_input_invalid"
            invalid_values = ("",)
        else:
            field, button, expected = dialog.otp, dialog.code_button, "code_input_invalid"
            invalid_values = ("", "12")
        for invalid in invalid_values:
            field.setText(invalid)
            assert button.isEnabled()
            button.click()
            finish_step()
            result = system.service.status()
            assert result.state == mode and result.code == expected
            assert field.isEnabled() and button.isEnabled()
            assert field.text() == ""
            assert result.attempts_remaining == 3
            assert system.transport.connected and system.transport.disconnect_calls == 0
            assert system.transport.phone_requests == 1
            assert not system.publication.credentials
            assert "Nhập" in dialog.status_label.text()
        field.setText("12345" if mode == "code" else "synthetic-correct-password")
        button.click()
        finish_step()
        assert system.service.status().state == "active"
        assert field.text() == "" and system.transport.phone_requests == 1
        assert set(system.publication.credentials) == {"telegram_api_id", "telegram_api_hash"}
    finally:
        dialog.reject()
        dialog._worker.thread.join(1)
    assert not dialog._worker.thread.is_alive()
