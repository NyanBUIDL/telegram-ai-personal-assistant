"""Private first-value observation carriers.

These objects grant no permission, ownership or freshness: root retains the exact
candidates and issued results, and equal field values are never admission. No
endpoint URL, question, context or credential is stored in any carrier.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

_MAX_TEXT = 256


def _identity(name: str, value: object) -> str:
    if (
        type(value) is not str
        or not value.strip()
        or value != value.strip()
        or len(value) > _MAX_TEXT
        or "://" in value
    ):
        raise ValueError(f"{name} must be a bounded nonempty identity string")
    return value


def _fingerprint(name: str, value: object) -> str:
    if type(value) is not str or not value.strip() or len(value) > _MAX_TEXT:
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _count(name: str, value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative int")
    return value


def candidate_fingerprint(provider: str, endpoint_id: str, requested_model: str) -> str:
    """Identity only (not permission or proof); distinct from the execution witness."""
    seed = f"{provider}|{endpoint_id}|{requested_model}"
    return "candidate-v1:" + hashlib.sha256(seed.encode()).hexdigest()


def execution_fingerprint(*parts: str) -> str:
    return "execution-v1:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


@dataclass(frozen=True)
class ChatModelCandidate:
    provider: str
    endpoint_id: str
    requested_model: str
    candidate_fingerprint: str

    def __post_init__(self) -> None:
        for name in ("provider", "endpoint_id", "requested_model"):
            _identity(name, getattr(self, name))
        _fingerprint("candidate_fingerprint", self.candidate_fingerprint)


@dataclass(frozen=True)
class VerifiedChatModel:
    provider: str
    endpoint_id: str
    requested_model: str
    capability_fingerprint: str

    def __post_init__(self) -> None:
        for name in ("provider", "endpoint_id", "requested_model"):
            _identity(name, getattr(self, name))
        _fingerprint("capability_fingerprint", self.capability_fingerprint)


@dataclass(frozen=True)
class SettledProviderExecution:
    request_id: str
    profile_id: str
    provider: str
    endpoint_id: str
    requested_model: str
    reported_model: str | None
    capability_fingerprint: str
    route: str | None
    fallback_used: bool
    pricing_version: str
    settled_at: datetime
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cache_write_tokens: int
    cost_usd: Decimal

    def __post_init__(self) -> None:
        for name in (
            "request_id",
            "profile_id",
            "provider",
            "endpoint_id",
            "requested_model",
            "pricing_version",
        ):
            _identity(name, getattr(self, name))
        _fingerprint("capability_fingerprint", self.capability_fingerprint)
        for name in ("reported_model", "route"):
            value = getattr(self, name)
            if value is not None:
                _identity(name, value)
        if type(self.fallback_used) is not bool:
            raise TypeError("fallback_used must be bool")
        for name in ("input_tokens", "output_tokens", "cached_tokens", "cache_write_tokens"):
            _count(name, getattr(self, name))
        if self.cached_tokens + self.cache_write_tokens > self.input_tokens:
            raise ValueError("cached tokens exceed input tokens")
        if (
            type(self.cost_usd) is not Decimal
            or not self.cost_usd.is_finite()
            or self.cost_usd < 0
        ):
            raise ValueError("cost_usd must be a finite nonnegative Decimal")
        settled = self.settled_at
        if type(settled) is not datetime or settled.utcoffset() is None:
            raise TypeError("settled_at must be a timezone-aware datetime")
        object.__setattr__(self, "settled_at", settled.astimezone(UTC))


@dataclass(frozen=True)
class ProviderAnswerResult:
    answer: str = field(repr=False)
    execution: SettledProviderExecution
    capability: VerifiedChatModel

    def __post_init__(self) -> None:
        if type(self.answer) is not str or not self.answer.strip():
            raise ValueError("answer must be nonempty text")
        if not isinstance(self.execution, SettledProviderExecution) or not isinstance(
            self.capability, VerifiedChatModel
        ):
            raise TypeError("execution and capability must be typed observations")
        execution, capability = self.execution, self.capability
        if (
            execution.provider,
            execution.endpoint_id,
            execution.requested_model,
            execution.capability_fingerprint,
        ) != (
            capability.provider,
            capability.endpoint_id,
            capability.requested_model,
            capability.capability_fingerprint,
        ):
            raise ValueError("execution and capability disagree")
