"""U03 Task2: private first-value observation carriers and observed method surface.

Types are imported inside tests so missing production code fails as a behaviour
assertion. These carriers grant no permission or freshness; root owns issuance.
"""

from __future__ import annotations

import dataclasses
import inspect
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tg_assistant.ai.engine import AiEngine
from tg_assistant.ai.router import AiRouter

FIELDS = {
    "ChatModelCandidate": ("provider", "endpoint_id", "requested_model", "candidate_fingerprint"),
    "VerifiedChatModel": ("provider", "endpoint_id", "requested_model", "capability_fingerprint"),
    "SettledProviderExecution": (
        "request_id",
        "profile_id",
        "provider",
        "endpoint_id",
        "requested_model",
        "reported_model",
        "capability_fingerprint",
        "route",
        "fallback_used",
        "pricing_version",
        "settled_at",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "cache_write_tokens",
        "cost_usd",
    ),
    "ProviderAnswerResult": ("answer", "execution", "capability"),
}
FORBIDDEN_NAME_PARTS = ("question", "context", "url", "token_value", "secret", "api_key", "prompt")


def observations():
    from tg_assistant.ai import observations as module

    return module


def candidate(**over):
    values = {
        "provider": "openai",
        "endpoint_id": "endpoint-openai-1",
        "requested_model": "gpt-5.6-terra",
        "candidate_fingerprint": "candidate-v1:" + "a" * 64,
    }
    return observations().ChatModelCandidate(**{**values, **over})


def capability(**over):
    values = {
        "provider": "openai",
        "endpoint_id": "endpoint-openai-1",
        "requested_model": "gpt-5.6-terra",
        "capability_fingerprint": "execution-v1:" + "b" * 64,
    }
    return observations().VerifiedChatModel(**{**values, **over})


def execution_values(**over):
    return {
        "request_id": "a" * 32,
        "profile_id": "profile-u03",
        "provider": "openai",
        "endpoint_id": "endpoint-openai-1",
        "requested_model": "gpt-5.6-terra",
        "reported_model": "gpt-5.6-terra-2026-10-01",
        "capability_fingerprint": "execution-v1:" + "b" * 64,
        "route": "first_value_route",
        "fallback_used": False,
        "pricing_version": "official-2026-10-03-v1",
        "settled_at": datetime(2026, 10, 10, 12, 0, tzinfo=UTC),
        "input_tokens": 1000,
        "output_tokens": 50,
        "cached_tokens": 200,
        "cache_write_tokens": 100,
        "cost_usd": Decimal("0.002290"),
        **over,
    }


def execution(**over):
    return observations().SettledProviderExecution(**execution_values(**over))


@pytest.mark.parametrize("name", sorted(FIELDS))
def test_private_types_are_frozen_dataclasses_with_ruled_fields(name):
    cls = getattr(observations(), name)
    assert dataclasses.is_dataclass(cls)
    assert tuple(f.name for f in dataclasses.fields(cls)) == FIELDS[name]
    for field in FIELDS[name]:
        assert not any(part in field for part in FORBIDDEN_NAME_PARTS)
    instance = {
        "ChatModelCandidate": candidate,
        "VerifiedChatModel": capability,
        "SettledProviderExecution": execution,
        "ProviderAnswerResult": lambda: observations().ProviderAnswerResult(
            answer="ok", execution=execution(), capability=capability()
        ),
    }[name]()
    with pytest.raises(AttributeError):  # FrozenInstanceError subclasses AttributeError
        setattr(instance, FIELDS[name][0], "changed")


def test_execution_keeps_exact_decimal_and_nullable_assertions():
    value = execution(reported_model=None, route=None, cost_usd=Decimal("0.002290"))
    assert type(value.cost_usd) is Decimal and str(value.cost_usd) == "0.002290"
    assert value.reported_model is None and value.route is None
    assert execution(cost_usd=Decimal(0)).cost_usd == 0


@pytest.mark.parametrize(
    "override",
    [
        {"request_id": ""},
        {"profile_id": ""},
        {"provider": ""},
        {"endpoint_id": ""},
        {"requested_model": ""},
        {"capability_fingerprint": ""},
        {"pricing_version": ""},
        {"endpoint_id": "https://api.openai.com/v1"},
        {"input_tokens": -1},
        {"output_tokens": -1},
        {"cached_tokens": -1},
        {"cache_write_tokens": -1},
        {"input_tokens": True},
        {"output_tokens": 1.5},
        {"cached_tokens": "3"},
        {"cached_tokens": 900, "cache_write_tokens": 200},
        {"fallback_used": 1},
        {"fallback_used": "false"},
        {"cost_usd": 0.00229},
        {"cost_usd": Decimal("-0.01")},
        {"cost_usd": Decimal("NaN")},
        {"cost_usd": Decimal("Infinity")},
        {"settled_at": datetime(2026, 10, 10, 12, 0)},
        {"settled_at": "2026-10-10T12:00:00+00:00"},
    ],
    ids=lambda value: ",".join(f"{k}={value[k]!r}" for k in value)[:60],
)
def test_execution_rejects_invalid_or_non_exact_values(override):
    with pytest.raises((TypeError, ValueError)):
        execution(**override)


