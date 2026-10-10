from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    PermissionName,
    TelegramChat,
    TelegramChatPermission,
    TelegramChatPolicy,
    TelegramMessage,
)
from ..security import contains_secret, redact
from .revocation import AuthorizationRevoked

DIGEST_CHAT_TYPES = ("group", "supergroup", "channel")
URL_RE = re.compile(r"(?i)\b(?:https?://|www\.|t\.me/)\S+")
WORD_RE = re.compile(r"\w+", re.UNICODE)
SPACE_RE = re.compile(r"\s+")
SOURCE_REF_RE = re.compile(r"\[S(\d+)]")


@dataclass(frozen=True, slots=True)
class DigestItem:
    chat_id: int
    message_id: int
    title: str
    username: str | None
    chat_type: str
    text: str
    sent_at: datetime


def digest_citation_priority(item: DigestItem) -> str:
    """Prefer explicitly configured direct publisher evidence over reposts."""
    if item.title.strip().casefold() == "coin68" or "coin68.com/" in item.text.casefold():
        return "primary_publisher"
    return "standard"


def _same_event(left: DigestItem, right: DigestItem) -> bool:
    left_tokens = _meaningful_tokens(_canonical_text(left.text))
    right_tokens = _meaningful_tokens(_canonical_text(right.text))
    shared = left_tokens & right_tokens
    if len(shared) < 3:
        return False
    return len(shared) / max(1, min(len(left_tokens), len(right_tokens))) >= 0.18


def prefer_primary_citations(answer: str, items: list[DigestItem]) -> str:
    """Deterministically replace repost citations with matching primary evidence.

    This is intentionally narrow: a replacement happens only when three or
    more meaningful tokens identify the same event. It changes presentation
    provenance only, never the source dataset or the generated claim text.
    """
    primary = [
        (index, item)
        for index, item in enumerate(items)
        if digest_citation_priority(item) == "primary_publisher"
    ]
    if not primary:
        return answer

    def replace(match: re.Match[str]) -> str:
        cited_index = int(match.group(1)) - 1
        if not 0 <= cited_index < len(items):
            return match.group(0)
        cited = items[cited_index]
        if digest_citation_priority(cited) == "primary_publisher":
            return match.group(0)
        preferred = next(
            (index for index, candidate in primary if _same_event(cited, candidate)),
            None,
        )
        return f"[S{preferred + 1}]" if preferred is not None else match.group(0)

    return SOURCE_REF_RE.sub(replace, answer)


@dataclass(slots=True)
class DigestSourceStats:
    title: str
    username: str | None
    chat_type: str
    raw_count: int = 0
    suitable_count: int = 0
    duplicate_count: int = 0
    unique_count: int = 0
    selected_count: int = 0


@dataclass(slots=True)
class DailyDigestDataset:
    joined_source_count: int
    authorized_source_count: int
    raw_message_count: int
    unsuitable_count: int
    duplicate_count: int
    unique_items: list[DigestItem]
    selected_items: list[DigestItem]
    authorization_epochs: tuple[tuple[int, int], ...]
    source_stats: dict[int, DigestSourceStats] = field(default_factory=dict)

    @property
    def active_source_count(self) -> int:
        return sum(stats.suitable_count > 0 for stats in self.source_stats.values())

    @property
    def missing_source_count(self) -> int:
        return max(0, self.joined_source_count - self.authorized_source_count)


def _canonical_text(text: str) -> str:
    without_urls = URL_RE.sub(" ", text.casefold())
    words = WORD_RE.findall(without_urls)
    return " ".join(words)


def _meaningful_tokens(canonical: str) -> set[str]:
    return {word for word in canonical.split() if len(word) >= 3}


def _is_suitable(text: str, canonical: str) -> bool:
    if not canonical or contains_secret(text):
        return False
    tokens = canonical.split()
    if len(canonical) < 15 or len(tokens) < 3:
        return False
    return any(char.isalpha() for char in canonical)


def _near_duplicate(left: set[str], right: set[str]) -> bool:
    if not left or not right:
        return False
    overlap = len(left & right)
    union = len(left | right)
    smaller = min(len(left), len(right))
    return overlap / union >= 0.82 or overlap / smaller >= 0.92


