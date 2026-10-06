"""Observe one existing SDK client; no second client/writer or live Telegram."""

from __future__ import annotations

import importlib
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from test_telegram_publication import OWNER, candidate, owned_engine, publish, system  # noqa: F401

from tg_assistant.config import Settings
from tg_assistant.db.base import configure_sqlite
from tg_assistant.desktop.instance import AlreadyRunning, InstanceGuard

# Imported actual migrated, independently owned fixtures.
# ruff: noqa: F811


class ExistingClient:
    def __init__(self):
        self.session = candidate()
        self.authorized = self.connected = True
        self.me = SimpleNamespace(id=OWNER, bot=False, deleted=False)
        self.get_me_calls = 0

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        self.get_me_calls += 1
        return self.me


@pytest.fixture
def observed(system, monkeypatch):
    module = importlib.import_module("tg_assistant.desktop.telegram_context")
    publication_module = importlib.import_module("tg_assistant.services.telegram_publication")
    engine = system.engine
    if engine.dialect.name == "sqlite":
        path = system.root / "db/assistant.sqlite3"
        path.parent.mkdir()
        destination = sqlite3.connect(path)
        with engine.connect() as connection:
            connection.connection.driver_connection.backup(destination)
        destination.close()
        engine = create_engine("sqlite:///" + str(path))
        event.listen(engine, "connect", configure_sqlite)
    url = engine.url
    values = dict(
        _env_file=None,
        data_dir=system.root,
        profile_id="publication-profile",
        storage_backend=engine.dialect.name,
    )
    if engine.dialect.name == "mysql":
        values.update(
            database_host=url.host,
            database_port=url.port,
            database_user=url.username,
            database_name=url.database,
        )
    settings = Settings(**values)
    publisher = publication_module.EncryptedSessionPublication(
        engine=engine,
        profile_root=system.root,
        profile_id=settings.profile_id,
        maintenance=system.fence,
        store=system.store,
        control_directory=system.control,
    )
    publish(publisher)
    native_fingerprint = publisher.current_fingerprint(OWNER)
    publisher.close()
    # A real runtime working copy is present; publisher must remain unavailable.
    (system.root / "sessions/account.session").write_bytes(b"synthetic-owned-runtime-copy")
    guard = InstanceGuard(system.control).acquire()
    monkeypatch.setattr(module, "native_control_directory", lambda: system.control, raising=False)
    client = ExistingClient()
    runtime = SimpleNamespace(
        user=SimpleNamespace(client=client, owner_id=OWNER), store=system.store, settings=settings
    )
    now = [datetime.now(UTC)]
    if hasattr(module, "RuntimeTelegramObservation"):
        observation = module.RuntimeTelegramObservation(
            settings,
            engine=engine,
            fence=system.fence,
            guard=guard,
            store=system.store,
            now=lambda: now[0],
            request_timeout=0.2,
        )
    else:

        async def unavailable(*args):
            return None

        observation = SimpleNamespace(
            refresh=unavailable,
            verification=lambda: None,
            connection_status=lambda: SimpleNamespace(state="unknown", checked_at=None),
            close=lambda: None,
        )
    yield SimpleNamespace(
        bridge=observation,
        runtime=runtime,
        client=client,
        now=now,
        system=system,
        native_fingerprint=native_fingerprint,
        guard=guard,
    )
    observation.close()
    guard.close()
    if engine is not system.engine:
        engine.dispose()


async def test_runtime_uses_existing_client_and_native_compatible_proof(observed):
    assert observed.bridge.verification() is None
    await observed.bridge.refresh(observed.runtime)
    proof = observed.bridge.verification()
    assert proof is not None and proof.owner_id == OWNER
    assert observed.client.get_me_calls == 1
    assert observed.bridge.connection_status().state == "ready"
    assert observed.bridge._fingerprint == observed.native_fingerprint
    observed.bridge.close()
    assert observed.client.connected  # only Application closes this actual client
    assert observed.guard.lock and not observed.guard.lock.closed


@pytest.mark.parametrize(
    "invalid", ["revoked", "bot", "owner", "disconnected", "session", "journal"]
)
async def test_runtime_refuses_invalid_or_changed_actual_evidence(observed, invalid):
    await observed.bridge.refresh(observed.runtime)
    assert observed.bridge.verification() is not None
    if invalid == "revoked":
        observed.client.authorized = False
    elif invalid == "bot":
        observed.client.me.bot = True
    elif invalid == "owner":
        observed.runtime.user.owner_id = OWNER + 1
    elif invalid == "disconnected":
        observed.client.connected = False
    elif invalid == "session":
        observed.client.session = candidate(9)
    else:
        (observed.system.root / "sessions/publication.journal.enc").write_bytes(b"pending")
    await observed.bridge.refresh(observed.runtime)
    assert observed.bridge.verification() is None
    assert observed.bridge.connection_status().state != "ready"


async def test_runtime_proof_age_does_not_renew_on_metadata_reads(observed):
    await observed.bridge.refresh(observed.runtime)
    measured = observed.bridge.connection_status().checked_at
    assert measured == observed.now[0]
    observed.now[0] += timedelta(seconds=30)
    assert observed.bridge.verification() is None
    assert observed.bridge.connection_status().state == "unknown"
    assert observed.bridge.connection_status().checked_at == measured
    with pytest.raises(AlreadyRunning):
        InstanceGuard(observed.system.control).acquire()


async def test_runtime_disconnection_immediately_invalidates_cached_proof(observed):
    await observed.bridge.refresh(observed.runtime)
    assert observed.bridge.verification() is not None
    observed.client.connected = False
    assert observed.bridge.verification() is None
    assert observed.bridge.connection_status().state != "ready"
