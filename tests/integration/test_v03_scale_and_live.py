"""Authorized safe V03 adaptation; isolated SQLite, actual vectors, synthetic transport.

The scalable fixture/helpers derive from Claude's proposal. These controls preserve
the accepted full-corpus and pre-submit fences and cover truthful late-row inventory.
They do not adopt generation cleanup or claim live/large-scale/backend acceptance.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

import tg_assistant.services.vector_recovery as vr
from tg_assistant.ai.budget import BudgetService
from tg_assistant.ai.vector import LocalVectorStore
from tg_assistant.config import Settings, save_settings
from tg_assistant.db.base import Base, Database
from tg_assistant.db.models import (
    AppSetting,
    KnowledgeSource,
    TelegramChat,
    TelegramChatPolicy,
    TelegramMessage,
    VectorSourceCoverage,
)
from tg_assistant.paths import ensure_runtime_dirs
from tg_assistant.policy import PolicyEngine
from tg_assistant.runtime import Application, make_local_embedding_engine
from tg_assistant.services.jobs import claim_runtime_job, worker_lease

OWNER = 7
TARGET, OTHER = 20, 30


class SyntheticSecrets:
    def get(self, key):
        return "synthetic-recovery-test-key"


def text_for(number: int) -> str:
    # Unique, long enough to be embedding-eligible, never deduplicated.
    return f"Synthetic useful knowledge item {number} about release planning topic {number * 7919}"


# --------------------------------------------------------------------------- harness


@pytest_asyncio.fixture
async def make_case(tmp_path_factory, monkeypatch):
    created = []

    async def factory(*, other_messages=3, missing_messages=1, indexed_in_target=1):
        base = tmp_path_factory.mktemp("r" + uuid4().hex[:6])
        settings = Settings(
            _env_file=None,
            data_dir=base / "profile",
            ai_provider="openai",
            embedding_provider="openai",
            cloud_consent=True,
            cloud_embedding_dimension=3,
            max_ai_requests_per_minute=600,
        )
        save_settings(settings)
        ensure_runtime_dirs(settings.data_dir, profile_id=settings.profile_id)
        monkeypatch.setenv("TG_ASSISTANT_DATA_DIR", str(settings.data_dir))
        database = Database(f"sqlite+aiosqlite:///{base / 'recovery.db'}")
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        app = Application.__new__(Application)
        app.settings, app.database, app.policy = settings, database, PolicyEngine()
        app.user = SimpleNamespace(owner_id=OWNER)
        app.bot = None
        app._knowledge_lock = asyncio.Lock()
        app.stopping = asyncio.Event()
        app.budget = BudgetService(1, 10)
        app.embedding_ai = make_local_embedding_engine(settings, SyntheticSecrets(), app.budget)
        app.rag = SimpleNamespace(
            vectors=LocalVectorStore(
                settings.resolved_semantic_vector_path,
                vector_size=3,
                profile=settings.embedding_profile,
            )
        )
        requests = []

        def transport(request):
            body = json.loads(request.content)
            requests.append(body)
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "model": "text-embedding-3-small",
                    "data": [
                        {"object": "embedding", "index": i, "embedding": [0.1, 0.2, 0.3]}
                        for i, _ in enumerate(body["input"])
                    ],
                    "usage": {"prompt_tokens": 10, "total_tokens": 10},
                },
            )

        await app.embedding_ai.client._client.aclose()
        app.embedding_ai.client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        )
        async with database.session() as session:
            for chat_id in (TARGET, OTHER):
                session.add(TelegramChat(chat_id=chat_id, chat_type="group"))
                await session.flush()
                await app.policy.apply_template(session, chat_id, "knowledge")
                await app.policy.set_ai_route(session, chat_id, mode="cloud_only")
            number = 0
            for message_id in range(1, indexed_in_target + missing_messages + 1):
                number += 1
                session.add(
                    TelegramMessage(
                        chat_id=TARGET,
                        message_id=message_id,
                        text=text_for(number),
                        sent_at=datetime.now(UTC),
                    )
                )
            for index in range(other_messages):
                number += 1
                session.add(
                    TelegramMessage(
                        chat_id=OTHER,
                        message_id=1000 + index,
                        text=text_for(number),
                        sent_at=datetime.now(UTC),
                    )
                )
        # Index every row except the first `missing_messages` of the target source.
        async with database.session() as session:
            rows = list(
                (await session.scalars(select(TelegramMessage).order_by(TelegramMessage.id))).all()
            )
        to_index = [
            row
            for row in rows
            if not (row.chat_id == TARGET and row.message_id > indexed_in_target)
        ]
        for start in range(0, len(to_index), 200):
            async with database.session() as session:
                chunk = [
                    await session.get(TelegramMessage, row.id)
                    for row in to_index[start : start + 200]
                ]
                await app._index_knowledge_rows(session, chunk)
        requests.clear()
        created.append(app)
        return SimpleNamespace(app=app, requests=requests)

    yield factory
    for app in created:
        app.rag.vectors.close()
        await app.embedding_ai.close()
        await app.database.close()


def service(app):
    return vr.VectorRecoveryService.for_application(app)


async def preview(app, chat_id=TARGET):
    return await service(app).preview(chat_id, app.settings.embedding_profile.store_id)


async def run_recovery(app, plan):
    recovery = service(app)
    operation = await recovery.enqueue(plan.plan_id, OWNER)
    claimed = await claim_runtime_job(app.database, ("vector_recovery",))
    assert claimed and claimed[0].id == operation
    with worker_lease(claimed[0]):
        return await recovery.run(claimed[0])


async def add_message(app, chat_id, message_id, number):
    async with app.database.session() as session:
        session.add(
            TelegramMessage(
                chat_id=chat_id,
                message_id=message_id,
                text=text_for(number),
                sent_at=datetime.now(UTC),
            )
        )


def vector_ids(app):
    return sorted(app.rag.vectors.reference_ids())


async def test_full_corpus_work_is_bounded_by_jobs_not_missing_rows(make_case, monkeypatch):
    original = vr.corpus_hash
    scans = []
    count = 0

    def measured(vectors):
        nonlocal count
        count += 1
        return original(vectors)

    monkeypatch.setattr(vr, "corpus_hash", measured)
    for missing in (2, 8):
        case = await make_case(other_messages=10, missing_messages=missing)
        plan = await preview(case.app)
        count = 0
        result = await run_recovery(case.app, plan)
        assert result.state.value == "completed"
        scans.append(count)
    assert scans[1] - scans[0] <= 2, scans
    assert scans[1] <= 6, scans


@pytest.mark.parametrize("source", [TARGET, OTHER])
async def test_late_append_remains_pending_with_truthful_coverage(make_case, monkeypatch, source):
    case = await make_case(other_messages=3, missing_messages=1)
    app = case.app
    plan = await preview(app)
    original = app.embedding_ai.embed_many
    inserted = False

    async def arrived(*args, **kwargs):
        nonlocal inserted
        response = await original(*args, **kwargs)
        if not inserted:
            inserted = True
            await add_message(app, source, 5000, 900)
        return response

    monkeypatch.setattr(app.embedding_ai, "embed_many", arrived)
    result = await run_recovery(app, plan)
    assert result.state.value == "completed"
    async with app.database.session() as session:
        row = await session.scalar(
            select(TelegramMessage).where(
                TelegramMessage.chat_id == source,
                TelegramMessage.message_id == 5000,
            )
        )
        assert row.vector_status == "pending"
        assert row.id not in vector_ids(app)
        coverage = await session.get(
            VectorSourceCoverage,
            {
                "store_id": plan.store_id,
                "chat_id": TARGET,
            },
        )
        inventory = await session.get(KnowledgeSource, TARGET)
        total = 3 if source == TARGET else 2
        assert inventory.mysql_message_count == total
        assert coverage.mysql_total == total
        assert coverage.missing_count == (1 if source == TARGET else 0)
        assert coverage.coverage_state == (
            "reconciliation_required" if source == TARGET else "healthy"
        )
        assert inventory.status == ("reconciliation_required" if source == TARGET else "completed")


@pytest.mark.parametrize("mutation", ["edit", "delete"])
async def test_fresh_planned_row_is_checked_at_actual_pre_submit(make_case, monkeypatch, mutation):
    case = await make_case(other_messages=3, missing_messages=1)
    app = case.app
    plan = await preview(app)
    original = app.embedding_ai.embed_many

    async def intercept(*args, **kwargs):
        fence = kwargs["pre_submit"]

        async def mutate_then_fence():
            async with app.database.session() as session:
                row = await session.scalar(
                    select(TelegramMessage).where(
                        TelegramMessage.chat_id == TARGET,
                        TelegramMessage.message_id == 2,
                    )
                )
                if mutation == "edit":
                    row.text = "Changed knowledge item after intent before external submission"
                else:
                    row.is_deleted = True
            await fence()

        kwargs["pre_submit"] = mutate_then_fence
        return await original(*args, **kwargs)

    monkeypatch.setattr(app.embedding_ai, "embed_many", intercept)
    result = await run_recovery(app, plan)
    assert result.state.value in {"failed", "uncertain"}
    assert case.requests == []
    async with app.database.session() as session:
        assert await session.get(AppSetting, vr.pointer_key(plan.store_id)) is None


async def test_streamed_fingerprint_preserves_legacy_plan_digest(make_case):
    from tg_assistant.services.vector_reliability import SourceIndexService

    case = await make_case(other_messages=3, missing_messages=1)
    app = case.app
    plan = await preview(app)
    async with app.database.session() as session:
        record = await session.get(AppSetting, vr.plan_key(plan.plan_id))
        rows = list(
            (
                await session.scalars(
                    select(TelegramMessage).order_by(
                        TelegramMessage.id,
                    )
                )
            ).all()
        )
        policies = list(
            (
                await session.scalars(
                    select(TelegramChatPolicy).order_by(
                        TelegramChatPolicy.chat_id,
                    )
                )
            ).all()
        )
        original = vr.digest(
            [
                [
                    (
                        row.id,
                        row.chat_id,
                        row.message_id,
                        row.text,
                        row.is_deleted,
                        row.vector_status,
                        row.vector_dirty,
                        row.metadata_json,
                    )
                    for row in rows
                ],
                [
                    (policy.chat_id, SourceIndexService.policy_version(policy))
                    for policy in policies
                ],
            ]
        )
        assert record.value["global_hash"] == original
        # Older plans lacking a cutoff retain their exact original interpretation.
        legacy = dict(record.value)
        legacy.pop("max_row_id")
        record.value = legacy
    assert (await run_recovery(app, plan)).state.value == "completed"


async def test_edit_of_next_planned_row_prevents_second_external_request(make_case, monkeypatch):
    case = await make_case(other_messages=3, missing_messages=2)
    app = case.app
    plan = await preview(app)
    original = app.embedding_ai.embed_many

    async def edit_next(*args, **kwargs):
        response = await original(*args, **kwargs)
        async with app.database.session() as session:
            row = await session.scalar(
                select(TelegramMessage).where(
                    TelegramMessage.chat_id == TARGET,
                    TelegramMessage.message_id == 3,
                )
            )
            row.text = "Edited next planned message before the next provider submission"
        return response

    monkeypatch.setattr(app.embedding_ai, "embed_many", edit_next)
    result = await run_recovery(app, plan)
    assert result.state.value in {"failed", "uncertain"}
    assert len(case.requests) == 1


async def test_vector_only_other_source_change_refuses_activation(make_case, monkeypatch):
    from qdrant_client.models import PointStruct

    case = await make_case(other_messages=3, missing_messages=1)
    app = case.app
    plan = await preview(app)
    original = app.embedding_ai.embed_many
    initial = app.rag.vectors

    async def corrupt(*args, **kwargs):
        response = await original(*args, **kwargs)
        selected, _ = initial.client.scroll(initial.COLLECTION, limit=100, with_vectors=True)
        point = next(p for p in selected if p.payload["chat_id"] == OTHER)
        initial.client.upsert(
            initial.COLLECTION,
            [
                PointStruct(
                    id=point.id,
                    payload=point.payload,
                    vector=[0.7, 0.8, 0.9],
                )
            ],
            wait=True,
        )
        return response

    monkeypatch.setattr(app.embedding_ai, "embed_many", corrupt)
    result = await run_recovery(app, plan)
    assert result.state.value in {"failed", "uncertain"}
    assert app.rag.vectors is initial
    async with app.database.session() as session:
        assert await session.get(AppSetting, vr.pointer_key(plan.store_id)) is None


async def test_paged_copy_keeps_all_other_source_points(make_case, monkeypatch):
    case = await make_case(other_messages=270, missing_messages=1)
    app = case.app
    before = set(vector_ids(app))
    old = app.rag.vectors
    scroll = old.client.scroll
    stage = vr.VectorRecoveryService._stage
    inside_stage = False
    page_sizes = []

    def observed(*args, **kwargs):
        result = scroll(*args, **kwargs)
        if inside_stage:
            assert kwargs["limit"] <= 256
            page_sizes.append(len(result[0]))
        return result

    async def entered(self, *args, **kwargs):
        nonlocal inside_stage
        inside_stage = True
        try:
            return await stage(self, *args, **kwargs)
        finally:
            inside_stage = False

    monkeypatch.setattr(old.client, "scroll", observed)
    monkeypatch.setattr(vr.VectorRecoveryService, "_stage", entered)
    assert (await run_recovery(app, await preview(app))).state.value == "completed"
    assert len(page_sizes) >= 2
    assert sum(page_sizes) == len(before)
    assert before <= set(vector_ids(app))
    assert len(app.rag.vectors.reference_ids(chat_id=OTHER)) == 270
