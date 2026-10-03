from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from tg_assistant.db.models import (
    BackgroundJob,
    PermissionName,
    TelegramChat,
    TelegramChatPolicy,
    TelegramMessage,
)
from tg_assistant.policy import PolicyEngine, SlidingWindowLimiter
from tg_assistant.runtime import Application, recover_interrupted_learning_jobs
from tg_assistant.services.actions import PendingActionService
from tg_assistant.services.revocation import (
    AuthorizationRevoked,
    RevocationService,
    require_authorization,
    source_epoch,
)


def mysql_fixture_url():
    import os
    import re

    from sqlalchemy.engine import make_url

    value = os.environ.get("TG_TEST_F01_MYSQL_URL")
    if not value:
        return None
    url = make_url(value)
    if (
        url.drivername != "mysql+asyncmy"
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or not re.fullmatch(r"codex_(?:f01_[a-z0-9_]+|ci_revocation)", url.database or "")
    ):
        raise ValueError("F01 MySQL fixture requires an isolated local test schema")
    return value


class Database:
    def __init__(self, session):
        self.value = session

    @asynccontextmanager
    async def session(self):
        yield self.value
        await self.value.commit()


def application(session):
    app = object.__new__(Application)
    app.database = Database(session)
    app.policy = PolicyEngine()
    app._knowledge_lock = asyncio.Lock()
    app.settings = SimpleNamespace(
        ai_provider="ollama",
        ollama_embedding_model="test",
        embedding_version="v1",
        max_input_tokens_per_request=500,
    )
    app.rag = SimpleNamespace(vectors=SimpleNamespace(upsert_many=lambda points: None))
    app.embedding_ai = SimpleNamespace(available=True)
    app.user = SimpleNamespace(owner_id=1)
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("job_type", "effect_started", "expected_status"),
    [
        ("history_link_delete", True, "uncertain"),
        ("history_link_delete", False, "running"),
        ("history_backfill", True, "running"),
    ],
)
async def test_restart_never_redispatches_uncertain_destructive_job(
    tmp_path, job_type, effect_started, expected_status
):
    from tg_assistant.db.base import Base
    from tg_assistant.db.base import Database as RealDatabase

    url = f"sqlite+aiosqlite:///{(tmp_path / 'interrupted.sqlite').as_posix()}"
    original = RealDatabase(url)
    try:
        async with original.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with original.session() as setup:
            job = BackgroundJob(
                job_type=job_type,
                status="running",
                attempts=1,
                locked_by="interrupted-runtime",
                locked_at=datetime.now(UTC) - timedelta(minutes=16),
                payload={"external_effect_started": effect_started},
            )
            setup.add(job)
            await setup.flush()
            job_id = job.id
    finally:
        await original.close()

    # A separate engine models a process restart; only dispatch is substituted
    # so this test cannot issue a real Telegram deletion.
    restarted = RealDatabase(url)
    dispatched = []

    async def record_dispatch(value):
        dispatched.append(value)

    app = application(None)
    app.database = restarted
    app._run_history_link_delete_job = record_dispatch
    app._run_history_backfill_job = record_dispatch
    try:
        await app._process_admin_jobs()
        await app._process_admin_jobs()
        async with restarted.session() as observed:
            persisted = await observed.get(BackgroundJob, job_id)
            assert persisted.status == expected_status
            if expected_status == "uncertain":
                assert dispatched == []
                assert persisted.attempts == 1
                assert persisted.locked_at is None
                assert persisted.locked_by is None
                assert persisted.run_after is None
                assert persisted.payload["requires_reconciliation"] is True
            else:
                assert dispatched == [job_id]
                assert persisted.attempts == 2
    finally:
        await restarted.close()


async def authorize(session):
    session.add(TelegramChat(chat_id=100, title="Source", chat_type="group"))
    await PolicyEngine().apply_template(session, 100, "knowledge")
    await PolicyEngine().set_group_ai_ask(session, 100, enabled=True)
    await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["inherit", "local_only", "local_first", "cloud_only", "cloud_first", "off"]
)
async def test_queued_job_cannot_reallow_blocked_chat(session, mode):
    await authorize(session)
    await PolicyEngine().set_ai_route(session, 100, mode=mode)
    job = BackgroundJob(job_type="learn_group", payload={"chat_id": 100, "owner_id": 1})
    session.add(job)
    await session.commit()
    await PolicyEngine().set_allowed(session, 100, False)
    await session.commit()
    calls = []
    app = application(session)

    async def sync(*args, **kwargs):
        calls.append(kwargs["chat_id"])
        return 0

    app.user.sync_history = sync
    await app._process_learning_jobs()
    policy = await session.scalar(
        select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == 100)
    )
    assert policy.allowed is False
    assert policy.ai_mode == mode
    assert calls == []
    assert job.status == "cancelled"


