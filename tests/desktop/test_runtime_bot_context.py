"""Runtime bot composition over real SQLite/guard and one existing account client."""

from __future__ import annotations

import asyncio
import importlib.util
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiogram.types import Update
from sqlalchemy import create_engine, event, update
from telethon.crypto import AuthKey

from tg_assistant.config import Settings
from tg_assistant.db.base import configure_sqlite
from tg_assistant.db.models import TelegramAccount
from tg_assistant.desktop import telegram_context
from tg_assistant.desktop.instance import AlreadyRunning, InstanceGuard
from tg_assistant.paths import current_user_sid
from tg_assistant.services.bot_credentials import ScopedBotCredentials
from tg_assistant.services.bot_pairing import AccountProof, PairingRepository
from tg_assistant.services.telegram_publication import EncryptedSessionPublication
from tg_assistant.telegram.bot_api import BotIdentity

_spec = importlib.util.spec_from_file_location(
    "runtime_bot_publication_fixtures",
    Path(__file__).resolve().parents[1] / "integration/test_telegram_publication.py",
)
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
owned_engine, system = _fixtures.owned_engine, _fixtures.system
OWNER = _fixtures.OWNER
TOKEN = "123456:SYNTHETIC_RUNTIME_TOKEN_CANARY"


async def wait_for_dispatch(polling, entered):
    waiter = asyncio.create_task(entered.wait())
    try:
        done, _ = await asyncio.wait({polling, waiter}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
        if polling in done:
            await polling
        assert waiter in done
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


class ExistingClient:
    def __init__(self):
        self.session = _fixtures.candidate()
        self.connected = True
        self.authorized = True
        self.me = SimpleNamespace(id=OWNER, bot=False, deleted=False)
        self.get_me_calls = 0

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        self.get_me_calls += 1
        return self.me


class MemoryCredentials:
    def __init__(self):
        self.values = {}

    def set_password(self, service, name, value):
        self.values[service, name] = value

    def get_password(self, service, name):
        return self.values.get((service, name))

    def delete_password(self, service, name):
        self.values.pop((service, name), None)


class OwnedBotTransport:
    """Controlled Bot API outcomes; real service/repository enforce publication."""
    def __init__(self, token):
        assert token == TOKEN
        self.bot = object()
        self.identity_checked_at = self.poll_checked_at = None
        self.calls = []
        self.updates = []
        self.closed = False
        self.close_gate = None
        self.now = lambda: datetime.now(UTC)

    async def verify_identity(self):
        self.calls.append("identity")
        self.identity_checked_at = self.now()
        return BotIdentity(900000001, "fixture_bot")

    async def poll_once(self, cursor=None, *, allowed_updates=("message",)):
        self.calls.append(("poll", cursor, allowed_updates))
        self.poll_checked_at = self.now()
        return list(self.updates)

    async def close(self):
        if self.close_gate is not None:
            await self.close_gate.wait()
        self.closed = True


@pytest_asyncio.fixture
async def runtime_system(system, monkeypatch):
    path = system.root / "db/assistant.sqlite3"
    path.parent.mkdir()
    destination = sqlite3.connect(path)
    with system.engine.connect() as connection:
        connection.connection.driver_connection.backup(destination)
    destination.close()
    engine = create_engine("sqlite:///" + str(path))
    event.listen(engine, "connect", configure_sqlite)
    settings = Settings(_env_file=None, data_dir=system.root, profile_id="publication-profile")
    publisher = EncryptedSessionPublication(
        engine=engine, profile_root=system.root, profile_id=settings.profile_id,
        maintenance=system.fence, store=system.store, control_directory=system.control,
    )
    assert _fixtures.publish(publisher)
    publisher.close()
    (system.root / "sessions/account.session").write_bytes(b"synthetic-runtime-working-copy")
    guard = InstanceGuard(system.control).acquire()
    monkeypatch.setattr(telegram_context, "native_control_directory", lambda: system.control)
    client = ExistingClient()
    runtime = SimpleNamespace(
        user=SimpleNamespace(client=client, owner_id=OWNER), settings=settings,
        store=system.store, _closed=False, _close_task=None, stopping=asyncio.Event(),
    )
    observer = telegram_context.RuntimeTelegramObservation(
        settings, engine=engine, fence=system.fence, guard=guard, store=system.store,
    )
    await observer.refresh(runtime)

    def proof():
        value = observer.verification()
        return AccountProof(int(value.owner_id), value.fingerprint) if value else None

    repo = PairingRepository(
        engine=engine, profile_id=settings.profile_id, profile_root=system.root,
        fence=system.fence, check_ownership=observer._check, account_verifier=proof,
        now=lambda: datetime.now(UTC),
    )
    backend = MemoryCredentials()
    credentials = ScopedBotCredentials(
        settings.profile_id, backend, observer._check, repo.current_reference, current_user_sid,
        publication_guard=repo.publication_guard, candidate_unpublished=repo.candidate_unpublished,
    )
    slot = credentials.allocate(TOKEN)
    repo.publish_enrollment(bot_id=900000001, username="fixture_bot", generation=slot.generation, credential_slot=slot.reference)
    invitation = repo.issue_challenge()
    assert repo.consume(invitation.payload, kind="nonce", sender_id=OWNER, update_id=1)
    observer.close()
    transports = []

    def factory(token):
        value = OwnedBotTransport(token)
        transports.append(value)
        return value

    contexts = []

    def compose():
        spec = importlib.util.find_spec("tg_assistant.desktop.runtime_bot_context")
        assert spec is not None, "Actual retained-worker runtime bot composition is missing"
        from tg_assistant.desktop.runtime_bot_context import RuntimeBotContext

        value = RuntimeBotContext(
            settings, engine=engine, fence=system.fence, guard=guard, runtime=runtime,
            _credential_backend=backend, _client_factory=factory,
        )
        contexts.append(value)
        return value

    value = SimpleNamespace(
        settings=settings, engine=engine, fence=system.fence, guard=guard,
        client=client, runtime=runtime, backend=backend, transports=transports,
        compose=compose, root=system.root,
    )
    try:
        yield value
    finally:
        runtime.stopping.clear()
        for transport in transports:
            if transport.close_gate is not None:
                transport.close_gate.set()
        for context in contexts:
            await context.close()
        guard.close()
        engine.dispose()


async def test_prepare_borrows_actual_existing_account_and_retains_guard(runtime_system):
    value = runtime_system
    context = value.compose()
    assert context.management_admitted() is False
    await context.prepare()
    assert context.management_admitted() is True
    assert context.account_observation._client_ref() is value.client
    assert context.setup_observation._account is context.account_observation
    assert context.bot is value.transports[0].bot
    assert len(value.transports) == 1
    with pytest.raises(AlreadyRunning):
        InstanceGuard(value.guard.directory).acquire()
    await context.close()
    context.setup_observation.close()
    assert value.client.connected and value.guard.lock and not value.guard.lock.closed


@pytest.mark.parametrize("changed", ["client", "owner", "flag", "stopping", "profile"])
async def test_current_runtime_binding_changes_withdraw_admission(runtime_system, changed):
    value = runtime_system
    context = value.compose()
    await context.prepare()
    assert context.management_admitted() is True
    if changed == "client":
        value.runtime.user.client = ExistingClient()
    elif changed == "owner":
        value.runtime.user.owner_id = OWNER + 1
    elif changed == "flag":
        with value.engine.begin() as connection:
            connection.execute(update(TelegramAccount).values(is_owner_paired=False))
    elif changed == "stopping":
        value.runtime.stopping.set()
    else:
        value.runtime.settings = Settings(_env_file=None, data_dir=value.root, profile_id="other-profile")
    assert context.management_admitted() is False
    assert context.bot_verification() is None and context.pairing_verification() is None


async def test_synchronous_adapter_cannot_release_before_async_bot_drain(runtime_system):
    context = runtime_system.compose()
    await context.prepare()
    with pytest.raises(ValueError, match="bot_shutdown_pending"):
        context.setup_observation.close()
    assert runtime_system.client.connected and not runtime_system.guard.lock.closed
    await context.close()
    context.setup_observation.close()
    assert context.management_admitted() is False


@pytest.mark.parametrize("changed", ["auth_key", "dc"])
async def test_same_connected_account_client_material_change_withdraws_admission(runtime_system, changed):
    value = runtime_system
    context = value.compose()
    await context.prepare()
    assert context.management_admitted() is True
    session = value.client.session
    if changed == "auth_key":
        session.auth_key = AuthKey(b"r" * 256)
    else:
        session.set_dc(session.dc_id + 1, session.server_address, session.port)
    assert value.runtime.user.client is value.client and value.client.connected
    assert context.management_admitted() is False
    assert context.bot_verification() is None and context.pairing_verification() is None
    assert not context.account_observation._closed
    assert not value.guard.lock.closed and not value.transports[0].closed


async def test_prepare_requires_current_real_durable_pair(runtime_system):
    with runtime_system.engine.begin() as connection:
        connection.execute(update(TelegramAccount).values(is_owner_paired=False))
    context = runtime_system.compose()
    with pytest.raises(ValueError, match="runtime_bot_unavailable"):
        await context.prepare()
    assert context.management_admitted() is False
    assert not runtime_system.transports


async def test_management_dispatch_uses_one_owned_sdk_and_acknowledges_after_feed(runtime_system):
    context = runtime_system.compose()
    await context.prepare()
    transport = runtime_system.transports[0]
    transport.updates = [Update.model_validate({"update_id": 20})]
    entered = asyncio.Event()
    release = asyncio.Event()
    acknowledged = asyncio.Event()
    seen = []
    original_ack = context.service.acknowledge_update

    def acknowledge(update_id):
        original_ack(update_id)
        acknowledged.set()

    context.service.acknowledge_update = acknowledge

    class Dispatcher:
        async def feed_update(self, bot, update):
            seen.append((bot, update.update_id))
            entered.set()
            await release.wait()

    polling = asyncio.create_task(context.run_updates(Dispatcher(), context.bot))
    try:
        await wait_for_dispatch(polling, entered)
        assert seen == [(transport.bot, 20)]
        assert context.service._cursor is None
        release.set()
        await asyncio.wait_for(acknowledged.wait(), 5)
    finally:
        polling.cancel()
        await asyncio.gather(polling, return_exceptions=True)
    assert context.service._cursor == 21
    assert len(runtime_system.transports) == 1


async def test_legitimate_handler_outlives_five_seconds_with_actual_health_poll(runtime_system):
    context = runtime_system.compose()
    await context.prepare()
    context._health_interval = 0.1
    transport = runtime_system.transports[0]
    transport.updates = [Update.model_validate({"update_id": 30})]
    entered, release, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Dispatcher:
        async def feed_update(self, bot, update):
            assert bot is transport.bot and update.update_id == 30
            entered.set()
            try:
                await release.wait()
            finally:
                exited.set()

    polling = asyncio.create_task(context.run_updates(Dispatcher(), context.bot))
    try:
        await wait_for_dispatch(polling, entered)
        prior_polls = sum(type(call) is tuple for call in transport.calls)
        await asyncio.sleep(5.1)
        assert not exited.is_set() and not polling.done()
        assert sum(type(call) is tuple for call in transport.calls) > prior_polls
        assert context.management_admitted() is True and context.service._cursor is None
        release.set()
        await asyncio.sleep(0)
    finally:
        polling.cancel()
        await asyncio.gather(polling, return_exceptions=True)


async def test_held_dispatch_crosses_original_poll_expiry_after_actual_health_measurement(runtime_system):
    value = runtime_system
    context = value.compose()
    clock = SimpleNamespace(utc=datetime.now(UTC), mono=0.0)
    context.service._now = lambda: clock.utc
    context.service._monotonic = lambda: clock.mono
    context.account_observation._now = lambda: clock.utc
    # The factory is called during prepare; capture the same controlled clock
    # before either actual service measurement is recorded.
    original_factory = context.service._client_factory

    def factory(token):
        transport = original_factory(token)
        transport.now = lambda: clock.utc
        return transport

    context.service._client_factory = factory
    await context.prepare()
    context._health_interval = 0.1
    transport = value.transports[0]
    transport.updates = [Update.model_validate({"update_id": 40})]
    entered, release, measured = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_refresh = context.service.refresh_pending_poll
    initial_calls = value.client.get_me_calls

    async def measure():
        result = await original_refresh()
        if clock.mono >= 29:
            measured.set()
        return result

    context.service.refresh_pending_poll = measure

    class Dispatcher:
        async def feed_update(self, bot, update):
            entered.set()
            await release.wait()

    polling = asyncio.create_task(context.run_updates(Dispatcher(), context.bot))
    try:
        await wait_for_dispatch(polling, entered)
        clock.utc += timedelta(seconds=29)
        clock.mono = 29
        await asyncio.wait_for(measured.wait(), 5)
        clock.utc += timedelta(seconds=2)
        clock.mono = 31
        assert value.client.get_me_calls > initial_calls
        assert context.service._identity_mono == context.service._poll_mono == 29
        assert context.management_admitted() is True
        assert context.service._cursor is None and not polling.done()
        assert len(value.transports) == 1
        assert all(call[1] is None for call in transport.calls if type(call) is tuple)
        release.set()
        await asyncio.sleep(0)
    finally:
        polling.cancel()
        await asyncio.gather(polling, return_exceptions=True)


async def test_close_retains_observer_and_guard_until_cancellation_resistant_dispatch_finishes(runtime_system):
    value = runtime_system
    context = value.compose()
    await context.prepare()
    transport = value.transports[0]
    transport.updates = [Update.model_validate({"update_id": 50})]
    entered, cancelled, release, completed = (asyncio.Event() for _ in range(4))

    class Dispatcher:
        async def feed_update(self, bot, update):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            completed.set()

    polling = asyncio.create_task(context.run_updates(Dispatcher(), context.bot))
    shutdown = None
    try:
        await wait_for_dispatch(polling, entered)
        shutdown = asyncio.create_task(context.close())
        await asyncio.wait_for(cancelled.wait(), 5)
        assert not shutdown.done() and not completed.is_set()
        assert context.account_observation._closed is False and transport.closed is False
        assert context.management_admitted() is False
        assert value.client.connected and not value.guard.lock.closed
        with pytest.raises(ValueError, match="bot_shutdown_pending"):
            context.setup_observation.close()
        release.set()
        await shutdown
        assert completed.is_set() and transport.closed
        assert context.account_observation._closed and value.client.connected
        assert not value.guard.lock.closed
    finally:
        release.set()
        if shutdown is not None:
            await asyncio.gather(shutdown, return_exceptions=True)
        polling.cancel()
        await asyncio.gather(polling, return_exceptions=True)


async def test_actual_account_loss_during_dispatch_withdraws_and_cancels_without_ack(runtime_system):
    value = runtime_system
    context = value.compose()
    await context.prepare()
    context._health_interval = 0.1
    transport = value.transports[0]
    transport.updates = [Update.model_validate({"update_id": 60})]
    entered, cancelled = asyncio.Event(), asyncio.Event()

    class Dispatcher:
        async def feed_update(self, bot, update):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    polling = asyncio.create_task(context.run_updates(Dispatcher(), context.bot))
    try:
        await wait_for_dispatch(polling, entered)
        value.client.authorized = False
        with pytest.raises(ValueError, match="^runtime_bot_unavailable$") as caught:
            await asyncio.wait_for(asyncio.shield(polling), 5)
        assert caught.value.__context__ is None
        assert cancelled.is_set() and context.service._cursor is None
        assert context.management_admitted() is False
        assert context._health_task is None and context._dispatch_task is None
        assert context.account_observation._closed is False and transport.closed is False
        assert not value.guard.lock.closed
    finally:
        polling.cancel()
        await asyncio.gather(polling, return_exceptions=True)
