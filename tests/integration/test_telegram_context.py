"""Real selected storage/guard/encryption; synthetic Telegram transport only."""

from __future__ import annotations

import asyncio
import importlib
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update
from test_telegram_publication import OWNER, candidate, owned_engine, publish, system  # noqa: F401

from tg_assistant.config import Settings
from tg_assistant.db.models import AppSetting, TelegramAccount
from tg_assistant.desktop.instance import AlreadyRunning, InstanceGuard

# Imported real fixtures intentionally share the parametrized owned DB harness.
# ruff: noqa: F811


class Transport:
    def __init__(self):
        self.authorized = True
        self.me = SimpleNamespace(id=OWNER, bot=False, deleted=False)
        self.disconnects = 0
        self.get_me_calls = 0
        self.gate = None
        self.connect_started = Event()

    async def connect(self):
        if self.gate:
            self.connect_started.set()
            await self.gate.wait()

    async def disconnect(self):
        self.disconnects += 1

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        self.get_me_calls += 1
        return self.me

    async def send_code_request(self, phone):
        return SimpleNamespace(phone_code_hash="synthetic-challenge")

    async def sign_in(self, *args, **kwargs):
        self.authorized = True
        self.session.set_dc(2, "149.154.167.51", 443)
        self.session.auth_key = candidate().auth_key


def make_context(system, *, saved=True, readonly=False):
    adapter = system.make()
    if saved:
        publish(adapter)
    transport = Transport()

    def factory(session, api_id, api_hash):
        assert api_id == 12345 and api_hash == "a" * 32
        transport.session = session
        return transport

    now = [datetime.now(UTC)]
    try:
        module = importlib.import_module("tg_assistant.desktop.telegram_context")
    except ModuleNotFoundError:
        # Callable preintegration seam: behavior assertions below establish RED.
        value = SimpleNamespace(
            refresh=lambda: None,
            verification=lambda: None,
            connection_status=lambda: SimpleNamespace(state="unknown", checked_at=None),
            close=lambda: adapter.close(),
        )
    else:
        value = module.TelegramContext(
            engine=system.engine,
            profile_root=system.root,
            profile_id="publication-profile",
            fence=system.fence,
            store=system.store,
            publication=adapter,
            client_factory=factory,
            now=lambda: now[0],
            request_timeout=0.2,
            readonly_resume=readonly,
        )
    return value, transport, now


def test_saved_resume_real_getme_and_authority(system):
    context, transport, _ = make_context(system)
    try:
        assert context.verification() is None  # disk presence is never authentication
        context.refresh()
        proof = context.verification()
        assert proof is not None and proof.owner_id == OWNER
        assert transport.get_me_calls == 1
        assert context.connection_status().state == "ready"
        assert context.connection_status().capabilities == ["authenticated"]
    finally:
        context.close()


def test_revocation_removes_proof_without_overwriting_session(system):
    context, transport, _ = make_context(system)
    try:
        original = (system.root / "sessions/account.session.enc").read_bytes()
        context.refresh()
        assert context.verification() is not None
        transport.authorized = False
        context.refresh()
        assert context.verification() is None
        assert context.connection_status().state == "disconnected"
        assert (system.root / "sessions/account.session.enc").read_bytes() == original
    finally:
        context.close()


def test_health_retains_measured_age_and_expires(system):
    context, _, now = make_context(system)
    try:
        context.refresh()
        measured = context.connection_status().checked_at
        assert measured == now[0]
        now[0] += timedelta(seconds=29)
        assert context.connection_status().checked_at == measured
        assert context.verification() is not None
        now[0] += timedelta(seconds=1)
        assert context.verification() is None
        assert context.connection_status().state == "unknown"
        assert context.connection_status().checked_at == measured
    finally:
        context.close()


def test_readonly_proof_inside_existing_sql_write_transaction(system):
    context, _, _ = make_context(system)
    try:
        context.refresh()
        with system.engine.connect() as connection:
            if system.engine.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.begin()
            proof = context.verification()
            assert proof is not None
            connection.rollback()
    finally:
        context.close()


