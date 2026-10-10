"""Application-owned source intent and bounded, non-authoritative answer history."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import monotonic
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import (
    BeforeValidator,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)
from sqlalchemy import (
    LargeBinary,
    String,
    case,
    cast,
    event,
    func,
    insert,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import object_session

from ..contracts import ContractModel, FirstSourceStatus, OwnerId, TelegramId
from ..db.models import AppSetting

META_BYTES = 8192
ROW_BYTES = 32768
TOTAL_BYTES = 8 * 1024 * 1024
MAX_ROWS = 128
MAX_COUNTER = 2**63 - 1
ACTIVE = frozenset({"accepted", "generating", "delivering"})
Counter = Annotated[StrictInt, Field(ge=0, le=MAX_COUNTER)]
Digest = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]
Epoch = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{32}$")]
Identity = Annotated[str, Field(strict=True, min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")]
TerminalCode = Literal["nonqualifying", "cancelled_before_effect", "deadline", "delivery_uncertain", "interrupted", "restore_requires_recheck"]


def _require(condition, code="first_value_state_invalid"):
    if not condition:
        raise ValueError(code)


def _timestamp(value, info):
    if info.mode == "json":
        _require(type(value) is str and len(value) <= 32)
        parsed = datetime.fromisoformat(value)
        _require(parsed.utcoffset() == timedelta(0)
                 and parsed.isoformat().replace("+00:00", "Z") == value)
    else:
        _require(type(value) is datetime and value.utcoffset() == timedelta(0))
    return value


UTCStamp = Annotated[datetime, BeforeValidator(_timestamp)]


class _Record(ContractModel):
    @field_validator("*", mode="after")
    @classmethod
    def safe_identity(cls, value):
        if isinstance(value, str):
            _require("://" not in value and not value.startswith(("/", "\\"))
                     and re.match(r"^[A-Za-z]:[/\\]", value) is None)
        if isinstance(value, datetime):
            _require(value.utcoffset() == timedelta(0))
        return value


class _Versioned(_Record):
    schema_version: Literal[1]
    namespace: Digest
    revision: Counter

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value):
        _require(type(value) is int and value == 1)
        return value


class _LearningAssociation(_Record):
    selection_generation: Counter
    restore_epoch: Epoch
    source_id: TelegramId
    source_epoch: Counter
    owner_id: OwnerId
    action_id: Annotated[str, Field(strict=True, pattern=r"^fv1-[a-f0-9]{32}$")]
    job_id: Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")]


class _SelectionHeader(_Versioned):
    selection_generation: Counter
    restore_epoch: Epoch
    learning: _LearningAssociation | None = None

    @model_validator(mode="after")
    def coherent(self):
        if self.learning is not None:
            _require((self.learning.selection_generation, self.learning.restore_epoch) ==
                     (self.selection_generation, self.restore_epoch))
        return self


class _RequestIdentity(_Record):
    bot_id: OwnerId
    enrollment_generation: Epoch
    owner_id: OwnerId
    incoming_message_id: OwnerId


class _Attempt(_Versioned, _RequestIdentity):
    request_digest: Digest
    pair_fingerprint: Digest
    selection_generation: Counter
    restore_epoch: Epoch
    source_id: TelegramId
    source_epoch: Counter
    accepted_at: UTCStamp
    expires_at: UTCStamp
    phase: Literal["accepted", "generating", "delivering", "completed", "failed", "cancelled", "uncertain"]
    chunk_count: Annotated[StrictInt, Field(ge=1, le=16)] | None = None
    submitted_ordinal: Annotated[StrictInt, Field(ge=0, le=16)] = 0
    returned_message_ids: tuple[OwnerId, ...] = ()
    terminal_code: TerminalCode | None = None

    @model_validator(mode="after")
    def coherent(self):
        _require(self.expires_at == self.accepted_at + timedelta(seconds=300))
        count = len(self.returned_message_ids)
        _require(count == len(set(self.returned_message_ids)) and count <= self.submitted_ordinal <= count + 1)
        _require(self.submitted_ordinal <= (self.chunk_count or 0))
        if self.phase in {"accepted", "generating"}:
            _require(self.chunk_count is None and self.submitted_ordinal == 0)
        if self.phase in {"delivering", "completed"}:
            _require(self.chunk_count is not None)
        if self.phase == "completed":
            _require(count == self.submitted_ordinal == self.chunk_count)
        _require((self.terminal_code is None) == (self.phase in ACTIVE or self.phase == "completed"))
        _require(self.request_digest == _request_digest(self.namespace, bot_id=self.bot_id,
            enrollment_generation=self.enrollment_generation, owner_id=self.owner_id,
            incoming_message_id=self.incoming_message_id))
        return self


class _Candidate(_Record):
    provider: Literal["ollama", "openai", "openrouter"]
    endpoint_id: Identity
    requested_model: Identity
    candidate_fingerprint: Identity


class _Binding(_Record):
    profile_id: Identity
    owner_id: OwnerId
    pair_fingerprint: Digest
    selection_generation: Counter
    restore_epoch: Epoch
    selected_chat_id: TelegramId
    source_epoch: Counter
    source_policy_fingerprint: Identity
    embedding_provider: Literal["ollama", "openai", "openrouter"]
    embedding_endpoint_id: Identity
    embedding_model: Identity
    embedding_model_version: Identity
    embedding_dimension: Annotated[StrictInt, Field(ge=1, le=MAX_COUNTER)]
    embedding_store_id: Identity
    active_index_generation: Counter
    vector_owner_fingerprint: Identity
    retrieval_eligibility_fingerprint: Identity
    chat_candidates: Annotated[tuple[_Candidate, ...], Field(min_length=1, max_length=3)]

    @model_validator(mode="after")
    def coherent(self):
        _require(len({c.provider for c in self.chat_candidates}) == len(self.chat_candidates))
        return self


class _Execution(_Record):
    request_id: Identity
    profile_id: Identity
    provider: Literal["ollama", "openai", "openrouter"]
    endpoint_id: Identity
    requested_model: Identity
    reported_model: None
    capability_fingerprint: Identity
    route: Identity | None
    fallback_used: StrictBool
    pricing_version: Identity
    settled_at: UTCStamp
    input_tokens: Counter
    output_tokens: Counter
    cached_tokens: Counter
    cache_write_tokens: Counter
    cost_usd: Annotated[str, Field(strict=True, min_length=1, max_length=64)]

    @model_validator(mode="after")
    def coherent(self):
        cost = Decimal(self.cost_usd)
        _require(cost.is_finite() and cost >= 0)
        _require(self.cached_tokens + self.cache_write_tokens <= self.input_tokens)
        return self


class _Reference(_Record):
    chat_id: TelegramId
    message_id: OwnerId
    reference_id: OwnerId
    content_hash: Digest
    context_hash: Digest
    semantic_used: StrictBool
    keyword_used: StrictBool

    @model_validator(mode="after")
    def coherent(self):
        _require(self.semantic_used or self.keyword_used)
        return self


class _Scope(_Record):
    selected_chat_id: TelegramId
    after: UTCStamp | None
    before: UTCStamp
    sender_id: TelegramId | None
    has: Literal["link", "file", "image", "document", "audio"] | None
    content_type: Literal["task", "decision"] | None
    query_route: Identity
    embedding_route: Identity

    @model_validator(mode="after")
    def coherent(self):
        _require(self.after is None or self.after <= self.before)
        return self


class _Receipt(_Versioned):
    request_digest: Digest
    completed_at: UTCStamp
    attempt_revision: Counter
    configuration_fingerprint: Digest
    binding: _Binding
    execution: _Execution
    query_embedding_request_id: Identity
    cited_refs: Annotated[tuple[_Reference, ...], Field(min_length=1, max_length=8)]
    retrieval_mode: Literal["semantic", "keyword", "hybrid"]
    scope: _Scope
    message_ids: Annotated[tuple[OwnerId, ...], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def coherent(self):
        _require(self.revision == 0 and self.execution.profile_id == self.binding.profile_id)
        _require(self.scope.selected_chat_id == self.binding.selected_chat_id)
        _require(all(ref.chat_id == self.binding.selected_chat_id for ref in self.cited_refs))
        _require(len({r.reference_id for r in self.cited_refs}) == len(self.cited_refs))
        _require(len({(r.chat_id, r.message_id) for r in self.cited_refs}) == len(self.cited_refs))
        _require(len(set(self.message_ids)) == len(self.message_ids))
        _require(self.execution.settled_at <= self.completed_at)
        _require(any((c.provider, c.endpoint_id, c.requested_model) ==
            (self.execution.provider, self.execution.endpoint_id, self.execution.requested_model)
            for c in self.binding.chat_candidates))
        return self


@dataclass(frozen=True)
class _NamespaceState:
    header: _SelectionHeader | None
    attempts: tuple[_Attempt, ...]
    receipts: tuple[_Receipt, ...]
    sizes: dict[str, int]


def _namespace(profile_id: str, windows_sid: str) -> str:
    return hashlib.sha256(json.dumps([profile_id, windows_sid], separators=(",", ":")).encode()).hexdigest()


def _request_digest(namespace: str, *, bot_id: int, enrollment_generation: str,
                    owner_id: int, incoming_message_id: int) -> str:
    request = _RequestIdentity(bot_id=bot_id, enrollment_generation=enrollment_generation,
                               owner_id=owner_id, incoming_message_id=incoming_message_id)
    return hashlib.sha256(json.dumps(["fv-request-1", namespace, str(request.bot_id),
        request.enrollment_generation, str(request.owner_id), str(request.incoming_message_id)],
        separators=(",", ":")).encode("ascii")).hexdigest()


def _row_key(namespace, kind, digest):
    suffix = base64.urlsafe_b64encode(bytes.fromhex(digest)).decode().rstrip("=")
    return f"fv1.{namespace}.{kind}.{suffix}"


def _strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            _require(key not in value)
            value[key] = item
        return value
    def invalid(_value):
        raise ValueError("first_value_state_invalid")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def _matches(attempt, receipt):
    b = receipt.binding
    _require((attempt.namespace, attempt.request_digest, attempt.revision, attempt.returned_message_ids,
              attempt.owner_id, attempt.source_id, attempt.source_epoch, attempt.pair_fingerprint,
              attempt.selection_generation, attempt.restore_epoch) ==
             (receipt.namespace, receipt.request_digest, receipt.attempt_revision, receipt.message_ids,
              b.owner_id, b.selected_chat_id, b.source_epoch, b.pair_fingerprint,
              b.selection_generation, b.restore_epoch))
    _require(attempt.accepted_at <= receipt.execution.settled_at <= receipt.completed_at <= attempt.expires_at)


def _read_namespace(connection, *, namespace: str) -> _NamespaceState:
    from .first_value_index import _bounded, _Refuse
    try:
        _require(re.fullmatch(r"[a-f0-9]{64}", namespace) is not None)
        key = "fv1." + namespace
        size = func.length(cast(AppSetting.value, LargeBinary))
        with _bounded(connection, 2.0):
            # pysqlite's legacy SELECT mode otherwise has no repeatable snapshot.
            if not connection.connection.driver_connection.in_transaction:
                connection.execute(text("BEGIN"))
            rows = connection.execute(select(func.substr(AppSetting.key, 1, 129),
                func.length(cast(AppSetting.key, LargeBinary)), size).where(or_(AppSetting.key == key,
                (AppSetting.key >= key + ".") & (AppSetting.key < key + "/")))
                .order_by(AppSetting.key).limit(258)).all()
            _require(len(rows) <= 257)
            sizes, kinds = {}, {}
            for row_key, key_bytes, value_bytes in rows:
                _require(type(key_bytes) is int and key_bytes <= 128)
                kind = "header" if row_key == key else None
                if kind is None:
                    match = re.fullmatch(re.escape(key) + r"\.(attempt|receipt)\.([A-Za-z0-9_-]{43})", row_key)
                    _require(match is not None)
                    kind, suffix = match.groups()
                    digest = base64.urlsafe_b64decode(suffix + "=").hex()
                    _require(_row_key(namespace, kind, digest) == row_key)
                cap = META_BYTES if kind == "header" else ROW_BYTES
                _require(type(value_bytes) is int and 0 < value_bytes <= cap)
                sizes[row_key], kinds[row_key] = value_bytes, kind
            _require(sum(sizes.values()) <= TOTAL_BYTES)
            _require(not rows or key in sizes)
            _require(all(list(kinds.values()).count(kind) <= MAX_ROWS for kind in ("attempt", "receipt")))
            parsed = {"header": [], "attempt": [], "receipt": []}
            for row_key, kind in kinds.items():
                cap = META_BYTES if kind == "header" else ROW_BYTES
                raw = connection.execute(select(case((size <= cap, cast(AppSetting.value, String)),
                    else_=None)).where(AppSetting.key == row_key)).scalar_one()
                _require(isinstance(raw, str) and len(raw.encode()) == sizes[row_key])
                _strict_json(raw)
                model = {"header": _SelectionHeader, "attempt": _Attempt, "receipt": _Receipt}[kind]
                record = model.model_validate_json(raw)
                _require(record.namespace == namespace)
                if kind != "header":
                    _require(_row_key(namespace, kind, record.request_digest) == row_key)
                parsed[kind].append(record)
        attempts, receipts = parsed["attempt"], parsed["receipt"]
        _require(sum(a.phase in ACTIVE for a in attempts) <= 1)
        _require(len({(a.bot_id, a.owner_id, a.incoming_message_id) for a in attempts}) == len(attempts))
        completed = {a.request_digest: a for a in attempts if a.phase == "completed"}
        _require(set(completed) == {r.request_digest for r in receipts})
        for receipt in receipts:
            _matches(completed[receipt.request_digest], receipt)
        return _NamespaceState(parsed["header"][0] if rows else None, tuple(attempts), tuple(receipts), sizes)
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError, ArithmeticError, DBAPIError, _Refuse):
        raise ValueError("first_value_state_invalid") from None


def _serialized(connection, record):
    try:
        values = record.model_dump(mode="json")
        raw = (connection.dialect._json_serializer or json.dumps)(values)
        _strict_json(raw)
        type(record).model_validate_json(raw)
        return values, len(raw.encode())
    except (ValueError, TypeError, RecursionError, OverflowError, ArithmeticError):
        raise ValueError("first_value_state_invalid") from None


def _capacity(connection, state, replacements, *, reserve=None):
    sizes = dict(state.sizes)
    for key, record in replacements.items():
        _, size = _serialized(connection, record)
        _require(size <= (META_BYTES if isinstance(record, _SelectionHeader) else ROW_BYTES), "history_full")
        sizes[key] = size
    active = reserve if reserve is not None else next((a for a in state.attempts if a.phase in ACTIVE), None)
    reserved = 0
    if active is not None:
        key = _row_key(active.namespace, "attempt", active.request_digest)
        replacement = replacements.get(key, active)
        if replacement.phase in ACTIVE:
            reserved = 2 * ROW_BYTES - sizes[key]
    _require(sum(sizes.values()) + reserved <= TOTAL_BYTES, "history_full")


def _cas(connection, key, before, after):
    values, _ = _serialized(connection, after)
    _require(after.namespace == before.namespace and after.revision == before.revision + 1)
    predicate = [AppSetting.key == key,
        func.json_type(AppSetting.value, "$.revision") == "integer",
        AppSetting.value["revision"].as_integer() == before.revision,
        AppSetting.value["namespace"].as_string() == before.namespace]
    if isinstance(before, _Attempt):
        predicate.append(AppSetting.value["request_digest"].as_string() == before.request_digest)
    result = connection.execute(update(AppSetting).where(*predicate).values(value=values))
    _require(result.rowcount == 1, "first_value_write_conflict")


def _cas_header(connection, *, before, after):
    state = _read_namespace(connection, namespace=after.namespace)
    _require(state.header == before, "first_value_write_conflict")
    key = "fv1." + after.namespace
    _capacity(connection, state, {key: after})
    if before is None:
        values, _ = _serialized(connection, after)
        connection.execute(insert(AppSetting).values(key=key, value=values))
    else:
        _cas(connection, key, before, after)


def _cas_attempt(connection, *, before, after):
    state = _read_namespace(connection, namespace=before.namespace)
    _require(before in state.attempts, "first_value_write_conflict")
    mutable = {"revision", "phase", "chunk_count", "submitted_ordinal", "returned_message_ids", "terminal_code"}
    _require(before.model_dump(exclude=mutable) == after.model_dump(exclude=mutable))
    _require(before.phase in ACTIVE and after.submitted_ordinal >= before.submitted_ordinal
             and after.returned_message_ids[:len(before.returned_message_ids)] == before.returned_message_ids)
    if after.phase == before.phase:
        _require(before.phase == "delivering" and after.chunk_count == before.chunk_count
                 and after.terminal_code == before.terminal_code)
        submitted = (after.submitted_ordinal == before.submitted_ordinal + 1
                     and after.returned_message_ids == before.returned_message_ids
                     and before.submitted_ordinal == len(before.returned_message_ids))
        returned = (after.submitted_ordinal == before.submitted_ordinal
                    and len(after.returned_message_ids) == len(before.returned_message_ids) + 1)
        _require(submitted or returned)
    else:
        allowed = {"accepted": {"generating", "failed", "cancelled"},
                   "generating": {"delivering", "failed", "uncertain"}, "delivering": {"uncertain"}}
        _require(after.phase in allowed[before.phase]
                 and after.submitted_ordinal == before.submitted_ordinal
                 and after.returned_message_ids == before.returned_message_ids
                 and (after.phase == "delivering" or after.chunk_count == before.chunk_count))
    key = _row_key(before.namespace, "attempt", before.request_digest)
    _capacity(connection, state, {key: after})
    _cas(connection, key, before, after)


def invalidate_first_value_after_restore(connection, *, profile_id: str, windows_sid: str) -> None:
    state = _read_namespace(connection, namespace=_namespace(profile_id, windows_sid))
    if state.header is None:
        return
    header = state.header
    after = header.model_copy(update={"revision": header.revision + 1,
        "selection_generation": header.selection_generation + 1, "restore_epoch": uuid4().hex, "learning": None})
    _cas_header(connection, before=header, after=after)
    for attempt in state.attempts:
        if attempt.phase in ACTIVE:
            updated = attempt.model_copy(update={"revision": attempt.revision + 1,
                "phase": "cancelled" if attempt.phase == "accepted" else "uncertain",
                "terminal_code": "restore_requires_recheck"})
            _cas_attempt(connection, before=attempt, after=updated)


class FirstValueService:
    def __init__(self, *, runtime, coordinator, windows_sid: str):
        if not isinstance(windows_sid, str) or not windows_sid or len(windows_sid) > 184:
            raise ValueError("first_value_binding_invalid")
        self.runtime, self.coordinator = runtime, coordinator
        self._bot = runtime.bot_runtime
        self._engine, self._fence = coordinator.engine, coordinator.fence
        self._profile_id = coordinator.profile_id
        self._namespace = _namespace(self._profile_id, windows_sid)
        self._key = "fv1." + self._namespace
        self._closing = False
        self._selection_tasks: set[asyncio.Task] = set()
        from .first_source_preview import FirstSourcePreviewService
        self.preview = FirstSourcePreviewService(self)

    def _admit(self):
        try:
            current = (
                not self._closing and self.runtime.first_value is self
                and self.runtime.bot_runtime is self._bot and self._bot._runtime is self.runtime
                and self.runtime.settings.profile_id == self._profile_id
                and self.coordinator.profile_id == self._profile_id
                and self.coordinator.engine is self._engine and self.coordinator.fence is self._fence
                and self._bot.engine is self._engine and self._bot.fence is self._fence
                and self.runtime.management_admitted() is True
            )
        except Exception:
            current = False
        if not current:
            raise PermissionError("owner_pairing_required")

    def _read_header(self, connection):
        return _read_namespace(connection, namespace=self._namespace).header

    def _check_connection(self, connection):
        from .maintenance import admitted
        self._admit()
        _require(connection.engine is self._engine and connection.in_transaction()
                 and connection.dialect.name == "sqlite"
                 and connection.connection.driver_connection.in_transaction
                 and admitted(self._fence.key), "first_value_transaction_required")

    @contextmanager
    def _transaction(self):
        """C runs this short transaction inside its retained real worker."""
        self._admit()
        with self._fence.operation(), self._engine.connect() as connection:
            connection.execute(text("BEGIN IMMEDIATE"))
            try:
                self._check_connection(connection)
                yield connection
                self._check_connection(connection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def history(self):
        self._admit()
        with self._fence.operation(), self._engine.connect() as connection:
            state = _read_namespace(connection, namespace=self._namespace)
            self._admit()
            return state

    def _request_authority(self, connection, request):
        self._check_connection(connection)
        pair = self._bot.pairing_verification()
        identity = self._bot.service.verified_identity
        repository = self._bot.repository
        _require(repository is self._bot.service.repository and repository.engine is self._engine
                 and pair is not None and identity is not None,
                 "first_value_binding_invalid")
        document, _ = repository._load(connection)
        enrollment, pairing = document["enrollment"], document["pairing"]
        _require(enrollment is not None and pairing is not None
                 and request.bot_id == identity.bot_id
                 and request.owner_id == self.runtime.user.owner_id == pair.owner_id
                 and enrollment["generation"] == request.enrollment_generation
                 and enrollment["bot_id"] == pairing["bot_id"] == str(request.bot_id)
                 and pairing["owner_id"] == str(request.owner_id)
                 and pairing["enrollment_generation"] == request.enrollment_generation,
                 "first_value_binding_invalid")
        return pair

    def _accept_in_transaction(self, connection, *, request, binding, accepted_at):
        self._check_connection(connection)
        request = _RequestIdentity.model_validate(request.model_dump())
        pair = self._request_authority(connection, request)
        state = _read_namespace(connection, namespace=self._namespace)
        for existing in state.attempts:
            if (existing.bot_id, existing.owner_id, existing.incoming_message_id) == (
                    request.bot_id, request.owner_id, request.incoming_message_id):
                return existing, False
        _require(not any(a.phase in ACTIVE for a in state.attempts), "first_value_busy")
        _require(len(state.attempts) < MAX_ROWS and len(state.receipts) < MAX_ROWS, "history_full")
        header = state.header
        selected = self.coordinator._read(connection)[0].options.source_id
        _require(header is not None and binding.profile_id == self._profile_id
                 and binding.owner_id == request.owner_id and binding.pair_fingerprint == pair.fingerprint
                 and binding.selected_chat_id == selected
                 and binding.selection_generation == header.selection_generation
                 and binding.restore_epoch == header.restore_epoch, "first_value_binding_invalid")
        _require(type(accepted_at) is datetime and accepted_at.utcoffset() == timedelta(0)
                 and accepted_at <= datetime.now(UTC) < accepted_at + timedelta(seconds=300), "deadline")
        digest = _request_digest(self._namespace, **request.model_dump())
        attempt = _Attempt(schema_version=1, namespace=self._namespace, request_digest=digest,
            revision=0, **request.model_dump(), pair_fingerprint=pair.fingerprint,
            selection_generation=header.selection_generation, restore_epoch=header.restore_epoch,
            source_id=selected, source_epoch=binding.source_epoch, accepted_at=accepted_at,
            expires_at=accepted_at + timedelta(seconds=300), phase="accepted")
        key = _row_key(self._namespace, "attempt", digest)
        _capacity(connection, state, {key: attempt}, reserve=attempt)
        values, _ = _serialized(connection, attempt)
        connection.execute(insert(AppSetting).values(key=key, value=values))
        self._check_connection(connection)
        return attempt, True

    def _transition_in_transaction(self, connection, *, attempt, phase, terminal_code=None, chunk_count=None):
        self._check_connection(connection)
        allowed = {"accepted": {"generating", "failed", "cancelled"},
                   "generating": {"delivering", "failed", "uncertain"},
                   "delivering": {"uncertain"}}
        _require(phase in allowed.get(attempt.phase, set()))
        if phase in ACTIVE:
            _require(datetime.now(UTC) < attempt.expires_at, "deadline")
        _require(chunk_count is None or phase == "delivering")
        updated = attempt.model_copy(update={"revision": attempt.revision + 1, "phase": phase,
            "terminal_code": terminal_code,
            "chunk_count": chunk_count if phase == "delivering" else attempt.chunk_count})
        _cas_attempt(connection, before=attempt, after=updated)
        self._check_connection(connection)
        return updated

    def _submit_in_transaction(self, connection, *, attempt, ordinal):
        self._check_connection(connection)
        _require(attempt.phase == "delivering" and type(ordinal) is int
                 and ordinal == attempt.submitted_ordinal + 1
                 and len(attempt.returned_message_ids) == attempt.submitted_ordinal)
        _require(datetime.now(UTC) < attempt.expires_at, "deadline")
        updated = attempt.model_copy(update={"revision": attempt.revision + 1, "submitted_ordinal": ordinal})
        _cas_attempt(connection, before=attempt, after=updated)
        self._check_connection(connection)
        return updated

    def _returned_in_transaction(self, connection, *, attempt, ordinal, message_id):
        self._check_connection(connection)
        _require(attempt.phase == "delivering" and type(ordinal) is int
                 and ordinal == attempt.submitted_ordinal == len(attempt.returned_message_ids) + 1
                 and type(message_id) is int and message_id > 0
                 and message_id not in attempt.returned_message_ids)
        updated = attempt.model_copy(update={"revision": attempt.revision + 1,
            "returned_message_ids": (*attempt.returned_message_ids, message_id)})
        _cas_attempt(connection, before=attempt, after=updated)
        self._check_connection(connection)
        return updated

    def _complete_in_transaction(self, connection, *, attempt, receipt, authorize):
        """Structural projections never replace C's exact issued-object verifier."""
        self._check_connection(connection)
        state = _read_namespace(connection, namespace=self._namespace)
        _require(attempt in state.attempts, "first_value_write_conflict")
        _require(attempt.phase == "delivering" and datetime.now(UTC) <= attempt.expires_at)
        completed = attempt.model_copy(update={"phase": "completed", "revision": attempt.revision + 1})
        _serialized(connection, completed)
        _serialized(connection, receipt)
        _matches(completed, receipt)
        _require(receipt.binding.profile_id == self._profile_id)
        header = state.header
        _require(header is not None and header.restore_epoch == attempt.restore_epoch
                 and header.selection_generation == attempt.selection_generation)
        attempt_key = _row_key(self._namespace, "attempt", attempt.request_digest)
        receipt_key = _row_key(self._namespace, "receipt", attempt.request_digest)
        _require(receipt_key not in state.sizes and len(state.receipts) < MAX_ROWS)
        _capacity(connection, state, {attempt_key: completed, receipt_key: receipt})
        _require(callable(authorize) and authorize(connection) is True, "first_value_proof_required")
        self._check_connection(connection)
        values, _ = _serialized(connection, receipt)
        connection.execute(insert(AppSetting).values(key=receipt_key, value=values))
        _cas(connection, attempt_key, attempt, completed)
        self._check_connection(connection)
        return completed

    def _recover_in_transaction(self, connection):
        """Sole startup owner calls once, before accepting new work; never GET."""
        self._check_connection(connection)
        state = _read_namespace(connection, namespace=self._namespace)
        for attempt in state.attempts:
            if attempt.phase in ACTIVE:
                self._transition_in_transaction(connection, attempt=attempt,
                    phase="cancelled" if attempt.phase == "accepted" else "uncertain",
                    terminal_code="interrupted")

    async def associate_learning(self, session, *, action, job, source_epoch):
        """Adjunct of the validated action's existing writer transaction, after flush."""
        from ..db.models import BackgroundJob, PendingAction, TelegramChatPolicy
        from .first_source_preview import _json
        from .maintenance import admitted
        self._admit()
        _require(admitted(self._fence.key), "first_value_transaction_required")
        database = self.runtime.database
        engine = database.engine
        sync = session.sync_session
        _require(session.bind is engine and database.sessions.kw.get("bind") is engine
                 and engine.dialect.name == "sqlite"
                 and Path(engine.url.database).resolve() == Path(self._engine.url.database).resolve()
                 and database.fence in (None, self._fence), "first_value_transaction_required")
        _require(isinstance(action, PendingAction) and isinstance(job, BackgroundJob)
                 and object_session(action) is sync and object_session(job) is sync
                 and action.status == "executing", "first_source_preview_stale")
        reference = (action.payload or {}).get("first_source_preview")
        capture = self.preview._captures.get(reference) if isinstance(reference, str) else None
        _require(capture is not None and sync.info.get("first_source_validated_actions", {}).get(action.action_id) is capture
                 and action.action_id == capture.action_id and action.action_type == "enable_group_learning"
                 and action.chat_id == capture.source_id and action.requested_by == capture.owner_id
                 and _json(action.payload) == capture.payload, "first_source_preview_stale")
        selected = _strict_json(capture.snapshot)[0]
        identity, pointers = self.preview._identity()
        identity = _json(identity)
        _require(len(pointers) == len(capture.owners)
                 and all(a is b for a, b in zip(pointers, capture.owners, strict=True)), "first_source_preview_stale")
        payload = dict(job.payload or {})
        _require(job.job_type == "learn_group" and job.status == "queued" and job.id is not None
                 and payload == dict(action_id=action.action_id, chat_id=action.chat_id,
                     owner_id=action.requested_by, authorization_epoch=source_epoch, limit=1000)
                 and type(source_epoch) is int and 0 <= source_epoch <= MAX_COUNTER,
                 "first_source_preview_stale")
        association = _LearningAssociation(selection_generation=selected[1], restore_epoch=selected[2],
            source_id=action.chat_id, source_epoch=source_epoch, owner_id=action.requested_by,
            action_id=action.action_id, job_id=job.id)
        active = [True]

        def check(current, *_):
            if not active[0]:
                return
            self._admit()
            _require(admitted(self._fence.key) and database.fence in (None, self._fence),
                     "first_value_transaction_required")
            _require(current is sync and self.runtime.database is database and database.engine is engine
                     and session.bind is engine and database.sessions.kw.get("bind") is engine
                     and datetime.now(UTC) < capture.expiry and monotonic() < capture.deadline,
                     "first_source_preview_stale")
            current_identity, current_pointers = self.preview._identity()
            _require(_json(current_identity) == identity and len(current_pointers) == len(pointers)
                     and all(a is b for a, b in zip(current_pointers, pointers, strict=True)), "first_source_preview_stale")
            connection = current.connection()
            _require(connection.engine is engine.sync_engine
                     and connection.connection.driver_connection.in_transaction,
                     "first_value_transaction_required")
            _require(self.preview._selection(connection) == selected
                     and job.payload == payload and job.id == association.job_id
                     and job.job_type == "learn_group" and job.status == "queued",
                     "first_source_preview_stale")
            epoch = connection.execute(select(TelegramChatPolicy.authorization_epoch)
                .where(TelegramChatPolicy.chat_id == association.source_id)).scalar_one_or_none()
            _require(epoch == association.source_epoch, "first_source_preview_stale")

        def write(current):
            check(current)
            connection = current.connection()
            # Exact persistent rows must already be flushed in this same session.
            action_row = connection.execute(select(PendingAction.action_id, PendingAction.requested_by,
                PendingAction.chat_id, PendingAction.status).where(PendingAction.action_id == action.action_id)).first()
            job_row = connection.execute(select(BackgroundJob.id, BackgroundJob.job_type, BackgroundJob.status,
                BackgroundJob.payload).where(BackgroundJob.id == job.id)).first()
            _require(action_row == (action.action_id, capture.owner_id, capture.source_id, "executing")
                     and job_row == (job.id, "learn_group", "queued", payload), "first_source_preview_stale")
            header = self._read_header(connection)
            after = header.model_copy(update={"revision": header.revision + 1, "learning": association})
            _cas_header(connection, before=header, after=after)
            check(current)

        await session.run_sync(write)
        event.listen(sync, "before_commit", check, once=True)
        event.listen(sync, "after_flush_postexec", check)

        def release(*_):
            active[0] = False

        event.listen(sync, "after_commit", release, once=True)
        event.listen(sync, "after_rollback", release, once=True)

    def _select_source(self, chat_id: int) -> None:
        self._admit()

        def metadata(connection):
            self._admit()
            prior = self.coordinator._read(connection)[0].options.source_id
            header = self._read_header(connection)
            original = header
            if header is None:
                header = _SelectionHeader(schema_version=1, namespace=self._namespace, revision=0,
                    selection_generation=0, restore_epoch=uuid4().hex)
            updated = header.model_copy(update={"revision": header.revision + 1,
                "selection_generation": header.selection_generation + int(prior != chat_id),
                "learning": header.learning if prior == chat_id else None})
            _cas_header(connection, before=original, after=updated)
            self._admit()

        self.coordinator.save_source_selection(chat_id, transaction_update=metadata)

    async def select_source(self, chat_id: int) -> None:
        self._admit()
        if type(chat_id) is not int or chat_id == 0:
            raise ValueError("source_selection_invalid")
        operation = asyncio.create_task(asyncio.to_thread(self._select_source, chat_id))
        self._selection_tasks.add(operation)
        cancelled = False
        try:
            while not operation.done():
                try:
                    await asyncio.shield(operation)
                except asyncio.CancelledError:
                    cancelled = True
                except Exception:
                    break
            if cancelled:
                # Consume a late failure while preserving the caller's cancellation.
                if not operation.cancelled():
                    operation.exception()
                raise asyncio.CancelledError
            operation.result()
        finally:
            if operation.done():
                self._selection_tasks.discard(operation)

    def selection_status(self) -> FirstSourceStatus:
        self._admit()
        with self._engine.connect() as connection:
            self._read_header(connection)
            source = self.coordinator._read(connection)[0].options.source_id
        identity = self._bot.service.verified_identity
        username = identity.username if identity is not None else None
        if not isinstance(username, str) or re.fullmatch(r"[A-Za-z0-9_]{1,32}", username) is None:
            username = None
        self._admit()
        return FirstSourceStatus(profile_id=self._profile_id, source_id=source,
            learning_operation=None, answer_operation=None, bot_username=username, test_available=False,
            code="source_selected" if source is not None else "source_required",
            message="Đã lưu nguồn đã chọn." if source is not None else "Chọn một nguồn Telegram.",
            next_action="Mở quản lý nguồn để kiểm tra quyền và dữ liệu sẽ dùng.")

    async def close(self) -> None:
        self._closing = True
        cancelled = False
        while self._selection_tasks:
            operations = tuple(self._selection_tasks)
            drain = asyncio.gather(*operations, return_exceptions=True)
            while not drain.done():
                try:
                    await asyncio.shield(drain)
                except asyncio.CancelledError:
                    cancelled = True
            drain.result()
            self._selection_tasks.difference_update(task for task in operations if task.done())
        if cancelled:
            raise asyncio.CancelledError