def deduplicate_digest_items(
    items: list[DigestItem],
) -> tuple[list[DigestItem], int, int, dict[int, DigestSourceStats]]:
    unique: list[DigestItem] = []
    canonical_seen: set[str] = set()
    token_sets: list[set[str]] = []
    token_index: dict[str, list[int]] = {}
    unsuitable = 0
    duplicates = 0
    stats: dict[int, DigestSourceStats] = {}
    for item in items:
        source = stats.setdefault(
            item.chat_id,
            DigestSourceStats(
                title=item.title,
                username=item.username,
                chat_type=item.chat_type,
            ),
        )
        source.raw_count += 1
        text = SPACE_RE.sub(" ", item.text).strip()
        canonical = _canonical_text(text)
        if not _is_suitable(text, canonical):
            unsuitable += 1
            continue
        source.suitable_count += 1
        tokens = _meaningful_tokens(canonical)
        duplicate = canonical in canonical_seen
        if not duplicate:
            candidates: set[int] = set()
            for token in sorted(tokens, key=len, reverse=True)[:8]:
                candidates.update(token_index.get(token, [])[-50:])
            duplicate = any(_near_duplicate(tokens, token_sets[index]) for index in candidates)
        if duplicate:
            duplicates += 1
            source.duplicate_count += 1
            continue
        canonical_seen.add(canonical)
        index = len(unique)
        unique.append(
            DigestItem(
                chat_id=item.chat_id,
                message_id=item.message_id,
                title=item.title,
                username=item.username,
                chat_type=item.chat_type,
                text=str(redact(text)),
                sent_at=item.sent_at,
            )
        )
        token_sets.append(tokens)
        for token in tokens:
            token_index.setdefault(token, []).append(index)
        source.unique_count += 1
    return unique, unsuitable, duplicates, stats


def select_digest_context(
    items: list[DigestItem],
    stats: dict[int, DigestSourceStats],
    *,
    max_context_chars: int = 36_000,
    max_item_chars: int = 700,
) -> list[DigestItem]:
    """Select fairly across sources before filling remaining context by recency."""
    by_source: dict[int, list[DigestItem]] = {}
    for item in items:
        by_source.setdefault(item.chat_id, []).append(item)
    def order_key(item: DigestItem) -> tuple[int, float]:
        priority = 0 if digest_citation_priority(item) == "primary_publisher" else 1
        sent_at = item.sent_at.timestamp() if item.sent_at.tzinfo else 0.0
        return priority, -sent_at

    representatives = sorted(
        (source_items[0] for source_items in by_source.values()),
        key=order_key,
    )
    remaining = sorted(
        (item for source_items in by_source.values() for item in source_items[1:]),
        key=order_key,
    )
    selected: list[DigestItem] = []
    selected_keys: set[tuple[int, int]] = set()
    used_chars = 0
    for item in [*representatives, *remaining]:
        key = (item.chat_id, item.message_id)
        if key in selected_keys:
            continue
        estimated_chars = min(len(item.text), max_item_chars) + 260
        if selected and used_chars + estimated_chars > max_context_chars:
            continue
        selected.append(item)
        selected_keys.add(key)
        used_chars += estimated_chars
        stats[item.chat_id].selected_count += 1
    return selected


