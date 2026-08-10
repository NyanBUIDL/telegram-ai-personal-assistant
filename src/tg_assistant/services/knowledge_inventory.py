from __future__ import annotations

import csv
import io
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    AppSetting,
    BackgroundJob,
    KnowledgeSource,
    PermissionName,
    TelegramChat,
    TelegramChatPermission,
    TelegramChatPolicy,
    TelegramMessage,
)
from ..security import redact

LEARNING_CHAT_TYPES = ("group", "supergroup", "channel")
AUDIT_REQUIRED_COLUMNS = {"chat_id", "can_hoc", "ghi_chu"}

STATUS_LABELS = {
    "learned": "Đã học",
    "learned_warning": "Đã học - cập nhật gần nhất lỗi",
    "not_learned": "Chưa học",
    "no_content": "Không học được - không lấy được nội dung",
    "queued": "Đang chờ học",
    "running": "Đang học",
    "failed": "Học lỗi",
}


@dataclass(slots=True)
class LearningInventoryRow:
    chat: TelegramChat
    source: KnowledgeSource
    reason: str


@dataclass(frozen=True, slots=True)
class LearningAuditEdit:
    chat_id: int
    should_learn: bool
    note: str


def knowledge_status_reason(status: str, last_error: str | None) -> str:
    if status == "learned":
        return "Đã có nội dung trong kho kiến thức hợp nhất."
    if status == "learned_warning":
        return f"Kho cũ vẫn dùng được; cập nhật gần nhất lỗi: {last_error or 'không rõ lỗi'}"
    if status == "not_learned":
        return "Nguồn chưa được chọn vào flow học."
    if status == "no_content":
        return (
            "Không lấy được nội dung văn bản; có thể nguồn trống, không cho đọc lịch sử "
            "hoặc chỉ có media."
        )
    if status == "queued":
        return "Nguồn đang nằm trong hàng đợi học."
    if status == "running":
        return "Worker đang đồng bộ và lập chỉ mục nguồn này."
    return f"Lần học gần nhất lỗi: {last_error or 'không rõ lỗi'}"


