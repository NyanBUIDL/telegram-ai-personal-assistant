"""Selected RAG uses real SQLite, SDK transport and local Qdrant; no live calls."""

import asyncio
import dataclasses
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, event, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_first_value_provider import CHAT, PROFILE, body, candidate_for, ok
from test_first_value_provider import rig as rig

from tg_assistant.ai import rag
from tg_assistant.ai.budget import reservation_model
from tg_assistant.ai.engine import AiEngine, AiPolicyError, make_embedding_engine
from tg_assistant.ai.router import AiRouter
from tg_assistant.ai.vector import LocalVectorStore
from tg_assistant.config import Settings
from tg_assistant.db.base import Database
from tg_assistant.db.models import (
    AiBudgetLock,
    AiQueryCache,
    AiUsage,
    TelegramChat,
    TelegramChatPolicy,
    TelegramMessage,
    VectorStore,
)
from tg_assistant.policy import PolicyEngine
from tg_assistant.services.revocation import (
    AuthorizationRevoked,
    AuthorizedAnswer,
    require_fresh_authorization,
)


def test_selected_producer_exists():
    assert callable(getattr(rag.RagService, "answer_selected_observed", None))


@pytest.mark.parametrize("text", ["admin:Bob", "admin:5", "login:abc"])
def test_purpose_parser_keeps_natural_colon_words(text):
    parser = getattr(rag, "parse_selected_query", None)
    assert callable(parser), "selected purpose parser missing"
    parsed = parser(f"in:-100 {text}", selected_chat_id=-100)
    assert parsed.text == text


@pytest.mark.parametrize(
    "text",
    [
        "in:-101 ask",
        "in:-100 in:-100 ask",
        "in: ask",
        "in:abc ask",
        "in:01 ask",
        "from:1 from:2 ask",
        "before:bad ask",
    ],
)
def test_purpose_parser_rejects_scope_and_malformed_filters(text):
    parser = getattr(rag, "parse_selected_query", None)
    assert callable(parser), "selected purpose parser missing"
    with pytest.raises(ValueError):
        parser(text, selected_chat_id=-100)


def test_purpose_parser_preserves_nonstandalone_filter_substrings():
    parser = getattr(rag, "parse_selected_query", None)
    assert callable(parser)
    assert (
        parser("login:abc admin:5 within:-100", selected_chat_id=-100).text
        == "login:abc admin:5 within:-100"
    )


@pytest.mark.parametrize(
    "answer", ["No references", "[S0]", "[S1][S9]", "[Sx]", "[S1][S2", "[S01]"]
)
def test_original_citations_reject_missing_or_bad_markers(answer):
    parser = getattr(rag, "selected_evidence_indexes", None)
    assert callable(parser), "strict original citation parser missing"
    assert parser(answer, item_count=2) is None


def test_original_adjacent_citations_are_accepted():
    parser = getattr(rag, "selected_evidence_indexes", None)
    assert callable(parser)
    assert parser("Both [S1][S2].", item_count=2) == [0, 1]


def test_parser_preserves_literal_question_whitespace():
    parsed = rag.parse_selected_query("in:-100 admin:Bob  and\nlogin:abc", selected_chat_id=-100)
    assert parsed.text == "admin:Bob  and\nlogin:abc"


def test_mixed_citations_with_spaces_are_denied():
    assert rag.selected_evidence_indexes("[S1][ S2]", item_count=2) is None


def test_missing_opening_bracket_and_oversized_source_number_are_denied():
    assert rag.selected_evidence_indexes("[S1] S2]", item_count=2) is None
    assert rag.selected_evidence_indexes("[S1][S" + "9" * 5000 + "]", item_count=2) is None


def test_selected_carriers_exist_and_reject_mutable_scope():
    from tg_assistant.ai import observations

    scope_type = getattr(observations, "RetrievalScope", None)
    assert scope_type is not None
    with pytest.raises((TypeError, ValueError)):
        scope_type(
            True,
            datetime.now(UTC),
            datetime.now(UTC),
            None,
            None,
            None,
            "local_rag",
            "cloud_embedding",
        )


