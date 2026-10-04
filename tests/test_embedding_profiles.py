from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import func, select

from tg_assistant.ai import budget as budget_module
from tg_assistant.ai import engine as engine_module
from tg_assistant.ai.budget import BudgetService
from tg_assistant.ai.engine import AiEngine
from tg_assistant.ai.rag import RagService
from tg_assistant.ai.router import AiRoute, AiRouter
from tg_assistant.ai.vector import LocalVectorStore
from tg_assistant.config import Settings
from tg_assistant.db.base import Base, Database
from tg_assistant.db.models import AiUsage, TelegramChat, TelegramMessage
from tg_assistant.policy import PolicyEngine
from tg_assistant.services.vector_reliability import ensure_vector_store_registry


def settings(tmp_path, **changes):
    return Settings(_env_file=None, data_dir=tmp_path, **changes)


@pytest.mark.parametrize("provider", ["openai", "openrouter"])
def test_cloud_profile_without_ollama(tmp_path, provider):
    selected = settings(tmp_path, embedding_provider=provider, cloud_consent=True)
    profile = selected.embedding_profile
    assert profile.provider == provider
    assert profile.model.endswith("text-embedding-3-small")
    assert profile.dimension == 1536
    assert profile.cloud_consent is True
    assert selected.active_embedding_model != selected.ollama_embedding_model
    engine = engine_module.make_embedding_engine(selected, BudgetService(1, 10), "fixture")
    assert engine.provider == provider
    assert engine.model is None  # independent embedding capability
    assert engine.available
    assert engine.client.max_retries == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("embedding_provider", "openai"),
        ("ollama_embedding_model", "another-model"),
        ("embedding_version", "next-version"),
        ("ollama_vector_size", 384),
        ("ollama_base_url", "http://localhost:11435/v1"),
    ],
)
def test_model_dimension_switch_new_store(tmp_path, field, value):
    first = settings(tmp_path, cloud_consent=True)
    changed = settings(tmp_path, cloud_consent=True, **{field: value})
    assert first.embedding_profile.store_id != changed.embedding_profile.store_id
    assert first.resolved_semantic_vector_path != changed.resolved_semantic_vector_path
    assert first.embedding_profile.endpoint_id is not None


def test_chat_switch_preserves_embedding_store(tmp_path):
    local = settings(tmp_path, ai_provider="ollama")
    cloud = settings(tmp_path, ai_provider="openai")
    assert local.embedding_profile == cloud.embedding_profile
    assert local.resolved_semantic_vector_path == cloud.resolved_semantic_vector_path


def test_store_identity_rejects_unknown_or_different_existing_corpus(tmp_path):
    selected = settings(tmp_path)
    profile = selected.embedding_profile
    store = LocalVectorStore(tmp_path / "vectors", vector_size=profile.dimension, profile=profile)
    store.upsert(1, [1.0] + [0.0] * (profile.dimension - 1), chat_id=10, message_id=1)
    store.close()
    changed = settings(tmp_path, embedding_version="next-version").embedding_profile
    with pytest.raises(ValueError, match="identity"):
        LocalVectorStore(tmp_path / "vectors", vector_size=changed.dimension, profile=changed)
    reopened = LocalVectorStore(
        tmp_path / "vectors", vector_size=profile.dimension, profile=profile
    )
    assert reopened.reference_ids() == [1]
    reopened.close()
    unknown = tmp_path / "unknown"
    unknown.mkdir()
    sentinel = unknown / "old-content"
    sentinel.write_bytes(b"preserve")
    with pytest.raises(ValueError, match="identity"):
        LocalVectorStore(unknown, vector_size=profile.dimension, profile=profile)
    assert sentinel.read_bytes() == b"preserve"


def test_no_hidden_cloud_provider_selection():
    engine = object()
    router = AiRouter({"openai": engine}, default_provider="ollama", cloud_consent=True)
    assert router.plan(AiRoute(mode="cloud_only", preferred_cloud_provider="openrouter")) == []
    assert router.plan(AiRoute(mode="local_first", cloud_fallback=True)) == []


def test_cloud_fallback_requires_global_and_route_consent():
    engines = {"ollama": object(), "openai": object()}
    denied = AiRouter(engines, default_provider="openai", cloud_consent=False)
    assert denied.plan(AiRoute(mode="cloud_only")) == []
    assert denied.plan(AiRoute(mode="local_first", cloud_fallback=True)) == ["ollama"]
    allowed = AiRouter(engines, default_provider="openai", cloud_consent=True)
    assert allowed.plan(AiRoute(mode="local_first")) == ["ollama"]
    assert allowed.plan(AiRoute(mode="local_first", cloud_fallback=True)) == ["ollama", "openai"]
    assert allowed.plan(AiRoute(mode="local_only", cloud_fallback=True)) == ["ollama"]