@pytest.mark.asyncio
async def test_revoke_during_embedding_prevents_upsert(session):
    await authorize(session)
    row = TelegramMessage(
        chat_id=100,
        message_id=1,
        text="A useful project update with enough meaningful words",
        sent_at=datetime.now(UTC),
    )
    session.add(row)
    await session.commit()
    app = application(session)
    points = []
    app.rag.vectors.upsert_many = points.extend

    async def embed(*args, **kwargs):
        await PolicyEngine().set_allowed(session, 100, False)
        await session.commit()
        return [[0.1]]

    app.embedding_ai.embed_many = embed
    try:
        await app._index_knowledge_rows(session, [row])
    except PermissionError:
        pass
    assert points == []
    assert row.vector_status != "indexed"


@pytest.mark.asyncio
async def test_revoke_before_delivery_is_silent(session):
    await authorize(session)
    app = application(session)
    sent = []

    async def send(chat_id, text, **kwargs):
        sent.append(text)

    app.user.client = SimpleNamespace(send_message=send)
    app.user.username = "assistant"
    app.ai = SimpleNamespace(available=True)
    app._group_ai_limiter = SlidingWindowLimiter()

    async def answer(*args, **kwargs):
        await PolicyEngine().set_allowed(session, 100, False)
        await session.commit()
        return "must never be delivered"

    app.rag.answer = answer
    await app._handle_group_ai_ask(100, 2, 3, "project status")
    assert "must never be delivered" not in sent


@pytest.mark.asyncio
async def test_retry_after_restart_keeps_revoked(session):
    await authorize(session)
    job = BackgroundJob(
        job_type="learn_group",
        status="running",
        attempts=1,
        payload={"chat_id": 100, "owner_id": 1},
    )
    session.add(job)
    await session.commit()
    await PolicyEngine().set_allowed(session, 100, False)
    await session.commit()
    session.expunge_all()
    await recover_interrupted_learning_jobs(session)
    await session.commit()
    restarted = await session.get(BackgroundJob, job.id)
    assert restarted.status == "cancelled"


@pytest.mark.asyncio
async def test_stale_grant_preview_cannot_confirm_after_revoke(session):
    await authorize(session)
    actions = PendingActionService()
    grant = await actions.create(
        session,
        action_type="set_chat_allowed",
        requested_by=1,
        chat_id=100,
        payload={"allowed": True},
    )
    await session.commit()
    await PolicyEngine().set_allowed(session, 100, False)
    await session.commit()
    with pytest.raises((PermissionError, ValueError)):
        await actions.confirm(session, grant.action_id, 1)


@pytest.mark.asyncio
async def test_block_increments_epoch(session):
    await authorize(session)
    policy = await session.scalar(
        select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == 100)
    )
    before = getattr(policy, "authorization_epoch", 0)
    await PolicyEngine().set_allowed(session, 100, False)
    await session.commit()
    assert getattr(policy, "authorization_epoch", 0) > before


@pytest.mark.asyncio
async def test_bulk_grant_is_cancelled_when_one_source_is_revoked(session):
    await authorize(session)
    action = await PendingActionService().create(
        session,
        action_type="enable_group_learning_bulk",
        requested_by=1,
        payload={"chat_ids": [100, 200]},
        preview="Explicit bulk consent",
    )
    await session.commit()
    await RevocationService(Database(session), 1).revoke_source(100, 1, "keep")
    assert action.status == "cancelled"
    with pytest.raises((PermissionError, ValueError)):
        await PendingActionService().confirm(session, action.action_id, 1)


@pytest.mark.asyncio
async def test_regrant_does_not_validate_old_request_epoch(session):
    await authorize(session)
    epoch = await require_authorization(session, 100, PermissionName.SEARCH_MESSAGES)
    await RevocationService(Database(session), 1).revoke_source(100, 1, "keep")
    await PolicyEngine().apply_template(session, 100, "knowledge")
    await session.commit()
    with pytest.raises(AuthorizationRevoked):
        await require_authorization(session, 100, PermissionName.SEARCH_MESSAGES, epoch)