@pytest_asyncio.fixture
async def selected(rig, tmp_path, request):
    from tg_assistant.ai.observations import SourceIndexBinding

    database = Database(str(rig.factory.kw["bind"].url))
    rig.factory = database.sessions
    value = SimpleNamespace(
        rig=rig,
        output="Found [S1].",
        embedding=[1.0, 0.0, 0.0],
        callbacks=0,
        tamper=None,
        answer_unavailable=False,
        embedding_payload=None,
        response_payload=None,
    )

    async def respond(request, wire):
        payload = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            response = {
                "object": "list",
                "model": payload["model"],
                "data": [{"object": "embedding", "index": 0, "embedding": value.embedding}],
                "usage": {"prompt_tokens": 30, "total_tokens": 30},
            }
            if value.embedding_payload is not None:
                response = value.embedding_payload
        else:
            if value.answer_unavailable:
                raise httpx.ConnectError("local unavailable", request=request)
            response = body(value.output, model=payload["model"])
            if value.response_payload is not None:
                response = value.response_payload
        return httpx.Response(200, json=response)

    value.wire = rig.wire(respond)
    value.settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        profile_id=PROFILE,
        ai_provider="ollama",
        embedding_provider="ollama",
        ollama_base_url=getattr(request, "param", "http://127.0.0.1:11434/v1"),
        ollama_embedding_model="embedding-u03-test",
        ollama_vector_size=3,
        embedding_version="v1",
        cloud_consent=False,
    )
    value.ai = AiEngine(
        api_key=None,
        budget=rig.budget,
        provider="ollama",
        base_url=value.settings.ollama_base_url,
        model="qwen3:u03-test",
        embedding_model=value.settings.active_embedding_model,
        max_output_tokens=200,
        embedding_dimension=3,
    )
    await value.ai.client._client.aclose()
    value.ai.client._client = httpx.AsyncClient(transport=value.wire.transport)
    rig.engines.append(value.ai)
    profile = value.settings.embedding_profile
    value.vectors = LocalVectorStore(tmp_path / "vectors", 3, profile=profile)
    value.policy = PolicyEngine()
    async with rig.factory() as session:
        session.add(TelegramChat(chat_id=CHAT, title="Source", chat_type="supergroup"))
        await session.flush()
        await value.policy.apply_template(session, CHAT, "knowledge")
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == CHAT)
        )
        policy.ai_mode = "local_only"
        session.add(
            VectorStore(
                store_id=profile.store_id,
                path=str(tmp_path / "vectors"),
                collection=value.vectors.COLLECTION,
                provider=profile.provider,
                endpoint_id=profile.endpoint_id,
                model=profile.model,
                embedding_version=profile.embedding_version,
                dimension=profile.dimension,
                role="semantic_active",
                state="active",
            )
        )
        await session.commit()
        epoch = policy.authorization_epoch
    value.binding = SourceIndexBinding(
        PROFILE,
        1,
        "pair",
        1,
        "restore",
        CHAT,
        epoch,
        "policy",
        profile.provider,
        profile.endpoint_id,
        profile.model,
        profile.embedding_version,
        3,
        profile.store_id,
        1,
        "vector",
        "eligible",
        (candidate_for(value.ai),),
    )
    value.service = rag.RagService(value.policy, value.ai, value.vectors)
    value.service.database = database

    async def provider_fence(epochs, permission, **kwargs):
        async with database.session() as current:
            await require_fresh_authorization(current, CHAT, permission, epochs[CHAT])

    value.service.provider_fence = provider_fence

    async def admit():
        value.callbacks += 1
        if value.tamper:
            await value.tamper(value)

    value.admit = admit

    async def add(
        text="stored semantic fact",
        *,
        message_id=1,
        when=None,
        vector=True,
        sender=5,
        media=False,
        chat_id=CHAT,
    ):
        async with rig.factory() as session:
            row = TelegramMessage(
                chat_id=chat_id,
                message_id=message_id,
                sender_id=sender,
                text=text,
                sent_at=when or datetime.now(UTC) - timedelta(hours=1),
                has_media=media,
            )
            session.add(row)
            await session.commit()
            if vector:
                value.vectors.upsert(
                    row.id, [1.0, 0.0, 0.0], chat_id=chat_id, message_id=message_id
                )
            return row

    value.add = add

    async def ask(question="semantic question", callback=admit):
        async with database.session() as session:
            return await value.service.answer_selected_observed(
                session,
                question,
                actor_id=1,
                owner_id=1,
                binding=value.binding,
                pre_submit=callback,
            )

    value.ask = ask
    yield value
    value.vectors.close()
    await database.engine.dispose()