@pytest.mark.asyncio
async def test_local_only_never_cloud_embeds_or_answers(tmp_path, session):
    engine = AiEngine(
        api_key="fixture",
        budget=BudgetService(1, 10),
        provider="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-5.6-terra",
        embedding_model="text-embedding-3-small",
        max_output_tokens=10,
        cloud_consent=True,
    )
    calls = []

    async def transport(request):
        calls.append(request)
        return httpx.Response(500, json={"error": {"message": "synthetic"}})

    await engine.client._client.aclose()
    engine.client._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    with pytest.raises(RuntimeError, match="source"):
        await engine.embed_many(session, ["local confidential corpus"], source_modes=["local_only"])
    with pytest.raises(RuntimeError, match="source"):
        await engine.answer(
            session, "question", ["local confidential corpus"], source_modes=["local_only"]
        )
    assert calls == []
    await engine.close()


def test_versioned_pricing_handles_tier_cache_and_expiry():
    snapshot = budget_module.pricing_for(
        "openai", "gpt-5.6-terra", now=datetime(2026, 10, 3, tzinfo=UTC)
    )
    assert snapshot.version and snapshot.source.startswith("https://developers.openai.com/")
    assert snapshot.cost(1000, 100, cached_tokens=500, cache_write_tokens=100) == Decimal("0.00235")
    assert snapshot.cost(1_000_000, 1_000_000) == Decimal("22")
    assert snapshot.reserve_cost(1000, 100) >= snapshot.cost(1000, 100, cache_write_tokens=1000)
    with pytest.raises(ValueError, match="pricing"):
        budget_module.pricing_for("openai", "gpt-5.6-sol", now=datetime(2026, 11, 22, tzinfo=UTC))
    with pytest.raises(ValueError, match="pricing"):
        budget_module.pricing_for("unknown", "gpt-5.6-terra")


@pytest_asyncio.fixture
async def budget_database(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'ledger.sqlite3'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield database
    await database.engine.dispose()


async def cloud_engine(budget, handler, *, embedding_dimension=3, provider="openai"):
    engine = AiEngine(
        api_key="fixture",
        budget=budget,
        provider=provider,
        base_url="https://api.openai.com/v1"
        if provider == "openai"
        else "https://openrouter.ai/api/v1",
        model="gpt-5.6-terra" if provider == "openai" else "openai/gpt-5.6-terra",
        embedding_model="text-embedding-3-small"
        if provider == "openai"
        else "openai/text-embedding-3-small",
        max_output_tokens=20,
        cloud_consent=True,
        embedding_dimension=embedding_dimension,
    )
    await engine.client._client.aclose()
    engine.client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return engine


def answer_response():
    return httpx.Response(
        200,
        json={
            "id": "fixture",
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "model": "gpt-5.6-terra",
            "output": [
                {
                    "id": "m",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "A useful answer [S1]", "annotations": []}
                    ],
                }
            ],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "input_tokens_details": {"cached_tokens": 0},
            },
        },
    )


