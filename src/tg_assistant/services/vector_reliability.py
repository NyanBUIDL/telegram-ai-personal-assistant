"""Local-first vector store registry and read-only source coverage checks."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from sqlalchemy import delete, select, update

from ..ai.local_first import EmbeddingDecision, embedding_decision
from ..ai.vector import LocalVectorStore
from ..config import Settings
from ..db.models import (
    AiBudgetReservation,
    AppSetting,
    KnowledgeSource,
    TelegramChatPolicy,
    TelegramMessage,
    VectorSourceCoverage,
    VectorStore,
)


def local_store_id(settings: Settings) -> str:
    """Stable identity; does not depend on the selected chat-completion provider."""
    return settings.embedding_profile.store_id


def legacy_store_id(settings: Settings) -> str:
    return (
        f"legacy-{sha256(str(settings.resolved_qdrant_path.resolve()).encode()).hexdigest()[:16]}"
    )


async def ensure_vector_store_registry(session, settings: Settings) -> VectorStore:
    """Register metadata only. Existing vectors are neither read nor modified here."""
    active_id = local_store_id(settings)
    profile = settings.embedding_profile
    active_path = str(settings.resolved_semantic_vector_path.resolve())
    active = await session.get(VectorStore, active_id)
    previous = await session.scalar(
        select(VectorStore).where(
            VectorStore.role == "semantic_active", VectorStore.store_id != active_id
        )
    )
    values = {
        "path": active_path,
        "collection": LocalVectorStore.COLLECTION,
        "provider": profile.provider,
        "endpoint_id": profile.endpoint_id,
        "model": profile.model,
        "embedding_version": settings.embedding_version,
        "dimension": profile.dimension,
        "role": "semantic_candidate" if previous is not None else "semantic_active",
        "state": "candidate" if previous is not None else "active",
    }
    if active is None:
        active = VectorStore(store_id=active_id, **values)
        session.add(active)
    else:
        if any(
            getattr(active, name) != value
            for name, value in values.items()
            if name not in {"role", "state"}
        ):
            raise ValueError("Registered vector identity mismatch")

    legacy_path = settings.resolved_qdrant_path.resolve()
    if legacy_path != Path(active_path):
        legacy_id = legacy_store_id(settings)
        legacy = await session.get(VectorStore, legacy_id)
        existing_path = await session.scalar(
            select(VectorStore).where(VectorStore.path == str(legacy_path))
        )
        legacy_values = {
            "path": str(legacy_path),
            "collection": LocalVectorStore.COLLECTION,
            "provider": "legacy",
            "model": None,
            "embedding_version": None,
            "dimension": settings.qdrant_vector_size,
            "role": "legacy_read_only",
            "state": "read_only",
        }
        if legacy is None and existing_path is None:
            session.add(VectorStore(store_id=legacy_id, **legacy_values))
        elif legacy is not None:
            if legacy.provider != "legacy" or legacy.path != str(legacy_path):
                raise ValueError("Legacy vector identity mismatch")
    return active


async def inspect_source_coverage(
    session, *, chat_id: int, settings: Settings, vectors: LocalVectorStore
) -> dict:
    """Compare MySQL source data with the active Local-first index without writes to Qdrant."""
    policy = await session.scalar(
        select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
    )
    rows = list(
        (
            await session.scalars(
                select(TelegramMessage).where(
                    TelegramMessage.chat_id == chat_id,
                    TelegramMessage.is_deleted.is_(False),
                )
            )
        ).all()
    )
    index = SourceIndexService(settings, vectors)
    canonical = index.admitted_canonicals(rows, policy)
    eligible_ids = {row.id for row in canonical.values()}
    active_ids = set(vectors.reference_ids(chat_id=chat_id))
    verified_ids = {row.id for row in canonical.values() if index.current(row)}
    missing_ids = eligible_ids - verified_ids
    orphan_ids = active_ids - eligible_ids
    eligible_total = len(eligible_ids)
    active_count = len(active_ids)
    coverage_percent = (
        round((len(verified_ids) / eligible_total) * 100, 1) if eligible_total else 100.0
    )
    coverage_state = "healthy" if not missing_ids and not orphan_ids else "reconciliation_required"
    now = datetime.now(UTC)
    store = await ensure_vector_store_registry(session, settings)
    store.last_reconciled_at = now
    coverage = await session.get(
        VectorSourceCoverage, {"store_id": store.store_id, "chat_id": chat_id}
    )
    values = {
        "mysql_total": len(rows),
        "eligible_total": eligible_total,
        "active_vector_count": active_count,
        "missing_count": len(missing_ids),
        "orphan_count": len(orphan_ids),
        "coverage_state": coverage_state,
        "reconciled_at": now,
    }
    if coverage is None:
        session.add(VectorSourceCoverage(store_id=store.store_id, chat_id=chat_id, **values))
    else:
        for name, value in values.items():
            setattr(coverage, name, value)
    return {
        "chat_id": str(chat_id),
        "mysql_total": len(rows),
        "expected_eligible_messages": eligible_total,
        "active_vectors": active_count,
        "missing_count": len(missing_ids),
        "orphan_count": len(orphan_ids),
        "coverage_percent": coverage_percent,
        "coverage_state": coverage_state,
        "active_vector_store": {
            "id": store.store_id,
            "path": store.path,
            "model": store.model,
            "embedding_version": store.embedding_version,
            "dimension": store.dimension,
        },
        "missing_reference_ids_sample": sorted(missing_ids)[:20],
        "orphan_reference_ids_sample": sorted(orphan_ids)[:20],
        "last_reconciled_at": now.isoformat(),
    }


class SourceIndexService:
    """One store's eligibility and durable point verification, shared by coverage."""

    def __init__(self, settings: Settings, vectors: LocalVectorStore):
        self.settings, self.vectors = settings, vectors
        self.profile = settings.embedding_profile

    @staticmethod
    def policy_version(policy) -> str:
        return sha256(
            json.dumps(
                {
                    name: getattr(policy, name, None)
                    for name in (
                        "authorization_epoch",
                        "allowed",
                        "ai_mode",
                        "filtering_level",
                        "retention_days",
                        "max_messages",
                        "max_vectors",
                        "max_storage_mb",
                    )
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def decision(self, row, policy):
        excluded = row.is_deleted
        if policy and policy.retention_days:
            sent_at = row.sent_at if row.sent_at.tzinfo else row.sent_at.replace(tzinfo=UTC)
            excluded = excluded or sent_at < datetime.now(UTC) - timedelta(
                days=policy.retention_days
            )
        if policy:
            excluded = excluded or not policy.allowed or policy.ai_mode == "off"
            excluded = excluded or (
                self.profile.provider in {"openai", "openrouter"} and policy.ai_mode == "local_only"
            )
        decision = embedding_decision(
            row.text,
            metadata=row.metadata_json,
            filtering_level=policy.filtering_level if policy else "standard",
            retention_excluded=excluded,
        )
        if (
            decision.eligible
            and len(decision.normalized_text.encode("utf-8")) + 16
            > self.settings.max_input_tokens_per_request
        ):
            return EmbeddingDecision(
                False, decision.normalized_text, decision.content_hash, "request_too_large"
            )
        return decision

    @staticmethod
    def point_cap(policy) -> int | None:
        caps = [
            value
            for value in (
                getattr(policy, "max_vectors", None),
                getattr(policy, "max_messages", None),
            )
            if value is not None
        ]
        return min(caps) if caps else None

    def admitted_canonicals(self, rows, policy) -> dict:
        """Deterministic quota plan shared by indexing and expected coverage.

        Preserve verified existing knowledge first, then existing point repairs,
        then oldest new candidates. Duplicates consume one slot in this source.
        Denied admissions are accounted exclusions, not missing derived points.
        """
        eligible = [row for row in rows if self.decision(row, policy).eligible]
        present = (
            set(self.vectors.reference_ids(chat_id=policy.chat_id if policy else rows[0].chat_id))
            if rows
            else set()
        )
        canonical = {}
        for row in sorted(
            eligible, key=lambda row: (not self.current(row), row.id not in present, row.id)
        ):
            canonical.setdefault(self.decision(row, policy).content_hash, row)
        cap = self.point_cap(policy)
        return dict(list(canonical.items())[: max(0, cap)]) if cap is not None else canonical

    def current(self, row) -> bool:
        decision = embedding_decision(
            row.text, metadata=row.metadata_json, filtering_level="relaxed"
        )
        metadata = row.metadata_json or {}
        return (
            row.vector_status == "indexed"
            and row.embedding_provider == self.profile.provider
            and row.embedding_model == self.profile.model
            and row.embedding_version == self.profile.embedding_version
            and metadata.get("embedding_store_id") == self.profile.store_id
            and metadata.get("embedding_content_hash") == decision.content_hash
            and self.vectors.has_current_point(
                row.id,
                chat_id=row.chat_id,
                message_id=row.message_id,
                content_hash=decision.content_hash,
            )
        )


def invalidate_restored_index(connection, *, store_id: str | None = None) -> int:
    """Invalidate derived metadata in caller's migrated, fenced staging transaction.

    Portable restores omit vectors. Keep policy, budgets, uncertain outcomes and
    source data intact; the store must be explicitly reconciled before activation.
    """
    messages = TelegramMessage.__table__
    rows = connection.execute(
        select(messages.c.id, messages.c.chat_id, messages.c.metadata_json)
    ).all()
    count = 0
    affected_chats = set()
    for row_id, chat_id, metadata in rows:
        metadata = dict(metadata or {})
        if store_id is not None and metadata.get("embedding_store_id") != store_id:
            continue
        request_id = metadata.get("embedding_request_id")
        reservation_state = (
            connection.execute(
                select(AiBudgetReservation.state).where(
                    AiBudgetReservation.request_id == request_id
                )
            ).scalar()
            if request_id
            else None
        )
        for key in ("embedding_store_id", "embedding_content_hash", "duplicate_reference_id"):
            metadata.pop(key, None)
        # A restore is not evidence of an uncertain provider outcome. Preserve
        # the unresolved intent so explicit recovery cannot blindly retry it.
        if reservation_state in {"settled", "released"}:
            metadata.pop("embedding_request_id", None)
        # Budget outcomes are retained. The new generation separates explicit
        # post-restore repair requests from historical settled reservations.
        metadata["embedding_repair_generation"] = (
            int(metadata.get("embedding_repair_generation", 0)) + 1
        )
        connection.execute(
            update(messages)
            .where(messages.c.id == row_id)
            .values(
                vector_status="pending",
                vector_dirty=True,
                embedding_provider=None,
                embedding_model=None,
                embedding_version=None,
                embedded_at=None,
                embedding_error=None,
                embedding_skip_reason=None,
                metadata_json=metadata,
            )
        )
        count += 1
        affected_chats.add(chat_id)
    checkpoints = select(AppSetting.key).where(AppSetting.key.like("knowledge_checkpoint:%"))
    for key in connection.execute(checkpoints).scalars():
        if store_id is None or key.startswith(f"knowledge_checkpoint:{store_id}:"):
            connection.execute(delete(AppSetting).where(AppSetting.key == key))
    stores = update(VectorStore).values(state="reconciliation_required", last_reconciled_at=None)
    coverage = update(VectorSourceCoverage).values(
        coverage_state="reconciliation_required", active_vector_count=0
    )
    if store_id is not None:
        stores = stores.where(VectorStore.store_id == store_id)
        coverage = coverage.where(VectorSourceCoverage.store_id == store_id)
    connection.execute(stores)
    connection.execute(coverage)
    connection.execute(
        update(KnowledgeSource)
        .where(KnowledgeSource.chat_id.in_(affected_chats))
        .values(status="reconciliation_required", vector_count=0)
    )
    return count