@pytest.mark.asyncio
async def test_real_database_session_wrapper_qualifies_unchanged_owner(selected):
    from tg_assistant.ai.observations import QualifyingRagSuccess

    database = selected.service.database
    assert isinstance(database, Database)
    assert database.sessions is selected.rig.factory
    assert database.session is not database.session
    await selected.add()
    result = await selected.ask()
    assert isinstance(result, QualifyingRagSuccess)
    assert selected.rig.http_total == 2
    assert [row.state for row in await selected.rig.rows()] == ["settled", "settled"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selected",
    [
        "http://127.0.0.1:11434/v1/",
        "http://localhost:11434/v1",
        "http://LOCALHOST:11434/v1",
        "http://[::1]:11434/v1",
    ],
    indirect=True,
)
async def test_canonical_sdk_endpoint_preserves_persistent_corpus(selected, tmp_path):
    from tg_assistant.ai.observations import QualifyingRagSuccess

    settings, vectors = selected.settings, selected.vectors
    profile = settings.embedding_profile
    path = settings.resolved_semantic_vector_path
    manifest = tmp_path / "vectors" / "embedding-profile.json"
    original_manifest = manifest.read_bytes()
    row = await selected.add()
    point_id = vectors.point_id(row.id, chat_id=CHAT, message_id=1)
    async with selected.rig.factory() as session:
        store = await session.get(VectorStore, profile.store_id)
        original_registry = (store.store_id, store.endpoint_id, store.path)
    if "localhost" in settings.ollama_base_url.lower():
        selected.ai.client.base_url = "http://127.0.0.1:11434/v1"
    assert str(selected.ai.client.base_url).endswith("/v1/")
    result = await selected.ask()
    assert isinstance(result, QualifyingRagSuccess)
    assert result.observation.binding is selected.binding
    assert result.observation.cited_refs[0].reference_id == row.id
    candidate = selected.binding.chat_candidates[0]
    assert candidate.endpoint_id.startswith("ollama-")
    assert len(candidate.endpoint_id) == len("ollama-") + 16
    assert result.observation.execution.endpoint_id == candidate.endpoint_id
    assert settings.embedding_profile == profile == vectors.profile
    assert settings.resolved_semantic_vector_path == path
    assert manifest.read_bytes() == original_manifest
    assert vectors.point_id(row.id, chat_id=CHAT, message_id=1) == point_id
    async with selected.rig.factory() as session:
        store = await session.get(VectorStore, profile.store_id)
        assert (store.store_id, store.endpoint_id, store.path) == original_registry
    vectors.close()
    selected.vectors = LocalVectorStore(
        tmp_path / "vectors", 3, profile=profile, require_existing=True
    )
    assert selected.vectors.search([1.0, 0.0, 0.0], allowed_chat_ids=[CHAT])[0][0] == row.id
    # Original spellings and IPv4/IPv6 identities never merge existing corpora.
    alternate = Settings(
        _env_file=None,
        **{
            **settings.model_dump(),
            "ollama_base_url": "http://localhost:11434/v1"
            if settings.ollama_base_url != "http://localhost:11434/v1"
            else "http://127.0.0.1:11434/v1",
        },
    )
    assert alternate.embedding_profile.store_id != profile.store_id
    with pytest.raises(ValueError, match="preserve the existing corpus"):
        LocalVectorStore(tmp_path / "vectors", 3, profile=alternate.embedding_profile)


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_constructor", [True, False])
async def test_other_embedding_endpoint_refused_before_effects(selected, wrong_constructor):
    await selected.add()
    if wrong_constructor:
        settings = Settings(
            _env_file=None,
            **{
                **selected.settings.model_dump(),
                "ollama_base_url": "http://127.0.0.1:11435/v1",
            },
        )
        engine = make_embedding_engine(settings, selected.rig.budget)
        await engine.client._client.aclose()
        engine.client._client = httpx.AsyncClient(transport=selected.wire.transport)
        selected.rig.engines.append(engine)
        selected.service.embedding_ai = engine
    else:
        selected.ai.client.base_url = "http://127.0.0.1:11435/v1"
    with pytest.raises(AiPolicyError):
        await selected.ask()
    assert selected.rig.http_total == 0
    assert not await selected.rig.rows()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["client", "configured"])
@pytest.mark.parametrize("after_embedding", [False, True])
async def test_endpoint_mutation_after_await_stops_next_submission(
    selected, owner, after_embedding
):
    await selected.add()

    async def mutate(value):
        if after_embedding and value.rig.http_total < 1:
            return
        if owner == "client":
            value.ai.client.base_url = "http://127.0.0.1:11435/v1"
        else:
            value.ai._configured_base_url = "http://localhost:11434/v1"

    selected.tamper = mutate
    with pytest.raises(AiPolicyError):
        await selected.ask()
    assert selected.rig.http_total == int(after_embedding)
    assert all(row.operation == "embedding" for row in await selected.rig.rows())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:11434/wrong",
        "http://127.0.0.1:11434/v1?query=1",
        "http://user:pass@127.0.0.1:11434/v1",
        "http://192.0.2.1:11434/v1",
        "http://[::1]:11434/v1",
    ],
)
async def test_invalid_or_other_canonical_live_endpoint_cannot_borrow_profile(selected, endpoint):
    await selected.add()
    selected.ai.client.base_url = endpoint
    with pytest.raises((AiPolicyError, ValueError)):
        await selected.ask()
    assert selected.rig.http_total == 0
    assert not await selected.rig.rows()


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_factory", [True, False])
async def test_first_callback_cannot_replace_real_database_factory_or_bind(
    selected, replace_factory
):
    database = selected.service.database
    original_factory = database.sessions
    foreign = create_async_engine("sqlite+aiosqlite:///:memory:")
    statements = []

    def record_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    async def replace():
        if replace_factory:
            database.sessions = async_sessionmaker(database.engine, expire_on_commit=False)
        else:
            database.sessions.configure(bind=foreign)

    for engine in (database.engine.sync_engine, foreign.sync_engine):
        event.listen(engine, "before_cursor_execute", record_sql)
    try:
        with pytest.raises(AiPolicyError):
            await selected.ask(callback=replace)
    finally:
        database.sessions = original_factory
        original_factory.configure(bind=database.engine)
        for engine in (database.engine.sync_engine, foreign.sync_engine):
            event.remove(engine, "before_cursor_execute", record_sql)
        await foreign.dispose()
    assert not statements
    assert selected.rig.http_total == 0
    assert not await selected.rig.rows()