@pytest.mark.asyncio
@pytest.mark.parametrize("permission", list(PermissionName))
async def test_permission_revocation_fences_old_epoch(session, permission):
    await authorize(session)
    policy = PolicyEngine()
    await policy.set_permission(session, 100, permission, True)
    await session.commit()
    epoch = await source_epoch(session, 100)
    await policy.set_permission(session, 100, permission, False)
    await session.commit()
    assert await source_epoch(session, 100) > epoch
    with pytest.raises(AuthorizationRevoked):
        await require_authorization(session, 100, permission, epoch)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "mysql"])
async def test_committed_revoke_during_embedding_from_another_transaction(tmp_path, backend):
    from tg_assistant.db.base import Base
    from tg_assistant.db.base import Database as RealDatabase

    url = f"sqlite+aiosqlite:///{(tmp_path / 'race.sqlite').as_posix()}"
    if backend == "mysql":
        url = mysql_fixture_url()
        if not url:
            pytest.skip("TG_TEST_F01_MYSQL_URL separate disposable MySQL schema not configured")
    database = RealDatabase(url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    started = asyncio.Event()
    release = asyncio.Event()
    points = []
    try:
        async with database.session() as session:
            await authorize(session)
            session.add(
                TelegramMessage(
                    chat_id=100,
                    message_id=1,
                    text="Meaningful release progress with complete details for the project",
                    sent_at=datetime.now(UTC),
                )
            )

        async def index():
            async with database.session() as session:
                row = await session.scalar(select(TelegramMessage))
                app = application(session)
                app.database = database
                app.rag.vectors.upsert_many = points.extend

                async def embed(*args, **kwargs):
                    started.set()
                    await release.wait()
                    return [[0.1]]

                app.embedding_ai.embed_many = embed
                with pytest.raises(AuthorizationRevoked):
                    await app._index_knowledge_rows(session, [row])

        task = asyncio.create_task(index())
        await asyncio.wait_for(started.wait(), 5)
        report = await RevocationService(database, 1).revoke_source(100, 1, "keep")
        assert report.authorization_epoch == 1
        release.set()
        await asyncio.wait_for(task, 5)
        assert points == []
        async with database.session() as session:
            assert (await session.scalar(select(TelegramMessage.vector_status))) != "indexed"
        # A fresh database object represents restart and preserves the fence.
        await database.close()
        database = RealDatabase(url)
        async with database.session() as session:
            with pytest.raises(AuthorizationRevoked):
                await require_authorization(session, 100, PermissionName.AUTO_KNOWLEDGE, 0)
    finally:
        release.set()
        await database.close()


@pytest.mark.asyncio
async def test_owner_rag_revoke_during_answer_returns_no_content_or_cache(session):
    from tg_assistant.ai.rag import RagService
    from tg_assistant.db.models import AiQueryCache

    await authorize(session)
    session.add(
        TelegramMessage(
            chat_id=100,
            message_id=1,
            text="project release confidential details milestone",
            sent_at=datetime.now(UTC),
        )
    )
    await session.commit()

    class Ai:
        provider = "ollama"
        model = "test"
        available = True

        async def answer(self, *args, **kwargs):
            await RevocationService(Database(session), 1).revoke_source(100, 1, "keep")
            return "revoked confidential content"

    rag = RagService(PolicyEngine(), Ai(), None)
    with pytest.raises(AuthorizationRevoked):
        await rag.answer(session, "project release", actor_id=1, owner_id=1, chat_ids=[100])
    assert (await session.scalars(select(AiQueryCache))).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke_after_first_chunk", [False, True])
async def test_owner_ask_command_rechecks_before_each_delivery(session, revoke_after_first_chunk):
    from aiogram.filters import CommandObject

    from tg_assistant.services.revocation import AuthorizedAnswer
    from tg_assistant.telegram.control_bot import ControlBot

    await authorize(session)
    epoch = await source_epoch(session, 100)
    control = ControlBot(
        "123456:synthetic-test-token", owner_id=1, database=Database(session), policy=PolicyEngine()
    )
    delivered = []
    response = AuthorizedAnswer("confidential " * 700, {100: epoch})

    async def answer(question):
        if not revoke_after_first_chunk:
            await RevocationService(Database(session), 1).revoke_source(100, 1, "keep")
        return response

    async def send(text, **kwargs):
        if "confidential" in text:
            delivered.append(text)
            if revoke_after_first_chunk:
                await RevocationService(Database(session), 1).revoke_source(100, 1, "keep")

    control._ask_ai = answer
    message = SimpleNamespace(from_user=SimpleNamespace(id=1), answer=send)
    handler = next(
        item.callback for item in control.router.message.handlers if item.callback.__name__ == "ask"
    )
    try:
        await handler(message, CommandObject(command="ask", args="project release"))
        assert len(delivered) == (1 if revoke_after_first_chunk else 0)
    finally:
        await control.close()


@pytest.mark.asyncio
async def test_revoke_inflight_external_job_requires_reconciliation_after_restart(session):
    await authorize(session)
    job = BackgroundJob(
        job_type="history_link_delete",
        status="running",
        payload={
            "chat_id": 100,
            "owner_id": 1,
            "authorization_epoch": 0,
            "external_effect_started": True,
        },
    )
    session.add(job)
    await session.commit()
    await RevocationService(Database(session), 1).revoke_source(100, 1, "keep")
    assert job.status == "uncertain"
    await recover_interrupted_learning_jobs(session)
    await session.commit()
    assert job.status == "uncertain"


@pytest.mark.asyncio
async def test_disable_auto_moderation_during_role_lookup_prevents_delete(session):
    from tg_assistant.telegram.user_client import UserClientAdapter

    await authorize(session)
    policy = PolicyEngine()
    await policy.set_link_spam_auto_moderation(session, 100, enabled=True)
    await session.commit()
    deleted = []

    class Client:
        async def get_permissions(self, chat_id, sender_id):
            await policy.set_permission(session, chat_id, PermissionName.AUTO_MODERATION, False)
            await session.commit()
            return SimpleNamespace(is_admin=False, is_creator=False)

        async def get_entity(self, chat_id):
            return SimpleNamespace(
                creator=False,
                broadcast=False,
                first_name=None,
                admin_rights=SimpleNamespace(delete_messages=True),
                default_banned_rights=None,
            )

        async def delete_messages(self, chat_id, message_ids):
            deleted.extend(message_ids)

    adapter = object.__new__(UserClientAdapter)
    adapter.client = Client()
    adapter.owner_id = 1
    adapter.policy = policy
    adapter.database = Database(session)
    adapter._trusted_admin_cache = {}
    message = SimpleNamespace(
        id=1, sender_id=42, out=False, message="link https://example.com", entities=[]
    )
    await adapter._auto_moderate_new_message(session, chat_id=100, message=message)
    assert deleted == []


@pytest.mark.asyncio
async def test_mysql_repeatable_read_epoch_recheck_observes_committed_revoke():
    from tg_assistant.db.base import Base
    from tg_assistant.db.base import Database as RealDatabase

    url = mysql_fixture_url()
    if not url:
        pytest.skip("TG_TEST_F01_MYSQL_URL separate disposable MySQL schema not configured")
    database = RealDatabase(url)
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        async with database.session() as setup:
            await authorize(setup)
        async with database.session() as stale:
            # A consistent read starts a MySQL REPEATABLE READ snapshot.
            assert await stale.scalar(select(TelegramChatPolicy.authorization_epoch)) == 0
            await RevocationService(database, 1).revoke_source(100, 1, "keep")
            with pytest.raises(AuthorizationRevoked):
                await require_authorization(stale, 100, PermissionName.SEARCH_MESSAGES, 0)
        async with database.session() as grant:
            with pytest.raises(AuthorizationRevoked):
                await PolicyEngine().set_allowed(grant, 100, True, expected_epoch=0)
        async with database.session() as current:
            assert await current.scalar(select(TelegramChatPolicy.allowed)) is False
            assert await current.scalar(select(TelegramChatPolicy.authorization_epoch)) == 1
    finally:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await database.close()


@pytest.mark.asyncio
async def test_mysql_grant_releases_policy_locks_before_completion_notice():
    from tg_assistant.db.base import Base
    from tg_assistant.db.base import Database as RealDatabase

    url = mysql_fixture_url()
    if not url:
        pytest.skip("TG_TEST_F01_MYSQL_URL separate disposable MySQL schema not configured")
    database = RealDatabase(url)
    revoked_during_notice = []
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with database.session() as setup:
            await authorize(setup)
            action = await PendingActionService().create(
                setup,
                action_type="enable_group_learning",
                requested_by=1,
                chat_id=100,
                payload={"limit": 100},
                preview="Explicit owner consent",
            )
            await PendingActionService().confirm(setup, action.action_id, 1)
        app = application(None)
        app.database = database
        app.stopping = asyncio.Event()

        async def notice(*args, **kwargs):
            app.stopping.set()
            await asyncio.wait_for(RevocationService(database, 1).revoke_source(100, 1, "keep"), 2)
            revoked_during_notice.append(True)

        app.bot = SimpleNamespace(bot=SimpleNamespace(send_message=notice))
        await asyncio.wait_for(app._execute_actions(), 8)
        assert revoked_during_notice == [True]
    finally:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["network_error", "cancelled"])
