"""Local-first vector store registry and read-only source coverage checks."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from sqlalchemy import select

from ..ai.local_first import embedding_decision
from ..ai.vector import LocalVectorStore
from ..config import Settings
from ..db.models import TelegramChatPolicy, TelegramMessage, VectorSourceCoverage, VectorStore


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
    filtering_level = policy.filtering_level if policy else "standard"
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
    eligible_ids = {
        row.id
        for row in rows
        if embedding_decision(
            row.text,
            metadata=row.metadata_json,
            filtering_level=filtering_level,
        ).eligible
    }
    active_ids = set(vectors.reference_ids(chat_id=chat_id))
    missing_ids = eligible_ids - active_ids
    orphan_ids = active_ids - {row.id for row in rows}
    eligible_total = len(eligible_ids)
    active_count = len(active_ids)
    coverage_percent = round((active_count / eligible_total) * 100, 1) if eligible_total else 100.0
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