@pytest.mark.asyncio
async def test_real_selected_semantic_execution_and_context_hash(selected):
    from tg_assistant.ai.observations import QualifyingRagSuccess

    row = await selected.add()
    result = await selected.ask()
    assert isinstance(result, QualifyingRagSuccess)
    assert isinstance(result.answer, AuthorizedAnswer)
    observation = result.observation
    assert observation.binding is selected.binding
    assert observation.retrieval_mode == "semantic"
    (ref,) = observation.cited_refs
    assert (ref.reference_id, ref.chat_id, ref.message_id) == (row.id, CHAT, 1)
    assert ref.content_hash == hashlib.sha256(row.text.encode()).hexdigest()
    request = selected.wire.requests[-1]["json"]
    context = request["input"].split("NGUỒN:\n", 1)[1].rsplit("\n\nCÂU HỎI:\n", 1)[0]
    assert ref.context_hash == hashlib.sha256(context.encode()).hexdigest()
    ledger = {item.request_id: item for item in await selected.rig.rows()}
    assert ledger[observation.query_embedding_request_id].operation == "embedding"
    assert ledger[observation.execution.request_id].operation == "answer"
    assert observation.execution.reported_model is None
    assert selected.callbacks >= 8


@pytest.mark.asyncio
@pytest.mark.parametrize("literal", ["admin:Bob", "admin:5", "login:abc"])
async def test_literal_question_reaches_both_actual_submissions(selected, literal):
    await selected.add(literal)
    result = await selected.ask(f"in:{CHAT} {literal}")
    assert result.observation.cited_refs[0].keyword_used
    embed, answer = selected.wire.requests
    assert embed["json"]["input"] == [literal]
    assert answer["json"]["input"].rsplit("\n\nCÂU HỎI:\n", 1)[1] == literal


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question", ["in:-12 question", f"in:{CHAT} in:{CHAT} question", "in: question"]
)
async def test_invalid_scope_no_sql_or_provider(selected, question):
    result = await selected.ask(question)
    assert result.code == "invalid_filter"
    assert selected.callbacks == 0 and not selected.wire.requests
    assert not await selected.rig.rows()


@pytest.mark.asyncio
async def test_none_callback_rejected_before_any_work(selected):
    with pytest.raises(AiPolicyError):
        await selected.ask(callback=None)
    assert not selected.wire.requests and not await selected.rig.rows()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output", ["No citations.", "Fact [S1][S9].", "Fact [Sx].", "Fact [S1][S2"]
)
async def test_original_bad_citation_cannot_be_rescued_by_appendix(selected, output):
    await selected.add()
    selected.output = output
    result = await selected.ask()
    assert result.code == "citations_invalid"
    assert len(selected.wire.requests) == 2


@pytest.mark.asyncio
async def test_keyword_only_provenance_after_valid_embedding(selected):
    await selected.add("keyword", vector=False)
    result = await selected.ask("keyword")
    assert result.observation.retrieval_mode == "keyword"
    (ref,) = result.observation.cited_refs
    assert ref.keyword_used is True and ref.semantic_used is False
    assert result.observation.query_embedding_request_id


@pytest.mark.asyncio
async def test_no_content_is_nonqualifying(selected):
    result = await selected.ask()
    assert result.code == "no_content"
    assert len(selected.wire.requests) == 1


@pytest.mark.asyncio
async def test_selected_bypasses_cache_read_and_write(selected):
    await selected.add()
    await selected.ask()
    async with selected.rig.factory() as session:
        assert list((await session.scalars(select(AiQueryCache))).all()) == []
    await selected.ask()
    assert len(selected.wire.requests) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("vector", [[1.0, 0.0], [float("nan"), 0, 0], []])
async def test_invalid_embedding_cannot_downgrade_to_keyword(selected, vector):
    await selected.add("keyword")
    selected.embedding = vector
    try:
        result = await selected.ask("keyword")
    except AiPolicyError:
        pass
    else:
        assert result.code in {"embedding_invalid", "embedding_failed"}
    assert not any(item["path"].endswith("/responses") for item in selected.wire.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [
        {},
        {"chat_id": -12},
        {"provider": "openai"},
        {"profile_id": "other"},
        {"operation": "answer"},
        {"route": "local_embedding"},
        {"actual_input_tokens": -1},
    ],
)
async def test_exact_embedding_settlement_required(selected, changed, monkeypatch):
    await selected.add()
    real_embed = selected.ai.embed

    async def embed_then_tamper(session, text, **kwargs):
        vector = await real_embed(session, text, **kwargs)
        async with selected.rig.factory() as fresh, fresh.begin():
            if "profile_id" in changed:
                fresh.add(AiBudgetLock(profile_id="other", version=0))
                await fresh.flush()
            if not changed:
                await fresh.execute(
                    delete(AiUsage).where(AiUsage.reservation_id == kwargs["request_id"])
                )
                await fresh.execute(
                    delete(reservation_model()).where(
                        reservation_model().request_id == kwargs["request_id"]
                    )
                )
            else:
                await fresh.execute(
                    update(reservation_model())
                    .where(reservation_model().request_id == kwargs["request_id"])
                    .values(**changed)
                )
        return vector

    monkeypatch.setattr(selected.ai, "embed", embed_then_tamper)
    result = await selected.ask()
    assert result.code == "embedding_unsettled"
    assert len(selected.wire.requests) == 1