async def test_grant_stays_committed_when_completion_notice_fails(tmp_path, failure):
    from tg_assistant.db.base import Base
    from tg_assistant.db.base import Database as RealDatabase
    from tg_assistant.db.models import PendingAction

    database = RealDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'grant.sqlite').as_posix()}")
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with database.session() as setup:
            await authorize(setup)
            action = await PendingActionService().create(
                setup,
                action_type="enable_group_learning",
                requested_by=1,
                chat_id=100,
                payload={"limit": 100},
                preview="Explicit owner consent",
            )
            await PendingActionService().confirm(setup, action.action_id, 1)
            action_id = action.action_id
        app = application(None)
        app.database = database
        app.stopping = asyncio.Event()

        async def notice(*args, **kwargs):
            app.stopping.set()
            if failure == "cancelled":
                raise asyncio.CancelledError()
            raise RuntimeError("synthetic notification transport failed")

        app.bot = SimpleNamespace(bot=SimpleNamespace(send_message=notice))
        if failure == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await app._execute_actions()
        else:
            await asyncio.wait_for(app._execute_actions(), 5)
        # Inspect durable rows from a different session, not the worker objects.
        async with database.session() as current:
            committed = await current.get(PendingAction, action_id)
            assert committed.status == "executed"
            assert committed.executed_at is not None
            assert await current.scalar(select(TelegramChatPolicy.allowed)) is True
            assert (await current.scalar(select(BackgroundJob.status))) == "queued"
    finally:
        await database.close()