@pytest.mark.asyncio
async def test_shared_corpus_excludes_local_only_for_cloud(budget_database):
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return answer_response()

    engine = await cloud_engine(BudgetService(1, 10), handler)
    try:
        async with budget_database.sessions() as session:
            policy = PolicyEngine()
            session.add_all(
                [
                    TelegramChat(chat_id=10, title="Local", chat_type="group"),
                    TelegramChat(chat_id=20, title="Consented", chat_type="group"),
                ]
            )
            await session.flush()
            for chat_id in [10, 20]:
                await policy.apply_template(session, chat_id, "knowledge")
            await policy.set_ai_route(session, 10, mode="local_only", cloud_fallback=True)
            session.add_all(
                [
                    TelegramMessage(
                        chat_id=10,
                        message_id=1,
                        text="market local confidential canary",
                        sent_at=datetime.now(UTC),
                    ),
                    TelegramMessage(
                        chat_id=20,
                        message_id=1,
                        text="market consented public canary",
                        sent_at=datetime.now(UTC),
                    ),
                ]
            )
            await session.commit()
            router = AiRouter({"openai": engine}, default_provider="openai", cloud_consent=True)
            rag = RagService(policy, engine, router=router)
            answer = await rag.answer(session, "market", owner_id=1, actor_id=1, chat_ids=[10, 20])
            assert "useful answer" in answer
            assert len(payloads) == 1
            assert "local confidential canary" not in json.dumps(payloads)
            assert "consented public canary" in payloads[0]["input"]
            assert payloads[0]["store"] is False
    finally:
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openrouter"])
async def test_cloud_embeddings_submit_without_ollama(budget_database, provider):
    payloads = []

    def handler(request):
        payloads.append((str(request.url), json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "text-embedding-3-small",
                "data": [{"object": "embedding", "index": 0, "embedding": [1, 0, 0]}],
                "usage": {"prompt_tokens": 5, "total_tokens": 5},
            },
        )

    engine = await cloud_engine(BudgetService(1, 10), handler, provider=provider)
    try:
        async with budget_database.sessions() as session:
            result = await engine.embed_many(
                session, ["consented content"], source_modes=["inherit"]
            )
            assert result == [[1, 0, 0]]
            if provider == "openai":
                assert payloads == [
                    (
                        "https://api.openai.com/v1/embeddings",
                        {
                            "input": ["consented content"],
                            "model": "text-embedding-3-small",
                            "dimensions": 3,
                            "encoding_format": "float",
                        },
                    )
                ]
            else:
                assert payloads[0][0] == "https://openrouter.ai/api/v1/embeddings"
                assert payloads[0][1]["provider"] == {"allow_fallbacks": False, "order": ["openai"]}
                assert payloads[0][1]["model"] == "openai/text-embedding-3-small"
            assert await session.scalar(select(func.count(AiUsage.id))) == 1
    finally:
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "500", "missing_usage"])
async def test_submitted_unknown_outcome_keeps_budget_and_does_not_retry(budget_database, failure):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic input must not echo", request=request)
        if failure == "500":
            return httpx.Response(500, json={"error": {"message": "synthetic input must not echo"}})
        response = answer_response()
        data = json.loads(response.content)
        data.pop("usage")
        return httpx.Response(200, json=data)

    budget = BudgetService(1, 10)
    engine = await cloud_engine(budget, handler)
    try:
        async with budget_database.sessions() as session:
            with pytest.raises(engine_module.AiUncertainError) as error:
                await engine.answer(
                    session,
                    "question",
                    ["context"],
                    source_modes=["inherit"],
                    request_id="uncertain-request",
                )
            assert "synthetic input" not in str(error.value)
            assert count == 1
            pending = await budget.state(session)
            assert pending.daily_spend > 0
            with pytest.raises(engine_module.AiUncertainError):
                await engine.answer(
                    session,
                    "question",
                    ["context"],
                    source_modes=["inherit"],
                    request_id="uncertain-request",
                )
            assert count == 1
            assert (await budget.state(session)).daily_spend == pending.daily_spend
            with pytest.raises(RuntimeError):
                await budget.release(session, "uncertain-request")
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_secret_and_unknown_source_denied_before_transport(session):
    payloads = []
    engine = await cloud_engine(BudgetService(1, 10), lambda request: payloads.append(request))
    try:
        for texts, modes in [
            (["api_key=sk-" + "x" * 40], ["inherit"]),
            (["content"], ["unknown"]),
            (["content"], None),
        ]:
            with pytest.raises(engine_module.AiPolicyError):
                await engine.embed_many(session, texts, source_modes=modes)
        assert payloads == []
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_existing_store_is_not_relabelled_or_candidate_promoted(tmp_path, session):
    selected = settings(tmp_path)
    first = await ensure_vector_store_registry(session, selected)
    await session.flush()
    assert first.state == "active"
    changed = settings(tmp_path, embedding_version="next-version")
    candidate = await ensure_vector_store_registry(session, changed)
    await session.flush()
    assert candidate.store_id != first.store_id
    assert candidate.state == "candidate"
    assert candidate.role == "semantic_candidate"
    assert first.state == "active"
    assert first.embedding_version == "local-v1"


