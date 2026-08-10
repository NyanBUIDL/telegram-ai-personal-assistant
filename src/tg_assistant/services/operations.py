from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.vector import LocalVectorStore
from ..db.models import (
    BackgroundJob,
    KnowledgeSource,
    RuntimeMetric,
    TelegramAttachment,
    TelegramChatPolicy,
    TelegramMessage,
)

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CleanupReport:
    expired_messages: int
    expired_vectors: int
    orphan_vectors: int
    media_files: int
    media_bytes: int
    dry_run: bool


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())


async def refresh_source_usage(
    session: AsyncSession,
    chat_id: int,
    vectors: LocalVectorStore | None,
) -> KnowledgeSource:
    source = await session.get(KnowledgeSource, chat_id)
    if not source:
        source = KnowledgeSource(chat_id=chat_id)
        session.add(source)
    source.mysql_message_count = int(
        (
            await session.scalar(
                select(func.count(TelegramMessage.id)).where(
                    TelegramMessage.chat_id == chat_id,
                    TelegramMessage.is_deleted.is_(False),
                )
            )
        )
        or 0
    )
    source.text_message_count = int(
        (
            await session.scalar(
                select(func.count(TelegramMessage.id)).where(
                    TelegramMessage.chat_id == chat_id,
                    TelegramMessage.is_deleted.is_(False),
                    TelegramMessage.text.is_not(None),
                )
            )
        )
        or 0
    )
    source.message_storage_bytes = int(
        (
            await session.scalar(
                select(func.sum(func.length(TelegramMessage.text))).where(
                    TelegramMessage.chat_id == chat_id,
                    TelegramMessage.is_deleted.is_(False),
                )
            )
        )
        or 0
    )
    source.media_storage_bytes = int(
        (
            await session.scalar(
                select(func.sum(TelegramAttachment.size_bytes))
                .join(
                    TelegramMessage,
                    TelegramMessage.id == TelegramAttachment.telegram_message_id,
                )
                .where(TelegramMessage.chat_id == chat_id)
            )
        )
        or 0
    )
    source.vector_count = vectors.count(chat_id=chat_id) if vectors else 0
    return source


def allowed_index_count(
    policy: TelegramChatPolicy | None,
    source: KnowledgeSource,
    requested: int,
) -> int:
    remaining = requested
    if policy and policy.max_vectors is not None:
        remaining = min(remaining, max(0, policy.max_vectors - source.vector_count))
    if policy and policy.max_messages is not None:
        remaining = min(remaining, max(0, policy.max_messages - source.vector_count))
    if policy and policy.max_storage_mb is not None:
        available_bytes = max(
            0,
            policy.max_storage_mb * 1024 * 1024
            - source.message_storage_bytes
            - source.media_storage_bytes,
        )
        if available_bytes <= 0:
            return 0
    return remaining