@pytest.mark.parametrize("mutation", ["owner", "marker", "cipher", "credentials", "journal"])
def test_proof_refuses_changed_durable_binding(system, mutation):
    context, _, _ = make_context(system)
    try:
        context.refresh()
        assert context.verification() is not None
        if mutation == "owner":
            with system.engine.begin() as connection:
                connection.execute(update(TelegramAccount).values(telegram_user_id=OWNER + 1))
        elif mutation == "marker":
            with system.engine.begin() as connection:
                key, raw = connection.execute(select(AppSetting.key, AppSetting.value)).one()
                raw["generation"] = "b" * 32
                connection.execute(
                    update(AppSetting).where(AppSetting.key == key).values(value=raw)
                )
        elif mutation == "cipher":
            (system.root / "sessions/account.session.enc").write_bytes(b"corrupt")
        elif mutation == "credentials":
            system.store.set("telegram_api_hash", "b" * 32)
        else:
            (system.root / "sessions/publication.journal.enc").write_bytes(b"pending")
        assert context.verification() is None
        assert context.connection_status().state != "ready"
    finally:
        context.close()


def test_close_disconnects_before_real_guard_release(system):
    context, transport, _ = make_context(system)
    context.refresh()
    with pytest.raises(AlreadyRunning):
        InstanceGuard(system.control).acquire()
    context.close()
    assert transport.disconnects == 1
    assert context.verification() is None
    with InstanceGuard(system.control):
        pass


def test_fresh_phone_login_relay_comes_only_from_authenticated_service(system):
    context, transport, _ = make_context(system, saved=False)
    try:
        assert context.verification() is None
        future = context.runner.submit(
            context.service.submit_phone("+84900000000", api_id=12345, api_hash="a" * 32)
        )
        assert future.result(3).state == "code"
        assert context.verification() is None
        result = context.runner.submit(context.service.submit_code("12345")).result(3)
        assert result.state == "active"
        assert context.verification().owner_id == OWNER
        assert transport.get_me_calls == 1
    finally:
        context.close()


def test_borrowed_worker_guard_readonly_and_not_released(system, monkeypatch):
    module = importlib.import_module("tg_assistant.services.telegram_publication")
    adapter = system.make()
    publish(adapter)
    adapter.close()
    monkeypatch.setattr(module, "native_control_directory", lambda: system.control)
    guard = InstanceGuard(system.control).acquire()
    reader = module.EncryptedSessionPublication(
        engine=system.engine,
        profile_root=system.root,
        profile_id="publication-profile",
        maintenance=system.fence,
        store=system.store,
        _retained_guard=guard,
    )
    try:
        before = (system.root / "sessions/account.session.enc").read_bytes()
        restored = reader.read_current_readonly()
        assert reader.is_current_readonly(restored, OWNER) is True
        assert reader.current_fingerprint(OWNER)
        reader.close()
        assert guard.lock is not None and not guard.lock.closed
        assert (system.root / "sessions/account.session.enc").read_bytes() == before
        with pytest.raises(AlreadyRunning):
            InstanceGuard(system.control).acquire()
    finally:
        reader.close()
        guard.close()


def test_foreign_or_unheld_borrowed_guard_refused_without_release(system, monkeypatch):
    module = importlib.import_module("tg_assistant.services.telegram_publication")
    monkeypatch.setattr(module, "native_control_directory", lambda: system.control)
    for guard in (InstanceGuard(system.control), InstanceGuard(system.control / "other").acquire()):
        try:
            with pytest.raises(module.PublicationUnavailable):
                module.EncryptedSessionPublication(
                    engine=system.engine,
                    profile_root=system.root,
                    profile_id="publication-profile",
                    maintenance=system.fence,
                    store=system.store,
                    _retained_guard=guard,
                )
            if guard.lock:
                assert not guard.lock.closed
        finally:
            guard.close()


def test_new_auth_invalidates_old_proof_before_network_wait(system):
    context, transport, _ = make_context(system)
    try:
        context.refresh()
        assert context.verification() is not None
        transport.gate = asyncio.Event()
        pending = context.runner.submit(
            context.service.submit_phone("+84900000000", api_id=12345, api_hash="a" * 32)
        )
        assert transport.connect_started.wait(2)
        assert context.verification() is None
        context.runner.loop.call_soon_threadsafe(transport.gate.set)
        assert pending.result(3).state == "code"
    finally:
        if transport.gate:
            context.runner.loop.call_soon_threadsafe(transport.gate.set)
        context.close()