@pytest.mark.asyncio
async def test_budget_release_replay_quotas_and_expired_snapshot(budget_database):
    budget = BudgetService(1, 10, daily_token_limit=100, monthly_token_limit=1000)
    async with budget_database.sessions() as session:
        request = dict(
            request_id="reserved-once",
            provider="openai",
            model="gpt-5.6-terra",
            input_tokens=50,
            output_tokens=30,
            operation="answer",
            feature="normal_ask",
        )
        first = await budget.reserve(session, **request)
        assert await budget.reserve(session, **request) == first
        with pytest.raises(RuntimeError):
            await budget.reserve(session, **{**request, "input_tokens": 51})
        with pytest.raises(RuntimeError, match="token"):
            await budget.reserve(session, **{**request, "request_id": "blocked-quota"})
        await budget.release(session, first.request_id)
        assert (await budget.state(session)).daily_spend == 0
        assert not await budget.mark_submitted(session, first.request_id)
        second = await budget.reserve(session, **{**request, "request_id": "settle-once"})
        assert await budget.mark_submitted(session, second.request_id)
        # Reconciliation uses the captured snapshot even after rates expire/change.
        from tg_assistant.db.models import AiBudgetReservation

        async with budget_database.sessions() as current, current.begin():
            row = await current.get(AiBudgetReservation, second.request_id)
            row.pricing_rates = {**row.pricing_rates, "expires_at": "2000-01-01T00:00:00+00:00"}
        await budget.reconcile(
            session,
            second.request_id,
            input_tokens=20,
            output_tokens=10,
            cached_tokens=5,
            cache_write_tokens=5,
        )
        with pytest.raises(RuntimeError, match="Conflicting"):
            await budget.reconcile(
                session,
                second.request_id,
                input_tokens=21,
                output_tokens=10,
                cached_tokens=5,
                cache_write_tokens=5,
            )
        assert (await budget.state(session)).daily_spend == pytest.approx(0.0001535)


@pytest.mark.asyncio
async def test_cloud_revoked_after_reservation_never_submits(budget_database):
    calls = []
    engine = await cloud_engine(BudgetService(1, 10), lambda request: calls.append(request))

    async def revoked():
        raise PermissionError("revoked")

    try:
        async with budget_database.sessions() as session:
            with pytest.raises(PermissionError):
                await engine.answer(
                    session, "question", ["content"], source_modes=["inherit"], pre_submit=revoked
                )
            assert calls == []
            assert (await engine.budget.state(session)).daily_spend == 0
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_budget_concurrent_reservations(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'budget.sqlite3'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    budgets = [BudgetService(0.005, 1), BudgetService(0.005, 1)]

    async def reserve(index):
        async with database.sessions() as session:
            return await budgets[index].reserve(
                session,
                request_id=f"parallel-{index}",
                provider="openai",
                model="gpt-5.6-terra",
                input_tokens=1000,
                output_tokens=100,
                operation="answer",
                feature="normal_ask",
            )

    results = await asyncio.gather(*(reserve(i) for i in range(2)), return_exceptions=True)
    accepted = [item for item in results if not isinstance(item, Exception)]
    assert len(accepted) == 1
    assert len([item for item in results if isinstance(item, RuntimeError)]) == 1
    async with database.sessions() as session:
        state = await budgets[0].state(session)
        assert state.daily_spend >= 0.0037
        token = accepted[0]
        assert await budgets[0].mark_submitted(session, token.request_id)
        assert not await budgets[1].mark_submitted(session, token.request_id)
        await budgets[0].reconcile(
            session, token.request_id, input_tokens=100, output_tokens=10, cached_tokens=50
        )
        await budgets[1].reconcile(
            session, token.request_id, input_tokens=100, output_tokens=10, cached_tokens=50
        )
        assert await session.scalar(select(func.count(AiUsage.id))) == 1
        assert (await budgets[0].state(session)).daily_spend == pytest.approx(0.00023)
    await database.engine.dispose()


@pytest.mark.asyncio
async def test_mysql_budget_reservations_are_atomic_across_independent_services():
    raw = os.environ.get("TG_TEST_MYSQL_URL")
    if not raw:
        pytest.skip("Disposable loopback MySQL fixture not configured")
    url = sa.engine.make_url(raw)
    assert url.host == "127.0.0.1" and url.port == 13307
    assert url.username == "root" and url.password == "codex-disposable-fixture-only-2026"
    name = "codex_v01_" + uuid4().hex
    admin = sa.create_engine(url.set(drivername="mysql+pymysql", database=None))
    database = None
    with admin.begin() as connection:
        connection.exec_driver_sql(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
    try:
        database = Database(
            url.set(drivername="mysql+asyncmy", database=name).render_as_string(hide_password=False)
        )
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async def reserve(index):
            service = BudgetService(0.005, 1)
            async with database.sessions() as session:
                return await service.reserve(
                    session,
                    request_id=f"mysql-{index}",
                    provider="openai",
                    model="gpt-5.6-terra",
                    input_tokens=1000,
                    output_tokens=100,
                    operation="answer",
                    feature="normal_ask",
                )

        results = await asyncio.gather(reserve(0), reserve(1), return_exceptions=True)
        assert len([item for item in results if not isinstance(item, Exception)]) == 1
        assert len([item for item in results if isinstance(item, RuntimeError)]) == 1
        async with database.sessions() as session:
            assert (await BudgetService(0.005, 1).state(session)).daily_spend == pytest.approx(
                0.0037
            )
    finally:
        if database is not None:
            await database.engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f"DROP DATABASE `{name}`")
        admin.dispose()