async def cleanup_storage(
    session: AsyncSession,
    vectors: LocalVectorStore | None,
    *,
    media_root: Path,
    dry_run: bool,
) -> CleanupReport:
    now = datetime.now(UTC)
    expired_ids: list[int] = []
    media_paths: list[tuple[Path, int]] = []
    policies = list(
        (
            await session.scalars(
                select(TelegramChatPolicy).where(
                    TelegramChatPolicy.allowed.is_(True),
                )
            )
        ).all()
    )
    for policy in policies:
        if policy.retention_days:
            cutoff = now - timedelta(days=int(policy.retention_days))
            expired_ids.extend(
                (
                    await session.scalars(
                        select(TelegramMessage.id).where(
                            TelegramMessage.chat_id == policy.chat_id,
                            TelegramMessage.sent_at < cutoff,
                        )
                    )
                ).all()
            )
        if policy.max_messages:
            over_limit_ids = (
                await session.scalars(
                    select(TelegramMessage.id)
                    .where(TelegramMessage.chat_id == policy.chat_id)
                    .order_by(TelegramMessage.sent_at.desc())
                    .offset(int(policy.max_messages))
                )
            ).all()
            expired_ids.extend(over_limit_ids)
        if policy.max_storage_mb:
            text_rows = (
                await session.execute(
                    select(
                        TelegramMessage.id,
                        func.length(TelegramMessage.text),
                    )
                    .where(TelegramMessage.chat_id == policy.chat_id)
                    .order_by(TelegramMessage.sent_at.desc())
                )
            ).all()
            budget = int(policy.max_storage_mb) * 1024 * 1024
            used = 0
            for reference_id, text_bytes in text_rows:
                used += int(text_bytes or 0)
                if used > budget:
                    expired_ids.append(int(reference_id))
    expired_ids = list(dict.fromkeys(int(value) for value in expired_ids))
    if expired_ids:
        attachments = (
            await session.execute(
                select(TelegramAttachment.local_path, TelegramAttachment.size_bytes).where(
                    TelegramAttachment.telegram_message_id.in_(expired_ids),
                    TelegramAttachment.local_path.is_not(None),
                )
            )
        ).all()
        media_paths = [(Path(path).resolve(), int(size or 0)) for path, size in attachments if path]

    vector_ids = set(vectors.reference_ids()) if vectors else set()
    valid_ids = set(
        int(value) for value in (await session.scalars(select(TelegramMessage.id))).all()
    )
    quota_vector_ids: set[int] = set()
    if vectors:
        for policy in policies:
            if not policy.max_vectors:
                continue
            chat_vector_ids = sorted(
                vectors.reference_ids(chat_id=policy.chat_id),
                reverse=True,
            )
            quota_vector_ids.update(chat_vector_ids[int(policy.max_vectors) :])
    orphan_ids = sorted((vector_ids - valid_ids - set(expired_ids)) | quota_vector_ids)

    safe_media_root = media_root.resolve()
    safe_media = []
    for path, size in media_paths:
        try:
            path.relative_to(safe_media_root)
        except ValueError:
            continue
        safe_media.append((path, size))

    if not dry_run:
        if vectors:
            vectors.delete_reference_ids([*expired_ids, *orphan_ids])
        if expired_ids:
            await session.execute(
                delete(TelegramMessage).where(TelegramMessage.id.in_(expired_ids))
            )
        for path, _size in safe_media:
            if path.exists():
                path.unlink()

    return CleanupReport(
        expired_messages=len(expired_ids),
        expired_vectors=len(vector_ids & set(expired_ids)),
        orphan_vectors=len(orphan_ids),
        media_files=len(safe_media),
        media_bytes=sum(size for _path, size in safe_media),
        dry_run=dry_run,
    )


def _process_stats() -> tuple[int | None, float | None, list[dict]]:
    try:
        import psutil
    except ImportError:
        return None, None, []
    process = psutil.Process(os.getpid())
    children = []
    for child in process.children(recursive=True):
        try:
            children.append(
                {
                    "pid": child.pid,
                    "name": child.name(),
                    "rss_bytes": child.memory_info().rss,
                    "cpu_percent": child.cpu_percent(),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return process.memory_info().rss, process.cpu_percent(), children


def _vram_bytes() -> int | None:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None
    try:
        result = subprocess.run(
            [
                str(Path(executable).resolve()),
                "--query-compute-apps=pid,used_memory",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    total_mb = 0
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == 2 and parts[0] == str(os.getpid()):
            try:
                total_mb += int(parts[1])
            except ValueError:
                continue
    return total_mb * 1024 * 1024


async def collect_runtime_metric(
    session: AsyncSession,
    *,
    data_root: Path,
    vector_root: Path,
    media_root: Path,
) -> RuntimeMetric:
    rss, cpu, children = _process_stats()
    disk_capacity: dict[str, int] | None = None
    try:
        disk = shutil.disk_usage(data_root)
        disk_capacity = {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
        }
    except OSError:
        pass
    statuses = dict(
        (
            await session.execute(
                select(BackgroundJob.status, func.count(BackgroundJob.id))
                .where(BackgroundJob.job_type == "learn_group")
                .group_by(BackgroundJob.status)
            )
        ).all()
    )
    metric = RuntimeMetric(
        collected_at=datetime.now(UTC),
        process_id=os.getpid(),
        process_name="telegram-assistant",
        rss_bytes=rss,
        cpu_percent=cpu,
        vram_bytes=_vram_bytes(),
        data_bytes=directory_size(data_root),
        vector_bytes=directory_size(vector_root),
        media_bytes=directory_size(media_root),
        queued_jobs=int(statuses.get("queued", 0)),
        running_jobs=int(statuses.get("running", 0)),
        paused_jobs=int(statuses.get("paused", 0)) + int(statuses.get("pause_requested", 0)),
        details={
            "children": children,
            "disk_capacity": disk_capacity,
            "vram_status": (
                "unavailable" if shutil.which("nvidia-smi") is None else "available"
            ),
        },
    )
    session.add(metric)
    return metric