def test_borrowed_guard_cannot_publish_or_reconcile(system, monkeypatch):
    module = importlib.import_module("tg_assistant.services.telegram_publication")
    adapter = system.make()
    publish(adapter)
    adapter.close()
    monkeypatch.setattr(module, "native_control_directory", lambda: system.control)
    guard = InstanceGuard(system.control).acquire()
    reader = module.EncryptedSessionPublication(
        engine=system.engine,
        profile_root=system.root,
        profile_id="publication-profile",
        maintenance=system.fence,
        store=system.store,
        _retained_guard=guard,
    )
    try:
        original = (system.root / "sessions/account.session.enc").read_bytes()
        credentials = dict(system.store.values)
        with pytest.raises(module.PublicationUnavailable):
            publish(reader, candidate(9))
        with pytest.raises(module.PublicationUnavailable):
            reader.reconcile()
        assert (system.root / "sessions/account.session.enc").read_bytes() == original
        assert system.store.values == credentials
    finally:
        reader.close()
        guard.close()


def test_close_timeout_retains_guard_then_can_finish_drain(system, monkeypatch):
    context, _, _ = make_context(system)
    context.refresh()
    original = context.runner.submit

    class TimeoutFuture:
        def result(self, timeout):
            raise TimeoutError("synthetic-drain-timeout")

    def stalled(coroutine):
        coroutine.close()
        return TimeoutFuture()

    monkeypatch.setattr(context.runner, "submit", stalled)
    with pytest.raises(TimeoutError):
        context.close()
    assert context.verification() is None
    with pytest.raises(AlreadyRunning):
        InstanceGuard(system.control).acquire()
    monkeypatch.setattr(context.runner, "submit", original)
    context.close()
    with InstanceGuard(system.control):
        pass


def test_worker_resume_never_calls_mutating_recovery(system, monkeypatch):
    context, transport, _ = make_context(system, readonly=True)

    def forbidden(*args, **kwargs):
        raise AssertionError("mutating publisher path called")

    monkeypatch.setattr(context.publication, "_operation", forbidden)
    try:
        context.refresh()
        assert context.verification() is not None
        assert transport.get_me_calls == 1
        assert context.connection_status().state == "ready"
    finally:
        context.close()


def test_shared_native_dialog_preserves_verified_context_and_blank_inputs(system):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from tg_assistant.desktop.dialogs.telegram import TelegramDialog

    application = QApplication.instance() or QApplication([])
    context, transport, _ = make_context(system)
    try:
        context.refresh()
        dialog = TelegramDialog(context.service, runner=context.runner)
        dialog.show()
        application.processEvents()
        assert all(
            widget.text() == ""
            for widget in (
                dialog.api_id,
                dialog.api_hash,
                dialog.phone,
                dialog.otp,
                dialog.password,
            )
        )
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        application.processEvents()
        assert dialog._closed
        assert transport.disconnects == 0
        assert context.verification() is not None
        with pytest.raises(AlreadyRunning):
            InstanceGuard(system.control).acquire()
    finally:
        context.close()
    assert transport.disconnects == 1


def test_shared_native_dialog_cancel_drains_before_guard_release(system):
    from PySide6.QtWidgets import QApplication

    from tg_assistant.desktop.dialogs.telegram import TelegramDialog

    application = QApplication.instance() or QApplication([])
    context, transport, _ = make_context(system, saved=False)
    try:
        assert (
            context.runner.submit(
                context.service.submit_phone("+84900000000", api_id=12345, api_hash="a" * 32)
            )
            .result(3)
            .state
            == "code"
        )
        dialog = TelegramDialog(context.service, runner=context.runner)
        dialog.show()
        application.processEvents()
        dialog.reject()
        assert context.verification() is None
    finally:
        context.close()
    assert transport.disconnects == 1
    assert not context.runner.thread.is_alive()
    with InstanceGuard(system.control):
        pass


@pytest.mark.parametrize("factory_name", ["open_telegram_context", "open_worker_telegram_probe"])
def test_factory_refuses_different_selected_storage_before_credentials(
    system, monkeypatch, factory_name
):
    module = importlib.import_module("tg_assistant.desktop.telegram_context")
    settings = Settings(
        _env_file=None,
        data_dir=system.root,
        profile_id="publication-profile",
        storage_backend=system.engine.dialect.name,
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("factory accepted different selected storage")

    monkeypatch.setattr(module, "EncryptedSessionPublication", forbidden)
    kwargs = dict(engine=system.engine, fence=system.fence, secret_store_factory=forbidden)
    if factory_name == "open_worker_telegram_probe":
        kwargs["guard"] = InstanceGuard(system.control)
    with pytest.raises(ValueError, match="telegram_storage_mismatch"):
        getattr(module, factory_name)(settings, **kwargs)