def test_execution_settlement_time_is_utc_or_rejected():
    plus7 = datetime(2026, 10, 10, 19, 0, tzinfo=timezone(timedelta(hours=7)))
    try:
        value = execution(settled_at=plus7)
    except (TypeError, ValueError):
        return
    assert value.settled_at.utcoffset() == timedelta(0)
    assert value.settled_at == plus7


@pytest.mark.parametrize("factory", ["candidate", "capability"])
@pytest.mark.parametrize(
    "override",
    [
        {"provider": ""},
        {"endpoint_id": ""},
        {"requested_model": ""},
        {"endpoint_id": "http://127.0.0.1:11434/v1"},
        {"provider": None},
    ],
)
def test_candidate_and_capability_reject_blank_or_url_identity(factory, override):
    with pytest.raises((TypeError, ValueError)):
        {"candidate": candidate, "capability": capability}[factory](**override)


@pytest.mark.parametrize("field", ["candidate_fingerprint", "capability_fingerprint"])
def test_fingerprints_must_be_nonempty_strings(field):
    factory = candidate if field == "candidate_fingerprint" else capability
    for bad in ("", None, 7):
        with pytest.raises((TypeError, ValueError)):
            factory(**{field: bad})


def test_answer_result_requires_typed_consistent_evidence():
    module = observations()
    result = module.ProviderAnswerResult(answer="Xin chào", execution=execution(), capability=capability())
    assert result.answer == "Xin chào"
    for bad in ("", "  \n", None, b"bytes"):
        with pytest.raises((TypeError, ValueError)):
            module.ProviderAnswerResult(answer=bad, execution=execution(), capability=capability())
    with pytest.raises((TypeError, ValueError)):
        module.ProviderAnswerResult(answer="ok", execution=execution().__dict__, capability=capability())
    with pytest.raises((TypeError, ValueError)):
        module.ProviderAnswerResult(answer="ok", execution=execution(), capability=candidate())
    for mismatch in (
        {"provider": "openrouter"},
        {"endpoint_id": "endpoint-other"},
        {"requested_model": "gpt-5.6-luna"},
        {"capability_fingerprint": "execution-v1:" + "c" * 64},
    ):
        with pytest.raises((TypeError, ValueError)):
            module.ProviderAnswerResult(
                answer="ok", execution=execution(), capability=capability(**mismatch)
            )


def test_carriers_do_not_repr_endpoint_urls_or_private_markers():
    module = observations()
    result = module.ProviderAnswerResult(answer="ok", execution=execution(), capability=capability())
    for carrier in (candidate(), capability(), result.execution):
        text = repr(carrier)
        assert "://" not in text and "sk-" not in text


def test_candidate_fingerprint_is_distinct_from_execution_witness():
    assert candidate().candidate_fingerprint != capability().capability_fingerprint
    verified = {f.name for f in dataclasses.fields(observations().VerifiedChatModel)}
    assert "candidate_fingerprint" not in verified and "capability_fingerprint" in verified


@pytest.mark.parametrize("owner", [AiEngine, AiRouter])
def test_answer_observed_is_async_with_mandatory_keyword_callback(owner):
    method = getattr(owner, "answer_observed", None)
    assert method is not None and inspect.iscoroutinefunction(method)
    parameters = inspect.signature(method).parameters
    pre_submit = parameters["pre_submit"]
    assert pre_submit.kind is inspect.Parameter.KEYWORD_ONLY
    assert pre_submit.default is inspect.Parameter.empty  # None must be rejected, not defaulted


def test_engine_answer_observed_signature_matches_ruling():
    parameters = inspect.signature(AiEngine.answer_observed).parameters
    assert list(parameters)[:4] == ["self", "session", "question", "context"]
    keyword_only = [n for n, p in parameters.items() if p.kind is inspect.Parameter.KEYWORD_ONLY]
    assert keyword_only == [
        "candidate",
        "feature",
        "route",
        "chat_id",
        "fallback_used",
        "source_modes",
        "pre_submit",
    ]
    assert parameters["fallback_used"].default is False and parameters["source_modes"].default is None
    for required in ("candidate", "feature", "route", "chat_id"):
        assert parameters[required].default is inspect.Parameter.empty


def test_router_answer_observed_signature_matches_ruling():
    parameters = inspect.signature(AiRouter.answer_observed).parameters
    assert list(parameters)[:4] == ["self", "session", "question", "context"]
    keyword_only = [n for n, p in parameters.items() if p.kind is inspect.Parameter.KEYWORD_ONLY]
    assert keyword_only == [
        "route",
        "candidates",
        "feature",
        "query_route",
        "chat_id",
        "source_modes",
        "pre_submit",
    ]
    assert parameters["source_modes"].default is None
    for required in ("route", "candidates", "feature", "query_route", "chat_id"):
        assert parameters[required].default is inspect.Parameter.empty


def test_ordinary_answer_interfaces_keep_their_signatures():
    engine = inspect.signature(AiEngine.answer).parameters
    for name in (
        "include_source_refs",
        "feature",
        "route",
        "chat_id",
        "fallback_used",
        "source_modes",
        "request_id",
        "pre_submit",
    ):
        assert name in engine and engine[name].default is not inspect.Parameter.empty
    router = inspect.signature(AiRouter.answer).parameters
    for name in ("route", "include_source_refs", "feature", "query_route", "chat_id", "pre_submit"):
        assert name in router
    assert router["pre_submit"].default is None
