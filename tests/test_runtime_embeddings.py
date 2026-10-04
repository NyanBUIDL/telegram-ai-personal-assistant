"""Actual runtime/provider/ledger/vector boundaries with synthetic transports."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import select

from tg_assistant.ai.budget import BudgetService
from tg_assistant.ai.engine import AiPolicyError, AiUncertainError
from tg_assistant.ai.rag import RagService
from tg_assistant.ai.router import AiRouter
from tg_assistant.ai.vector import LocalVectorStore
from tg_assistant.config import Settings, save_settings
from tg_assistant.db.base import Base, Database
from tg_assistant.db.models import (
    AiBudgetReservation,
    AiQueryCache,
    AiUsage,
    AppSetting,
    BackgroundJob,
    PermissionName,
    TelegramChat,
    TelegramChatPolicy,
    TelegramMessage,
    VectorStore,
)
from tg_assistant.policy import PolicyEngine
from tg_assistant.runtime import Application, make_ai_engine, make_local_embedding_engine
from tg_assistant.services.jobs import JobRepository, worker_lease
from tg_assistant.services.revocation import AuthorizationRevoked
from tg_assistant.services.vector_reliability import ensure_vector_store_registry


class FixtureStore:
    def __init__(self):
        self.requested = []

    def get(self, key):
        self.requested.append(key)
        return "synthetic-sdk-key"


@pytest.mark.parametrize("provider", ["openai", "openrouter"])
async def test_runtime_embedding_factory_independent_without_ollama(tmp_path, provider):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "profile",
        ai_provider="off",
        embedding_provider=provider,
        cloud_consent=True,
        cloud_embedding_dimension=3,
    )
    store = FixtureStore()
    engine = make_local_embedding_engine(settings, store, BudgetService(1, 10))
    try:
        assert engine.provider == provider
        assert engine.model is None
        assert engine.embedding_dimension == 3
        assert store.requested == [f"{provider}_api_key"]
        assert engine.client.max_retries == 0
    finally:
        await engine.close()


async def test_unconsented_runtime_factory_does_not_read_cloud_key(tmp_path):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "profile",
        ai_provider="openai",
        embedding_provider="openai",
        cloud_consent=False,
    )
    store = FixtureStore()
    chat = make_ai_engine(settings, store, BudgetService(1, 10))
    embedding = make_local_embedding_engine(settings, store, BudgetService(1, 10))
    try:
        assert store.requested == []
        assert not chat.available and not embedding.available
    finally:
        await chat.close()
        await embedding.close()


@pytest_asyncio.fixture(params=["sqlite", "mysql"])
async def runtime_case(tmp_path, monkeypatch, request):
    monkeypatch.setattr("tg_assistant.config.project_root", lambda: tmp_path / "no-installation")
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
    admin = None
    if request.param == "mysql":
        value = os.environ.get("TG_TEST_MYSQL_URL")
        if not value:
            pytest.skip("Disposable MySQL URL required")
        url = sa.engine.make_url(value)
        assert url.host in {"127.0.0.1", "localhost"} and url.port in {3306, 13307}
        assert (url.database or "").startswith("codex_")
        name = "codex_v01_" + uuid4().hex
        admin = sa.create_engine(url.set(database=None))
        with admin.connect() as connection:
            connection.exec_driver_sql(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
        dburl = url.set(database=name, drivername="mysql+asyncmy").render_as_string(
            hide_password=False
        )
    else:
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
        if admin:
            with admin.connect() as connection:
                connection.exec_driver_sql(f"DROP DATABASE `{name}`")
            admin.dispose()


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


async def test_actual_index_cloud_without_ollama_stages_then_reserves(runtime_case):
    app, calls, _ = runtime_case
    async with app.database.session() as session:
        selected = await rows(session, [1, 2])
        selected[-1].metadata_json = {"synthetic_ingestion": True}
        result = await app._index_knowledge_rows(session, selected)
        assert result.indexed == 1
        assert len(calls) == 1
        assert calls[0]["input"] == [selected[-1].normalized_text]
        assert "Local-only" not in json.dumps(calls)
        assert selected[-1].embedding_provider == "openai"
        assert (
            selected[-1].metadata_json["embedding_store_id"]
            == app.settings.embedding_profile.store_id
        )
    assert app.rag.vectors.reference_ids(chat_id=10) == []
    assert len(app.rag.vectors.reference_ids(chat_id=20)) == 1


@pytest.mark.parametrize("sequential", [False, True])
async def test_duplicate_content_remains_indexed_in_each_authorized_source(
    runtime_case, sequential
):
    app, calls, _ = runtime_case
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
        if sequential:
            assert (await app._index_knowledge_rows(session, await rows(session, [2]))).indexed == 1
            assert (await app._index_knowledge_rows(session, await rows(session, [4]))).indexed == 1
        else:
            assert (
                await app._index_knowledge_rows(session, await rows(session, [2, 4]))
            ).indexed == 2
    assert len(app.rag.vectors.reference_ids(chat_id=20)) == 1
    assert len(app.rag.vectors.reference_ids(chat_id=30)) == 1
    # Group caps and reservation identity remain attributed to each source.
    assert len(calls) == 2
    async with app.database.session() as session:
        reservations = list((await session.scalars(select(AiBudgetReservation))).all())
        assert {row.chat_id for row in reservations} == {20, 30}


async def test_duplicate_same_source_still_filters_with_positive_index_control(runtime_case):
    app, calls, _ = runtime_case
    async with app.database.session() as session:
        original = (await rows(session, [2]))[0]
        session.add(
            TelegramMessage(chat_id=20, message_id=4, text=original.text, sent_at=datetime.now(UTC))
        )
    async with app.database.session() as session:
        result = await app._index_knowledge_rows(session, await rows(session, [2, 4]))
        assert result.indexed == 1 and result.duplicate == 1
    assert len(calls) == 1 and len(app.rag.vectors.reference_ids(chat_id=20)) == 1


async def test_application_close_releases_independent_embedding_http_client(runtime_case):
    app, calls, _ = runtime_case

    async def closed():
        pass

    app.stopping = asyncio.Event()
    app.admin_server, app.bot = None, None
    app.scheduler = SimpleNamespace(running=False)
    app.ai_router = app.ollama = app.coingecko = app.user = SimpleNamespace(close=closed)
    transport = app.embedding_ai.client._client
    assert not transport.is_closed
    await app.close()
    assert transport.is_closed
    assert calls == []


async def test_uncertain_index_retry_different_attempt_and_overlap_never_resubmits(runtime_case):
    app, calls, set_handler = runtime_case
    set_handler(
        lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("synthetic", request=request))
    )
    async with app.database.session() as session:
        epoch = await session.scalar(
            select(TelegramChatPolicy.authorization_epoch).where(TelegramChatPolicy.chat_id == 20)
        )
        session.add(
            BackgroundJob(
                job_type="history_backfill",
                status="queued",
                payload={"chat_id": 20, "authorization_epoch": epoch},
            )
        )
    repo = JobRepository(app.database.engine.url, profile_id=app.settings.profile_id)
    now = datetime.now(UTC)
    first = repo.claim("history_backfill", "first-attempt", now, 30)
    with worker_lease(first):
        async with app.database.session() as session:
            with pytest.raises(AiUncertainError):
                await app._index_knowledge_rows(session, await rows(session, [2]))
    second = repo.claim("history_backfill", "second-attempt", now + timedelta(seconds=31), 30)
    assert first.claim_token != second.claim_token
    with worker_lease(second):
        async with app.database.session() as session:
            assert (await session.get(BackgroundJob, second.id)).attempts == 2
            with pytest.raises(AiUncertainError):
                await app._index_knowledge_rows(session, await rows(session, [2, 3]))
    repo.close()
    assert len(calls) == 1
    set_handler(None)
    async with app.database.session() as session:
        assert (await app._index_knowledge_rows(session, await rows(session, [3]))).indexed == 1
    assert len(calls) == 2
    async with app.database.session() as session:
        reservations = list((await session.scalars(select(AiBudgetReservation))).all())
        assert sorted(row.state for row in reservations) == ["settled", "uncertain"]


async def test_known_unsubmitted_release_can_retry_same_durable_intent(runtime_case):
    app, calls, _ = runtime_case
    admit = app.embedding_ai._admit_request

    async def refuse(**kwargs):
        raise RuntimeError("synthetic rate admission refused before HTTP")

    app.embedding_ai._admit_request = refuse
    async with app.database.session() as session:
        with pytest.raises(RuntimeError, match="synthetic rate"):
            await app._index_knowledge_rows(session, await rows(session, [2]))
    app.embedding_ai._admit_request = admit
    async with app.database.session() as session:
        assert (await app._index_knowledge_rows(session, await rows(session, [2]))).indexed == 1
    assert len(calls) == 1


async def test_changed_content_with_same_store_reembeds_known_settled_row(runtime_case):
    app, calls, _ = runtime_case
    async with app.database.session() as session:
        assert (await app._index_knowledge_rows(session, await rows(session, [2]))).indexed == 1
    async with app.database.session() as session:
        row = (await rows(session, [2]))[0]
        row.text = "Updated meaningful release details differ from the previous indexed content"
    async with app.database.session() as session:
        assert (await app._index_knowledge_rows(session, await rows(session, [2]))).indexed == 1
    assert len(calls) == 2


@pytest.mark.parametrize(
    "change", ["consent", "epoch", "local_only", "global_off", "dashboard_off", "intent"]
)
async def test_runtime_rechecks_after_budget_await_before_http(runtime_case, change):
    app, calls, _ = runtime_case
    admit = app.embedding_ai._admit_request

    async def interrupt(**kwargs):
        if change == "consent":
            save_settings(app.settings.model_copy(update={"cloud_consent": False}))
        elif change == "epoch":
            async with app.database.session() as current:
                await app.policy.set_allowed(current, 20, False)
        elif change == "local_only":
            async with app.database.session() as current:
                await app.policy.set_ai_route(current, 20, mode="local_only")
        elif change == "global_off":
            save_settings(app.settings.model_copy(update={"ai_provider": "off"}))
        elif change == "dashboard_off":
            async with app.database.session() as current:
                current.add(AppSetting(key="ai_enabled", value=False))
        else:
            async with app.database.session() as current:
                row = (await rows(current, [2]))[0]
                row.metadata_json = {**row.metadata_json, "embedding_request_id": "another-intent"}
        await admit(**kwargs)

    app.embedding_ai._admit_request = interrupt
    async with app.database.session() as session:
        with pytest.raises((AiPolicyError, AuthorizationRevoked)):
            await app._index_knowledge_rows(session, await rows(session, [2]))
    assert calls == []
    async with app.database.session() as session:
        assert (await session.scalar(select(AiBudgetReservation))).state == "released"


async def test_runtime_chat_switch_preserves_cloud_embedding_without_ollama(runtime_case):
    app, _, _ = runtime_case
    app.store = FixtureStore()
    app.bot = None
    app.ai = make_ai_engine(app.settings, app.store, app.budget)
    app.ai_router = SimpleNamespace(close=app.ai.close)

    async def forbidden(*args):
        raise AssertionError(
            "Chat switch must not probe an independent cloud embedding with Ollama"
        )

    app.ollama = SimpleNamespace(embedding_dimension=forbidden)
    old = app.settings.embedding_profile
    try:
        await app._switch_ai_provider("ollama")
        assert app.settings.embedding_profile == old
        assert app.embedding_ai.provider == "openai"
        assert app.rag.vectors.profile == old
    finally:
        await app.ai_router.close()


async def test_overlapping_concurrent_runtime_intents_admit_one_http(runtime_case):
    app, calls, _ = runtime_case
    admitted, resume = asyncio.Event(), asyncio.Event()
    admit = app.embedding_ai._admit_request

    async def pause(**kwargs):
        admitted.set()
        await resume.wait()
        await admit(**kwargs)

    app.embedding_ai._admit_request = pause

    async def first():
        async with app.database.session() as session:
            return await app._index_knowledge_rows(session, await rows(session, [2]))

    task = asyncio.create_task(first())
    try:
        await asyncio.wait_for(admitted.wait(), 5)
        async with app.database.session() as session:
            with pytest.raises(AiUncertainError):
                await app._index_knowledge_rows(session, await rows(session, [2, 3]))
        resume.set()
        assert (await asyncio.wait_for(task, 5)).indexed == 1
        assert len(calls) == 1
    finally:
        resume.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_distinct_runtime_batches_respect_pending_cap_and_resume_known_denial(runtime_case):
    app, calls, _ = runtime_case
    app.budget.daily_limit = 0.000002
    admitted, resume = asyncio.Event(), asyncio.Event()
    admit = app.embedding_ai._admit_request

    async def pause(**kwargs):
        admitted.set()
        await resume.wait()
        await admit(**kwargs)

    app.embedding_ai._admit_request = pause

    async def first():
        async with app.database.session() as session:
            return await app._index_knowledge_rows(session, await rows(session, [2]))

    task = asyncio.create_task(first())
    try:
        await asyncio.wait_for(admitted.wait(), 5)
        async with app.database.session() as session:
            with pytest.raises(RuntimeError, match="budget"):
                await app._index_knowledge_rows(session, await rows(session, [3]))
        assert calls == []
        resume.set()
        assert (await asyncio.wait_for(task, 5)).indexed == 1
        async with app.database.session() as session:
            assert (await app._index_knowledge_rows(session, await rows(session, [3]))).indexed == 1
        assert len(calls) == 2
    finally:
        resume.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_candidate_store_refuses_index_before_http(runtime_case):
    app, calls, _ = runtime_case
    async with app.database.session() as session:
        session.add(
            VectorStore(
                store_id="old-corpus",
                path="synthetic-old-corpus",
                collection="old",
                provider="ollama",
                model="old",
                embedding_version="v1",
                dimension=3,
                role="semantic_active",
                state="active",
            )
        )
    async with app.database.session() as session:
        with pytest.raises(AiPolicyError):
            await app._index_knowledge_rows(session, await rows(session, [2]))
    assert calls == []
    async with app.database.session() as session:
        assert (await session.get(VectorStore, "old-corpus")).role == "semantic_active"
        assert (
            await session.get(VectorStore, app.settings.embedding_profile.store_id)
        ).role == "semantic_candidate"


async def test_same_model_old_store_tag_cannot_reuse_wrong_endpoint_vectors(runtime_case):
    app, calls, _ = runtime_case
    async with app.database.session() as session:
        row = (await rows(session, [2]))[0]
        row.vector_status = "indexed"
        row.embedding_provider, row.embedding_model = "openai", "text-embedding-3-small"
        row.embedding_version = app.settings.embedding_version
        row.metadata_json = {"embedding_store_id": "old-endpoint-or-dimension"}
    async with app.database.session() as session:
        result = await app._index_knowledge_rows(session, await rows(session, [2]))
        assert result.indexed == 1 and result.reused == 0
    assert len(calls) == 1


async def test_reclaimed_worker_after_budget_await_never_submits(runtime_case):
    app, calls, _ = runtime_case
    async with app.database.session() as session:
        epoch = await session.scalar(
            select(TelegramChatPolicy.authorization_epoch).where(TelegramChatPolicy.chat_id == 20)
        )
        job = BackgroundJob(
            job_type="history_backfill",
            status="queued",
            payload={"chat_id": 20, "authorization_epoch": epoch},
        )
        session.add(job)
    repo = JobRepository(app.database.engine.url, profile_id=app.settings.profile_id)
    now = datetime.now(UTC)
    lease = repo.claim("history_backfill", "first", now, 30)
    admit = app.embedding_ai._admit_request

    async def reclaim(**kwargs):
        from datetime import timedelta

        assert repo.claim("history_backfill", "second", now + timedelta(seconds=31), 30)
        await admit(**kwargs)

    app.embedding_ai._admit_request = reclaim
    try:
        with worker_lease(lease):
            async with app.database.session() as session:
                with pytest.raises(AuthorizationRevoked):
                    await app._index_knowledge_rows(session, await rows(session, [2]))
        assert calls == []
        async with app.database.session() as session:
            assert (await session.scalar(select(AiBudgetReservation))).state == "released"
    finally:
        repo.close()


async def test_real_history_classifier_carries_policy_and_staged_boundary(runtime_case):
    app, calls, _ = runtime_case
    engine = make_ai_engine(app.settings, FixtureStore(), app.budget)
    app.ai_router = SimpleNamespace(engines={"openai": engine})
    async with app.database.session() as session:
        await app.policy.set_permission(session, 20, PermissionName.DELETE_ANY_MESSAGES, True)

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "fixture",
                "object": "response",
                "created_at": 0,
                "model": "gpt-5.6-terra",
                "status": "completed",
                "output": [
                    {
                        "id": "m",
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": '{"results":[]}', "annotations": []}
                        ],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            },
        )

    await engine.client._client.aclose()
    engine.client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        async with app.database.session() as session:
            row = (await rows(session, [2]))[0]
            row.metadata_json = {"staged_classifier": True}
            assert (
                await app._classify_history_delete_with_openai(
                    session, 20, "old release", [{"message_id": 2, "text": row.text}]
                )
                == []
            )
        assert len(calls) == 1
    finally:
        await engine.close()


@pytest.mark.parametrize("runtime_case", ["sqlite"], indirect=True)
async def test_cli_reindex_uses_independent_provider_and_runtime_guards(runtime_case, monkeypatch):
    app, calls, _ = runtime_case
    from tg_assistant import cli

    app.rag.vectors.close()
    # Execute the CLI coroutine on this test loop while retaining its actual body.
    queued = []
    monkeypatch.setattr(cli, "get_settings", lambda: app.settings)
    monkeypatch.setattr(cli, "SecretStore", FixtureStore)
    monkeypatch.setattr(cli, "make_database", lambda *args: app.database)
    monkeypatch.setattr(cli, "make_local_embedding_engine", lambda *args: app.embedding_ai)
    real_run = asyncio.run
    monkeypatch.setattr(asyncio, "run", queued.append)
    cli.reindex()
    monkeypatch.setattr(asyncio, "run", real_run)
    await queued[0]
    assert len(calls) == 1 and len(calls[0]["input"]) == 2
    assert "Local-only" not in json.dumps(calls)


async def test_runtime_direct_answer_source_metadata_and_rag_writer_boundary(runtime_case):
    app, calls, _ = runtime_case
    engine = make_ai_engine(app.settings, FixtureStore(), app.budget)
    router = AiRouter({"openai": engine}, default_provider="openai", cloud_consent=True)
    app.ai, app.ai_router = engine, router

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "fixture",
                "object": "response",
                "created_at": 0,
                "model": "gpt-5.6-terra",
                "status": "completed",
                "output": [
                    {
                        "id": "m",
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Useful release update [S1]",
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            },
        )

    await engine.client._client.aclose()
    engine.client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        async with app.database.session() as session:
            row = (await rows(session, [2]))[0]
            chat = await session.scalar(select(TelegramChat).where(TelegramChat.chat_id == 20))
            row.metadata_json = {"staged_direct_answer": True}
            answer = await app._answer_from_message_rows(
                session, chat=chat, question="release update", rows=[row], empty_message="none"
            )
            assert "Useful release" in answer
        rag = RagService(app.policy, engine, app.rag.vectors, router, embedding_ai=app.embedding_ai)
        rag.database, rag.provider_fence = app.database, app._model_fence
        async with app.database.session() as session:
            await ensure_vector_store_registry(session, app.settings)
        async with app.database.session() as session:
            (await rows(session, [2]))[0].metadata_json = {"staged_rag_answer": True}
            assert "Useful release" in await rag.answer(
                session, "release update", actor_id=1, owner_id=1, chat_ids=[10, 20]
            )
        assert "Local-only" not in json.dumps(calls)
    finally:
        await router.close()


@pytest.mark.parametrize("routed", [False, True])
@pytest.mark.parametrize("limited", [False, True])
async def test_known_group_answer_budget_and_reservation_attribution(runtime_case, routed, limited):
    app, calls, _ = runtime_case
    app.budget.group_daily_token_limit = 1 if limited else 100_000
    engine = make_ai_engine(app.settings, FixtureStore(), app.budget)
    router = AiRouter({"openai": engine}, default_provider="openai", cloud_consent=True)
    app.ai = engine
    if routed:
        app.ai_router = router

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "fixture",
                "object": "response",
                "created_at": 0,
                "model": "gpt-5.6-terra",
                "status": "completed",
                "output": [
                    {
                        "id": "m",
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Useful release update",
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            },
        )

    await engine.client._client.aclose()
    engine.client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        async with app.database.session() as session:
            row = (await rows(session, [2]))[0]
            chat = await session.scalar(select(TelegramChat).where(TelegramChat.chat_id == 20))

            async def answer():
                return await app._answer_from_message_rows(
                    session, chat=chat, question="release update", rows=[row], empty_message="none"
                )

            if limited:
                with pytest.raises(RuntimeError, match="Group"):
                    await answer()
            else:
                assert "Useful release" in await answer()
        assert len(calls) == (0 if limited else 1)
        async with app.database.session() as session:
            reservations = list(await session.scalars(select(AiBudgetReservation)))
            assert [item.chat_id for item in reservations] == ([] if limited else [20])
    finally:
        await router.close()


async def test_known_group_rag_query_embedding_obeys_group_cap(runtime_case):
    app, calls, _ = runtime_case
    app.budget.group_daily_token_limit = 1
    engine = make_ai_engine(app.settings, FixtureStore(), app.budget)
    router = AiRouter({"openai": engine}, default_provider="openai", cloud_consent=True)
    app.ai, app.ai_router = engine, router
    rag = RagService(app.policy, engine, app.rag.vectors, router, embedding_ai=app.embedding_ai)
    rag.database, rag.provider_fence = app.database, app._model_fence
    try:
        async with app.database.session() as session:
            await ensure_vector_store_registry(session, app.settings)
        async with app.database.session() as session:
            with pytest.raises(RuntimeError, match="Group"):
                await rag.answer(session, "release update", actor_id=1, owner_id=1, chat_ids=[20])
        assert calls == [], "Over-budget group query must not call embedding provider"
    finally:
        await router.close()


@pytest.mark.parametrize(
    "cap",
    [
        "daily_token_limit",
        "monthly_token_limit",
        "group_daily_token_limit",
        "feature_daily_token_limit",
        "provider_daily_token_limit",
        None,
        "concurrent",
        "revoked",
    ],
)
async def test_rag_cached_answer_atomic_caps_and_zero_provider_cost(runtime_case, cap, monkeypatch):
    from tg_assistant.ai.local_first import estimate_tokens

    app, calls, _ = runtime_case
    engine = make_ai_engine(app.settings, FixtureStore(), app.budget)
    router = AiRouter({"openai": engine}, default_provider="openai", cloud_consent=True)
    app.ai, app.ai_router = engine, router
    rag = RagService(app.policy, engine, router=router, embedding_ai=app.embedding_ai)
    rag.database, rag.provider_fence = app.database, app._model_fence

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "fixture",
                "object": "response",
                "created_at": 0,
                "model": "gpt-5.6-terra",
                "status": "completed",
                "output": [
                    {
                        "id": "m",
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Useful release update",
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            },
        )

    async def answer():
        async with app.database.session() as session:
            return await rag.answer(
                session, "release update", actor_id=1, owner_id=1, chat_ids=[20]
            )

    await engine.client._client.aclose()
    engine.client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        first = await answer()
        assert "Useful release" in first and len(calls) == 1
        async with app.database.session() as session:
            cached = await session.scalar(select(AiQueryCache))
            tokens = estimate_tokens(cached.response)
        if cap == "revoked":
            original_admit = app.budget.admit_cache_hit

            async def revoke_after_admission(*args, **kwargs):
                await original_admit(*args, **kwargs)
                async with app.database.session() as current:
                    await app.policy.set_allowed(current, 20, False)

            monkeypatch.setattr(app.budget, "admit_cache_hit", revoke_after_admission)
        elif cap == "concurrent":
            app.budget.group_daily_token_limit = 15 + tokens
        elif cap is not None:
            setattr(app.budget, cap, 1)
        # A local cache hit needs neither a provider price nor remaining USD.
        app.budget.daily_limit = app.budget.monthly_limit = 0
        monkeypatch.setattr(
            "tg_assistant.ai.budget.pricing_for",
            lambda *args, **kwargs: pytest.fail("Cache must not price a provider call"),
        )
        if cap == "concurrent":
            answers = await asyncio.gather(answer(), answer(), return_exceptions=True)
            assert sum(isinstance(value, str) for value in answers) == 1
            assert sum(isinstance(value, RuntimeError) for value in answers) == 1
        elif cap == "revoked":
            with pytest.raises(AuthorizationRevoked):
                await answer()
        elif cap is not None:
            with pytest.raises(RuntimeError):
                await answer()
        else:
            assert await answer() == first
        assert len(calls) == 1
        async with app.database.session() as session:
            hits = list(await session.scalars(select(AiUsage).where(AiUsage.cache_hit.is_(True))))
            reservations = list(
                await session.scalars(
                    select(AiBudgetReservation).where(AiBudgetReservation.operation == "cache_hit")
                )
            )
            count = 1 if cap in {None, "concurrent", "revoked"} else 0
            assert len(hits) == len(reservations) == count
            assert (await session.scalar(select(AiQueryCache))).hit_count == count
            for usage, reservation in zip(hits, reservations, strict=True):
                assert (
                    usage.estimated_cost_usd
                    == reservation.actual_cost_usd
                    == reservation.reserved_cost_usd
                    == 0
                )
                assert (
                    usage.cached_tokens == tokens and usage.input_tokens == usage.output_tokens == 0
                )
                assert usage.chat_id == reservation.chat_id == 20
                assert (
                    usage.reservation_id == reservation.request_id
                    and reservation.state == "settled"
                )
                assert usage.pricing_version == "cache-zero-v1"
    finally:
        await router.close()