@pytest.mark.asyncio
async def test_default_window_applies_before_keyword_limit_and_semantic_candidates(selected):
    now = datetime.now(UTC)
    await selected.add("keyword old", message_id=1, when=now - timedelta(days=8))
    await selected.add("keyword current", message_id=2, when=now - timedelta(hours=1))
    for message_id in range(3, 15):
        await selected.add("keyword future", message_id=message_id, when=now + timedelta(days=1))
    result = await selected.ask("keyword")
    assert [ref.message_id for ref in result.observation.cited_refs] == [2]
    assert result.observation.before <= datetime.now(UTC)
    assert "future" not in selected.wire.requests[-1]["json"]["input"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filter_text,matching,other",
    [
        ("from:5", {"sender": 5}, {"sender": 6}),
        ("has:link", {"text": "https://example.test"}, {"text": "no link"}),
        ("has:file", {"media": True}, {"media": False}),
        ("type:task", {"text": "TODO"}, {"text": "plain fact"}),
        ("type:decision", {"text": "QUYẾT ĐỊNH"}, {"text": "plain fact"}),
    ],
)
async def test_filters_apply_semantic_and_actual_rows(selected, filter_text, matching, other):
    await selected.add(message_id=1, **matching)
    await selected.add(message_id=2, **other)
    result = await selected.ask(f"{filter_text} semantic question")
    assert [ref.message_id for ref in result.observation.cited_refs] == [1]


@pytest.mark.asyncio
async def test_explicit_before_has_exclusive_sql_and_inclusive_scope(selected):
    await selected.add(message_id=1, when=datetime(2026, 10, 1, 23, 59, 59, 999999, tzinfo=UTC))
    await selected.add(message_id=2, when=datetime(2026, 10, 2, tzinfo=UTC))
    result = await selected.ask("before:2026-10-01 semantic question")
    assert result.observation.before == datetime(2026, 10, 1, 23, 59, 59, 999999, tzinfo=UTC)
    assert result.observation.after is None
    assert result.observation.cited_refs[0].message_id == 1


@pytest.mark.asyncio
async def test_admission_denial_and_cancellation_never_submit(selected):
    await selected.add()

    async def deny():
        raise AuthorizationRevoked("source_authorization_revoked")

    with pytest.raises(AuthorizationRevoked):
        await selected.ask(callback=deny)

    async def cancel():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await selected.ask(callback=cancel)
    assert not selected.wire.requests


@pytest.mark.asyncio
async def test_real_concurrency_uses_own_embedding_and_answer_reservation(selected):
    await selected.add()
    first, second = await asyncio.gather(selected.ask(), selected.ask())
    ids = {
        first.observation.query_embedding_request_id,
        first.observation.execution.request_id,
        second.observation.query_embedding_request_id,
        second.observation.execution.request_id,
    }
    assert len(ids) == 4
    assert ids == {row.request_id for row in await selected.rig.rows()}


@pytest.mark.asyncio
async def test_original_adjacent_citations_capture_both_real_contexts(selected):
    await selected.add(message_id=1)
    await selected.add(message_id=2)
    selected.output = "Both [S1][S2]."
    result = await selected.ask()
    assert {ref.message_id for ref in result.observation.cited_refs} == {1, 2}
    contexts = (
        selected.wire.requests[-1]["json"]["input"]
        .split("NGUỒN:\n", 1)[1]
        .rsplit("\n\nCÂU HỎI:\n", 1)[0]
        .split("\n\n[S2]")
    )
    assert (
        result.observation.cited_refs[0].context_hash
        == hashlib.sha256(contexts[0].encode()).hexdigest()
    )
    assert (
        result.observation.cited_refs[1].context_hash
        == hashlib.sha256(("[S2]" + contexts[1]).encode()).hexdigest()
    )


@pytest.mark.asyncio
async def test_cache_read_is_bypassed_with_populated_cache(selected):
    await selected.add()
    async with selected.rig.factory() as session:
        session.add(
            AiQueryCache(
                cache_key="a" * 64,
                normalized_query_hash="b" * 64,
                knowledge_version="v",
                route="local_rag",
                feature="normal_ask",
                response="Cached [S1]",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await session.commit()
    engine = selected.rig.factory.kw["bind"].sync_engine

    def reject_cache_sql(conn, cursor, statement, parameters, context, executemany):
        assert "ai_query_cache" not in statement.lower()

    event.listen(engine, "before_cursor_execute", reject_cache_sql)
    try:
        result = await selected.ask()
        assert result.observation.execution
    finally:
        event.remove(engine, "before_cursor_execute", reject_cache_sql)


async def configure_route(selected, mode, *, fallback=True, consent=True):
    cloud = selected.rig.engine(
        "openai", selected.rig.wire(ok("Cloud fact [S1].")), consent=consent
    )
    router = AiRouter(
        {"ollama": selected.ai, "openai": cloud}, default_provider="openai", cloud_consent=consent
    )
    selected.service.router = router
    selected.binding = dataclasses.replace(
        selected.binding, chat_candidates=(candidate_for(selected.ai), candidate_for(cloud))
    )
    async with selected.rig.factory() as session:
        await session.execute(
            update(TelegramChatPolicy)
            .where(TelegramChatPolicy.chat_id == CHAT)
            .values(ai_mode=mode, cloud_fallback=fallback)
        )
        await session.commit()
    return cloud


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expected",
    [
        ("inherit", "openai"),
        ("local_only", "ollama"),
        ("local_first", "ollama"),
        ("cloud_only", "openai"),
        ("cloud_first", "openai"),
        ("off", None),
    ],
)
async def test_all_six_modes_preserve_current_route(selected, mode, expected):
    await selected.add()
    await configure_route(selected, mode)
    result = await selected.ask()
    if expected:
        assert result.observation.execution.provider == expected
        assert result.observation.execution.fallback_used is False
    else:
        assert result.code == "route_unavailable" and not selected.wire.requests


@pytest.mark.asyncio
async def test_genuine_local_unavailability_qualifies_actual_consented_fallback(selected):
    await selected.add()
    await configure_route(selected, "local_first")
    selected.answer_unavailable = True
    result = await selected.ask()
    assert result.observation.execution.provider == "openai"
    assert result.observation.execution.fallback_used is True
    assert result.observation.execution.requested_model == "gpt-5.6-terra"
    assert result.observation.execution.cost_usd > 0
    assert sorted(row.state for row in await selected.rig.rows()) == [
        "settled",
        "settled",
        "uncertain",
    ]


@pytest.mark.asyncio
async def test_no_cloud_fallback_after_second_local_admission_denial(selected):
    await selected.add()
    await configure_route(selected, "local_first")

    async def deny_after_submitted(value):
        if any(
            row.operation == "answer" and row.state == "submitted" for row in await value.rig.rows()
        ):
            raise AuthorizationRevoked("source_authorization_revoked")

    selected.tamper = deny_after_submitted
    with pytest.raises(AuthorizationRevoked):
        await selected.ask()
    assert selected.rig.http_total == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ["", "   "])
