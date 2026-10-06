"""Owner-approved derived generations; active corpus survives failed builds."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from qdrant_client.models import PointStruct
from sqlalchemy import func, select, text, update

from ..ai.engine import AiPolicyError, AiUncertainError
from ..ai.vector import LocalVectorStore
from ..config import Settings, current_cloud_consent, current_model_enabled
from ..contracts import OperationResult, RecoveryPlan
from ..db.models import (
    AiBudgetReservation,
    AppSetting,
    AuditLog,
    BackgroundJob,
    PermissionName,
    TelegramChatPolicy,
    TelegramMessage,
    VectorSourceCoverage,
    VectorStore,
)
from ..paths import current_user_sid
from .jobs import LeaseLost, utc
from .operations import refresh_source_usage
from .revocation import AuthorizationRevoked, require_authorization
from .vector_paths import active_vector_path, generation_path, pointer_key, write_ready
from .vector_reliability import SourceIndexService


def digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def plan_key(plan_id: str) -> str:
    return f"recovery_plan:{plan_id}"


def intent_key(store_id: str, row_id: int) -> str:
    return f"recovery_intent:{store_id}:{row_id}"


def points(vectors):
    offset = None
    result = []
    while True:
        selected, offset = vectors.client.scroll(
            vectors.COLLECTION, offset=offset, limit=256, with_payload=True, with_vectors=True
        )
        result.extend(selected)
        if offset is None:
            return result


def corpus_hash(vectors) -> str:
    return digest(
        sorted(
            [(str(point.id), point.payload, point.vector) for point in points(vectors)],
            key=lambda value: value[0],
        )
    )


class VectorRecoveryService:
    def __init__(
        self,
        database,
        *,
        settings_getter,
        vectors_getter,
        owner_getter,
        embedding_getter=None,
        model_fence=None,
        publish=None,
        lock=None,
    ):
        self.database = database
        self.settings_getter, self.vectors_getter = settings_getter, vectors_getter
        self.owner_getter, self.embedding_getter = owner_getter, embedding_getter
        self.model_fence, self.publish = model_fence, publish
        self.lock = lock or asyncio.Lock()

    @classmethod
    def for_application(cls, application):
        def publish(vectors):
            old = application.rag.vectors
            application.rag.vectors = vectors
            if old is not vectors:
                old.close()

        return cls(
            application.database,
            settings_getter=lambda: application.settings,
            vectors_getter=lambda: application.rag.vectors,
            owner_getter=lambda: application.user.owner_id,
            embedding_getter=lambda: application.embedding_ai,
            model_fence=application._model_fence,
            publish=publish,
            lock=application._knowledge_lock,
        )

    def owner(self, actor=None):
        owner = self.owner_getter()
        if (
            type(owner) is not int
            or owner <= 0
            or (actor is not None and (type(actor) is not int or actor != owner))
        ):
            raise PermissionError("recovery_not_owner")
        return owner

    async def _global_hash(self, session, index, *, max_row_id=None, locked=False):
        # Preserve the original canonical JSON digest while streaming SQL columns.
        # No ORM corpus materialization or change to persisted legacy fingerprints.
        query = select(
            TelegramMessage.id,
            TelegramMessage.chat_id,
            TelegramMessage.message_id,
            TelegramMessage.text,
            TelegramMessage.is_deleted,
            TelegramMessage.vector_status,
            TelegramMessage.vector_dirty,
            TelegramMessage.metadata_json,
        ).order_by(TelegramMessage.id)
        policies = select(TelegramChatPolicy).order_by(TelegramChatPolicy.chat_id)
        if max_row_id is not None:
            query = query.where(TelegramMessage.id <= max_row_id)
        if locked:
            query, policies = query.with_for_update(), policies.with_for_update()
        fingerprint = sha256(b"[[")
        first = True
        result = await session.stream(query.execution_options(yield_per=256))
        try:
            async for row in result:
                if not first:
                    fingerprint.update(b",")
                fingerprint.update(
                    json.dumps(
                        list(row),
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                )
                first = False
        finally:
            await result.close()
        selected = (
            await session.scalars(
                policies.execution_options(populate_existing=True),
            )
        ).all()
        fingerprint.update(b"],")
        fingerprint.update(
            json.dumps(
                [(policy.chat_id, index.policy_version(policy)) for policy in selected],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        fingerprint.update(b"]")
        return fingerprint.hexdigest()

    async def _view(
        self,
        session,
        chat_id,
        store_id,
        *,
        locked=False,
        max_row_id=None,
        full=True,
    ):
        settings = self.settings_getter()
        profile = settings.embedding_profile
        if store_id != profile.store_id:
            raise ValueError("recovery_store_changed")
        store_query = select(VectorStore).where(VectorStore.store_id == store_id)
        policy_query = select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
        rows_query = (
            select(TelegramMessage)
            .where(TelegramMessage.chat_id == chat_id)
            .order_by(TelegramMessage.id)
        )
        if max_row_id is not None:
            rows_query = rows_query.where(TelegramMessage.id <= max_row_id)
        if locked:
            store_query, policy_query, rows_query = (
                store_query.with_for_update(),
                policy_query.with_for_update(),
                rows_query.with_for_update(),
            )
        store = await session.scalar(store_query.execution_options(populate_existing=True))
        policy = await session.scalar(policy_query.execution_options(populate_existing=True))
        if store is None or policy is None:
            raise ValueError("recovery_source_unavailable")
        path = await active_vector_path(session, settings, owner_id=self.owner())
        identity = profile.model_dump(mode="json", exclude={"cloud_consent"})
        if (
            store.path != str(path.resolve())
            or store.provider != profile.provider
            or store.model != profile.model
            or store.endpoint_id != profile.endpoint_id
            or store.embedding_version != profile.embedding_version
            or store.dimension != profile.dimension
            or store.role == "legacy_read_only"
        ):
            raise ValueError("recovery_registry_changed")
        vectors = self.vectors_getter()
        if vectors is None or vectors.profile is None or vectors.profile.store_id != store_id:
            raise ValueError("recovery_corpus_unavailable")
        location = getattr(vectors.client._client, "location", None)
        if location is None or Path(location).resolve() != path.resolve():
            raise ValueError("recovery_corpus_not_selected")
        rows = list(
            (await session.scalars(rows_query.execution_options(populate_existing=True))).all()
        )
        index = SourceIndexService(settings, vectors)
        canonicals = index.admitted_canonicals(rows, policy)
        pointer = await session.get(AppSetting, pointer_key(store_id))
        generation = pointer.value["generation"] if pointer else 0
        source_hash = digest(
            [
                index.policy_version(policy),
                [
                    (
                        row.id,
                        row.message_id,
                        row.is_deleted,
                        index.decision(row, policy).content_hash,
                        row.vector_status,
                        row.vector_dirty,
                        row.metadata_json,
                    )
                    for row in rows
                ],
                sorted(row.id for row in canonicals.values()),
            ]
        )
        global_hash = (
            await self._global_hash(
                session,
                index,
                max_row_id=max_row_id,
                locked=locked,
            )
            if full
            else None
        )
        return {
            "settings": settings,
            "identity": identity,
            "store": store,
            "policy": policy,
            "rows": rows,
            "canonical": canonicals,
            "index": index,
            "generation": generation,
            "source_hash": source_hash,
            "corpus_hash": corpus_hash(vectors) if full else None,
            "global_hash": global_hash,
        }

    async def preview(self, chat_id: int, store_id: str) -> RecoveryPlan:
        owner = self.owner()
        async with self.database.session() as session:
            max_row_id = int(await session.scalar(select(func.max(TelegramMessage.id))) or 0)
            view = await self._view(session, chat_id, store_id, max_row_id=max_row_id)
            epoch = await require_authorization(session, chat_id, PermissionName.AUTO_KNOWLEDGE)
            plan = RecoveryPlan(
                plan_id=uuid4().hex,
                chat_id=str(chat_id),
                store_id=store_id,
                authorization_epoch=epoch,
                index_generation=view["generation"],
                expected_count=len(view["canonical"]),
                expires_at=datetime.now(UTC) + timedelta(seconds=300),
            )
            expected_ids = {row.id for row in view["canonical"].values()}
            active_ids = set(self.vectors_getter().reference_ids(chat_id=chat_id))
            verified_ids = {
                row.id for row in view["canonical"].values() if view["index"].current(row)
            }
            missing, orphan = len(expected_ids - verified_ids), len(active_ids - expected_ids)
            coverage = {
                "chat_id": str(chat_id),
                "expected_eligible_messages": len(expected_ids),
                "active_vectors": len(active_ids),
                "missing_count": missing,
                "orphan_count": orphan,
                "coverage_percent": round(100 * len(verified_ids) / len(expected_ids), 1)
                if expected_ids
                else 100.0,
                "coverage_state": "healthy"
                if not missing and not orphan
                else "reconciliation_required",
            }
            session.add(
                AppSetting(
                    key=plan_key(plan.plan_id),
                    value={
                        "plan": plan.model_dump(mode="json"),
                        "owner_id": owner,
                        "profile_id": view["settings"].profile_id,
                        "sid": current_user_sid(),
                        "identity": view["identity"],
                        "source_hash": view["source_hash"],
                        "corpus_hash": view["corpus_hash"],
                        "state": "preview",
                        "global_hash": view["global_hash"],
                        "generation_id": uuid4().hex,
                        "max_row_id": max_row_id,
                        "coverage": coverage,
                    },
                )
            )
        return plan

    async def _validate(self, session, record, *, expiry=True, locked=False, full=True):
        owner = self.owner()
        plan = RecoveryPlan.model_validate(record["plan"])
        settings = self.settings_getter()
        if (
            record.get("owner_id") != owner
            or record.get("profile_id") != settings.profile_id
            or record.get("sid") != current_user_sid()
        ):
            raise PermissionError("recovery_owner_changed")
        if expiry and plan.expires_at <= datetime.now(UTC):
            raise ValueError("recovery_plan_expired")
        cutoff = record.get("max_row_id")
        if cutoff is not None and (type(cutoff) is not int or cutoff < 0):
            raise ValueError("recovery_plan_stale")
        view = await self._view(
            session,
            int(plan.chat_id),
            plan.store_id,
            locked=locked,
            max_row_id=cutoff,
            full=full,
        )
        try:
            await require_authorization(
                session, int(plan.chat_id), PermissionName.AUTO_KNOWLEDGE, plan.authorization_epoch
            )
        except AuthorizationRevoked:
            raise ValueError("recovery_plan_stale") from None
        if (
            view["identity"] != record["identity"]
            or view["generation"] != plan.index_generation
            or view["source_hash"] != record["source_hash"]
            or (full and view["global_hash"] != record["global_hash"])
            or (full and view["corpus_hash"] != record["corpus_hash"])
        ):
            raise ValueError("recovery_plan_stale")
        return plan, view

    async def enqueue_in_session(self, session, plan_id: str, owner_id: int) -> str:
        self.owner(owner_id)
        row = await session.scalar(
            select(AppSetting).where(AppSetting.key == plan_key(plan_id)).with_for_update()
        )
        if row is None or not isinstance(row.value, dict):
            raise ValueError("recovery_plan_unknown")
        record = dict(row.value)
        if record.get("owner_id") != owner_id:
            raise PermissionError("recovery_not_owner")
        if record.get("operation_id"):
            return record["operation_id"]
        if record.get("state") != "preview":
            raise ValueError("recovery_plan_used")
        plan, _ = await self._validate(session, record, locked=True)
        operation = str(uuid4())
        record.update(state="queued", operation_id=operation)
        consumed = await session.execute(
            update(AppSetting)
            .where(AppSetting.key == row.key, AppSetting.value["state"].as_string() == "preview")
            .values(value=record)
            .execution_options(synchronize_session=False)
        )
        if consumed.rowcount != 1:
            raise ValueError("recovery_plan_used")
        row.value = record
        session.add(
            BackgroundJob(
                id=operation,
                job_type="vector_recovery",
                status="queued",
                max_attempts=3,
                payload={
                    "plan_id": plan_id,
                    "chat_id": int(plan.chat_id),
                    "owner_id": owner_id,
                    "authorization_epoch": plan.authorization_epoch,
                    "authorization_epochs": {plan.chat_id: plan.authorization_epoch},
                    "phase": "queued",
                    "progress": 0,
                    "request_ids": {},
                },
            )
        )
        return operation

    async def enqueue(self, plan_id: str, owner_id: int) -> str:
        async with self.database.session() as session:
            if session.bind.dialect.name == "sqlite":
                await session.execute(text("BEGIN IMMEDIATE"))
            return await self.enqueue_in_session(session, plan_id, owner_id)

    async def _job(self, session, lease):
        job = await session.scalar(
            select(BackgroundJob)
            .where(BackgroundJob.id == lease.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            job is None
            or job.job_type != "vector_recovery"
            or job.status != "running"
            or job.claim_token != lease.claim_token
            or job.lease_expires_at is None
            or utc(job.lease_expires_at) <= datetime.now(UTC)
        ):
            raise LeaseLost("recovery_job_lease_lost")
        try:
            self.owner((job.payload or {}).get("owner_id"))
        except PermissionError:
            raise LeaseLost("recovery_owner_changed") from None
        row = await session.get(AppSetting, plan_key(job.payload["plan_id"]))
        if row is None or row.value.get("operation_id") != job.id:
            raise ValueError("recovery_plan_unknown")
        return job, row

    async def _fence(self, lease, *, mark_submission=False, full=True):
        async with self.database.session() as session:
            job, row = await self._job(session, lease)
            # Narrow fences still freshly read every planned target row and its
            # current policy/canonical decisions. They omit only global corpus work.
            plan, view = await self._validate(session, row.value, expiry=False, full=full)
            if mark_submission:
                unresolved = [
                    await session.get(AiBudgetReservation, request_id)
                    for request_id in job.payload.get("request_ids", {}).values()
                ]
                if any(item and item.state in {"submitted", "uncertain"} for item in unresolved):
                    job.payload = {**job.payload, "external_effect_started": True}
        if not self.model_fence:
            raise ValueError("recovery_model_fence_unavailable")
        settings = view["settings"]
        current = Settings(
            _env_file=None, data_dir=settings.data_dir, profile_id=settings.profile_id
        )
        if current.embedding_profile.store_id != plan.store_id:
            raise ValueError("recovery_profile_changed")
        cloud = settings.embedding_profile.provider in {"openai", "openrouter"}
        if not current_model_enabled(settings, embedding=True):
            raise AiPolicyError("recovery_embeddings_disabled")
        if cloud and not current_cloud_consent(settings):
            raise AiPolicyError("recovery_consent_required")
        modes = await self.model_fence(
            {int(plan.chat_id): plan.authorization_epoch},
            PermissionName.AUTO_KNOWLEDGE,
            cloud=view["settings"].embedding_profile.provider in {"openai", "openrouter"},
        )
        return plan, view, modes

    async def _prior_intent(self, session, message):
        request_id = (message.metadata_json or {}).get("embedding_request_id")
        if not request_id:
            return
        reservation = await session.get(AiBudgetReservation, request_id)
        if reservation is None or reservation.state in {"reserved", "submitted", "uncertain"}:
            raise AiUncertainError("recovery_existing_intent_requires_reconciliation")
        if reservation.state == "settled" and message.vector_status != "indexed":
            raise AiUncertainError("recovery_existing_outcome_requires_reconciliation")

    async def _stage(self, lease, plan, view, record):
        path = generation_path(view["settings"], record["generation_id"])
        staged = LocalVectorStore(
            path,
            vector_size=view["settings"].embedding_profile.dimension,
            profile=view["settings"].embedding_profile,
        )
        try:
            # Copy one page and its SQL references at a time; preserve source checks.
            own_ids = {row.id for row in view["canonical"].values()}
            old = self.vectors_getter()
            offset = None
            while True:
                page, offset = old.client.scroll(
                    old.COLLECTION,
                    offset=offset,
                    limit=256,
                    with_payload=True,
                    with_vectors=True,
                )
                wanted = {(point.payload or {}).get("reference_id") for point in page}
                async with self.database.session() as session:
                    rows = {
                        row.id: row
                        for row in (
                            await session.scalars(
                                select(TelegramMessage).where(TelegramMessage.id.in_(wanted)),
                            )
                        ).all()
                    }
                retained = []
                for point in page:
                    payload = point.payload or {}
                    row = rows.get(payload.get("reference_id"))
                    if (
                        row
                        and not row.is_deleted
                        and view["index"].current(row)
                        and (row.chat_id != int(plan.chat_id) or row.id in own_ids)
                    ):
                        retained.append(
                            PointStruct(
                                id=point.id,
                                vector=point.vector,
                                payload=payload,
                            )
                        )
                if retained:
                    staged.client.upsert(staged.COLLECTION, retained, wait=True)
                if offset is None:
                    return staged

        except BaseException:
            staged.close()
            raise

    async def _repair(self, lease, staged, plan, view):
        canonical = list(view["canonical"].values())
        for message in canonical:
            decision = view["index"].decision(message, view["policy"])
            if staged.has_current_point(
                message.id,
                chat_id=message.chat_id,
                message_id=message.message_id,
                content_hash=decision.content_hash,
            ):
                async with self.database.session() as session:
                    job, _ = await self._job(session, lease)
                    request_id = job.payload.get("request_ids", {}).get(str(message.id))
                    if request_id:
                        reservation = await session.get(AiBudgetReservation, request_id)
                        if reservation is None or reservation.state != "settled":
                            raise AiUncertainError("recovery_staged_outcome_unverified")
                continue
            await self._fence(lease, full=False)
            async with self.database.session() as session:
                if session.bind.dialect.name == "sqlite":
                    await session.execute(text("BEGIN IMMEDIATE"))
                job, _ = await self._job(session, lease)
                await session.scalar(
                    select(VectorStore)
                    .where(VectorStore.store_id == plan.store_id)
                    .with_for_update()
                )
                current = await session.get(TelegramMessage, message.id)
                await self._prior_intent(session, current)
                request_id = digest([plan.plan_id, message.id, decision.content_hash])
                prior_intent = await session.scalar(
                    select(AppSetting)
                    .where(AppSetting.key == intent_key(plan.store_id, message.id))
                    .with_for_update()
                )
                if prior_intent and prior_intent.value.get("request_id") != request_id:
                    previous = await session.get(
                        AiBudgetReservation, prior_intent.value["request_id"]
                    )
                    if (
                        previous is None
                        or previous.state != "settled"
                        or not prior_intent.value.get("activated")
                    ):
                        raise AiUncertainError("recovery_previous_request_requires_reconciliation")
                reservation = await session.get(AiBudgetReservation, request_id)
                if reservation and reservation.state in {"settled", "submitted", "uncertain"}:
                    raise AiUncertainError("recovery_outcome_requires_reconciliation")
                intent = {
                    "request_id": request_id,
                    "plan_id": plan.plan_id,
                    "content_hash": decision.content_hash,
                    "activated": False,
                }
                if prior_intent:
                    prior_intent.value = intent
                else:
                    session.add(AppSetting(key=intent_key(plan.store_id, message.id), value=intent))
                job.payload = {
                    **job.payload,
                    "request_ids": {
                        **job.payload.get("request_ids", {}),
                        str(message.id): request_id,
                    },
                }
            _, _, modes = await self._fence(lease, full=False)

            async def before_submit():
                await self._fence(lease, mark_submission=True, full=False)

            async with self.database.session() as session:
                vectors = await self.embedding_getter().embed_many(
                    session,
                    [decision.normalized_text],
                    chat_id=message.chat_id,
                    source_modes=list(modes.values()),
                    request_id=request_id,
                    pre_submit=before_submit,
                )
            await self._fence(lease, full=False)
            if len(vectors) != 1:
                raise AiPolicyError("recovery_embedding_unavailable")
            staged.upsert_many(
                [(message.id, vectors[0], message.chat_id, message.message_id)],
                content_hashes={message.id: decision.content_hash},
            )
            if not staged.has_current_point(
                message.id,
                chat_id=message.chat_id,
                message_id=message.message_id,
                content_hash=decision.content_hash,
            ):
                raise ValueError("recovery_point_unverified")
            async with self.database.session() as session:
                job, _ = await self._job(session, lease)
                reservation = await session.get(AiBudgetReservation, request_id)
                if reservation is None or reservation.state != "settled":
                    raise AiUncertainError("recovery_outcome_requires_reconciliation")
                job.payload = {
                    **job.payload,
                    "external_effect_started": False,
                    "progress": min(
                        90, int(90 * (canonical.index(message) + 1) / max(1, len(canonical)))
                    ),
                }

    def _verify(self, staged, plan, view):
        canonical = list(view["canonical"].values())
        expected = {row.id for row in canonical}
        if set(staged.reference_ids(chat_id=int(plan.chat_id))) != expected:
            raise ValueError("recovery_count_unverified")
        for row in canonical:
            decision = view["index"].decision(row, view["policy"])
            if not staged.has_current_point(
                row.id,
                chat_id=row.chat_id,
                message_id=row.message_id,
                content_hash=decision.content_hash,
            ):
                raise ValueError("recovery_point_unverified")
            retrieved = staged.client.retrieve(
                staged.COLLECTION,
                ids=[staged.point_id(row.id, chat_id=row.chat_id, message_id=row.message_id)],
                with_vectors=True,
            )
            hits = staged.search(
                retrieved[0].vector,
                allowed_chat_ids=[row.chat_id],
                allowed_reference_ids=[row.id],
                limit=1,
            )
            if not hits or hits[0][0] != row.id:
                raise ValueError("recovery_retrieval_unverified")

    async def _activate(self, lease, staged, plan, view, record):
        await self._fence(lease)
        settings = self.settings_getter()
        pointer = {
            "profile_id": settings.profile_id,
            "sid": current_user_sid(),
            "owner_id": self.owner(),
            "store_id": plan.store_id,
            "identity": record["identity"],
            "generation_id": record["generation_id"],
            "generation": plan.index_generation + 1,
            "state": "ready",
            "verified_corpus": corpus_hash(staged),
        }
        path = generation_path(settings, record["generation_id"], existing=True)
        ready_file = path / "recovery-ready.json"
        if not ready_file.exists():
            write_ready(path, pointer)
        elif json.loads(ready_file.read_text(encoding="utf-8")) != pointer:
            raise ValueError("recovery_generation_changed")
        async with self.database.session() as session:
            if session.bind.dialect.name == "sqlite":
                await session.execute(text("BEGIN IMMEDIATE"))
            job, plan_row = await self._job(session, lease)
            _, fresh = await self._validate(session, plan_row.value, expiry=False, locked=True)
            # The registry row serializes competing generation commits on MySQL;
            # SQLite holds its writer lock before the final read.
            current_pointer = await session.scalar(
                select(AppSetting)
                .where(AppSetting.key == pointer_key(plan.store_id))
                .with_for_update()
            )
            if current_pointer:
                if current_pointer.value["generation"] != plan.index_generation:
                    raise ValueError("recovery_plan_stale")
                current_pointer.value = pointer
            else:
                session.add(AppSetting(key=pointer_key(plan.store_id), value=pointer))
            profile = settings.embedding_profile
            canonical = {row.id: row for row in fresh["canonical"].values()}
            content_canonicals = fresh["canonical"]
            for row in fresh["rows"]:
                decision = fresh["index"].decision(row, fresh["policy"])
                if row.id in canonical:
                    row.normalized_text, row.content_hash = (
                        decision.normalized_text,
                        decision.content_hash,
                    )
                    row.vector_status, row.vector_dirty = "indexed", False
                    row.embedding_provider, row.embedding_model = profile.provider, profile.model
                    row.embedding_version, row.embedded_at = (
                        profile.embedding_version,
                        datetime.now(UTC),
                    )
                    row.embedding_skip_reason, row.embedding_error = None, None
                    request_id = job.payload.get("request_ids", {}).get(str(row.id))
                    if request_id:
                        intent = await session.get(AppSetting, intent_key(plan.store_id, row.id))
                        if intent is None or intent.value.get("request_id") != request_id:
                            raise AiUncertainError("recovery_intent_changed")
                        intent.value = {**intent.value, "activated": True}
                    row.metadata_json = {
                        **(row.metadata_json or {}),
                        "embedding_store_id": profile.store_id,
                        "embedding_content_hash": decision.content_hash,
                        "embedding_policy_version": fresh["index"].policy_version(fresh["policy"]),
                        **({"embedding_request_id": request_id} if request_id else {}),
                    }
                else:
                    row.vector_dirty = False
                    row.vector_status = "skipped"
                    canonical_row = content_canonicals.get(decision.content_hash)
                    row.embedding_skip_reason = (
                        decision.reason
                        if not decision.eligible
                        else "duplicate"
                        if canonical_row
                        else "quota_exceeded"
                    )
                    if canonical_row:
                        row.metadata_json = {
                            **(row.metadata_json or {}),
                            "duplicate_reference_id": canonical_row.id,
                        }
            for previous in (
                await session.scalars(
                    select(VectorStore)
                    .where(
                        VectorStore.role == "semantic_active", VectorStore.store_id != plan.store_id
                    )
                    .with_for_update()
                )
            ).all():
                previous.role, previous.state = "semantic_previous", "ready"
            fresh["store"].path = str(path.resolve())
            fresh["store"].role, fresh["store"].state = "semantic_active", "active"
            fresh["store"].last_reconciled_at = datetime.now(UTC)
            coverage = await session.get(
                VectorSourceCoverage, {"store_id": plan.store_id, "chat_id": int(plan.chat_id)}
            )
            # Snapshot completion does not imply that newly arrived source rows
            # have been indexed. Report current whole-source coverage truthfully.
            current_rows = list(
                (
                    await session.scalars(
                        select(TelegramMessage)
                        .where(
                            TelegramMessage.chat_id == int(plan.chat_id),
                            TelegramMessage.is_deleted.is_(False),
                        )
                        .with_for_update()
                        .execution_options(populate_existing=True),
                    )
                ).all()
            )
            current_index = SourceIndexService(settings, staged)
            eligible_ids = {
                row.id
                for row in current_index.admitted_canonicals(
                    current_rows,
                    fresh["policy"],
                ).values()
            }
            active_ids = set(staged.reference_ids(chat_id=int(plan.chat_id)))
            verified_ids = {row.id for row in current_rows if current_index.current(row)}
            missing_ids, orphan_ids = eligible_ids - verified_ids, active_ids - eligible_ids
            incomplete = bool(missing_ids or orphan_ids)
            values = {
                "mysql_total": len(current_rows),
                "eligible_total": len(eligible_ids),
                "active_vector_count": len(active_ids),
                "missing_count": len(missing_ids),
                "orphan_count": len(orphan_ids),
                "coverage_state": "reconciliation_required" if incomplete else "healthy",
                "reconciled_at": datetime.now(UTC),
            }
            if coverage is None:
                session.add(
                    VectorSourceCoverage(
                        store_id=plan.store_id, chat_id=int(plan.chat_id), **values
                    )
                )
            else:
                for name, value in values.items():
                    setattr(coverage, name, value)
            plan_row.value = {**plan_row.value, "state": "ready"}
            source = await refresh_source_usage(session, int(plan.chat_id), staged)
            source.status, source.last_job_id, source.last_error = (
                "reconciliation_required" if incomplete else "completed",
                job.id,
                "recovery_index_pending" if incomplete else None,
            )
            source.last_indexed_count = len(canonical)
            source.last_learned_at = datetime.now(UTC)
            job.status = "completed"
            job.payload = {
                **job.payload,
                "phase": "completed",
                "progress": 100,
                "external_effect_started": False,
            }
            job.last_error = None
            session.add(
                AuditLog(
                    occurred_at=datetime.now(UTC),
                    actor_id=self.owner(),
                    action="vector_recovery_verified",
                    target_type="knowledge_source",
                    target_id=plan.chat_id,
                    outcome="success",
                    correlation_id=job.id,
                    details_redacted={
                        "store_id": plan.store_id,
                        "expected_count": plan.expected_count,
                        "index_generation": pointer["generation"],
                    },
                )
            )
            job.locked_by = job.locked_at = job.claim_token = job.lease_expires_at = None
        # The candidate is already open; publication cannot accidentally create
        # an empty generation after a committed pointer. Old bytes are retained.
        if self.publish:
            self.publish(staged)
        else:
            staged.close()

    async def run(self, lease) -> OperationResult:
        staged = None
        view = {}
        async with self.lock:
            try:
                plan, view, _ = await self._fence(lease)
                async with self.database.session() as session:
                    job, row = await self._job(session, lease)
                    record = dict(row.value)
                    row.value = {**record, "state": "building"}
                    job.payload = {**job.payload, "phase": "building"}
                staged = await self._stage(lease, plan, view, record)
                await self._repair(lease, staged, plan, view)
                self._verify(staged, plan, view)
                await self._activate(lease, staged, plan, view, record)
                staged = None
                return self.result(lease.id, "completed", "recovery_verified", 100)
            except LeaseLost:
                raise
            except (
                AiUncertainError,
                AiPolicyError,
                AuthorizationRevoked,
                ValueError,
                OSError,
                RuntimeError,
            ) as exc:
                async with self.database.session() as session:
                    job, row = await self._job(session, lease)
                    uncertain = isinstance(exc, AiUncertainError) or bool(
                        job.payload.get("external_effect_started")
                    )
                    for request_id in job.payload.get("request_ids", {}).values():
                        reservation = await session.get(AiBudgetReservation, request_id)
                        uncertain |= bool(
                            reservation and reservation.state in {"submitted", "uncertain"}
                        )
                    # A prior foreign row intent also requires reconciliation.
                    for message in view.get("canonical", {}).values():
                        request_id = (message.metadata_json or {}).get("embedding_request_id")
                        if request_id:
                            reservation = await session.get(AiBudgetReservation, request_id)
                            uncertain |= reservation is None or reservation.state in {
                                "reserved",
                                "submitted",
                                "uncertain",
                            }
                    state = "uncertain" if uncertain else "failed"
                    job.status, job.last_error = (
                        state,
                        "recovery_requires_reconciliation"
                        if uncertain
                        else "recovery_not_verified",
                    )
                    job.payload = {
                        **job.payload,
                        "phase": state,
                        "requires_reconciliation": uncertain,
                    }
                    row.value = {**row.value, "state": state}
                    session.add(
                        AuditLog(
                            occurred_at=datetime.now(UTC),
                            actor_id=self.owner(),
                            action="vector_recovery_incomplete",
                            target_type="knowledge_source",
                            target_id=str(job.payload["chat_id"]),
                            outcome=state,
                            correlation_id=job.id,
                            reason=job.last_error,
                        )
                    )
                    job.locked_by = job.locked_at = job.claim_token = job.lease_expires_at = None
                return self.result(
                    lease.id,
                    state,
                    "recovery_requires_reconciliation" if uncertain else "recovery_not_verified",
                )
            finally:
                if staged is not None:
                    staged.close()

    @staticmethod
    def result(operation, state, code, progress=None):
        if state in {"pause_requested", "cancel_requested"}:
            state = "running"
        return OperationResult(
            operation_id=operation,
            state=state,
            progress=progress,
            code=code,
            message="Khôi phục đã kiểm chứng."
            if state == "completed"
            else "Khôi phục chưa hoàn tất; kho cũ được giữ lại.",
            next_action=None if state == "completed" else "Kiểm tra trước khi thử lại.",
        )

    async def control(self, operation_id, operation, owner_id):
        self.owner(owner_id)
        async with self.database.session() as session:
            job = await session.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.id == operation_id, BackgroundJob.job_type == "vector_recovery"
                )
                .with_for_update()
            )
            if job is None or job.payload.get("owner_id") != owner_id:
                raise PermissionError("recovery_not_owner")
            if operation == "pause" and job.status in {"queued", "running"}:
                job.status = "paused" if job.status == "queued" else "pause_requested"
                job.paused_at = datetime.now(UTC)
            elif operation == "resume" and job.status == "paused":
                row = await session.get(AppSetting, plan_key(job.payload["plan_id"]))
                await self._validate(session, row.value, expiry=False, locked=True)
                if job.payload.get("external_effect_started"):
                    raise ValueError("recovery_requires_reconciliation")
                # MySQL DATETIME may round fractional seconds into the future.
                # Resuming is immediately eligible, with no scheduled delay.
                job.status, job.run_after, job.paused_at = "queued", None, None
            else:
                raise ValueError("recovery_control_refused")
            session.add(
                AuditLog(
                    occurred_at=datetime.now(UTC),
                    actor_id=owner_id,
                    action=f"vector_recovery_{operation}",
                    target_type="background_job",
                    target_id=job.id,
                    outcome=job.status,
                    correlation_id=job.id,
                )
            )
            return self.result(
                job.id, job.status, "recovery_control_updated", job.payload.get("progress")
            )
