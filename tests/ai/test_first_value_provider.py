"""U03 Task2 provider slice: observed first-answer execution (tests first).

Real pieces: installed AsyncOpenAI (Responses API) over httpx.MockTransport, a
disposable file-backed SQLite ledger and the real BudgetService/AiEngine/AiRouter.
Only the HTTP wire and deliberate ledger faults are synthetic. A synthetic
Responses transport does not prove a real Ollama build supports this wire path.
New observed types/methods are imported inside tests so a missing implementation
is a behavioural failure rather than a collection error.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import inspect
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from openai import AsyncOpenAI
from sqlalchemy import delete, event, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tg_assistant.ai.budget import PRICING_VERSION, BudgetService, pricing_for, reservation_model
from tg_assistant.ai.engine import AiEngine, AiPolicyError, AiUnavailableError, AiUncertainError
from tg_assistant.ai.router import AiRoute, AiRouter
from tg_assistant.db.base import Base, configure_sqlite
from tg_assistant.services.provider_connections import ModelSelection
from tg_assistant.services.revocation import AuthorizationRevoked

PROFILE = "profile-u03"
CHAT = -1001234567890
API_KEY = "sk-test-u03-never-leak"
QUESTION_MARK = "QUESTION-MARKER-7f3a"
CONTEXT_MARK = "CONTEXT-MARKER-91bc"
QUESTION = f"Câu hỏi riêng tư {QUESTION_MARK}?"
CONTEXT = f"[S1] Nguồn riêng tư {CONTEXT_MARK}."
LOCAL_URL = "http://127.0.0.1:11434/v1"
CLOUD_URL = "https://api.openai.com/v1"
LOCAL_MODEL = "qwen3:u03-test"
CLOUD_MODEL = "gpt-5.6-terra"
ROUTE = "first_value_route"
FEATURE = "first_value_test"
USAGE = {
    "input_tokens": 1000,
    "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 100},
    "output_tokens": 50,
    "output_tokens_details": {"reasoning_tokens": 0},
    "total_tokens": 1050,
}
@pytest.mark.parametrize("owner", [AiEngine, AiRouter])
def test_owned_observed_answer_method_exists(owner):
    assert callable(getattr(owner, "answer_observed", None)), "owned observed answer is missing"


def candidate_for(engine):
    """Candidate identity per the root ruling; the fingerprint format is a test assumption."""
    from tg_assistant.ai.observations import ChatModelCandidate

    selection = ModelSelection.parse(
        engine.provider,
        {
            "service": "chat_ai",
            "model": engine.model,
            "endpoint": str(engine.client.base_url),
            "cloud_consent": engine.cloud_consent,
        },
    )
    seed = f"{engine.provider}|{selection.endpoint_id}|{engine.model}"
    return ChatModelCandidate(
        provider=engine.provider,
        endpoint_id=selection.endpoint_id,
        requested_model=engine.model,
        candidate_fingerprint="candidate-v1:" + hashlib.sha256(seed.encode()).hexdigest(),
    )


def text_part(text):
    return {"type": "output_text", "text": text, "annotations": []}


def message(parts, status="completed"):
    return {"type": "message", "id": "msg_1", "role": "assistant", "status": status, "content": parts}


def body(text="Trả lời thử nghiệm.", *, model, status="completed", usage=USAGE, output=None, **extra):
    data = {
        "id": "resp_test",
        "object": "response",
        "created_at": 1.0,
        "model": model,
        "status": status,
        "output": [message([text_part(text)])] if output is None else output,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        **extra,
    }
    if status is None:
        del data["status"]
    if usage is not None:
        data["usage"] = usage
    return data


def ok(text="Trả lời thử nghiệm.", *, reported=None, **kwargs):
    async def respond(request, wire):
        requested = json.loads(request.content)["model"]
        return httpx.Response(200, json=body(text, model=reported or requested, **kwargs))

    return respond


def unreachable():
    async def respond(request, wire):
        raise httpx.ConnectError("refused", request=request)

    return respond


class Wire:
    """One synthetic Responses endpoint that records every wire request."""

    def __init__(self, respond=None):
        self.requests = []
        self.respond = respond or ok()
        self.transport = httpx.MockTransport(self._handle)

    async def _handle(self, request):
        self.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "json": json.loads(request.content or b"{}"),
                "auth": request.headers.get("authorization"),
            }
        )
        return await self.respond(request, self)


class TamperBudget(BudgetService):
    """Real ledger; after a genuine settlement optionally corrupt that row (None = delete)."""

    tamper = None

    async def reconcile(self, session, request_id, **kwargs):
        await super().reconcile(session, request_id, **kwargs)
        if self.tamper is None:
            return
        Reservation = reservation_model()
        statement = (
            delete(Reservation)
            if self.tamper == {}
            else update(Reservation).values(**self.tamper)
        )
        async with self._sessions(session)() as current, current.begin():
            await current.execute(statement.where(Reservation.request_id == request_id))


class Rig:
    def __init__(self, factory, budget):
        self.factory, self.budget = factory, budget
        self.wires, self.engines = [], []

    def wire(self, respond=None):
        wire = Wire(respond)
        self.wires.append(wire)
        return wire

    def engine(self, provider, wire, *, consent=True, **kwargs):
        local = provider == "ollama"
        url = LOCAL_URL if local else CLOUD_URL
        engine = AiEngine(
            api_key=None if local else API_KEY,
            budget=self.budget,
            provider=provider,
            base_url=url,
            model=LOCAL_MODEL if local else CLOUD_MODEL,
            embedding_model="embedding-u03-test",
            max_output_tokens=200,
            cloud_consent=consent,
            **kwargs,
        )
        engine.client = AsyncOpenAI(
            api_key="ollama" if local else API_KEY,
            base_url=url,
            http_client=httpx.AsyncClient(transport=wire.transport),
            max_retries=0,
            timeout=5.0,
        )
        self.engines.append(engine)
        return engine

    def router(self, local, cloud, *, consent=True, default="openai"):
        return AiRouter(
            {"ollama": local, "openai": cloud}, default_provider=default, cloud_consent=consent
        )

    async def rows(self):
        async with self.factory() as session:
            found = await session.scalars(select(reservation_model()))
            return list(found.all())

    async def states(self):
        return sorted(row.state for row in await self.rows())

    @property
    def http_total(self):
        return sum(len(wire.requests) for wire in self.wires)


@pytest_asyncio.fixture
async def rig(tmp_path):
    database = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'u03.db').as_posix()}")
    event.listen(database.sync_engine, "connect", configure_sqlite)
    async with database.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    value = Rig(
        async_sessionmaker(database, expire_on_commit=False),
        TamperBudget(10.0, 100.0, profile_id=PROFILE),
    )
    yield value
    for engine in value.engines:
        await engine.close()
    await database.dispose()


class Probe:
    """Mandatory pre_submit callback that records the real ledger/wire state at each call."""

    def __init__(self, rig, *, fail_on=None, exc=AiPolicyError, after=None):
        self.rig, self.fail_on, self.exc, self.after = rig, fail_on, exc, after
        self.calls, self.states, self.http = 0, [], []

    async def __call__(self):
        self.calls += 1
        self.http.append(self.rig.http_total)
        self.states.append(await self.rig.states())
        if self.after:
            self.after(self.calls)
        if self.calls == self.fail_on:
            raise self.exc("withdrawn")


async def observe(rig, engine, *, probe=None, candidate=None, question=QUESTION, chat_id=CHAT, **kwargs):
    kwargs.setdefault("source_modes", ["cloud_first"])
    kwargs.setdefault("fallback_used", False)
    async with rig.factory() as session:
        return await engine.answer_observed(
            session,
            question,
            [CONTEXT],
            candidate=candidate or candidate_for(engine),
            feature=FEATURE,
            route=ROUTE,
            chat_id=chat_id,
            pre_submit=probe if probe is not None else Probe(rig),
            **kwargs,
        )


async def observe_route(rig, router, route, candidates, *, probe=None, **kwargs):
    kwargs.setdefault("source_modes", ["cloud_first"])
    async with rig.factory() as session:
        return await router.answer_observed(
            session,
            QUESTION,
            [CONTEXT],
            route=route,
            candidates=candidates,
            feature=FEATURE,
            query_route=ROUTE,
            chat_id=CHAT,
            pre_submit=probe if probe is not None else Probe(rig),
            **kwargs,
        )


def as_utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def assert_matches_row(execution, row, *, local):
    assert row.state == "settled" and row.submitted_at is not None and row.settled_at is not None
    assert execution.request_id == row.request_id
    assert execution.profile_id == row.profile_id == PROFILE
    assert execution.provider == row.provider
    assert execution.requested_model == row.model
    assert execution.route == row.route == ROUTE
    assert execution.fallback_used is row.fallback_used
    assert row.is_local is local and row.operation == "answer" and row.feature == FEATURE
    assert row.chat_id == CHAT
    assert execution.pricing_version == row.pricing_version
    assert execution.input_tokens == row.actual_input_tokens
    assert execution.output_tokens == row.actual_output_tokens
    assert execution.cached_tokens == row.cached_tokens
    assert execution.cache_write_tokens == row.cache_write_tokens
    for count in (
        execution.input_tokens,
        execution.output_tokens,
        execution.cached_tokens,
        execution.cache_write_tokens,
    ):
        assert type(count) is int and count >= 0
    assert type(execution.cost_usd) is Decimal and execution.cost_usd.is_finite()
    assert execution.cost_usd >= 0 and execution.cost_usd == Decimal(row.actual_cost_usd)
    assert execution.settled_at.utcoffset() == timedelta(0)
    assert execution.settled_at == as_utc(row.settled_at)


def assert_no_private_content(*carriers):
    for carrier in carriers:
        text = repr(carrier) + repr(dataclasses.asdict(carrier))
        for forbidden in (QUESTION_MARK, CONTEXT_MARK, API_KEY, "://", "127.0.0.1", "openai.com"):
            assert forbidden not in text


# --- exact same-call evidence -------------------------------------------------------------


@pytest.mark.asyncio
async def test_cloud_observed_returns_exact_same_call_settlement(rig):
    from tg_assistant.ai.observations import (
        ProviderAnswerResult,
        SettledProviderExecution,
        VerifiedChatModel,
    )

    wire = rig.wire(ok("Câu trả lời đã kiểm chứng.", reported="gpt-5.6-terra-2026-10-01"))
    engine = rig.engine("openai", wire)
    assert isinstance(engine.client, AsyncOpenAI)
    candidate = candidate_for(engine)
    probe = Probe(rig)

    result = await observe(rig, engine, probe=probe, candidate=candidate, fallback_used=False)

    assert isinstance(result, ProviderAnswerResult) and result.answer == "Câu trả lời đã kiểm chứng."
    assert isinstance(result.execution, SettledProviderExecution)
    assert isinstance(result.capability, VerifiedChatModel)
    [row] = await rig.rows()
    assert_matches_row(result.execution, row, local=False)
    execution = result.execution
    assert execution.requested_model == CLOUD_MODEL
    assert execution.reported_model is None  # untrusted provider text is never retained
    assert execution.pricing_version == PRICING_VERSION
    expected = pricing_for("openai", CLOUD_MODEL).cost(1000, 50, cached_tokens=200, cache_write_tokens=100)
    assert (execution.input_tokens, execution.output_tokens) == (1000, 50)
    assert (execution.cached_tokens, execution.cache_write_tokens) == (200, 100)
    assert execution.cost_usd == expected == Decimal("0.00229")
    capability = result.capability
    assert (capability.provider, capability.endpoint_id) == (candidate.provider, candidate.endpoint_id)
    assert capability.requested_model == candidate.requested_model
    assert execution.capability_fingerprint == capability.capability_fingerprint
    assert capability.capability_fingerprint and capability.capability_fingerprint != (
        candidate.candidate_fingerprint
    )
    assert_no_private_content(candidate, execution, capability)
    with pytest.raises(AttributeError):
        execution.cost_usd = Decimal(0)
    # Exactly one Responses call; no /models or hidden probe request.
    assert [(r["method"], r["path"]) for r in wire.requests] == [("POST", "/v1/responses")]
    sent = wire.requests[0]["json"]
    assert sent["model"] == CLOUD_MODEL and sent["store"] is False and QUESTION_MARK in sent["input"]
    assert wire.requests[0]["auth"] == f"Bearer {API_KEY}"


@pytest.mark.asyncio
async def test_local_observed_settles_zero_cost_with_local_pricing(rig):
    wire = rig.wire(ok("Trả lời cục bộ."))
    engine = rig.engine("ollama", wire)
    result = await observe(rig, engine, source_modes=None)
    [row] = await rig.rows()
    assert_matches_row(result.execution, row, local=True)
    assert result.execution.provider == "ollama" and result.execution.cost_usd == Decimal(0)
    assert result.execution.pricing_version == "local-zero-v1"
    assert result.execution.reported_model is None
    assert result.execution.requested_model == LOCAL_MODEL
    assert "store" not in wire.requests[0]["json"] and len(wire.requests) == 1


@pytest.mark.asyncio
async def test_capability_fingerprint_is_per_call_not_candidate_identity(rig):
    engine = rig.engine("openai", rig.wire())
    candidate = candidate_for(engine)
    first = await observe(rig, engine, candidate=candidate)
    second = await observe(rig, engine, candidate=candidate)
    assert first.execution.request_id != second.execution.request_id
    assert first.capability.capability_fingerprint != second.capability.capability_fingerprint
    assert candidate.candidate_fingerprint not in {
        first.capability.capability_fingerprint,
        second.capability.capability_fingerprint,
    }


@pytest.mark.asyncio
async def test_ordinary_answer_string_interface_is_unchanged(rig):
    engine = rig.engine("openai", rig.wire(ok("Chuỗi thường.")))
    async with rig.factory() as session:
        text = await engine.answer(
            session, QUESTION, [CONTEXT], source_modes=["cloud_first"], chat_id=CHAT
        )
    assert text == "Chuỗi thường."
    assert await rig.states() == ["settled"]


def test_profile_authority_comes_from_budget_not_caller():
    for owner in (AiEngine, AiRouter):
        parameters = inspect.signature(owner.answer_observed).parameters
        assert not {"profile", "profile_id", "budget", "request_id"} & set(parameters)


# --- mandatory pre_submit and its boundaries ----------------------------------------------


@pytest.mark.asyncio
async def test_pre_submit_runs_at_reserved_and_submitted_boundaries_before_http(rig):
    probe = Probe(rig)
    await observe(rig, rig.engine("openai", rig.wire()), probe=probe)
    assert probe.calls >= 2
    assert probe.states[:2] == [["reserved"], ["submitted"]]
    assert probe.http[:2] == [0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "openai"])
async def test_missing_pre_submit_rejects_before_budget_or_provider(rig, provider):
    wire = rig.wire()
    engine = rig.engine(provider, wire)
    candidate = candidate_for(engine)
    async with rig.factory() as session:
        with pytest.raises((TypeError, ValueError, AiPolicyError)):
            await engine.answer_observed(
                session,
                QUESTION,
                [CONTEXT],
                candidate=candidate,
                feature=FEATURE,
                route=ROUTE,
                chat_id=CHAT,
                source_modes=["cloud_first"],
                pre_submit=None,
            )
    assert wire.requests == [] and await rig.rows() == []


@pytest.mark.asyncio
async def test_router_missing_pre_submit_touches_no_engine(rig):
    local, cloud = rig.wire(), rig.wire()
    router = rig.router(rig.engine("ollama", local), rig.engine("openai", cloud))
    candidates = tuple(candidate_for(e) for e in router.engines.values())
    with pytest.raises((TypeError, ValueError, AiPolicyError)):
        async with rig.factory() as session:
            await router.answer_observed(
                session,
                QUESTION,
                [CONTEXT],
                route=AiRoute("local_first", "openai", True),
                candidates=candidates,
                feature=FEATURE,
                query_route=ROUTE,
                chat_id=CHAT,
                source_modes=["cloud_first"],
                pre_submit=None,
            )
    assert rig.http_total == 0 and await rig.rows() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "openai"])
async def test_withdrawal_at_first_boundary_releases_without_http_or_fallback(rig, provider):
    first, second = rig.wire(), rig.wire()
    local, cloud = (rig.engine("ollama", first), rig.engine("openai", second))
    router = rig.router(local, cloud)
    mode = "local_first" if provider == "ollama" else "cloud_first"
    probe = Probe(rig, fail_on=1)
    with pytest.raises(AiPolicyError):
        await observe_route(
            rig,
            router,
            AiRoute(mode, "openai", True),
            (candidate_for(local), candidate_for(cloud)),
            probe=probe,
        )
    assert rig.http_total == 0 and probe.calls == 1
    assert await rig.states() == ["released"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "openai"])
async def test_withdrawal_after_mark_submitted_blocks_http_and_all_fallback(rig, provider):
    local, cloud = rig.engine("ollama", rig.wire()), rig.engine("openai", rig.wire())
    router = rig.router(local, cloud)
    mode = "local_first" if provider == "ollama" else "cloud_first"
    probe = Probe(rig, fail_on=2)
    with pytest.raises(Exception) as caught:
        await observe_route(
            rig,
            router,
            AiRoute(mode, "openai", True),
            (candidate_for(local), candidate_for(cloud)),
            probe=probe,
        )
    # The policy cause survives; a submitted local engine must not look "unavailable".
    assert not isinstance(caught.value, AiUnavailableError)
    assert isinstance(caught.value, (AiPolicyError, AiUncertainError))
    assert rig.http_total == 0 and probe.calls == 2
    assert await rig.states() == ["uncertain"]


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["local_enabled", "cloud_consent"])
async def test_consent_or_enabled_withdrawn_after_submit_blocks_fallback(rig, target):
    state = {"on": True}
    local_wire, cloud_wire = rig.wire(), rig.wire()
    if target == "local_enabled":
        local = rig.engine("ollama", local_wire, enabled_check=lambda: state["on"])
        cloud = rig.engine("openai", cloud_wire)
        mode = "local_first"
    else:
        local = rig.engine("ollama", local_wire)
        cloud = rig.engine("openai", cloud_wire, consent_check=lambda: state["on"])
        mode = "cloud_first"
    # A callback-only marker cannot see this: pre_submit passes, then _current_consent fails.
    probe = Probe(rig, after=lambda calls: state.update(on=False) if calls == 1 else None)
    with pytest.raises(Exception) as caught:
        await observe_route(
            rig,
            rig.router(local, cloud),
            AiRoute(mode, "openai", True),
            (candidate_for(local), candidate_for(cloud)),
            probe=probe,
        )
    assert not isinstance(caught.value, AiUnavailableError)
    assert isinstance(caught.value, (AiPolicyError, AiUncertainError))
    assert rig.http_total == 0
    assert await rig.states() == ["uncertain"]


@pytest.mark.asyncio
async def test_admission_withdrawn_after_awaited_execution_denies_result(rig):
    wire = rig.wire()
    engine = rig.engine("openai", wire)
    probe = Probe(rig, fail_on=3)
    with pytest.raises(AiPolicyError):
        await observe(rig, engine, probe=probe)
    assert probe.calls == 3 and len(wire.requests) == 1  # no replay


@pytest.mark.asyncio
@pytest.mark.parametrize("swap", ["model", "endpoint"])
async def test_request_identity_change_during_execution_denies_result(rig, swap):
    engine = None

    async def respond(request, wire):
        if swap == "model":
            engine.model = "gpt-5.6-luna"
        else:
            engine.client = AsyncOpenAI(
                api_key=API_KEY, base_url="https://api.openai.com/v2", max_retries=0
            )
        return httpx.Response(200, json=body(model=CLOUD_MODEL))

    wire = rig.wire(respond)
    engine = rig.engine("openai", wire)
    with pytest.raises(AiPolicyError):
        await observe(rig, engine, candidate=candidate_for(engine))
    assert len(wire.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["model", "provider", "endpoint_id"])
async def test_candidate_mismatch_rejected_before_any_work(rig, change):
    wire = rig.wire()
    engine = rig.engine("openai", wire)
    candidate = candidate_for(engine)
    wrong = {"model": "requested_model", "provider": "provider", "endpoint_id": "endpoint_id"}[change]
    forged = dataclasses.replace(candidate, **{wrong: "ollama" if change == "provider" else "other"})
    with pytest.raises(AiPolicyError):
        await observe(rig, engine, candidate=forged)
    async with rig.factory() as session:
        with pytest.raises(AiPolicyError):  # observe() would substitute a valid candidate for None
            await engine.answer_observed(
                session,
                QUESTION,
                [CONTEXT],
                candidate=None,
                feature=FEATURE,
                route=ROUTE,
                chat_id=CHAT,
                source_modes=["cloud_first"],
                pre_submit=Probe(rig),
            )
    assert wire.requests == [] and await rig.rows() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("modes", [["local_only"], ["off"], None])
async def test_cloud_source_policy_denied_before_reservation_or_http(rig, modes):
    wire = rig.wire()
    engine = rig.engine("openai", wire)
    with pytest.raises(AiPolicyError):
        await observe(rig, engine, source_modes=modes)
    assert wire.requests == [] and await rig.rows() == []


@pytest.mark.asyncio
async def test_cloud_without_consent_cannot_issue_result(rig):
    wire = rig.wire()
    engine = rig.engine("openai", wire, consent=False)
    with pytest.raises(AiPolicyError):
        await observe(rig, engine)
    assert wire.requests == [] and await rig.rows() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "openai"])
async def test_legacy_budget_cannot_qualify(rig, provider):
    class LegacyBudget:
        recorded = 0

        async def ensure_token_limits(self, *args, **kwargs):
            return None

        async def record(self, *args, **kwargs):
            self.recorded += 1

    wire = rig.wire()
    engine = rig.engine(provider, wire)
    engine.budget = legacy = LegacyBudget()
    with pytest.raises(AiPolicyError):
        await observe(rig, engine, candidate=candidate_for(engine))
    assert wire.requests == [] and legacy.recorded == 0


# --- nonqualifying output, ledger faults --------------------------------------------------

REFUSAL = {"type": "refusal", "refusal": "Tôi không thể giúp."}
CALL = {
    "type": "function_call",
    "id": "fc_1",
    "call_id": "call_1",
    "name": "lookup",
    "arguments": "{}",
    "status": "completed",
}
NONQUALIFYING = {
    "completed_with_error": {"error": {"code": "server_error", "message": "x"}},
    "completed_with_incomplete_details": {"incomplete_details": {"reason": "max_output_tokens"}},
    "user_message": {"output": [{**message([text_part("answer")]), "role": "user"}]},
    "empty": {"text": ""},
    "whitespace": {"text": " \n\t "},
    "refusal_only": {"output": [message([REFUSAL])]},
    "text_plus_refusal": {"output": [message([text_part("một phần"), REFUSAL])]},
    "no_output": {"output": []},
    "tool_call_only": {"output": [CALL]},
    "message_incomplete": {"output": [message([text_part("dở")], status="incomplete")]},
    "incomplete": {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
    "failed": {"status": "failed", "error": {"code": "server_error", "message": "x"}},
    "in_progress": {"status": "in_progress"},
    "cancelled": {"status": "cancelled"},
    "status_missing": {"status": None},
    "status_unknown": {"status": "paused-by-proxy"},
    "usage_missing": {"usage": None},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "openai"])
@pytest.mark.parametrize("case", sorted(NONQUALIFYING))
async def test_unsupported_empty_refused_or_incomplete_output_does_not_qualify(rig, provider, case):
    wire = rig.wire(ok(**NONQUALIFYING[case]))
    engine = rig.engine(provider, wire)
    with pytest.raises(Exception) as caught:
        await observe(rig, engine)
    assert len(wire.requests) == 1  # never an automatic retry
    for leaked in (QUESTION_MARK, CONTEXT_MARK, API_KEY):
        assert leaked not in str(caught.value) + repr(caught.value)
    assert "released" not in await rig.states()


LEDGER_FAULTS = {
    "row_missing": {},
    "not_settled": {"state": "submitted"},
    "no_settled_at": {"settled_at": None},
    "settled_before_request": {"settled_at": datetime(2020, 1, 1, tzinfo=UTC)},
    "stale_occurred_at": {"occurred_at": datetime.now(UTC) - timedelta(days=3)},
    "negative_cost": {"actual_cost_usd": Decimal("-0.01")},
    "token_mismatch": {"actual_input_tokens": 999},
    "blank_pricing_version": {"pricing_version": ""},
    "other_profile": {"profile_id": "profile-other"},
    "other_provider": {"provider": "openrouter"},
    "other_model": {"model": "gpt-5.6-luna"},
    "other_operation": {"operation": "embedding"},
    "other_feature": {"feature": "other_feature"},
    "other_route": {"route": "other_route"},
    "other_chat": {"chat_id": 999},
    "flipped_fallback": {"fallback_used": True},
    "flipped_locality": {"is_local": True},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", sorted(LEDGER_FAULTS))
async def test_missing_or_inconsistent_ledger_denies_result(rig, fault):
    wire = rig.wire()
    engine = rig.engine("openai", wire)
    rig.budget.tamper = LEDGER_FAULTS[fault]
    with pytest.raises(AiUncertainError):
        await observe(rig, engine)
    assert len(wire.requests) == 1


@pytest.mark.asyncio
async def test_unsettled_call_cannot_borrow_prior_identical_settled_row(rig):
    wire = rig.wire()
    engine = rig.engine("openai", wire)
    candidate = candidate_for(engine)
    first = await observe(rig, engine, candidate=candidate)
    rig.budget.tamper = {"state": "submitted"}
    with pytest.raises(AiUncertainError):
        await observe(rig, engine, candidate=candidate)
    assert len(wire.requests) == 2
    rows = {row.request_id: row for row in await rig.rows()}
    assert rows[first.execution.request_id].state == "settled" and len(rows) == 2


# --- fallback, modes, cancellation, concurrency -------------------------------------------


@pytest.mark.asyncio
async def test_unavailable_local_then_consented_cloud_reports_winning_provenance(rig):
    local_wire, cloud_wire = rig.wire(unreachable()), rig.wire()
    local, cloud = rig.engine("ollama", local_wire), rig.engine("openai", cloud_wire)
    candidates = (candidate_for(local), candidate_for(cloud))
    probe = Probe(rig)

    result = await observe_route(
        rig, rig.router(local, cloud), AiRoute("local_first", "openai", True), candidates, probe=probe
    )

    rows = {row.provider: row for row in await rig.rows()}
    assert set(rows) == {"ollama", "openai"} and rows["ollama"].state != "settled"
    assert rows["ollama"].fallback_used is False
    winner = result.execution
    assert winner.provider == "openai" and winner.fallback_used is True
    assert winner.request_id == rows["openai"].request_id != rows["ollama"].request_id
    assert_matches_row(winner, rows["openai"], local=False)
    assert (result.capability.provider, result.capability.endpoint_id) == (
        candidates[1].provider,
        candidates[1].endpoint_id,
    )
    assert result.capability.requested_model == CLOUD_MODEL
    assert len(local_wire.requests) == len(cloud_wire.requests) == 1


EXPECTED_PLANS = {
    True: {
        "inherit": ["openai"],
        "local_only": ["ollama"],
        "local_first": ["ollama", "openai"],
        "cloud_only": ["openai"],
        "cloud_first": ["openai", "ollama"],
        "off": [],
    },
    False: {
        "inherit": [],
        "local_only": ["ollama"],
        "local_first": ["ollama"],
        "cloud_only": [],
        "cloud_first": ["ollama"],
        "off": [],
    },
}


@pytest.mark.asyncio
@pytest.mark.parametrize("consent", [True, False])
@pytest.mark.parametrize("mode", sorted(EXPECTED_PLANS[True]))
async def test_six_modes_keep_current_plans_and_first_planned_provider_wins(rig, mode, consent):
    local_wire, cloud_wire = rig.wire(), rig.wire()
    local, cloud = rig.engine("ollama", local_wire), rig.engine("openai", cloud_wire)
    router = rig.router(local, cloud, consent=consent)
    route = AiRoute(mode, "openai", True)
    plan = EXPECTED_PLANS[consent][mode]
    assert router.plan(route) == plan
    candidates = tuple(candidate_for(e) for e in ((local, cloud) if consent else (local,)))
    if not plan:
        with pytest.raises(AiPolicyError):
            await observe_route(rig, router, route, candidates)
        assert rig.http_total == 0 and await rig.rows() == []
        return
    result = await observe_route(rig, router, route, candidates)
    assert result.execution.provider == plan[0] and result.execution.fallback_used is False
    assert rig.http_total == 1
    assert len((local_wire if plan[0] == "ollama" else cloud_wire).requests) == 1


@pytest.mark.asyncio
async def test_local_only_never_falls_back_or_accepts_cloud_candidate(rig):
    local_wire, cloud_wire = rig.wire(unreachable()), rig.wire()
    local, cloud = rig.engine("ollama", local_wire), rig.engine("openai", cloud_wire)
    router = rig.router(local, cloud)
    with pytest.raises(RuntimeError, match="AI provider unavailable"):
        await observe_route(
            rig, router, AiRoute("local_only", "openai", True), (candidate_for(local), candidate_for(cloud))
        )
    assert cloud_wire.requests == [] and len(local_wire.requests) == 1
    with pytest.raises(AiPolicyError):
        await observe_route(rig, router, AiRoute("local_only", "openai", True), (candidate_for(cloud),))
    assert cloud_wire.requests == [] and len(local_wire.requests) == 1


@pytest.mark.asyncio
async def test_no_consent_or_missing_candidate_prevents_cloud_fallback(rig):
    for consent, with_cloud_candidate in ((False, True), (True, False)):
        local_wire, cloud_wire = rig.wire(unreachable()), rig.wire()
        local, cloud = rig.engine("ollama", local_wire), rig.engine("openai", cloud_wire)
        candidates = (candidate_for(local),) + ((candidate_for(cloud),) if with_cloud_candidate else ())
        expected = AiPolicyError if consent else RuntimeError
        with pytest.raises(expected) as caught:
            await observe_route(
                rig, rig.router(local, cloud, consent=consent), AiRoute("local_first", "openai", True), candidates
            )
        assert type(caught.value) is expected
        assert cloud_wire.requests == []


@pytest.mark.asyncio
async def test_source_policy_denial_on_fallback_cloud_stops_without_http(rig):
    local_wire, cloud_wire = rig.wire(unreachable()), rig.wire()
    local, cloud = rig.engine("ollama", local_wire), rig.engine("openai", cloud_wire)
    with pytest.raises(AiPolicyError):
        await observe_route(
            rig,
            rig.router(local, cloud),
            AiRoute("local_first", "openai", True),
            (candidate_for(local), candidate_for(cloud)),
            source_modes=["local_only"],
        )
    assert cloud_wire.requests == []


@pytest.mark.asyncio
async def test_uncertain_submitted_cloud_outcome_is_never_retried_or_replaced(rig):
    async def server_error(request, wire):
        return httpx.Response(500, json={"error": {"message": "boom"}})

    cloud_wire, local_wire = rig.wire(server_error), rig.wire()
    cloud, local = rig.engine("openai", cloud_wire), rig.engine("ollama", local_wire)
    with pytest.raises(AiUncertainError):
        await observe_route(
            rig,
            rig.router(local, cloud),
            AiRoute("cloud_first", "openai", True),
            (candidate_for(cloud), candidate_for(local)),
        )
    assert len(cloud_wire.requests) == 1 and local_wire.requests == []
    assert await rig.states() == ["uncertain"]


@pytest.mark.asyncio
async def test_cancellation_is_not_unavailable_and_never_replayed(rig):
    entered = asyncio.Event()

    async def hang(request, wire):
        entered.set()
        await asyncio.Event().wait()

    local_wire, cloud_wire = rig.wire(hang), rig.wire()
    local, cloud = rig.engine("ollama", local_wire), rig.engine("openai", cloud_wire)
    task = asyncio.create_task(
        observe_route(
            rig,
            rig.router(local, cloud),
            AiRoute("local_first", "openai", True),
            (candidate_for(local), candidate_for(cloud)),
        )
    )
    await asyncio.wait_for(entered.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(local_wire.requests) == 1 and cloud_wire.requests == []
    assert await rig.states() == ["uncertain"]


@pytest.mark.asyncio
async def test_concurrent_distinct_calls_never_exchange_result_or_ledger(rig):
    b_done = asyncio.Event()
    usage_b = {**USAGE, "input_tokens": 2000, "output_tokens": 20, "total_tokens": 2020}

    async def respond(request, wire):
        text = json.loads(request.content)["input"]
        if "TAG-A" in text:
            await asyncio.wait_for(b_done.wait(), 10)  # A finishes after B's whole HTTP leg
            return httpx.Response(200, json=body("answer-A", model=CLOUD_MODEL))
        response = httpx.Response(200, json=body("answer-B", model=CLOUD_MODEL, usage=usage_b))
        b_done.set()
        return response

    wire = rig.wire(respond)
    engine = rig.engine("openai", wire)
    candidate = candidate_for(engine)
    first, second = await asyncio.wait_for(
        asyncio.gather(
            observe(rig, engine, candidate=candidate, question="TAG-A?", chat_id=-1001),
            observe(rig, engine, candidate=candidate, question="TAG-B?", chat_id=-1002),
        ),
        30,
    )
    assert (first.answer, second.answer) == ("answer-A", "answer-B")
    assert (first.execution.input_tokens, second.execution.input_tokens) == (1000, 2000)
    assert first.execution.request_id != second.execution.request_id
    assert first.capability.capability_fingerprint != second.capability.capability_fingerprint
    rows = {row.request_id: row for row in await rig.rows()}
    assert rows[first.execution.request_id].chat_id == -1001
    assert rows[second.execution.request_id].chat_id == -1002
    assert rows[first.execution.request_id].actual_input_tokens == 1000
    assert rows[second.execution.request_id].actual_input_tokens == 2000
    assert len(wire.requests) == 2


# --- review blockers: arbitrary admission failure and raw usage strictness ----------------


def exception_chain(error):
    seen = []
    while error is not None and error not in seen:
        seen.append(error)
        error = error.__cause__ or error.__context__
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [PermissionError, AuthorizationRevoked, RuntimeError])
@pytest.mark.parametrize("provider", ["ollama", "openai"])
async def test_arbitrary_second_admission_failure_keeps_cause_and_never_falls_back(
    rig, provider, error
):
    local_wire, cloud_wire = rig.wire(), rig.wire()
    local, cloud = rig.engine("ollama", local_wire), rig.engine("openai", cloud_wire)
    mode = "local_first" if provider == "ollama" else "cloud_first"
    probe = Probe(rig, fail_on=2, exc=error)

    with pytest.raises(Exception) as caught:
        await observe_route(
            rig,
            rig.router(local, cloud),
            AiRoute(mode, "openai", True),
            (candidate_for(local), candidate_for(cloud)),
            probe=probe,
        )

    # The original admission failure must stay observable; it must not become "unavailable".
    assert type(caught.value) is error and str(caught.value) == "withdrawn"  # the original
    assert probe.calls == 2
    assert rig.http_total == 0  # neither the submitted engine nor an alternate was called
    rows = await rig.rows()
    # Exactly the submitted reservation, left uncertain; no reservation for the other provider.
    assert [(row.provider, row.state) for row in rows] == [(provider, "uncertain")]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [PermissionError, AuthorizationRevoked])
async def test_ordinary_router_second_admission_denial_keeps_cause_without_fallback(rig, error):
    local_wire, cloud_wire = rig.wire(), rig.wire()
    local, cloud = rig.engine("ollama", local_wire), rig.engine("openai", cloud_wire)
    probe = Probe(rig, fail_on=2, exc=error)
    async with rig.factory() as session:
        with pytest.raises(error) as caught:
            await rig.router(local, cloud).answer(
                session,
                QUESTION,
                [CONTEXT],
                route=AiRoute("local_first", "openai", True),
                source_modes=["cloud_first"],
                chat_id=CHAT,
                pre_submit=probe,
            )
    assert type(caught.value) is error and rig.http_total == 0 and probe.calls == 2
    assert await rig.states() == ["uncertain"]


def usage_with(**changes):
    usage = {
        **USAGE,
        "input_tokens_details": dict(USAGE["input_tokens_details"]),
        "output_tokens_details": dict(USAGE["output_tokens_details"]),
    }
    for key, value in changes.items():
        if key in ("cached_tokens", "cache_write_tokens"):
            if value is MISSING:
                del usage["input_tokens_details"][key]
            else:
                usage["input_tokens_details"][key] = value
        elif value is MISSING:
            del usage[key]
        else:
            usage[key] = value
    return usage


MISSING = object()
MALFORMED_USAGE = {
    "input_missing": {"input_tokens": MISSING},
    "output_missing": {"output_tokens": MISSING},
    "input_fractional": {"input_tokens": 1000.9},
    "output_fractional": {"output_tokens": 50.9},
    "cached_fractional": {"cached_tokens": 200.5},
    "cache_write_fractional": {"cache_write_tokens": 100.5},
    "input_bool": {"input_tokens": True},
    "output_bool": {"output_tokens": True},
    "cached_bool": {"cached_tokens": True},
    "cache_write_bool": {"cache_write_tokens": True},
    "input_string": {"input_tokens": "1000"},
    "output_string": {"output_tokens": "50"},
    "cached_string": {"cached_tokens": "200"},
    "cache_write_string": {"cache_write_tokens": "100"},
    "input_integral_float": {"input_tokens": 1000.0},
    "output_integral_float": {"output_tokens": 50.0},
    "cached_integral_float": {"cached_tokens": 200.0},
    "cache_write_integral_float": {"cache_write_tokens": 100.0},
    "details_not_object": {"input_tokens_details": "x"},
    "details_null": {"input_tokens_details": None},
    "cached_null": {"cached_tokens": None},
    "cache_exceeds_input": {"cached_tokens": 950, "cache_write_tokens": 100},
    "input_negative": {"input_tokens": -1},
    "output_negative": {"output_tokens": -1},
    "cached_negative": {"cached_tokens": -1},
    "cache_write_negative": {"cache_write_tokens": -1},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "openai"])
@pytest.mark.parametrize("case", sorted(MALFORMED_USAGE))
async def test_malformed_raw_usage_does_not_qualify_settle_or_fall_back(rig, provider, case):
    # The real AsyncOpenAI parses this wire body first. Where the SDK itself coerces a value
    # (observed for bool/string in the root probe) this still asserts the product outcome
    # on the actual wire/ledger, so a RED there records an SDK-level limit, not a mirror.
    bad = rig.wire(ok(usage=usage_with(**MALFORMED_USAGE[case])))
    other = rig.wire()
    local, cloud = (
        (rig.engine("ollama", bad), rig.engine("openai", other))
        if provider == "ollama"
        else (rig.engine("ollama", other), rig.engine("openai", bad))
    )
    mode = "local_first" if provider == "ollama" else "cloud_first"

    with pytest.raises(Exception) as caught:
        await observe_route(
            rig,
            rig.router(local, cloud),
            AiRoute(mode, "openai", True),
            (candidate_for(local), candidate_for(cloud)),
        )

    assert len(bad.requests) == 1 and other.requests == []  # no retry, no fallback
    states = await rig.states()
    assert states and "settled" not in states and "released" not in states
    assert all(row.provider == provider for row in await rig.rows())
    for leaked in (QUESTION_MARK, CONTEXT_MARK, API_KEY):
        assert leaked not in str(caught.value) + repr(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "openai"])
async def test_exact_zero_raw_usage_counts_still_qualify(rig, provider):
    zero = {
        "input_tokens": 0,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        "output_tokens": 0,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 0,
    }
    engine = rig.engine(provider, rig.wire(ok(usage=zero)))
    result = await observe(rig, engine)
    [row] = await rig.rows()
    assert_matches_row(result.execution, row, local=provider == "ollama")
    assert (
        result.execution.input_tokens,
        result.execution.output_tokens,
        result.execution.cached_tokens,
        result.execution.cache_write_tokens,
    ) == (0, 0, 0, 0)
    assert result.execution.cost_usd == Decimal(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "openai"])
async def test_ordinary_embedding_total_tokens_usage_stays_compatible(rig, provider):
    async def respond(request, wire):
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "embedding-u03-test",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.25, 0.5]}],
                "usage": {"prompt_tokens": 7, "total_tokens": 7},
            },
        )

    engine = rig.engine(provider, rig.wire(respond))
    engine.embedding_model = "text-embedding-3-small"
    async with rig.factory() as session:
        vectors = await engine.embed_many(session, ["xin chào"], source_modes=["cloud_first"])
    assert vectors == [[0.25, 0.5]]
    [row] = await rig.rows()
    assert row.state == "settled" and row.operation == "embedding"
    assert (row.actual_input_tokens, row.actual_output_tokens) == (7, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("indices", [[7], [-1], [0, 0], [0, 2]])
async def test_embedding_indices_must_match_inputs_before_vectors_are_used(rig, indices):
    async def respond(request, wire):
        return httpx.Response(200, json={
            "object": "list", "model": "text-embedding-3-small",
            "data": [{"object": "embedding", "index": index,
                      "embedding": [float(index), 0.5]} for index in indices],
            "usage": {"prompt_tokens": 7, "total_tokens": 7},
        })

    wire = rig.wire(respond)
    engine = rig.engine("openai", wire)
    engine.embedding_model = "text-embedding-3-small"
    async with rig.factory() as session:
        with pytest.raises(AiPolicyError):
            await engine.embed_many(session, ["input"] * len(indices),
                                    source_modes=["cloud_first"] * len(indices))
    [row] = await rig.rows()
    assert row.state == "settled" and len(wire.requests) == 1


@pytest.mark.asyncio
async def test_out_of_order_embedding_indices_preserve_input_mapping(rig):
    async def respond(request, wire):
        return httpx.Response(200, json={
            "object": "list", "model": "text-embedding-3-small",
            "data": [{"object": "embedding", "index": index,
                      "embedding": [float(index), 0.5]} for index in [1, 0]],
            "usage": {"prompt_tokens": 7, "total_tokens": 7},
        })

    engine = rig.engine("openai", rig.wire(respond))
    engine.embedding_model = "text-embedding-3-small"
    async with rig.factory() as session:
        vectors = await engine.embed_many(session, ["first", "second"],
                                          source_modes=["cloud_first", "cloud_first"])
    assert vectors == [[0.0, 0.5], [1.0, 0.5]]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "openai"])
@pytest.mark.parametrize("reported", [API_KEY, CONTEXT_MARK])
async def test_untrusted_reported_model_cannot_enter_execution_metadata(rig, provider, reported):
    engine = rig.engine(provider, rig.wire(ok(reported=reported)))
    result = await observe(rig, engine)
    assert result.execution.reported_model is None
    assert result.execution.requested_model == engine.model
    assert_no_private_content(result.execution, result.capability)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["completed_with_error", "completed_with_incomplete_details", "user_message"])
async def test_contradictory_local_response_cannot_trigger_cloud_fallback(rig, case):
    local_wire, cloud_wire = rig.wire(ok(**NONQUALIFYING[case])), rig.wire()
    local, cloud = rig.engine("ollama", local_wire), rig.engine("openai", cloud_wire)
    with pytest.raises(AiUncertainError):
        await observe_route(rig, rig.router(local, cloud), AiRoute("local_first", "openai", True),
                            (candidate_for(local), candidate_for(cloud)))
    assert len(local_wire.requests) == 1 and cloud_wire.requests == []
