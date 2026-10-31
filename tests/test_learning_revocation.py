import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select

from tg_assistant.db.base import Base, Database
from tg_assistant.db.models import (
    BackgroundJob,
    KnowledgeSource,
    PendingAction,
    TelegramChat,
    TelegramChatPermission,
    TelegramChatPolicy,
)
from tg_assistant.policy import PolicyEngine
from tg_assistant.runtime import Application, EmbeddingIndexResult
from tg_assistant.services.actions import PendingActionService


@pytest_asyncio.fixture
async def learning_flow():
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    app = object.__new__(Application)
    app.policy = PolicyEngine()
    app._knowledge_lock = asyncio.Lock()
    app.embedding_ai = SimpleNamespace(available=True)
    app.rag = SimpleNamespace(vectors=SimpleNamespace(count=lambda **_kwargs: 0))
    app.settings = SimpleNamespace(
        ai_provider="ollama",
        resolved_semantic_vector_path=Path("offline-vectors"),
        ollama_embedding_model="offline-model",
        embedding_version="test",
    )
    app.bot = None
    syncs, embeddings = [], []
    block_after_sync = False

    @asynccontextmanager
    async def runtime_session():
        nonlocal block_after_sync
        async with database.session() as session:
            yield session
        if block_after_sync and syncs:
            block_after_sync = False
            async with database.session() as session:
                await app.policy.set_allowed(session, -1001, False)

    async def sync_history(_session, **kwargs):
        syncs.append(kwargs["chat_id"])
        return 0

    async def index_rows(_session, rows, **_kwargs):
        embeddings.append(list(rows))
        return EmbeddingIndexResult()

    app.database = SimpleNamespace(session=runtime_session)
    app.user = SimpleNamespace(owner_id=42, sync_history=sync_history)
    app._index_knowledge_rows = index_rows
    async with database.session() as session:
        session.add(TelegramChat(chat_id=-1001, title="Offline source", chat_type="supergroup"))
        await app.policy.apply_template(session, -1001, "knowledge")

    async def confirm_learning(*, older=False):
        async with database.session() as session:
            actions = PendingActionService()
            action = await actions.create(
                session,
                action_type="enable_group_learning",
                requested_by=42,
                chat_id=-1001,
                payload={"limit": 1000},
                preview="Offline source",
            )
            await actions.confirm(session, action.action_id, 42)
            if older:
                action.confirmed_at = datetime.now(UTC) - timedelta(seconds=10)
            action_id = action.action_id
        cycle = iter((False, True))
        app.stopping = SimpleNamespace(is_set=lambda: next(cycle, True))
        await app._execute_actions()
        async with database.session() as session:
            action = await session.get(PendingAction, action_id)
            assert action.status == "executed", action.error
            return action.payload["job_id"]

    def block_between_phases():
        nonlocal block_after_sync
        block_after_sync = True

    yield SimpleNamespace(
        app=app,
        database=database,
        syncs=syncs,
        embeddings=embeddings,
        confirm_learning=confirm_learning,
        block_between_phases=block_between_phases,
    )
    await database.close()


async def test_first_block_records_revocation_and_allow_keeps_history(learning_flow):
    async with learning_flow.database.session() as session:
        policy = await learning_flow.app.policy.set_allowed(session, -2002, False)
        assert policy.revoked_at is not None
        revoked_at = policy.revoked_at
        await learning_flow.app.policy.set_allowed(session, -2002, True)
        assert policy.revoked_at.replace(tzinfo=UTC) == revoked_at


async def test_confirmed_queue_then_block_never_regrants_or_syncs(learning_flow):
    job_id = await learning_flow.confirm_learning(older=True)
    async with learning_flow.database.session() as session:
        await learning_flow.app.policy.set_allowed(session, -1001, False)

    await learning_flow.app._process_learning_jobs()

    assert learning_flow.syncs == []
    assert learning_flow.embeddings == []
    async with learning_flow.database.session() as session:
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == -1001)
        )
        assert not policy.allowed
        assert (await session.get(BackgroundJob, job_id)).status == "cancelled"
        assert not (await session.get(KnowledgeSource, -1001)).requested_for_learning