async def test_no_output_never_qualifies(selected, output):
    await selected.add()
    selected.output = output
    result = await selected.ask()
    assert result.code == "provider_failed"


@pytest.mark.asyncio
async def test_after_is_inclusive_for_both_paths(selected):
    await selected.add("keyword", message_id=1, when=datetime(2026, 10, 1, tzinfo=UTC))
    await selected.add(
        "keyword", message_id=2, when=datetime(2026, 9, 30, 23, 59, 59, 999999, tzinfo=UTC)
    )
    result = await selected.ask("after:2026-10-01 keyword")
    (ref,) = result.observation.cited_refs
    assert ref.message_id == 1 and ref.semantic_used and ref.keyword_used


@pytest.mark.asyncio
async def test_missing_same_answer_settlement_never_qualifies(selected, monkeypatch):
    await selected.add()
    real_reconcile = selected.rig.budget.reconcile

    async def reconcile_then_remove(session, request_id, **kwargs):
        await real_reconcile(session, request_id, **kwargs)
        async with selected.rig.factory() as fresh, fresh.begin():
            row = await fresh.get(reservation_model(), request_id)
            if row.operation == "answer":
                await fresh.execute(delete(AiUsage).where(AiUsage.reservation_id == request_id))
                await fresh.execute(
                    delete(reservation_model()).where(reservation_model().request_id == request_id)
                )

    monkeypatch.setattr(selected.rig.budget, "reconcile", reconcile_then_remove)
    result = await selected.ask()
    assert result.code == "provider_failed"


@pytest.mark.asyncio
async def test_embedding_ledger_cost_and_version_must_match_actual_local_pricing(
    selected, monkeypatch
):
    await selected.add()
    real_embed = selected.ai.embed

    async def wrong_cost(session, text, **kwargs):
        vector = await real_embed(session, text, **kwargs)
        async with selected.rig.factory() as fresh, fresh.begin():
            await fresh.execute(
                update(reservation_model())
                .where(reservation_model().request_id == kwargs["request_id"])
                .values(actual_cost_usd=1, pricing_version="fake")
            )
        return vector

    monkeypatch.setattr(selected.ai, "embed", wrong_cost)
    result = await selected.ask()
    assert result.code == "embedding_unsettled"
    assert len(selected.wire.requests) == 1


@pytest.mark.asyncio
async def test_initial_root_admission_precedes_sql(selected):
    engine = selected.rig.factory.kw["bind"].sync_engine
    statements = []

    def record_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_sql)

    async def deny():
        raise AuthorizationRevoked("root_denied")

    try:
        with pytest.raises(AuthorizationRevoked):
            await selected.ask(callback=deny)
    finally:
        event.remove(engine, "before_cursor_execute", record_sql)
    assert not statements


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "owner",
    [
        "embedding_budget",
        "embedding_client",
        "embedding_model",
        "profile",
        "fallback_engine",
        "fallback_client",
        "fallback_model",
        "fallback_budget",
    ],
)
async def test_first_callback_cannot_adopt_replacement_owner(selected, owner):
    from tg_assistant.ai.budget import BudgetService

    await selected.add()
    cloud = await configure_route(selected, "local_first")
    statements = []
    engine = selected.rig.factory.kw["bind"].sync_engine

    def record_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    async def replace():
        if owner == "embedding_budget":
            selected.ai.budget = BudgetService(10, 100, profile_id=PROFILE)
        elif owner == "embedding_client":
            selected.ai.client = cloud.client
        elif owner == "embedding_model":
            selected.ai.embedding_model += "-replaced"
        elif owner == "profile":
            selected.vectors.profile = selected.vectors.profile.model_copy()
        elif owner == "fallback_engine":
            selected.service.router.engines["openai"] = selected.rig.engine(
                "openai", selected.rig.wire(ok("Replacement [S1]."))
            )
        elif owner == "fallback_client":
            cloud.client = selected.ai.client
        elif owner == "fallback_model":
            cloud.model += "-replaced"
        else:
            cloud.budget = BudgetService(10, 100, profile_id=PROFILE)

    event.listen(engine, "before_cursor_execute", record_sql)
    try:
        with pytest.raises(AiPolicyError):
            await selected.ask(callback=replace)
    finally:
        event.remove(engine, "before_cursor_execute", record_sql)
    assert not statements
    assert selected.rig.http_total == 0
    assert not await selected.rig.rows()


