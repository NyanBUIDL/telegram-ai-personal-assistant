"""Actual runtime/provider/ledger/vector boundaries with synthetic transports."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import select

from tg_assistant.ai.budget import BudgetService
from tg_assistant.ai.engine import AiUncertainError
from tg_assistant.ai.vector import LocalVectorStore
from tg_assistant.config import Settings, save_settings
from tg_assistant.db.base import Base, Database
from tg_assistant.db.models import (
    AppSetting,
    TelegramChat,
    TelegramChatPolicy,
    TelegramMessage,
    VectorStore,
)
from tg_assistant.policy import PolicyEngine
from tg_assistant.runtime import Application, make_local_embedding_engine
from tg_assistant.services.revocation import AuthorizationRevoked


class FixtureStore:
    def __init__(self):
        self.requested = []

    def get(self, key):
        self.requested.append(key)
        return "synthetic-sdk-key"


@pytest_asyncio.fixture(params=["sqlite"])
async def incremental_case(tmp_path, monkeypatch, request):
    selected = Settings(
        _env_file=None,
        data_dir=tmp_path / "profile",
        ai_provider="openai",
        embedding_provider="openai",
        cloud_consent=True,
        cloud_embedding_dimension=3,
    )
    save_settings(selected)
    monkeypatch.setenv("TG_ASSISTANT_DATA_DIR", str(selected.data_dir))
    dburl = f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"
    db = Database(dburl)
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    policy = PolicyEngine()
    async with db.session() as session:
        for chat_id, mode in ((10, "local_only"), (20, "cloud_only")):
            session.add(TelegramChat(chat_id=chat_id, chat_type="group"))
            await session.flush()
            await policy.apply_template(session, chat_id, "knowledge")
            await policy.set_ai_route(session, chat_id, mode=mode)
        for message_id, chat_id, text in (
            (1, 10, "Local-only synthetic canary never exported"),
            (2, 20, "Meaningful release update for a synthetic project"),
            (3, 20, "Distinct useful project plan for the next release"),
        ):
            session.add(
                TelegramMessage(
                    chat_id=chat_id, message_id=message_id, text=text, sent_at=datetime.now(UTC)
                )
            )
    app = Application.__new__(Application)
    app.settings, app.database, app.policy = selected, db, policy
    app.budget = BudgetService(1, 10)
    app.embedding_ai = make_local_embedding_engine(selected, FixtureStore(), app.budget)
    app.rag = SimpleNamespace(
        vectors=LocalVectorStore(
            selected.resolved_semantic_vector_path,
            vector_size=3,
            profile=selected.embedding_profile,
        )
    )
    app._knowledge_lock = asyncio.Lock()
    calls = []
    handler = None

    def transport(request):
        calls.append(json.loads(request.content))
        if handler:
            return handler(request)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "text-embedding-3-small",
                "data": [
                    {"object": "embedding", "index": i, "embedding": [0.1, 0.2, 0.3]}
                    for i, _ in enumerate(calls[-1]["input"])
                ],
                "usage": {"prompt_tokens": 10, "total_tokens": 10},
            },
        )

    await app.embedding_ai.client._client.aclose()
    app.embedding_ai.client._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))

    def set_handler(value):
        nonlocal handler
        handler = value

    try:
        yield app, calls, set_handler
    finally:
        app.rag.vectors.close()
        await app.embedding_ai.close()
        await db.close()


async def rows(session, message_ids):
    return list(
        (
            await session.scalars(
                select(TelegramMessage)
                .where(TelegramMessage.message_id.in_(message_ids))
                .order_by(TelegramMessage.id)
            )
        ).all()
    )


async def test_reuse_requires_point_exists(incremental_case):
    app, calls, _ = incremental_case
    async with app.database.session() as session:
        selected = await rows(session, [2])
        assert (await app._index_knowledge_rows(session, selected)).indexed == 1
        app.rag.vectors.delete_reference_ids([selected[0].id])
        result = await app._index_knowledge_rows(session, selected)
        assert result.indexed == 1 and result.reused == 0
    assert len(calls) == 2  # missing point requires a new explicitly budgeted repair intent
    assert len(app.rag.vectors.reference_ids(chat_id=20)) == 1


async def test_coverage_matches_dedupe(incremental_case):
    from tg_assistant.services.vector_reliability import inspect_source_coverage

    app, _, _ = incremental_case
    async with app.database.session() as session:
        original = (await rows(session, [2]))[0]
        session.add(
            TelegramMessage(chat_id=20, message_id=4, text=original.text, sent_at=datetime.now(UTC))
        )
    async with app.database.session() as session:
        result = await app._index_knowledge_rows(session, await rows(session, [2, 4]))
        coverage = await inspect_source_coverage(
            session, chat_id=20, settings=app.settings, vectors=app.rag.vectors
        )
        # message 3 remains eligible and missing; duplicate 4 is accounted by 2.
        assert result.indexed == 1 and result.duplicate == 1
        assert coverage["expected_eligible_messages"] == 2
        assert coverage["missing_count"] == 1
        assert result.processed == sum(
            (
                result.indexed,
                result.reused,
                result.filtered,
                result.duplicate,
                result.skipped,
                result.failed,
            )
        )


async def test_edit_before_checkpoint_reindexed(incremental_case):
    from tg_assistant.runtime import knowledge_checkpoint_key, knowledge_rows_query
    from tg_assistant.telegram.user_client import UserClientAdapter

    app, _, _ = incremental_case
    async with app.database.session() as session:
        selected = await rows(session, [2, 3])
        await app._index_knowledge_rows(session, selected)
        checkpoint = max(row.id for row in selected)
        session.add(
            AppSetting(
                key=knowledge_checkpoint_key(app.settings.embedding_profile.store_id, 20),
                value=checkpoint,
            )
        )
    user = UserClientAdapter.__new__(UserClientAdapter)
    async with app.database.session() as session:
        message = SimpleNamespace(
            chat_id=20,
            id=2,
            message="Revised useful project release timeline",
            edit_date=datetime.now(UTC),
            media=None,
        )
        await user._upsert_message(session, message)
    async with app.database.session() as session:
        selected = list(
            (
                await session.scalars(knowledge_rows_query(20, limit=100, checkpoint_id=checkpoint))
            ).all()
        )
        assert [row.message_id for row in selected] == [2]
        assert (await app._index_knowledge_rows(session, selected)).indexed == 1


async def test_deleted_vector_unsearchable(incremental_case):
    app, _, _ = incremental_case
    async with app.database.session() as session:
        selected = await rows(session, [2])
        await app._index_knowledge_rows(session, selected)
        selected[0].is_deleted = True
    async with app.database.session() as session:
        result = await app._index_knowledge_rows(session, await rows(session, [2]))
        assert result.skipped == 1
    assert app.rag.vectors.search([0.1, 0.2, 0.3], allowed_chat_ids=[20]) == []


async def test_partial_batch_keeps_previous_vectors(incremental_case):
    app, _, set_handler = incremental_case
    async with app.database.session() as session:
        selected = await rows(session, [2])
        await app._index_knowledge_rows(session, selected)
        prior = app.rag.vectors.reference_ids(chat_id=20)
    set_handler(
        lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("synthetic", request=request))
    )
    async with app.database.session() as session:
        with pytest.raises(AiUncertainError):
            await app._index_knowledge_rows(session, await rows(session, [3]))
    assert app.rag.vectors.reference_ids(chat_id=20) == prior


async def test_duplicate_in_source_b_survives_block_source_a(incremental_case):
    app, _, _ = incremental_case
    async with app.database.session() as session:
        original = (await rows(session, [2]))[0]
        session.add(TelegramChat(chat_id=30, chat_type="group"))
        await session.flush()
        await app.policy.apply_template(session, 30, "knowledge")
        await app.policy.set_ai_route(session, 30, mode="cloud_only")
        session.add(
            TelegramMessage(chat_id=30, message_id=4, text=original.text, sent_at=datetime.now(UTC))
        )
    async with app.database.session() as session:
        result = await app._index_knowledge_rows(session, await rows(session, [2, 4]))
        assert result.indexed == 2
        await app.policy.set_allowed(session, 20, False)
        app.rag.vectors.delete_reference_ids(app.rag.vectors.reference_ids(chat_id=20))
    assert len(app.rag.vectors.search([0.1, 0.2, 0.3], allowed_chat_ids=[30])) == 1


async def test_edit_during_admission_invalidates_captured_hash(incremental_case):
    from tg_assistant.telegram.user_client import UserClientAdapter

    app, calls, _ = incremental_case
    admit = app.embedding_ai._admit_request

    async def edit(**kwargs):
        user = UserClientAdapter.__new__(UserClientAdapter)
        async with app.database.session() as current:
            await user._upsert_message(
                current,
                SimpleNamespace(
                    chat_id=20,
                    id=2,
                    message="Changed meaningful message while embedding admission waits",
                    edit_date=datetime.now(UTC),
                    media=None,
                ),
            )
        await admit(**kwargs)

    app.embedding_ai._admit_request = edit
    async with app.database.session() as session:
        with pytest.raises(AuthorizationRevoked):
            await app._index_knowledge_rows(session, await rows(session, [2]))
    assert calls == []


async def test_restore_does_not_blind_retry_uncertain_reservation(incremental_case):
    from tg_assistant.services.vector_reliability import invalidate_restored_index

    app, calls, set_handler = incremental_case
    set_handler(
        lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("synthetic", request=request))
    )
    async with app.database.session() as session:
        with pytest.raises(AiUncertainError):
            await app._index_knowledge_rows(session, await rows(session, [2]))
    async with app.database.engine.begin() as connection:
        await connection.run_sync(invalidate_restored_index)
    set_handler(None)
    # Explicit store reconciliation can reactivate a validated restored store,
    # but cannot authorize replay of an uncertain external provider request.
    async with app.database.session() as session:
        store = await session.get(VectorStore, app.settings.embedding_profile.store_id)
        store.state = "active"
    async with app.database.session() as session:
        with pytest.raises(AiUncertainError):
            await app._index_knowledge_rows(session, await rows(session, [2]))
    assert len(calls) == 1


async def test_filtered_canonical_does_not_hide_eligible_duplicate(incremental_case):
    app, _, _ = incremental_case
    from tg_assistant.services.vector_reliability import inspect_source_coverage

    async with app.database.session() as session:
        original = (await rows(session, [2]))[0]
        original.sent_at = datetime.now(UTC) - timedelta(days=30)
        session.add(
            TelegramMessage(chat_id=20, message_id=4, text=original.text, sent_at=datetime.now(UTC))
        )
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == 20)
        )
        policy.retention_days = 7
    async with app.database.session() as session:
        result = await app._index_knowledge_rows(session, await rows(session, [2, 3, 4]))
        coverage = await inspect_source_coverage(
            session, chat_id=20, settings=app.settings, vectors=app.rag.vectors
        )
        assert result.indexed == 2 and result.skipped == 1
        assert coverage["coverage_state"] == "healthy"
        assert coverage["expected_eligible_messages"] == 2


async def test_corrupt_point_hash_prevents_reuse(incremental_case):
    app, calls, _ = incremental_case
    async with app.database.session() as session:
        selected = await rows(session, [2])
        await app._index_knowledge_rows(session, selected)
        point_id = app.rag.vectors.point_id(selected[0].id, chat_id=20, message_id=2)
        app.rag.vectors.client.set_payload(
            app.rag.vectors.COLLECTION, {"content_hash": "wrong"}, points=[point_id]
        )
        result = await app._index_knowledge_rows(session, selected)
        assert result.indexed == 1 and result.reused == 0
    assert len(calls) == 2


async def test_scoped_point_identity_survives_sql_pk_replacement(incremental_case):
    app, _, _ = incremental_case
    vectors = app.rag.vectors
    first = vectors.point_id(1, chat_id=20, message_id=2)
    assert first == vectors.point_id(999, chat_id=20, message_id=2)
    assert first != vectors.point_id(1, chat_id=30, message_id=2)
    vectors.upsert_many([(1, [0.1, 0.2, 0.3], 20, 2)], content_hashes={1: "digest"})
    vectors.upsert_many([(999, [0.1, 0.2, 0.3], 20, 2)], content_hashes={999: "digest"})
    assert vectors.reference_ids(chat_id=20) == [999]
    assert (
        vectors.search([0.1, 0.2, 0.3], allowed_chat_ids=[20], allowed_reference_ids=[999])[0][0]
        == 999
    )


@pytest.fixture(params=["sqlite"])
def incremental_connection(tmp_path, request):
    from pathlib import Path

    from alembic.config import Config

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'incremental.db'}")
    try:
        with engine.connect() as connection:
            root = Path(__file__).resolve().parents[2]
            config = Config(str(root / "alembic.ini"))
            config.set_main_option("script_location", str(root / "alembic"))
            config.attributes["connection"] = connection
            yield connection, config
    finally:
        engine.dispose()


def test_migration0009_and_restore_metadata_are_real_and_repeatable(incremental_connection):
    from alembic import command
    from tg_assistant.db.migrations import fingerprint
    from tg_assistant.services.vector_reliability import invalidate_restored_index

    connection, config = incremental_connection
    command.upgrade(config, "0008")
    # Existing source content survives the append-only migration.
    connection.execute(sa.text("INSERT INTO telegram_chats(chat_id,chat_type) VALUES (20,'group')"))
    connection.execute(
        sa.text(
            "INSERT INTO telegram_messages(chat_id,message_id,text,sent_at,is_outgoing,is_deleted,has_media,vector_status) VALUES (20,2,'Meaningful synthetic release update',CURRENT_TIMESTAMP,0,0,0,'indexed')"
        )
    )
    connection.commit()
    command.upgrade(config, "head")
    columns = {col["name"]: col for col in sa.inspect(connection).get_columns("telegram_messages")}
    assert columns["vector_dirty"]["nullable"] is False
    assert connection.execute(sa.text("SELECT vector_dirty FROM telegram_messages")).scalar() == 0
    before = fingerprint(connection)
    command.upgrade(config, "head")
    assert fingerprint(connection) == before
    assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar() == "0009"
    connection.execute(
        sa.insert(AppSetting).values(key="knowledge_checkpoint:embedding-test:20", value=2)
    )
    connection.commit()
    assert invalidate_restored_index(connection) == 1
    connection.commit()
    observed = connection.execute(
        sa.select(TelegramMessage.text, TelegramMessage.vector_status, TelegramMessage.vector_dirty)
    ).one()
    assert observed == ("Meaningful synthetic release update", "pending", True)
    assert (
        connection.execute(
            sa.select(AppSetting.key).where(AppSetting.key.like("knowledge_checkpoint:%"))
        ).first()
        is None
    )


async def test_dirty_repair_at_quota_does_not_admit_new_points(incremental_case):
    from tg_assistant.runtime import knowledge_rows_query
    from tg_assistant.telegram.user_client import UserClientAdapter

    app, _, _ = incremental_case
    async with app.database.session() as session:
        await app._index_knowledge_rows(session, await rows(session, [2]))
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == 20)
        )
        policy.max_vectors = 1
        user = UserClientAdapter.__new__(UserClientAdapter)
        await user._upsert_message(
            session,
            SimpleNamespace(
                chat_id=20,
                id=2,
                message="Edited meaningful release data under a full vector quota",
                edit_date=datetime.now(UTC),
                media=None,
            ),
        )
    async with app.database.session() as session:
        dirty = list(
            (await session.scalars(knowledge_rows_query(20, limit=0, checkpoint_id=999))).all()
        )
        assert [row.message_id for row in dirty] == [2]
        result = await app._index_knowledge_rows(session, await rows(session, [2, 3]))
        assert result.indexed == 1 and result.skipped == 1
    assert app.rag.vectors.count(chat_id=20) == 1


async def test_oversized_content_uses_identical_coverage_eligibility(incremental_case):
    from tg_assistant.services.vector_reliability import inspect_source_coverage

    app, calls, _ = incremental_case
    app.settings = app.settings.model_copy(update={"max_input_tokens_per_request": 20})
    async with app.database.session() as session:
        result = await app._index_knowledge_rows(session, await rows(session, [2, 3]))
        coverage = await inspect_source_coverage(
            session, chat_id=20, settings=app.settings, vectors=app.rag.vectors
        )
        assert result.skipped == 2
        assert coverage["expected_eligible_messages"] == 0
        assert coverage["coverage_state"] == "healthy"
    assert calls == []


@pytest.mark.parametrize("cap_name", ["max_vectors", "max_messages"])
async def test_quota_excluded_rows_are_accounted_without_missing_coverage(
    incremental_case, cap_name
):
    from tg_assistant.services.vector_reliability import inspect_source_coverage

    app, _, _ = incremental_case
    async with app.database.session() as session:
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == 20)
        )
        setattr(policy, cap_name, 1)
    async with app.database.session() as session:
        result = await app._index_knowledge_rows(session, await rows(session, [2, 3]))
        coverage = await inspect_source_coverage(
            session, chat_id=20, settings=app.settings, vectors=app.rag.vectors
        )
        assert (result.processed, result.indexed, result.skipped) == (2, 1, 1)
        assert coverage["expected_eligible_messages"] == 1
        assert coverage["active_vectors"] == 1
        assert coverage["missing_count"] == 0
        assert coverage["coverage_state"] == "healthy"


async def test_quota_deferred_admissions_cannot_starve_dirty_delete(incremental_case):
    from tg_assistant.runtime import knowledge_rows_query
    from tg_assistant.services.vector_reliability import inspect_source_coverage

    app, _, _ = incremental_case
    async with app.database.session() as session:
        await session.execute(sa.delete(TelegramMessage).where(TelegramMessage.chat_id == 20))
        session.add(
            TelegramMessage(
                id=200,
                chat_id=20,
                message_id=200,
                text="Previously indexed meaningful project data",
                sent_at=datetime.now(UTC),
            )
        )
    async with app.database.session() as session:
        original = (await rows(session, [200]))[0]
        await app._index_knowledge_rows(session, [original])
        original.is_deleted = True
        original.vector_dirty = True
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == 20)
        )
        policy.max_vectors = 1
        for message_id in range(10, 110):
            session.add(
                TelegramMessage(
                    id=message_id,
                    chat_id=20,
                    message_id=message_id,
                    text=f"Distinct meaningful queued project admission number {message_id}",
                    sent_at=datetime.now(UTC),
                    vector_dirty=True,
                )
            )
    selections = []
    for _ in range(2):
        async with app.database.session() as session:
            selected = list(
                (await session.scalars(knowledge_rows_query(20, limit=0, checkpoint_id=999))).all()
            )
            selections.append([row.id for row in selected])
            result = await app._index_knowledge_rows(session, selected)
            assert result.processed == sum(
                (
                    result.indexed,
                    result.reused,
                    result.filtered,
                    result.duplicate,
                    result.skipped,
                    result.failed,
                )
            )
    assert 200 not in app.rag.vectors.reference_ids(chat_id=20)
    assert selections[1] != selections[0]
    assert app.rag.vectors.count(chat_id=20) <= 1
    async with app.database.session() as session:
        assert (await session.get(TelegramMessage, 200)).vector_dirty is False
        coverage = await inspect_source_coverage(
            session, chat_id=20, settings=app.settings, vectors=app.rag.vectors
        )
        assert coverage["coverage_state"] == "healthy"


async def test_bounded_initial_quota_batch_matches_admission_plan(incremental_case):
    from tg_assistant.runtime import knowledge_rows_query
    from tg_assistant.services.vector_reliability import inspect_source_coverage

    app, _, _ = incremental_case
    async with app.database.session() as session:
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == 20)
        )
        policy.max_vectors = 1
        (await rows(session, [3]))[0].sent_at = datetime.now(UTC) + timedelta(seconds=1)
    async with app.database.session() as session:
        selected = list((await session.scalars(knowledge_rows_query(20, limit=1))).all())
        result = await app._index_knowledge_rows(session, selected)
        assert result.indexed == 1
        coverage = await inspect_source_coverage(
            session, chat_id=20, settings=app.settings, vectors=app.rag.vectors
        )
        assert coverage["coverage_state"] == "healthy"


async def test_quota_deferred_row_reconsidered_below_checkpoint_when_capacity_returns(
    incremental_case,
):
    from tg_assistant.runtime import knowledge_rows_query

    app, _, _ = incremental_case
    async with app.database.session() as session:
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == 20)
        )
        policy.max_vectors = 1
    async with app.database.session() as session:
        await app._index_knowledge_rows(session, await rows(session, [2, 3]))
        admitted = (await rows(session, [2]))[0]
        admitted.is_deleted = True
        admitted.vector_dirty = True
        deferred = (await rows(session, [3]))[0]
        assert deferred.embedding_skip_reason == "quota_exceeded" and deferred.vector_dirty is False
    async with app.database.session() as session:
        dirty = list(
            (await session.scalars(knowledge_rows_query(20, limit=0, checkpoint_id=999))).all()
        )
        assert (await app._index_knowledge_rows(session, dirty)).skipped == 1
    async with app.database.session() as session:
        ready = list(
            (await session.scalars(knowledge_rows_query(20, limit=1, checkpoint_id=999))).all()
        )
        assert [row.message_id for row in ready] == [3]
        assert (await app._index_knowledge_rows(session, ready)).indexed == 1
    assert app.rag.vectors.count(chat_id=20) == 1


@pytest.mark.parametrize("caller", ["refresh", "learning"])
async def test_mixed_dirty_clean_quota_batch_keeps_admitted_candidate_reachable(
    incremental_case, caller
):
    from tg_assistant.db.models import BackgroundJob
    from tg_assistant.runtime import knowledge_checkpoint_key
    from tg_assistant.services.vector_reliability import inspect_source_coverage

    app, calls, _ = incremental_case

    async def synthetic_history(session, **kwargs):
        return 0

    app.user = SimpleNamespace(owner_id=1, sync_history=synthetic_history)
    async with app.database.session() as session:
        await app.policy.set_allowed(session, 10, False)
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == 20)
        )
        policy.max_vectors = 1
        (await rows(session, [3]))[0].vector_dirty = True
    for _ in range(3):
        if caller == "learning":
            async with app.database.session() as session:
                epoch = await session.scalar(
                    select(TelegramChatPolicy.authorization_epoch).where(
                        TelegramChatPolicy.chat_id == 20
                    )
                )
                session.add(
                    BackgroundJob(
                        job_type="learn_group",
                        payload={
                            "chat_id": 20,
                            "owner_id": 1,
                            "authorization_epoch": epoch,
                            "limit": 1,
                        },
                    )
                )
            await app._process_learning_jobs()
        else:
            await app._refresh_group_knowledge()
    async with app.database.session() as session:
        original = (await rows(session, [2]))[0]
        coverage = await inspect_source_coverage(
            session, chat_id=20, settings=app.settings, vectors=app.rag.vectors
        )
        assert app.rag.vectors.reference_ids(chat_id=20) == [original.id]
        assert len(calls) == 1
        assert coverage["coverage_state"] == "healthy"
        checkpoint = await session.get(
            AppSetting, knowledge_checkpoint_key(app.settings.embedding_profile.store_id, 20)
        )
        assert int(checkpoint.value) >= original.id
        if caller == "learning":
            jobs = list(
                (
                    await session.scalars(
                        select(BackgroundJob).where(BackgroundJob.job_type == "learn_group")
                    )
                ).all()
            )
            assert all(job.status == "completed" and job.payload["invariant_ok"] for job in jobs)


@pytest.mark.parametrize("caller", ["refresh", "learning"])
async def test_higher_dirty_delete_does_not_advance_checkpoint_over_clean_pending(
    incremental_case, caller
):
    from tg_assistant.db.models import BackgroundJob
    from tg_assistant.runtime import knowledge_checkpoint_key

    app, calls, _ = incremental_case

    async def synthetic_history(session, **kwargs):
        return 0

    app.user = SimpleNamespace(owner_id=1, sync_history=synthetic_history)
    async with app.database.session() as session:
        await app.policy.set_allowed(session, 10, False)
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == 20)
        )
        policy.max_vectors = 1
        newer = (await rows(session, [3]))[0]
        newer.is_deleted = True
        newer.vector_dirty = True
        older = (await rows(session, [2]))[0]
        older_id = older.id
    for cycle in range(2):
        if caller == "learning":
            async with app.database.session() as session:
                epoch = await session.scalar(
                    select(TelegramChatPolicy.authorization_epoch).where(
                        TelegramChatPolicy.chat_id == 20
                    )
                )
                session.add(
                    BackgroundJob(
                        job_type="learn_group",
                        payload={
                            "chat_id": 20,
                            "owner_id": 1,
                            "authorization_epoch": epoch,
                            "limit": 1,
                        },
                    )
                )
            await app._process_learning_jobs()
        else:
            await app._refresh_group_knowledge()
        if cycle == 0:
            async with app.database.session() as session:
                checkpoint = await session.get(
                    AppSetting,
                    knowledge_checkpoint_key(app.settings.embedding_profile.store_id, 20),
                )
                assert checkpoint is not None and int(checkpoint.value) < older_id
    assert app.rag.vectors.reference_ids(chat_id=20) == [older_id]
    assert len(calls) == 1


async def test_pending_admission_remains_reachable_after_legacy_checkpoint_gap(incremental_case):
    from tg_assistant.runtime import knowledge_checkpoint_after, knowledge_rows_query

    app, calls, _ = incremental_case
    async with app.database.session() as session:
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == 20)
        )
        policy.max_vectors = 1
        (await rows(session, [3]))[0].embedding_skip_reason = "quota_exceeded"
        (await rows(session, [3]))[0].vector_status = "skipped"
    async with app.database.session() as session:
        selected = list(
            (await session.scalars(knowledge_rows_query(20, limit=1, checkpoint_id=3))).all()
        )
        assert [row.message_id for row in selected] == [2]
        assert (await app._index_knowledge_rows(session, selected)).indexed == 1
        assert await knowledge_checkpoint_after(session, 20, 3, selected) == 3
        assert (
            list((await session.scalars(knowledge_rows_query(20, limit=0, checkpoint_id=3))).all())
            == []
        )
    assert len(calls) == 1 and app.rag.vectors.count(chat_id=20) == 1


@pytest.mark.parametrize("unresolved_state", ["submitted", "uncertain"])
async def test_legacy_checkpoint_pending_intent_never_forces_provider_retry(
    incremental_case, unresolved_state
):
    from tg_assistant.db.models import AiBudgetReservation
    from tg_assistant.runtime import knowledge_rows_query

    app, calls, set_handler = incremental_case
    set_handler(
        lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("synthetic", request=request))
    )
    async with app.database.session() as session:
        with pytest.raises(AiUncertainError):
            await app._index_knowledge_rows(session, await rows(session, [2]))
    async with app.database.session() as session:
        pending = (await rows(session, [2]))[0]
        pending.vector_dirty = False  # old overadvanced checkpoint lost its dirty queue
        reservation = await session.get(
            AiBudgetReservation, pending.metadata_json["embedding_request_id"]
        )
        reservation.state = unresolved_state
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == 20)
        )
        policy.max_vectors = 1
    set_handler(None)
    async with app.database.session() as session:
        selected = list(
            (await session.scalars(knowledge_rows_query(20, limit=1, checkpoint_id=3))).all()
        )
        assert [row.message_id for row in selected] == [2]
        with pytest.raises(AiUncertainError):
            await app._index_knowledge_rows(session, selected)
    assert len(calls) == 1 and app.rag.vectors.count(chat_id=20) == 0