async def test_stale_resume_after_block_and_allow_does_not_restore_permissions(learning_flow):
    job_id = await learning_flow.confirm_learning(older=True)
    async with learning_flow.database.session() as session:
        await learning_flow.app.policy.set_allowed(session, -1001, False)
        await learning_flow.app.policy.set_allowed(session, -1001, True)
        # Simulate a stale resume/retry racing with revocation.
        (await session.get(BackgroundJob, job_id)).status = "queued"

    await learning_flow.app._process_learning_jobs()

    assert learning_flow.syncs == []
    async with learning_flow.database.session() as session:
        permissions = list((await session.scalars(select(TelegramChatPermission))).all())
        assert all(not permission.enabled for permission in permissions)
        assert (await session.get(BackgroundJob, job_id)).status == "cancelled"


async def test_new_confirmation_after_block_can_create_and_run_fresh_job(learning_flow):
    old_job_id = await learning_flow.confirm_learning(older=True)
    async with learning_flow.database.session() as session:
        policy = await learning_flow.app.policy.set_allowed(session, -1001, False)
        policy.revoked_at = datetime.now(UTC) - timedelta(seconds=2)

    new_job_id = await learning_flow.confirm_learning()
    assert new_job_id != old_job_id
    await learning_flow.app._process_learning_jobs()

    assert learning_flow.syncs == [-1001]
    async with learning_flow.database.session() as session:
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == -1001)
        )
        assert policy.allowed
        assert (await session.get(BackgroundJob, new_job_id)).status == "completed"
        assert (await session.get(BackgroundJob, old_job_id)).status == "cancelled"


async def test_block_between_sync_and_embedding_stops_derived_work(learning_flow):
    job_id = await learning_flow.confirm_learning(older=True)
    learning_flow.block_between_phases()
    await learning_flow.app._process_learning_jobs()

    assert learning_flow.syncs == [-1001]
    assert learning_flow.embeddings == []
    async with learning_flow.database.session() as session:
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == -1001)
        )
        assert not policy.allowed
        assert (await session.get(BackgroundJob, job_id)).status == "cancelled"


@pytest.mark.parametrize("confirmation_offset", [-10, 0.5])
async def test_delayed_queue_uses_confirmation_time_and_denies_same_second(
    learning_flow, confirmation_offset
):
    async with learning_flow.database.session() as session:
        actions = PendingActionService()
        action = await actions.create(
            session,
            action_type="enable_group_learning",
            requested_by=42,
            chat_id=-1001,
            payload={"limit": 1000},
            preview="Offline source",
        )
        await actions.confirm(session, action.action_id, 42)
        at = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=2)
        action.confirmed_at = at + timedelta(seconds=confirmation_offset)
        policy = await learning_flow.app.policy.set_allowed(session, -1001, False)
        policy.revoked_at = at + timedelta(seconds=0.1)
        job = BackgroundJob(
            job_type="learn_group",
            status="queued",
            payload={"action_id": action.action_id, "chat_id": -1001, "owner_id": 42},
        )
        session.add(job)
        await session.flush()
        job_id = job.id

    await learning_flow.app._process_learning_jobs()

    assert learning_flow.syncs == []
    async with learning_flow.database.session() as session:
        assert (await session.get(BackgroundJob, job_id)).status == "cancelled"


async def test_cancellation_honors_pause_requested_during_sync(learning_flow):
    job_id = await learning_flow.confirm_learning()

    async def interrupted_sync(_session, **_kwargs):
        async with learning_flow.database.session() as session:
            job = await session.get(BackgroundJob, job_id)
            job.status = "pause_requested"
            job.paused_at = datetime.now(UTC)
        raise asyncio.CancelledError

    learning_flow.app.user.sync_history = interrupted_sync
    with pytest.raises(asyncio.CancelledError):
        await learning_flow.app._process_learning_jobs()
    async with learning_flow.database.session() as session:
        job = await session.get(BackgroundJob, job_id)
        source = await session.get(KnowledgeSource, -1001)
        assert job.status == source.status == "paused"
        assert job.locked_by is None and job.locked_at is None
    assert learning_flow.embeddings == []