@pytest.mark.asyncio
@pytest.mark.parametrize("async_bind", [True, False])
async def test_first_callback_cannot_rebind_caller_before_foreign_sql(selected, async_bind):
    foreign = create_async_engine("sqlite+aiosqlite:///:memory:")
    statements = []
    owning = selected.rig.factory.kw["bind"]

    def record_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    for engine in (owning.sync_engine, foreign.sync_engine):
        event.listen(engine, "before_cursor_execute", record_sql)
    try:
        async with selected.rig.factory() as session:

            async def rebind():
                if async_bind:
                    session.bind = foreign
                session.sync_session.bind = foreign.sync_engine

            with pytest.raises(AiPolicyError):
                await selected.service.answer_selected_observed(
                    session,
                    "semantic question",
                    actor_id=1,
                    owner_id=1,
                    binding=selected.binding,
                    pre_submit=rebind,
                )
    finally:
        for engine in (owning.sync_engine, foreign.sync_engine):
            event.remove(engine, "before_cursor_execute", record_sql)
        await foreign.dispose()
    assert not statements
    assert selected.rig.http_total == 0
    assert not await selected.rig.rows()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["vectors", "embedding_budget", "fallback_engine"])
async def test_callback_return_cannot_submit_after_owner_replacement(selected, owner):
    from tg_assistant.ai.budget import BudgetService

    await selected.add()
    await configure_route(selected, "local_first")

    async def mutate_at_submission(value):
        if any(
            row.operation == "embedding" and row.state == "submitted"
            for row in await value.rig.rows()
        ):
            if owner == "vectors":
                value.service.vectors = None
            elif owner == "embedding_budget":
                value.ai.budget = BudgetService(10, 100, profile_id=PROFILE)
            else:
                value.service.router.engines["openai"] = value.ai

    selected.tamper = mutate_at_submission
    with pytest.raises(AiPolicyError):
        await selected.ask()
    assert selected.rig.http_total == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_id", True),
        ("selected_chat_id", 0),
        ("selection_generation", True),
        ("source_epoch", -1),
        ("active_index_generation", False),
        ("embedding_dimension", 0),
        ("embedding_store_id", "https://private.test"),
        ("chat_candidates", []),
    ],
)
async def test_binding_strict_carrier_fields(selected, field, value):
    with pytest.raises((ValueError, TypeError)):
        dataclasses.replace(selected.binding, **{field: value})


@pytest.mark.asyncio
async def test_carriers_immutable_nested_references_and_utc(selected):
    await selected.add()
    result = await selected.ask()
    observation = result.observation
    with pytest.raises(dataclasses.FrozenInstanceError):
        observation.binding.owner_id = 2
    with pytest.raises(ValueError):
        dataclasses.replace(observation, cited_refs=list(observation.cited_refs))
    with pytest.raises(ValueError):
        dataclasses.replace(observation.cited_refs[0], semantic_used=1)
    with pytest.raises(ValueError):
        dataclasses.replace(observation.cited_refs[0], message_id=True)
    with pytest.raises(ValueError):
        dataclasses.replace(observation.cited_refs[0], content_hash="fake")
    assert observation.before.tzinfo is UTC


@pytest.mark.asyncio
async def test_wrong_caller_database_cannot_supply_rows_or_settlement(selected):
    wrong = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with async_sessionmaker(wrong)() as session:
            result = await selected.service.answer_selected_observed(
                session,
                "question",
                actor_id=1,
                owner_id=1,
                binding=selected.binding,
                pre_submit=selected.admit,
            )
        assert result.code == "owner_unavailable"
        assert not selected.wire.requests
    finally:
        await wrong.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing-vector", "wrong-index", "wrong-count"])
async def test_actual_sdk_malformed_embedding_never_submits_chat(selected, fault):
    await selected.add("keyword")
    payload = {
        "object": "list",
        "model": selected.ai.embedding_model,
        "data": [{"object": "embedding", "index": 0, "embedding": [1.0, 0.0, 0.0]}],
        "usage": {"prompt_tokens": 30, "total_tokens": 30},
    }
    if fault == "missing-vector":
        payload["data"][0]["embedding"] = None
    elif fault == "wrong-index":
        payload["data"][0]["index"] = 7
    else:
        payload["data"] = []
    selected.embedding_payload = payload
    with pytest.raises((AiPolicyError, TypeError, AttributeError)):
        await selected.ask("keyword")
    (ledger,) = await selected.rig.rows()
    assert ledger.operation == "embedding" and ledger.state == "settled"
    assert len(selected.wire.requests) == 1


@pytest.mark.asyncio
async def test_refusal_is_nonqualifying_without_appendix(selected):
    await selected.add()
    selected.response_payload = body(
        model=selected.ai.model,
        output=[
            {
                "type": "message",
                "id": "m",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "refusal", "refusal": "No"}],
            }
        ],
    )
    result = await selected.ask()
    assert result.code == "provider_failed"