def test_epoch_migration_marks_legacy_running_delete_uncertain():
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, text

    path = Path(__file__).resolve().parents[1] / "alembic/versions/0006_authorization_epoch.py"
    spec = importlib.util.spec_from_file_location("authorization_epoch_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE telegram_chat_policies (chat_id BIGINT PRIMARY KEY, allowed BOOLEAN NOT NULL)"
                )
            )
            connection.execute(text("INSERT INTO telegram_chat_policies VALUES (100, 1)"))
            connection.execute(
                text(
                    "CREATE TABLE background_jobs (id INTEGER PRIMARY KEY, job_type TEXT, status TEXT)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO background_jobs VALUES (1, 'learn_group', 'queued'), (2, 'history_link_delete', 'running'), (3, 'history_link_delete', 'queued')"
                )
            )
            connection.execute(text("CREATE TABLE pending_actions (chat_id BIGINT, status TEXT)"))
            connection.execute(text("INSERT INTO pending_actions VALUES (100, 'confirmed')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            assert (
                connection.scalar(text("SELECT authorization_epoch FROM telegram_chat_policies"))
                == 0
            )
            assert dict(
                connection.execute(text("SELECT id, status FROM background_jobs")).all()
            ) == {1: "cancelled", 2: "uncertain", 3: "cancelled"}
            assert connection.scalar(text("SELECT status FROM pending_actions")) == "cancelled"
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "mysql+asyncmy://127.0.0.1/production",
        "mysql+asyncmy://example.com/codex_f01_epoch",
        "mysql+asyncmy://127.0.0.1/codex_migration_test",
    ],
)
def test_mysql_fixture_rejects_unsafe_schema_before_metadata_writes(monkeypatch, unsafe_url):
    monkeypatch.setenv("TG_TEST_F01_MYSQL_URL", unsafe_url)
    with pytest.raises(ValueError, match="isolated local"):
        mysql_fixture_url()


@pytest.mark.asyncio
async def test_auto_delete_transport_failure_records_uncertain_effect(session):
    from test_sync import FakeModerationClient, fake_message

    from tg_assistant.db.models import AuditLog
    from tg_assistant.telegram.user_client import UserClientAdapter

    await authorize(session)
    policy = PolicyEngine()
    await policy.set_link_spam_auto_moderation(session, 100, enabled=True)
    await session.commit()

    class LostResponseClient(FakeModerationClient):
        async def delete_messages(self, chat_id, message_ids):
            await super().delete_messages(chat_id, message_ids)
            raise RuntimeError("response lost after request started")

    adapter = object.__new__(UserClientAdapter)
    adapter.client = LostResponseClient(is_admin=False)
    adapter.owner_id = 1
    adapter.policy = policy
    adapter.database = Database(session)
    adapter._trusted_admin_cache = {}
    await adapter._auto_moderate_new_message(
        session,
        chat_id=100,
        message=fake_message(1, "https://example.com", datetime.now(UTC)),
    )
    assert (
        await session.scalar(
            select(AuditLog.outcome).where(AuditLog.action == "auto_delete_non_admin_link")
        )
        == "uncertain"
    )
