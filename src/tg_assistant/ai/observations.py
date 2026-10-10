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


def _telegram_id(name: str, value: object, *, positive: bool = False) -> None:
    if type(value) is not int or value == 0 or (positive and value < 0):
        raise ValueError(f"{name} must be an exact Telegram integer ID")


def _utc_bound(name: str, value: object) -> datetime:
    if type(value) is not datetime or value.utcoffset() is None:
        raise ValueError(f"{name} must be an aware datetime")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class SourceIndexBinding:
    profile_id: str
    owner_id: int
    pair_fingerprint: str
    selection_generation: int
    restore_epoch: str
    selected_chat_id: int
    source_epoch: int
    source_policy_fingerprint: str
    embedding_provider: str
    embedding_endpoint_id: str
    embedding_model: str
    embedding_model_version: str
    embedding_dimension: int
    embedding_store_id: str
    active_index_generation: int
    vector_owner_fingerprint: str
    retrieval_eligibility_fingerprint: str
    chat_candidates: tuple[ChatModelCandidate, ...]

    def __post_init__(self) -> None:
        for name in (
            "profile_id",
            "restore_epoch",
            "embedding_provider",
            "embedding_endpoint_id",
            "embedding_model",
            "embedding_model_version",
            "embedding_store_id",
        ):
            _identity(name, getattr(self, name))
        for name in (
            "pair_fingerprint",
            "source_policy_fingerprint",
            "vector_owner_fingerprint",
            "retrieval_eligibility_fingerprint",
        ):
            _fingerprint(name, getattr(self, name))
        _telegram_id("owner_id", self.owner_id, positive=True)
        _telegram_id("selected_chat_id", self.selected_chat_id)
        for name in (
            "selection_generation",
            "source_epoch",
            "active_index_generation",
            "embedding_dimension",
        ):
            _count(name, getattr(self, name))
        if not self.embedding_dimension:
            raise ValueError("embedding_dimension must be positive")
        if (
            type(self.chat_candidates) is not tuple
            or not self.chat_candidates
            or not all(isinstance(item, ChatModelCandidate) for item in self.chat_candidates)
        ):
            raise ValueError("chat_candidates must be an immutable candidate tuple")
        if len({item.provider for item in self.chat_candidates}) != len(self.chat_candidates):
            raise ValueError("chat_candidates must have unique providers")


@dataclass(frozen=True)
class CitedReference:
    chat_id: int
    message_id: int
    reference_id: int
    content_hash: str
    context_hash: str
    semantic_used: bool
    keyword_used: bool

    def __post_init__(self) -> None:
        _telegram_id("chat_id", self.chat_id)
        for name in ("message_id", "reference_id"):
            _telegram_id(name, getattr(self, name), positive=True)
        for name in ("content_hash", "context_hash"):
            value = getattr(self, name)
            if (
                type(value) is not str
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
            ):
                raise ValueError(f"{name} must be SHA256")
        if (
            type(self.semantic_used) is not bool
            or type(self.keyword_used) is not bool
            or not (self.semantic_used or self.keyword_used)
        ):
            raise ValueError("reference requires exact retrieval participants")


@dataclass(frozen=True)
class RetrievalScope:
    selected_chat_id: int
    after: datetime | None
    before: datetime
    sender_id: int | None
    has: str | None
    content_type: str | None
    query_route: str
    embedding_route: str

    def __post_init__(self) -> None:
        _telegram_id("selected_chat_id", self.selected_chat_id)
        if self.sender_id is not None:
            _telegram_id("sender_id", self.sender_id)
        object.__setattr__(self, "before", _utc_bound("before", self.before))
        if self.after is not None:
            object.__setattr__(self, "after", _utc_bound("after", self.after))
            if self.after > self.before:
                raise ValueError("retrieval window is empty")
        if self.has not in (
            None,
            "link",
            "file",
            "image",
            "document",
            "audio",
        ) or self.content_type not in (None, "task", "decision"):
            raise ValueError("unsupported retrieval filter")
        _identity("query_route", self.query_route)
        _identity("embedding_route", self.embedding_route)


@dataclass(frozen=True)
class FirstValueAnswerObservation:
    binding: SourceIndexBinding
    execution: SettledProviderExecution
    query_embedding_request_id: str
    cited_refs: tuple[CitedReference, ...]
    retrieval_mode: str
    after: datetime | None
    before: datetime
    sender_id: int | None
    has: str | None
    content_type: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.binding, SourceIndexBinding) or not isinstance(
            self.execution, SettledProviderExecution
        ):
            raise TypeError("typed binding and execution required")
        _identity("query_embedding_request_id", self.query_embedding_request_id)
        if (
            type(self.cited_refs) is not tuple
            or not 1 <= len(self.cited_refs) <= 8
            or not all(
                isinstance(ref, CitedReference) and ref.chat_id == self.binding.selected_chat_id
                for ref in self.cited_refs
            )
        ):
            raise ValueError("bounded immutable selected references required")
        if len({(ref.chat_id, ref.message_id) for ref in self.cited_refs}) != len(self.cited_refs):
            raise ValueError("duplicate cited references")
        if self.retrieval_mode not in ("semantic", "keyword", "hybrid"):
            raise ValueError("unsupported retrieval mode")
        scope = RetrievalScope(
            self.binding.selected_chat_id,
            self.after,
            self.before,
            self.sender_id,
            self.has,
            self.content_type,
            self.execution.route or "local_rag",
            "cloud_embedding",
        )
        object.__setattr__(self, "after", scope.after)
        object.__setattr__(self, "before", scope.before)
        if self.execution.profile_id != self.binding.profile_id:
            raise ValueError("execution profile mismatch")


@dataclass(frozen=True)
class QualifyingRagSuccess:
    answer: str = field(repr=False)
    observation: FirstValueAnswerObservation

    def __post_init__(self) -> None:
        if (
            not isinstance(self.answer, str)
            or not self.answer.strip()
            or not isinstance(self.observation, FirstValueAnswerObservation)
        ):
            raise ValueError("qualified answer and observation required")


@dataclass(frozen=True)
class NonqualifyingRagResult:
    code: str
    answer: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _identity("code", self.code)
        if self.answer is not None and not isinstance(self.answer, str):
            raise ValueError("answer must be text or None")


RagObservedResult = QualifyingRagSuccess | NonqualifyingRagResult