@pytest.mark.asyncio
async def test_original_hash_differs_from_exact_redacted_bounded_context(selected):
    raw = "private token=unsafe " + "long fact " * 4000
    row = await selected.add(raw)
    result = await selected.ask()
    (ref,) = result.observation.cited_refs
    context = (
        selected.wire.requests[-1]["json"]["input"]
        .split("NGUỒN:\n", 1)[1]
        .rsplit("\n\nCÂU HỎI:\n", 1)[0]
    )
    assert "unsafe" not in context and "[REDACTED]" in context
    assert len(context) < len(raw)
    assert ref.content_hash == hashlib.sha256(row.text.encode()).hexdigest()
    assert ref.context_hash == hashlib.sha256(context.encode()).hexdigest()
    assert ref.context_hash != ref.content_hash


@pytest.mark.asyncio
async def test_source_revocation_during_embedding_response_stops_chat(selected, monkeypatch):
    await selected.add()
    real_embed = selected.ai.embed

    async def revoke_after_response(session, text, **kwargs):
        vector = await real_embed(session, text, **kwargs)
        async with selected.rig.factory() as current, current.begin():
            await current.execute(
                update(TelegramChatPolicy)
                .where(TelegramChatPolicy.chat_id == CHAT)
                .values(allowed=False, authorization_epoch=selected.binding.source_epoch + 1)
            )
        return vector

    monkeypatch.setattr(selected.ai, "embed", revoke_after_response)
    with pytest.raises(AuthorizationRevoked):
        await selected.ask()
    assert len(selected.wire.requests) == 1


@pytest.mark.asyncio
async def test_default_question_time_inclusive_and_future_microsecond_excluded(
    selected, monkeypatch
):
    asked_at = datetime(2026, 10, 10, tzinfo=UTC)
    monkeypatch.setattr(rag, "rag_time_window", lambda: (asked_at - timedelta(days=7), asked_at))
    await selected.add("keyword", message_id=1, when=asked_at)
    await selected.add("keyword", message_id=2, when=asked_at + timedelta(microseconds=1))
    result = await selected.ask("keyword")
    (ref,) = result.observation.cited_refs
    assert ref.message_id == 1 and ref.keyword_used and ref.semantic_used
    assert result.observation.before == asked_at


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openrouter"])
async def test_independent_cloud_query_embedding_uses_bound_profile_and_real_ledger(
    selected, tmp_path, provider
):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        profile_id=PROFILE,
        embedding_provider=provider,
        embedding_version="cloud-v1",
        cloud_embedding_dimension=3,
        cloud_consent=True,
    )
    embedding = make_embedding_engine(settings, selected.rig.budget, "synthetic-fixture-key")
    await embedding.client._client.aclose()
    embedding.client._client = httpx.AsyncClient(transport=selected.wire.transport)
    selected.rig.engines.append(embedding)
    profile = settings.embedding_profile
    selected.vectors.close()
    selected.vectors = LocalVectorStore(tmp_path / "cloud-vectors", 3, profile=profile)
    selected.service.vectors = selected.vectors
    selected.service.embedding_ai = embedding
    selected.binding = dataclasses.replace(
        selected.binding,
        embedding_provider=profile.provider,
        embedding_endpoint_id=profile.endpoint_id,
        embedding_model=profile.model,
        embedding_model_version=profile.embedding_version,
        embedding_store_id=profile.store_id,
    )
    async with selected.rig.factory() as session:
        await session.execute(update(VectorStore).values(role="archived", state="inactive"))
        session.add(
            VectorStore(
                store_id=profile.store_id,
                path=str(tmp_path / "cloud-vectors"),
                collection=selected.vectors.COLLECTION,
                provider=profile.provider,
                endpoint_id=profile.endpoint_id,
                model=profile.model,
                embedding_version=profile.embedding_version,
                dimension=profile.dimension,
                role="semantic_active",
                state="active",
            )
        )
        await session.execute(
            update(TelegramChatPolicy)
            .where(TelegramChatPolicy.chat_id == CHAT)
            .values(ai_mode="local_first")
        )
        await session.commit()
    await selected.add()
    result = await selected.ask()
    observation = result.observation
    ledger = {row.request_id: row for row in await selected.rig.rows()}
    query = ledger[observation.query_embedding_request_id]
    assert (query.provider, query.model, query.route, query.is_local) == (
        provider,
        "text-embedding-3-small" if provider == "openai" else "openai/text-embedding-3-small",
        "cloud_embedding",
        False,
    )
    assert query.actual_cost_usd > 0
    assert observation.execution.provider == "ollama"
    assert selected.wire.requests[0]["json"]["dimensions"] == 3


@pytest.mark.asyncio
async def test_query_secret_detected_before_model_or_embedding_transport(selected):
    await selected.add()
    with pytest.raises(AiPolicyError):
        await selected.ask("token=private")
    assert not selected.wire.requests


@pytest.mark.asyncio
async def test_unconsented_local_failure_never_attempts_cloud(selected):
    await selected.add()
    await configure_route(selected, "local_first", consent=False)
    selected.answer_unavailable = True
    with pytest.raises(RuntimeError):
        await selected.ask()
    assert len(selected.rig.wires[1].requests) == 0