async def reconcile_knowledge_sources(
    session: AsyncSession,
) -> list[LearningInventoryRow]:
    """Refresh the persistent source inventory from MySQL's source-of-truth tables."""
    chats = list(
        (
            await session.scalars(
                select(TelegramChat)
                .where(TelegramChat.chat_type.in_(LEARNING_CHAT_TYPES))
                .order_by(TelegramChat.title, TelegramChat.chat_id)
            )
        ).all()
    )
    if not chats:
        return []
    chat_ids = [chat.chat_id for chat in chats]
    stats_rows = (
        await session.execute(
            select(
                TelegramMessage.chat_id,
                func.sum(
                    case(
                        (TelegramMessage.is_deleted.is_(False), 1),
                        else_=0,
                    )
                ),
                func.sum(
                    case(
                        (
                            and_(
                                TelegramMessage.is_deleted.is_(False),
                                TelegramMessage.text.is_not(None),
                                func.length(func.trim(TelegramMessage.text)) > 0,
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
            )
            .where(TelegramMessage.chat_id.in_(chat_ids))
            .group_by(TelegramMessage.chat_id)
        )
    ).all()
    stats = {
        int(chat_id): (int(message_count or 0), int(text_count or 0))
        for chat_id, message_count, text_count in stats_rows
    }
    policies = set(
        (
            await session.scalars(
                select(TelegramChatPolicy.chat_id).where(
                    TelegramChatPolicy.chat_id.in_(chat_ids),
                    TelegramChatPolicy.allowed.is_(True),
                )
            )
        ).all()
    )
    auto_knowledge = set(
        (
            await session.scalars(
                select(TelegramChatPermission.chat_id).where(
                    TelegramChatPermission.chat_id.in_(chat_ids),
                    TelegramChatPermission.permission == PermissionName.AUTO_KNOWLEDGE.value,
                    TelegramChatPermission.enabled.is_(True),
                )
            )
        ).all()
    )
    checkpoint_rows = (
        await session.scalars(
            select(AppSetting).where(AppSetting.key.like("knowledge_checkpoint:%"))
        )
    ).all()
    checkpoints: dict[int, int] = {}
    for checkpoint in checkpoint_rows:
        try:
            checkpoints[int(checkpoint.key.rsplit(":", 1)[1])] = int(checkpoint.value or 0)
        except (TypeError, ValueError):
            continue
    jobs = list(
        (
            await session.scalars(
                select(BackgroundJob)
                .where(BackgroundJob.job_type == "learn_group")
                .order_by(BackgroundJob.created_at.desc())
                .limit(10_000)
            )
        ).all()
    )
    latest_jobs: dict[int, BackgroundJob] = {}
    ever_indexed: set[int] = set()
    for job in jobs:
        payload = job.payload or {}
        try:
            chat_id = int(payload["chat_id"])
        except (KeyError, TypeError, ValueError):
            continue
        latest_jobs.setdefault(chat_id, job)
        if int(payload.get("indexed_count", 0) or 0) > 0:
            ever_indexed.add(chat_id)
    existing = {
        source.chat_id: source
        for source in (
            await session.scalars(
                select(KnowledgeSource).where(KnowledgeSource.chat_id.in_(chat_ids))
            )
        ).all()
    }
    rows: list[LearningInventoryRow] = []
    for chat in chats:
        message_count, text_count = stats.get(chat.chat_id, (0, 0))
        checkpoint_id = checkpoints.get(chat.chat_id, 0)
        job = latest_jobs.get(chat.chat_id)
        payload = dict(job.payload or {}) if job else {}
        has_existing_corpus = checkpoint_id > 0 and text_count > 0 and chat.chat_id in ever_indexed
        if job and job.status in {"queued", "running"}:
            status = job.status
        elif job and job.status == "failed":
            status = "learned_warning" if has_existing_corpus else "failed"
        elif has_existing_corpus:
            status = "learned"
        elif job and job.status == "completed":
            status = "no_content"
        else:
            status = "not_learned"
        source = existing.get(chat.chat_id)
        if not source:
            source = KnowledgeSource(chat_id=chat.chat_id)
            session.add(source)
        source.status = status
        source.mysql_message_count = message_count
        source.text_message_count = text_count
        source.last_indexed_count = int(payload.get("indexed_count", 0) or 0)
        source.last_job_id = job.id if job else None
        source.last_error = str(redact(job.last_error))[:1000] if job and job.last_error else None
        if status in {"learned", "learned_warning"} and source.last_learned_at is None:
            source.last_learned_at = job.updated_at if job else None
        if chat.chat_id not in policies or chat.chat_id not in auto_knowledge:
            if status not in {"queued", "running"}:
                source.requested_for_learning = False
        rows.append(
            LearningInventoryRow(
                chat=chat,
                source=source,
                reason=knowledge_status_reason(status, source.last_error),
            )
        )
    await session.flush()
    order = {
        "learned": 0,
        "learned_warning": 1,
        "queued": 2,
        "running": 3,
        "not_learned": 4,
        "no_content": 5,
        "failed": 6,
    }
    return sorted(
        rows,
        key=lambda row: (
            order.get(row.source.status, 99),
            (row.chat.title or "").casefold(),
            row.chat.chat_id,
        ),
    )


def _csv_safe(value: object) -> object:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def build_learning_inventory_csv(rows: list[LearningInventoryRow]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "chat_id",
            "ten_nguon",
            "loai",
            "username",
            "trang_thai",
            "ly_do",
            "tin_mysql",
            "tin_co_noi_dung",
            "embedding_gan_nhat",
            "can_hoc",
            "ghi_chu",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                f"'{row.chat.chat_id}",
                _csv_safe((row.chat.title or "").strip() or "Nguồn không có tiêu đề"),
                row.chat.chat_type,
                _csv_safe(f"@{row.chat.username}" if row.chat.username else ""),
                STATUS_LABELS.get(row.source.status, row.source.status),
                _csv_safe(row.reason),
                row.source.mysql_message_count,
                row.source.text_message_count,
                row.source.last_indexed_count,
                "CO" if row.source.requested_for_learning else "",
                _csv_safe(row.source.owner_note or ""),
            ]
        )
    return output.getvalue().encode("utf-8-sig")


def _normalized(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.strip())
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn").upper()


def _parse_chat_id(value: str) -> int:
    cleaned = value.strip().lstrip("'").replace(",", "").replace(" ", "")
    try:
        decimal_value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Chat ID không hợp lệ: {value}") from exc
    integral = decimal_value.to_integral_value()
    if decimal_value != integral:
        raise ValueError(f"Chat ID không phải số nguyên: {value}")
    return int(integral)


def parse_learning_inventory_csv(
    content: bytes,
    *,
    max_rows: int = 2_000,
) -> list[LearningAuditEdit]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("File phải là CSV UTF-8.") from exc
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    columns = set(reader.fieldnames or [])
    missing = AUDIT_REQUIRED_COLUMNS - columns
    if missing:
        raise ValueError(f"CSV thiếu cột: {', '.join(sorted(missing))}.")
    edits: dict[int, LearningAuditEdit] = {}
    truthy = {"1", "CO", "C", "YES", "Y", "TRUE", "X"}
    falsy = {"", "0", "KHONG", "NO", "N", "FALSE"}
    for index, row in enumerate(reader, start=1):
        if index > max_rows:
            raise ValueError(f"CSV vượt quá {max_rows} dòng.")
        chat_id = _parse_chat_id(str(row.get("chat_id", "")))
        requested = _normalized(str(row.get("can_hoc", "")))
        if requested not in truthy | falsy:
            raise ValueError(f"Dòng {index + 1}: cột can_hoc chỉ nhận CO/KHONG hoặc để trống.")
        note = str(row.get("ghi_chu", "") or "").strip()
        if len(note) > 1_000:
            raise ValueError(f"Dòng {index + 1}: ghi_chu vượt quá 1.000 ký tự.")
        edits[chat_id] = LearningAuditEdit(
            chat_id=chat_id,
            should_learn=requested in truthy,
            note=note,
        )
    return list(edits.values())


def inventory_counts(rows: list[LearningInventoryRow]) -> dict[str, int]:
    return {status: sum(row.source.status == status for row in rows) for status in STATUS_LABELS}
