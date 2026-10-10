"""Bounded current-index reader for the first-value answer (U03 Task 3, reader half).

Every object issued here is private evidence: it grants no public or root authority
and is accepted only by exact identity retention. Equal field values, copies and
pickles are never admission. The reader borrows (never opens or closes) the runtime
vector store, database, coordinator engine and knowledge lock.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import LargeBinary, String, case, cast, func, select
from sqlalchemy.engine import Connection

from ..ai.budget import PricingSnapshot
from ..ai.observations import CitedReference, RetrievalScope, SourceIndexBinding
from ..db.base import fold_text
from ..db.models import AiBudgetReservation as Reservation
from ..db.models import (
    AppSetting,
    TelegramChatPermission,
    TelegramChatPolicy,
    VectorStore,
)
from ..db.models import TelegramMessage as Message
from ..paths import APP_NAME
from .vector_paths import GENERATION_ID, pointer_key
from .vector_reliability import SourceIndexService

TEXT_BYTES = 64 * 1024
META_BYTES = 8 * 1024
JSON_BYTES = 8 * 1024
FILE_BYTES = 64 * 1024
ONBOARDING_BYTES = 256 * 1024
MAX_REFERENCES = 8
VM_STEPS = 100_000
OBSERVATION_AGE = 30.0
CHECK_AGE = 5.0
READ_DEADLINE = 2.0
CHECK_DEADLINE = 2.0
FINAL_BUDGET = 1.0
MAX_RETAINED = 16
SEARCH = "search_messages"
CLOUD_PROVIDERS = frozenset({"openai", "openrouter"})  # as SourceIndexService.decision
COST_TOLERANCE = Decimal("0.000000000001")  # Numeric(24, 12) storage
TASK_MARKERS = ("cần ", "deadline", "todo")  # as SearchService.keyword
DECISION_MARKERS = ("quyết định", "thống nhất")
LINK_MARKERS = ("http://", "https://")
HAS_VALUES = (None, "link", "file", "image", "document", "audio")
CONTENT_TYPES = (None, "task", "decision")


class _Refuse(Exception):
    """Internal fail-closed signal; never leaves this module."""


@dataclass(frozen=True)
class SourceIndexObservation:
    """Measured facts only. Authority is the exact issued instance held by the reader."""

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


@dataclass(frozen=True, eq=False)
class VerifiedReferences:
    selected_chat_id: int
    reference_ids: tuple[int, ...]


@dataclass(eq=False)
class _Capture:
    vectors: object
    client: object
    local_client: object
    token: tuple
    owner_id: int
    location: str | None
    epoch: int
    chat_id: int = 0
    measure: tuple = ()
    mono: float = 0.0
    utc: datetime | None = None
    # Private initial row witnesses: row id -> (chat, message, raw sha, embedding hash, request).
    witnesses: dict = field(default_factory=dict)


@dataclass(eq=False)
class _Issued:
    observation: SourceIndexObservation
    capture: _Capture


@dataclass(eq=False)
class _Bound:
    binding: SourceIndexBinding
    capture: _Capture


@dataclass(eq=False)
class _Checked:
    checked: VerifiedReferences
    binding: SourceIndexBinding
    capture: _Capture
    scope: RetrievalScope
    references: tuple
    mono: float
    utc: datetime
    expiry: datetime | None


def _digest(*parts) -> str:
    return hashlib.sha256(
        json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _same_path(left, right) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _guard(path: Path, *, regular: bool) -> None:
    """Known-path alias/ownership/hardlink proof, no tree walk and no ACL change."""
    from ..desktop import instance

    instance._refuse_reparse(path)
    instance._assert_owned_path(path)
    if regular and (not path.is_file() or path.stat().st_nlink != 1):
        raise _Refuse


def _read_json(path: Path):
    _guard(path, regular=True)
    with path.open("rb") as file:
        raw = file.read(FILE_BYTES + 1)
    if len(raw) > FILE_BYTES:
        raise _Refuse
    return json.loads(raw)


@contextmanager
def _bounded(connection, seconds: float):
    """SQLite VM-step and monotonic budget; always cleared before the connection returns."""
    raw = getattr(connection.connection, "dbapi_connection", None)
    if raw is None or not hasattr(raw, "set_progress_handler"):
        raise _Refuse
    deadline = time.monotonic() + seconds
    remaining = VM_STEPS // 1000

    def handler():
        nonlocal remaining
        remaining -= 1
        return 1 if remaining <= 0 or time.monotonic() >= deadline else 0

    raw.set_progress_handler(handler, 1000)
    try:
        yield
    finally:
        raw.set_progress_handler(None, 0)


def _setting_text(connection, key: str, limit: int) -> str | None:
    size = func.length(cast(AppSetting.value, LargeBinary))
    row = connection.execute(
        select(size, case((size <= limit, cast(AppSetting.value, String)), else_=None)).where(
            AppSetting.key == key
        )
    ).first()
    if row is None:
        return None
    if row[1] is None:
        raise _Refuse
    return row[1]


class CurrentIndexReader:
    def __init__(self, *, runtime, first_value, windows_sid: str):
        coordinator = first_value.coordinator
        namespace = hashlib.sha256(
            json.dumps([coordinator.profile_id, windows_sid], separators=(",", ":")).encode()
        ).hexdigest()
        engine = coordinator.engine
        database = runtime.database.engine
        if (
            first_value.runtime is not runtime
            or getattr(runtime.rag, "database", None) is not runtime.database
            or getattr(runtime.rag, "policy", None) is not runtime.policy
            or runtime.database.sessions.kw.get("bind") is not database
            or first_value._namespace != namespace
            or engine.dialect.name != "sqlite"
            or runtime.settings.profile_id != coordinator.profile_id
            or not _same_path(engine.url.database, database.url.database)
            or runtime.database.fence not in (None, coordinator.fence)
        ):
            raise ValueError("current_index_binding_invalid")
        self._runtime, self._first_value, self._sid = runtime, first_value, windows_sid
        self._coordinator, self._engine = coordinator, engine
        self._profile_id = coordinator.profile_id
        self._settings = runtime.settings
        self._profile = runtime.settings.embedding_profile
        self._base = self._stable()
        self._closed = False
        self._epoch = 0
        self._issued: dict[int, _Issued] = {}
        self._bound: dict[int, _Bound] = {}
        self._checked: dict[int, _Checked] = {}
        self._tasks: dict[asyncio.Task, list[bool]] = {}

    # ------------------------------------------------------------------ RAM ownership

    def _stable(self) -> tuple:
        runtime, rag = self._runtime, self._runtime.rag
        coordinator = self._first_value.coordinator
        return (
            runtime, runtime.settings, rag, getattr(rag, "database", None),
            getattr(rag, "policy", None), runtime.database, runtime.policy,
            runtime.database.engine, runtime.database.sessions,
            runtime.database.sessions.kw.get("bind"), runtime.database.fence,
            runtime._knowledge_lock, runtime.first_value, runtime.bot_runtime, runtime.user,
            coordinator, coordinator.engine, coordinator.fence,
        )  # fmt: skip

    def _alive(self) -> bool:
        if self._closed:
            return False
        try:
            now = self._stable()
            ok = len(now) == len(self._base) and all(a is b for a, b in zip(now, self._base, strict=True))
            ok = (ok and self._settings.embedding_profile == self._profile
                  and self._settings.enable_embeddings is True)
            if ok:
                self._first_value._admit()
        except Exception:
            ok = False
        if not ok:
            self.withdraw()
        return ok

    def _owners_ok(self, capture: _Capture) -> bool:
        """The exact vector observation token (incarnation and revision) must be unchanged."""
        if not self._alive() or capture.epoch != self._epoch:
            return False
        try:
            runtime = self._runtime
            token = capture.vectors.observation_token()
            return (
                runtime.rag.vectors is capture.vectors
                and capture.vectors.client is capture.client
                and getattr(capture.client, "_client", None) is capture.local_client
                and runtime.user.owner_id == capture.owner_id
                and token is not None
                and token == capture.token
            )
        except Exception:
            return False

    def _capture(self) -> _Capture | None:
        if not self._alive():
            return None
        runtime = self._runtime
        vectors = runtime.rag.vectors
        client = vectors.client
        token = vectors.observation_token()
        owner_id = runtime.user.owner_id
        if token is None or type(owner_id) is not int or owner_id <= 0:
            return None
        local_client = getattr(client, "_client", None)
        location = getattr(local_client, "location", None)
        return _Capture(vectors, client, local_client, token, owner_id,
                        str(location) if location is not None else None, self._epoch)  # fmt: skip

    @staticmethod
    def _fresh(mono: float, utc: datetime, limit: float) -> bool:
        elapsed = time.monotonic() - mono
        wall = (datetime.now(UTC) - utc).total_seconds()
        return 0 <= elapsed <= limit and -1.0 <= wall <= limit

    @staticmethod
    def _remember(store: dict, key: object, entry: object) -> None:
        store.pop(id(key), None)
        store[id(key)] = entry
        while len(store) > MAX_RETAINED:
            store.pop(next(iter(store)))

    # ------------------------------------------------------------------ bounded worker

    async def _offload(self, work, seconds: float):
        """Run work in a thread while the borrowed knowledge lock stays held until it drains."""
        lock = self._runtime._knowledge_lock
        flag = [False]

        async def job():
            async with lock:
                flag[0] = True
                worker = asyncio.ensure_future(asyncio.to_thread(work))
                cancelled = False
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        cancelled = True
                    except Exception:
                        break
                if cancelled:
                    if not worker.cancelled():
                        worker.exception()
                    raise asyncio.CancelledError
                return worker.result()

        task = asyncio.create_task(job())
        self._tasks[task] = flag
        task.add_done_callback(self._reap)
        try:
            await asyncio.wait({task}, timeout=seconds)
        except asyncio.CancelledError:
            if not flag[0]:
                task.cancel()
            raise
        if not task.done():
            if not flag[0]:
                task.cancel()
            return None  # a started worker stays owned; its late result is discarded
        if task.cancelled() or task.exception() is not None:
            return None
        return task.result()

    def _reap(self, task: asyncio.Task) -> None:
        self._tasks.pop(task, None)
        if not task.cancelled():
            task.exception()

    # ------------------------------------------------------------------ SQL measurement

    def _measure(self, connection, chat_id: int) -> tuple[tuple, object]:
        """Returns (measure tuple, measured source policy row)."""
        header = self._first_value._read_header(connection)
        if header is None:
            raise _Refuse
        _setting_text(connection, self._coordinator._key, ONBOARDING_BYTES)
        if self._coordinator._read(connection)[0].options.source_id != chat_id:
            raise _Refuse
        policy = connection.execute(
            select(
                TelegramChatPolicy.allowed, TelegramChatPolicy.authorization_epoch,
                TelegramChatPolicy.ai_mode, TelegramChatPolicy.retention_days,
                TelegramChatPolicy.filtering_level, TelegramChatPolicy.max_messages,
                TelegramChatPolicy.max_storage_mb, TelegramChatPolicy.max_vectors,
                TelegramChatPolicy.preferred_cloud_provider, TelegramChatPolicy.cloud_fallback,
                TelegramChatPolicy.revoked_at,
            ).where(TelegramChatPolicy.chat_id == chat_id)
        ).first()  # fmt: skip
        permission = connection.execute(
            select(TelegramChatPermission.enabled).where(
                TelegramChatPermission.chat_id == chat_id,
                TelegramChatPermission.permission == SEARCH,
            )
        ).scalar_one_or_none()
        if (
            policy is None
            or policy.allowed is not True
            or permission is not True
            or policy.ai_mode == "off"
            or type(policy.authorization_epoch) is not int
        ):
            raise _Refuse
        profile = self._profile
        registry = connection.execute(
            select(
                VectorStore.path, VectorStore.collection, VectorStore.provider,
                VectorStore.endpoint_id, VectorStore.model, VectorStore.embedding_version,
                VectorStore.dimension, VectorStore.role, VectorStore.state,
            ).where(VectorStore.store_id == profile.store_id)
        ).first()  # fmt: skip
        if (
            registry is None
            or registry.collection != "telegram_messages"
            or (registry.provider, registry.endpoint_id, registry.model) != (
                profile.provider, profile.endpoint_id, profile.model)
            or (registry.embedding_version, registry.dimension) != (
                profile.embedding_version, profile.dimension)
            or registry.role == "legacy_read_only"
            or registry.state != "active"
        ):  # fmt: skip
            raise _Refuse
        pointer = _setting_text(connection, pointer_key(profile.store_id), JSON_BYTES)
        policy_fp = _digest("policy-v1", chat_id, tuple(policy), permission)
        measure = (header.selection_generation, header.restore_epoch, policy.authorization_epoch,
                   policy_fp, tuple(registry), pointer)  # fmt: skip
        return measure, policy

    def _rows(self, connection, chat_id: int, *conditions, limit: int) -> list:
        text_bytes = func.length(cast(Message.text, LargeBinary))
        meta_bytes = func.length(cast(Message.metadata_json, LargeBinary))
        return connection.execute(
            select(
                Message.id, Message.chat_id, Message.message_id, Message.sender_id,
                Message.sent_at, Message.is_deleted, Message.has_media, Message.vector_status,
                Message.vector_dirty, Message.content_hash, Message.embedding_provider,
                Message.embedding_model, Message.embedding_version,
                case((text_bytes <= TEXT_BYTES, Message.text), else_=None).label("text"),
                case((meta_bytes <= META_BYTES, cast(Message.metadata_json, String)),
                     else_=None).label("meta"),
            ).where(Message.chat_id == chat_id, *conditions).limit(limit)
        ).all()  # fmt: skip

    def _valid_row(self, row, policy, service: SourceIndexService, now: datetime):
        """Returns (embedding hash, request id, expiry) for an actually indexed, current row.

        Eligibility is the existing SourceIndexService.decision under the measured policy and
        the stored embedding_policy_version must be the exact current policy_version.
        """
        profile = self._profile
        if (
            row.is_deleted or row.vector_dirty or row.vector_status != "indexed"
            or row.text is None or row.meta is None
            or (row.embedding_provider, row.embedding_model, row.embedding_version)
            != (profile.provider, profile.model, profile.embedding_version)
        ):  # fmt: skip
            return None
        try:
            meta = json.loads(row.meta)
            if type(meta) is not dict:
                return None
            decision = service.decision(
                SimpleNamespace(is_deleted=row.is_deleted, sent_at=row.sent_at, text=row.text,
                                metadata_json=meta), policy)  # fmt: skip
            policy_version = service.policy_version(policy)
        except Exception:
            return None
        request_id = meta.get("embedding_request_id")
        if (
            not decision.eligible
            or decision.content_hash != row.content_hash
            or meta.get("embedding_content_hash") != decision.content_hash
            or meta.get("embedding_store_id") != profile.store_id
            or meta.get("embedding_policy_version") != policy_version
            or type(request_id) is not str or not 0 < len(request_id) <= 64
        ):  # fmt: skip
            return None
        expiry = None
        if policy.retention_days:
            expiry = _utc(row.sent_at) + timedelta(days=policy.retention_days)
            if expiry <= now:
                return None
        return decision.content_hash, request_id, expiry

    def _ledger_ok(self, item, row, now: datetime) -> bool:
        """Coherence of one settled embedding reservation; reads the ledger, never writes."""
        profile = self._profile
        try:
            counts = (item.actual_input_tokens, item.actual_output_tokens, item.cached_tokens,
                      item.cache_write_tokens)  # fmt: skip
            if (
                item.profile_id != self._profile_id
                or (item.provider, item.model) != (profile.provider, profile.model)
                or (item.operation, item.feature, item.route)
                != ("embedding", "embedding", "cloud_embedding")
                or item.chat_id != row.chat_id
                or item.state != "settled"
                or item.fallback_used is not False
                or item.last_error_code is not None
                or not item.pricing_version
                or item.rates is None
                or not all(type(count) is int and count >= 0 for count in counts)
                or counts[2] + counts[3] > counts[0]
                or item.submitted_at is None or item.settled_at is None
            ):  # fmt: skip
                return False
            local = profile.provider not in CLOUD_PROVIDERS
            occurred, submitted, settled = (
                _utc(item.occurred_at), _utc(item.submitted_at), _utc(item.settled_at))
            if item.is_local is not local or not occurred <= submitted <= settled <= now:
                return False
            snapshot = PricingSnapshot.from_dict(json.loads(item.rates))
            rates = (snapshot.input_rate, snapshot.output_rate, snapshot.cached_rate,
                     snapshot.cache_write_rate)  # fmt: skip
            if (
                (snapshot.provider, snapshot.model, snapshot.version)
                != (item.provider, item.model, item.pricing_version)
                or not all(rate.is_finite() and rate >= 0 for rate in rates)
                or (local and any(rate != 0 for rate in rates))
            ):  # fmt: skip
                return False
            actual = Decimal(item.actual_cost_usd) if item.actual_cost_usd is not None else None
            cost = snapshot.cost(counts[0], counts[1], cached_tokens=counts[2],
                                 cache_write_tokens=counts[3])  # fmt: skip
            return (
                actual is not None and actual.is_finite() and actual >= 0
                and abs(actual - cost) <= COST_TOLERANCE
                and (not local or actual == 0)
            )  # fmt: skip
        except Exception:
            return False

    def _settled(self, connection, valid: dict) -> set[int]:
        """Row ids whose embedding intent is the exact coherent settled embedding reservation."""
        if not valid:
            return set()
        size = func.length(cast(Reservation.pricing_rates, LargeBinary))
        found = {
            item.request_id: item
            for item in connection.execute(
                select(
                    Reservation.request_id, Reservation.profile_id, Reservation.provider,
                    Reservation.model, Reservation.pricing_version, Reservation.operation,
                    Reservation.feature, Reservation.route, Reservation.chat_id,
                    Reservation.is_local, Reservation.fallback_used, Reservation.state,
                    Reservation.occurred_at, Reservation.submitted_at,
                    Reservation.settled_at, Reservation.actual_input_tokens,
                    Reservation.actual_output_tokens, Reservation.cached_tokens,
                    Reservation.cache_write_tokens, Reservation.actual_cost_usd,
                    Reservation.last_error_code,
                    case((size <= JSON_BYTES, cast(Reservation.pricing_rates, String)),
                         else_=None).label("rates"),
                ).where(Reservation.request_id.in_([item[1] for item in valid.values()]))
            ).all()
        }  # fmt: skip
        now = datetime.now(UTC)
        return {
            row_id
            for row_id, (row, request_id) in valid.items()
            if request_id in found and self._ledger_ok(found[request_id], row, now)
        }

    def _points(self, capture: _Capture, rows: dict) -> set[int]:
        """Row ids whose actual Qdrant point carries the exact identity and content hash."""
        vectors, profile = capture.vectors, self._profile
        ids = {
            vectors.point_id(row.id, chat_id=row.chat_id, message_id=row.message_id): row.id
            for row, _ in rows.values()
        }
        if not ids:
            return set()
        points = capture.client.retrieve(
            vectors.COLLECTION, ids=list(ids), with_payload=True, with_vectors=False
        )
        good = set()
        for point in points:
            row_id = ids.get(str(point.id))
            if row_id is None:
                continue
            row, digest = rows[row_id]
            payload = point.payload or {}
            if payload == {
                "reference_id": row.id, "chat_id": row.chat_id, "message_id": row.message_id,
                "content_hash": digest, "store_id": profile.store_id,
            }:  # fmt: skip
                good.add(row_id)
        return good

    # ------------------------------------------------------------------ read

    def _locate(self, capture: _Capture, measure: tuple) -> tuple[int, str]:
        """Actual published-owner proof: pointer/ready file/manifest/registry/runtime path."""
        settings, profile = self._settings, self._profile
        identity = profile.model_dump(mode="json", exclude={"cloud_consent"})
        marker = _read_json(settings.data_dir / ".tg-assistant-data")
        if marker != {"version": 1, "app": APP_NAME, "profile_id": settings.profile_id,
                      "sid": self._sid}:  # fmt: skip
            raise _Refuse
        root, pointer = settings.resolved_semantic_vector_path, measure[5]
        if pointer is None:
            generation, expected = 0, root
        else:
            record = json.loads(pointer)
            generation = record.get("generation") if type(record) is dict else None
            generation_id = record.get("generation_id") if type(record) is dict else None
            if (
                type(generation) is not int or generation <= 0
                or type(generation_id) is not str or not GENERATION_ID.fullmatch(generation_id)
                or record.get("profile_id") != settings.profile_id
                or record.get("sid") != self._sid
                or record.get("store_id") != profile.store_id
                or record.get("owner_id") != capture.owner_id
                or record.get("state") != "ready"
                or record.get("identity") != identity
            ):  # fmt: skip
                raise _Refuse
            expected = root / "generations" / generation_id
            if _read_json(expected / "recovery-ready.json") != record:
                raise _Refuse
        _guard(expected, regular=False)
        if _read_json(expected / "embedding-profile.json") != identity:
            raise _Refuse
        resolved = os.path.normcase(str(expected.resolve()))
        if capture.location is None:
            raise _Refuse
        _guard(Path(capture.location), regular=False)
        if (
            capture.vectors.profile != profile
            or not _same_path(measure[4][0], resolved)
            or not _same_path(Path(capture.location).resolve(), resolved)
        ):
            raise _Refuse
        return generation, resolved

    def _read_work(self, capture: _Capture, chat_id: int):
        now = datetime.now(UTC)
        service = SourceIndexService(self._settings, capture.vectors)
        with self._engine.connect() as connection, _bounded(connection, READ_DEADLINE):
            measure, policy = self._measure(connection, chat_id)
            generation, location = self._locate(capture, measure)
            text_bytes = func.length(cast(Message.text, LargeBinary))
            meta_bytes = func.length(cast(Message.metadata_json, LargeBinary))
            candidates = self._rows(
                connection, chat_id, Message.vector_status == "indexed",
                Message.vector_dirty.is_(False), Message.is_deleted.is_(False),
                text_bytes <= TEXT_BYTES, meta_bytes <= META_BYTES,
                Message.embedding_provider == self._profile.provider,
                limit=MAX_REFERENCES,
            )  # fmt: skip
            valid = {}
            for row in candidates:
                checked = self._valid_row(row, policy, service, now)
                if checked is not None:
                    valid[row.id] = (row, checked[1], checked[0])
            settled = self._settled(connection, {k: (v[0], v[1]) for k, v in valid.items()})
            rows = {k: (v[0], v[2]) for k, v in valid.items() if k in settled}
        good = self._points(capture, rows)
        if not good:
            raise _Refuse
        if capture.vectors.observation_token() != capture.token:
            raise _Refuse
        witnesses = {
            row_id: (row.chat_id, row.message_id,
                     hashlib.sha256(row.text.encode("utf-8")).hexdigest(), digest,
                     valid[row_id][1])
            for row_id, (row, digest) in rows.items() if row_id in good
        }  # fmt: skip
        return measure, generation, location, witnesses

    async def read_source_binding(self, chat_id: int) -> SourceIndexObservation | None:
        try:
            if self._closed or type(chat_id) is not int or chat_id == 0:
                return None
            capture = self._capture()
            if capture is None:
                return None
            mono, utc = time.monotonic(), datetime.now(UTC)
            found = await self._offload(lambda: self._read_work(capture, chat_id), READ_DEADLINE)
            if found is None or not self._owners_ok(capture):
                return None
            measure, generation, location, witnesses = found
            profile = self._profile
            owner = _digest("owner-v1", profile.store_id, generation, location, capture.token)
            observation = SourceIndexObservation(
                selected_chat_id=chat_id,
                source_epoch=measure[2],
                source_policy_fingerprint=measure[3],
                embedding_provider=profile.provider,
                embedding_endpoint_id=profile.endpoint_id,
                embedding_model=profile.model,
                embedding_model_version=profile.embedding_version,
                embedding_dimension=profile.dimension,
                embedding_store_id=profile.store_id,
                active_index_generation=generation,
                vector_owner_fingerprint=owner,
                retrieval_eligibility_fingerprint=_digest(
                    "eligibility-v1", profile.store_id, generation, measure[3], chat_id, SEARCH,
                    "limited-index"),
            )  # fmt: skip
            capture.chat_id, capture.measure, capture.witnesses = chat_id, measure, witnesses
            capture.mono, capture.utc = mono, utc
            self._remember(self._issued, observation, _Issued(observation, capture))
            return observation
        except Exception:
            return None

    # ------------------------------------------------------------------ bind

    def bind_current(self, observation, binding) -> bool:
        try:
            entry = self._issued.get(id(observation))
            if (
                entry is None or entry.observation is not observation
                or type(binding) is not SourceIndexBinding
            ):  # fmt: skip
                return False
            capture = entry.capture
            if not self._fresh(capture.mono, capture.utc, OBSERVATION_AGE):
                return False
            if not self._owners_ok(capture):
                return False
            names = (
                "selected_chat_id", "source_epoch", "source_policy_fingerprint",
                "embedding_provider", "embedding_endpoint_id", "embedding_model",
                "embedding_model_version", "embedding_dimension", "embedding_store_id",
                "active_index_generation", "vector_owner_fingerprint",
                "retrieval_eligibility_fingerprint",
            )  # fmt: skip
            expected = (self._profile_id, capture.owner_id, capture.measure[0], capture.measure[1])
            actual = (binding.profile_id, binding.owner_id, binding.selection_generation,
                      binding.restore_epoch)  # fmt: skip
            if actual != expected or any(
                getattr(binding, name) != getattr(observation, name) for name in names
            ):
                return False
            self._remember(self._bound, binding, _Bound(binding, capture))
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ references

    @staticmethod
    def _scope_ok(row, scope: RetrievalScope) -> bool:
        """The filters of SearchService.keyword, applied to the actual stored row."""
        sent = _utc(row.sent_at)
        if (
            (scope.after is not None and sent < scope.after)
            or sent > scope.before  # the root-captured upper bound is inclusive
            or (scope.sender_id is not None and row.sender_id != scope.sender_id)
            or scope.has not in HAS_VALUES
            or scope.content_type not in CONTENT_TYPES
        ):
            return False
        folded = fold_text(row.text) or ""
        if scope.has == "link":
            if not any(marker in folded for marker in LINK_MARKERS):
                return False
        elif scope.has is not None and row.has_media is not True:
            return False
        markers = {"task": TASK_MARKERS, "decision": DECISION_MARKERS}.get(scope.content_type)
        return markers is None or any(marker in folded for marker in markers)

    def _verify_rows(self, connection, capture: _Capture, scope, references) -> tuple:
        """Exact bounded SQL proof for cited rows. No Qdrant, disk or await."""
        now = datetime.now(UTC)
        service = SourceIndexService(self._settings, capture.vectors)
        measure, policy = self._measure(connection, capture.chat_id)
        if measure != capture.measure:
            raise _Refuse
        ids = [ref.reference_id for ref in references]
        rows = {
            row.id: row
            for row in self._rows(connection, capture.chat_id, Message.id.in_(ids),
                                  limit=MAX_REFERENCES)
        }  # fmt: skip
        valid, expiry = {}, None
        for ref in references:
            row = rows.get(ref.reference_id)
            checked = self._valid_row(row, policy, service, now) if row is not None else None
            if (
                checked is None
                or (row.chat_id, row.message_id) != (ref.chat_id, ref.message_id)
                or hashlib.sha256(row.text.encode("utf-8")).hexdigest() != ref.content_hash
                or not self._scope_ok(row, scope)
            ):  # fmt: skip
                raise _Refuse
            valid[row.id] = (row, checked[1], checked[0])
            if checked[2] is not None and (expiry is None or checked[2] < expiry):
                expiry = checked[2]
        if self._settled(connection, {k: (v[0], v[1]) for k, v in valid.items()}) != set(valid):
            raise _Refuse
        return expiry, {k: (v[0], v[2]) for k, v in valid.items()}

    def _witnesses_hold(self, connection, capture: _Capture) -> bool:
        """Bounded SQL recheck of the retained initial rows: at least one must be unchanged."""
        service = SourceIndexService(self._settings, capture.vectors)
        measure, policy = self._measure(connection, capture.chat_id)
        if measure != capture.measure or not capture.witnesses:
            return False
        now = datetime.now(UTC)
        valid = {}
        for row in self._rows(connection, capture.chat_id, Message.id.in_(list(capture.witnesses)),
                              limit=MAX_REFERENCES):  # fmt: skip
            checked = self._valid_row(row, policy, service, now)
            if checked is not None and capture.witnesses.get(row.id) == (
                row.chat_id, row.message_id,
                hashlib.sha256(row.text.encode("utf-8")).hexdigest(), checked[0], checked[1],
            ):  # fmt: skip
                valid[row.id] = (row, checked[1])
        return bool(self._settled(connection, valid))

    def _check_work(self, capture: _Capture, scope, references):
        with self._engine.connect() as connection, _bounded(connection, CHECK_DEADLINE):
            expiry, rows = self._verify_rows(connection, capture, scope, references)
        if self._points(capture, rows) != set(rows):
            raise _Refuse
        if capture.vectors.observation_token() != capture.token:
            raise _Refuse
        return (expiry,)

    async def check_used_references(self, binding, scope, references) -> VerifiedReferences | None:
        try:
            entry = self._bound.get(id(binding))
            if self._closed or entry is None or entry.binding is not binding:
                return None
            capture = entry.capture
            if (
                type(scope) is not RetrievalScope
                or scope.selected_chat_id != capture.chat_id
                or type(references) is not tuple
                or not 1 <= len(references) <= MAX_REFERENCES
                or not all(
                    type(ref) is CitedReference and ref.chat_id == capture.chat_id
                    for ref in references
                )
                or len({ref.reference_id for ref in references}) != len(references)
                or len({ref.message_id for ref in references}) != len(references)
                or not self._owners_ok(capture)
            ):  # fmt: skip
                return None
            mono, utc = time.monotonic(), datetime.now(UTC)
            done = await self._offload(
                lambda: self._check_work(capture, scope, references), CHECK_DEADLINE
            )
            if done is None or not self._owners_ok(capture):
                return None
            checked = VerifiedReferences(
                capture.chat_id, tuple(ref.reference_id for ref in references)
            )
            self._remember(self._checked, checked, _Checked(
                checked, binding, capture, scope, references, mono, utc, done[0]))  # fmt: skip
            return checked
        except Exception:
            return None

    def still_current_in_transaction(self, connection, binding, checked=None) -> bool:
        """RAM plus bounded SQL on the caller's connection only; no await, disk or Qdrant.

        checked=None is the pre-provider check: the exact bound binding, owners, vector token and
        age, then the retained initial rows rechecked in SQL. With checked it is the final check.
        """
        try:
            bound = self._bound.get(id(binding))
            if (
                self._closed or bound is None or bound.binding is not binding
                or not isinstance(connection, Connection)
                or connection.engine is not self._engine
            ):  # fmt: skip
                return False
            capture = bound.capture
            if checked is None:
                if not self._fresh(capture.mono, capture.utc, OBSERVATION_AGE):
                    return False
                if not self._owners_ok(capture):
                    return False
                with _bounded(connection, FINAL_BUDGET):
                    if not self._witnesses_hold(connection, capture):
                        return False
                return self._owners_ok(capture)
            entry = self._checked.get(id(checked))
            if (
                entry is None or entry.checked is not checked
                or entry.binding is not binding or entry.capture is not capture
                or not self._fresh(entry.mono, entry.utc, CHECK_AGE)
                or (entry.expiry is not None and datetime.now(UTC) >= entry.expiry)
                or not self._owners_ok(capture)
            ):  # fmt: skip
                return False
            with _bounded(connection, FINAL_BUDGET):
                self._verify_rows(connection, capture, entry.scope, entry.references)
            return self._owners_ok(capture)
        except Exception:
            return False

    # ------------------------------------------------------------------ lifecycle

    def withdraw(self) -> None:
        self._epoch += 1
        self._issued.clear()
        self._bound.clear()
        self._checked.clear()

    async def close(self) -> None:
        self._closed = True
        self.withdraw()
        for task, flag in tuple(self._tasks.items()):
            if not flag[0]:
                task.cancel()
        cancelled = False
        while self._tasks:
            tasks = tuple(self._tasks)
            drain = asyncio.gather(*tasks, return_exceptions=True)
            while not drain.done():
                try:
                    await asyncio.shield(drain)
                except asyncio.CancelledError:
                    cancelled = True
            for task in tasks:
                self._tasks.pop(task, None)
        if cancelled:
            raise asyncio.CancelledError