async def load_daily_digest_dataset(
    session: AsyncSession,
    *,
    since: datetime,
    until: datetime,
    max_context_chars: int = 36_000,
) -> DailyDigestDataset:
    joined_source_count = int(
        len(
            (
                await session.scalars(
                    select(TelegramChat.chat_id).where(
                        TelegramChat.chat_type.in_(DIGEST_CHAT_TYPES)
                    )
                )
            ).all()
        )
    )
    authorization_epochs = tuple(
        (chat_id, epoch)
        for chat_id, epoch in (
            await session.execute(
                select(TelegramChatPermission.chat_id, TelegramChatPolicy.authorization_epoch)
                .join(
                    TelegramChatPolicy,
                    TelegramChatPolicy.chat_id == TelegramChatPermission.chat_id,
                )
                .join(
                    TelegramChat,
                    TelegramChat.chat_id == TelegramChatPermission.chat_id,
                )
                .where(
                    TelegramChat.chat_type.in_(DIGEST_CHAT_TYPES),
                    TelegramChatPolicy.allowed.is_(True),
                    TelegramChatPermission.permission == PermissionName.SUMMARIZE.value,
                    TelegramChatPermission.enabled.is_(True),
                )
            )
        ).all()
    )
    authorized_ids = [chat_id for chat_id, _ in authorization_epochs]
    raw_rows = (
        await session.execute(
            select(TelegramMessage, TelegramChat)
            .join(TelegramChat, TelegramChat.chat_id == TelegramMessage.chat_id)
            .where(
                TelegramMessage.chat_id.in_(authorized_ids),
                TelegramMessage.sent_at >= since,
                TelegramMessage.sent_at < until,
                TelegramMessage.is_deleted.is_(False),
                TelegramMessage.text.is_not(None),
            )
            .order_by(TelegramMessage.sent_at.desc())
        )
    ).all()
    raw_items = [
        DigestItem(
            chat_id=message.chat_id,
            message_id=message.message_id,
            title=(chat.title or "").strip() or f"Chat {chat.chat_id}",
            username=chat.username,
            chat_type=chat.chat_type,
            text=message.text or "",
            sent_at=message.sent_at,
        )
        for message, chat in raw_rows
    ]
    unique, unsuitable, duplicates, stats = deduplicate_digest_items(raw_items)
    selected = select_digest_context(
        unique,
        stats,
        max_context_chars=max_context_chars,
    )
    return DailyDigestDataset(
        joined_source_count=joined_source_count,
        authorized_source_count=len(authorized_ids),
        raw_message_count=len(raw_items),
        unsuitable_count=unsuitable,
        duplicate_count=duplicates,
        unique_items=unique,
        selected_items=selected,
        authorization_epochs=authorization_epochs,
        source_stats=stats,
    )


def require_digest_authorization(session, epochs: tuple[tuple[int, int], ...]) -> None:
    """Use a fresh reader or the locked writer; never recapture an old dataset's epochs."""
    with session.no_autoflush:
        current = session.execute(
            select(TelegramChatPolicy.chat_id, TelegramChatPolicy.authorization_epoch)
            .join(TelegramChatPermission, TelegramChatPermission.chat_id == TelegramChatPolicy.chat_id)
            .where(
                TelegramChatPolicy.chat_id.in_([chat_id for chat_id, _ in epochs]),
                TelegramChatPolicy.allowed.is_(True),
                TelegramChatPermission.permission == PermissionName.SUMMARIZE.value,
                TelegramChatPermission.enabled.is_(True),
            )
        ).all()
    if dict(current) != dict(epochs):
        raise AuthorizationRevoked("digest_authorization_revoked")


def digest_coverage_text(dataset: DailyDigestDataset) -> str:
    return (
        "PHẠM VI RÀ SOÁT\n"
        f"• Nguồn group/channel đã tham gia: {dataset.joined_source_count}\n"
        f"• Nguồn đã cấp quyền tổng hợp: {dataset.authorized_source_count}\n"
        f"• Nguồn có nội dung phù hợp trong ngày: {dataset.active_source_count}\n"
        f"• Tin đã rà: {dataset.raw_message_count}\n"
        f"• Tin không phù hợp/không đủ nội dung: {dataset.unsuitable_count}\n"
        f"• Tin trùng đã loại: {dataset.duplicate_count}\n"
        f"• Tin duy nhất sau lọc: {len(dataset.unique_items)}\n"
        f"• Tin đưa vào ngữ cảnh AI: {len(dataset.selected_items)}"
    )


def digest_source_list(dataset: DailyDigestDataset) -> str:
    active = [
        (chat_id, stats)
        for chat_id, stats in dataset.source_stats.items()
        if stats.suitable_count > 0
    ]
    active.sort(key=lambda item: (-item[1].selected_count, item[1].title.casefold()))
    lines = [f"DANH SÁCH NGUỒN ĐÃ TỔNG HỢP ({len(active)})"]
    for _chat_id, stats in active:
        username = f" (@{stats.username.lstrip('@')})" if stats.username else ""
        lines.append(
            f"• {stats.title}{username}: {stats.suitable_count} tin phù hợp, "
            f"{stats.duplicate_count} tin trùng, {stats.selected_count} tin đưa vào AI"
        )
    return "\n".join(lines)


def referenced_item_indexes(answer: str, *, item_count: int, fallback: int = 10) -> list[int]:
    indexes: list[int] = []
    for raw_index in SOURCE_REF_RE.findall(answer):
        index = int(raw_index) - 1
        if 0 <= index < item_count and index not in indexes:
            indexes.append(index)
    if indexes:
        return indexes
    return list(range(min(item_count, fallback)))
