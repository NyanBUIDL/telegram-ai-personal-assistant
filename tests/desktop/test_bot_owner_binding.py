"""Native borrow admission against real SQLite/guard and synthetic SDK transport.

These fixtures never establish live Telegram health; only the private account
boundary, durable candidate identity and actual resource lifecycle are tested.
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from telethon.crypto import AuthKey

from tg_assistant.desktop import telegram_context as module
from tg_assistant.desktop.instance import AlreadyRunning, InstanceGuard
from tg_assistant.services import telegram_publication as publication_module

# Reuse O03's actual migrated disposable SQLite harness without adding a global
# import path or relying on pytest's collection order across directories.
_spec = importlib.util.spec_from_file_location(
    "o04_account_publication_fixtures",
    Path(__file__).resolve().parents[1] / "integration/test_telegram_publication.py",
)
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
OWNER, candidate, publish = _fixtures.OWNER, _fixtures.candidate, _fixtures.publish
owned_engine, system = _fixtures.owned_engine, _fixtures.system


class AccountTransport:
    """The existing O03 SDK adapter shape, with real disconnect gating."""

    def __init__(self, session):
        self.session = session
        self.connected = False
        self.authorized = True
        self.me = SimpleNamespace(id=OWNER, bot=False, deleted=False)
        self.disconnect_started = Event()
        self.disconnect_gate = None

    async def connect(self):
        self.connected = True

    def is_connected(self):
        return self.connected

    async def disconnect(self):
        self.disconnect_started.set()
        if self.disconnect_gate is not None:
            await self.disconnect_gate.wait()
        self.connected = False

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        return self.me


@pytest.fixture
def account(system):
    publication = system.make()
    publish(publication)
    transports = []

    def factory(session, api_id, api_hash):
        assert api_id == 12345 and api_hash == "a" * 32
        transport = AccountTransport(session)
        transports.append(transport)
        return transport

    now = [datetime.now(UTC)]
    context = module.TelegramContext(
        engine=system.engine,
        profile_root=system.root,
        profile_id="publication-profile",
        fence=system.fence,
        store=system.store,
        publication=publication,
        client_factory=factory,
        now=lambda: now[0],
        request_timeout=0.5,
    )
    value = SimpleNamespace(context=context, transports=transports, now=now, system=system)
    try:
        yield value
    finally:
        for transport in transports:
            if transport.disconnect_gate is not None and context.runner.thread.is_alive():
                context.runner.loop.call_soon_threadsafe(transport.disconnect_gate.set)
        context.close()


def borrow(context):
    # Behavior RED for a missing composition hook, rather than an import error.
    factory = getattr(context, "borrow_bot_binding", None)
    assert callable(factory), "native account must provide a lifetime-bound bot borrowing hook"
    return factory()


def assert_denied(binding):
    assert binding.account_verifier() is None
    with pytest.raises(ValueError, match="^bot_account_binding_unavailable$"):
        binding.check_ownership()
    with pytest.raises(ValueError, match="^bot_account_binding_unavailable$"):
        _ = binding.runner
    with pytest.raises(ValueError, match="^bot_account_binding_unavailable$"):
        _ = binding.owner_id
    with pytest.raises(ValueError, match="^bot_account_binding_unavailable$"):
        _ = binding.fingerprint


def test_unmeasured_saved_session_cannot_be_borrowed(account):
    with pytest.raises(ValueError, match="^bot_account_binding_unavailable$"):
        borrow(account.context)


def test_borrow_uses_actual_verified_owner_and_same_running_sdk_loop(account):
    context = account.context
    context.refresh()
    binding = borrow(context)
    proof = binding.account_verifier()
    assert proof is not None and proof.owner_id == OWNER
    assert binding.owner_id == OWNER
    assert type(binding.owner_id) is int and binding.owner_id > 0
    assert binding.fingerprint == proof.fingerprint
    assert binding.check_ownership() is None
    assert binding.runner is context.runner

    async def owned_loop():
        return asyncio.get_running_loop()

    assert binding.runner.submit(owned_loop()).result(2) is context.runner.loop
    with pytest.raises(AlreadyRunning):
        InstanceGuard(account.system.control).acquire()


@pytest.mark.parametrize("seconds", [30, -1])
def test_borrow_refuses_stale_or_clock_rollback_without_refreshing_age(account, seconds):
    context = account.context
    context.refresh()
    binding = borrow(context)
    measured = context.connection_status().checked_at
    account.now[0] += timedelta(seconds=seconds)
    assert_denied(binding)
    assert context.connection_status().checked_at == measured


@pytest.mark.parametrize("mutation", ["material", "candidate", "disconnected", "cancelled"])
def test_borrow_refuses_changed_or_unavailable_actual_sdk_candidate(account, mutation):
    context = account.context
    context.refresh()
    binding = borrow(context)
    client = account.transports[-1]
    if mutation == "material":
        client.session.auth_key = candidate(8).auth_key
    elif mutation == "candidate":
        context.service._client = AccountTransport(client.session)
        context.service._client.connected = True
    elif mutation == "disconnected":
        client.connected = False
    else:
        context.service.request_cancel()
    assert_denied(binding)


def test_old_borrow_cannot_adopt_new_same_owner_publication(account):
    context = account.context
    context.refresh()
    old = borrow(context)
    old_fingerprint = old.fingerprint
    publish(context.publication, candidate(9))
    # An actual owning SDK refresh verifies the new candidate before reborrowing.
    context.runner.submit(
        context.service.resume_existing(
            context.publication.read_current(), api_id=12345, api_hash="a" * 32
        )
    ).result(2)
    assert context.verification() is not None
    assert_denied(old)
    new = borrow(context)
    assert new.owner_id == OWNER and new.fingerprint != old_fingerprint
    assert new.account_verifier() is not None


@pytest.mark.parametrize(
    "mutation", ["sid", "profile", "engine", "selection", "guard", "publication"]
)
def test_borrow_checks_current_sid_selected_profile_engine_and_guard(
    account, monkeypatch, mutation
):
    context = account.context
    context.refresh()
    binding = borrow(context)
    other_engine = None
    if mutation == "sid":
        monkeypatch.setattr(module, "current_user_sid", lambda: "different-current-sid")
    elif mutation == "profile":
        context.profile_id = "different-profile"
    elif mutation == "engine":
        other_engine = create_engine("sqlite:///" + str(account.system.root / "other.sqlite3"))
        context.engine = other_engine
    elif mutation == "selection":
        monkeypatch.setattr(
            context.engine,
            "url",
            context.engine.url.set(database=str(account.system.root / "other.db")),
        )
    elif mutation == "guard":
        context.publication._guard.close()
    else:
        context.publication.close()
    try:
        assert_denied(binding)
    finally:
        if other_engine is not None:
            other_engine.dispose()


def test_actual_worker_retained_guard_cannot_be_borrowed_as_native_writer(account, monkeypatch):
    account.context.close()
    system = account.system
    monkeypatch.setattr(publication_module, "native_control_directory", lambda: system.control)
    guard = InstanceGuard(system.control).acquire()
    publication = publication_module.EncryptedSessionPublication(
        engine=system.engine,
        profile_root=system.root,
        profile_id="publication-profile",
        maintenance=system.fence,
        store=system.store,
        _retained_guard=guard,
    )
    context = module.TelegramContext(
        engine=system.engine,
        profile_root=system.root,
        profile_id="publication-profile",
        fence=system.fence,
        store=system.store,
        publication=publication,
        client_factory=lambda session, *_: AccountTransport(session),
        now=lambda: account.now[0],
        readonly_resume=True,
        request_timeout=0.5,
    )
    try:
        context.refresh()
        assert context.verification() is not None
        with pytest.raises(ValueError, match="^bot_account_binding_unavailable$"):
            borrow(context)
        # Even accidentally changing the view mode cannot transfer ownership of
        # the worker's actual retained guard into a native writer binding.
        context._readonly_resume = False
        with pytest.raises(ValueError, match="^bot_account_binding_unavailable$"):
            borrow(context)
        context.close()
        with pytest.raises(AlreadyRunning):
            InstanceGuard(system.control).acquire()
    finally:
        context.close()
        guard.close()


def test_private_ownership_failures_never_export_native_exception_text(account, monkeypatch):
    context = account.context
    context.refresh()
    binding = borrow(context)

    def unavailable():
        raise RuntimeError("synthetic-private-exception-canary")

    monkeypatch.setattr(context.publication, "_check", unavailable)
    assert_denied(binding)


def test_sdk_disposal_during_durable_read_cannot_leave_borrow_admitted(account, monkeypatch):
    context = account.context
    context.refresh()
    binding = borrow(context)
    original = context.publication.is_current_readonly

    def dispose_during_read(session, owner):
        # The owning loop disposes its real candidate before the disconnected
        # observation callback. A metadata read must check it again on return.
        context.runner.submit(context.service._dispose()).result(2)
        return original(session, owner)

    monkeypatch.setattr(context.publication, "is_current_readonly", dispose_during_read)
    assert_denied(binding)


def test_same_client_material_change_after_durable_read_withdraws_borrow(account, monkeypatch):
    context = account.context
    context.refresh()
    binding = borrow(context)
    original = context.publication.is_current_readonly

    def change_after_read(session, owner):
        result = original(session, owner)
        session.auth_key = AuthKey(b"c" * 256)
        return result

    monkeypatch.setattr(context.publication, "is_current_readonly", change_after_read)
    assert_denied(binding)


def test_readonly_account_observer_cannot_grant_native_bot_writer_ownership(account):
    context = account.context
    context.refresh()
    assert context.verification() is not None
    context._readonly_resume = True
    with pytest.raises(ValueError, match="^bot_account_binding_unavailable$"):
        borrow(context)


def test_closed_account_invalidates_borrow_before_sdk_drain_and_guard_release(account):
    context = account.context
    context.refresh()
    binding = borrow(context)
    runner = binding.runner
    transport = account.transports[-1]
    transport.disconnect_gate = asyncio.Event()
    failures = []

    def close():
        try:
            context.close()
        except Exception as exc:
            failures.append(type(exc).__name__)

    closer = Thread(target=close)
    closer.start()
    try:
        assert transport.disconnect_started.wait(2)
        assert_denied(binding)
        with pytest.raises(AlreadyRunning):
            InstanceGuard(account.system.control).acquire()
    finally:
        runner.loop.call_soon_threadsafe(transport.disconnect_gate.set)
        closer.join(3)
    assert not failures and not closer.is_alive()
    assert not runner.thread.is_alive() and not transport.connected
    assert_denied(binding)
    with InstanceGuard(account.system.control):
        pass
