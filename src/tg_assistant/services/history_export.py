from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable

from ..db.models import TelegramChat, TelegramMessage

PROMOTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("link", re.compile(r"(?i)(?:https?://|\b(?:t\.me|telegram\.me|www\.)\b)")),
    (
        "promotion_keyword",
        re.compile(
            r"(?i)\b(?:airdrop|giveaway|referral|affiliate|sponsored|promotion|"
            r"discount|sale|whitelist|presale|ido|launchpad|bonus)\b|"
            r"(?:khuyến\s*mãi|ưu\s*đãi|mã\s*giảm\s*giá|quà\s*tặng|đăng\s*ký\s*ngay)",
        ),
    ),
)


def parse_search_terms(value: str | None, *, max_terms: int = 20) -> list[str]:
    """Normalize a user supplied comma/newline-separated search list."""
    if not value:
        return []
    seen: set[str] = set()
    terms: list[str] = []
    for candidate in re.split(r"[,\n\r]+", value):
        term = " ".join(candidate.split()).strip()
        key = term.casefold()
        if not term or key in seen:
            continue
        seen.add(key)
        terms.append(term[:120])
        if len(terms) >= max_terms:
            break
    return terms


def promotion_reasons(text: str | None) -> list[str]:
    content = text or ""
    return [label for label, pattern in PROMOTION_PATTERNS if pattern.search(content)]


def matched_terms(text: str | None, terms: Iterable[str]) -> list[str]:
    content = (text or "").casefold()
    return [term for term in terms if term.casefold() in content]


def telegram_post_url(chat: TelegramChat, message_id: int) -> str:
    if chat.username:
        return f"https://t.me/{chat.username}/{message_id}"
    raw_id = str(chat.chat_id)
    if raw_id.startswith("-100"):
        return f"https://t.me/c/{raw_id[4:]}/{message_id}"
    return ""


def csv_safe(value: object) -> object:
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
        return f"'{value}"
    return value


def history_csv_header() -> list[str]:
    return [
        "chat_id",
        "source_name",
        "source_type",
        "message_id",
        "posted_at_utc",
        "sender_id",
        "content",
        "has_media",
        "is_promotion",
        "promotion_reason",
        "matches_search_terms",
        "matched_terms",
        "telegram_url",
    ]


def history_csv_row(
    chat: TelegramChat,
    message: TelegramMessage,
    terms: Iterable[str],
) -> tuple[list[object], bool, bool]:
    reasons = promotion_reasons(message.text)
    matches = matched_terms(message.text, terms)
    row: list[object] = [
        chat.chat_id,
        chat.title or "Nguồn không có tiêu đề",
        chat.chat_type,
        message.message_id,
        message.sent_at.isoformat(),
        message.sender_id or "",
        message.text or "",
        "yes" if message.has_media else "no",
        "yes" if reasons else "no",
        ", ".join(reasons),
        "yes" if matches else "no",
        ", ".join(matches),
        telegram_post_url(chat, message.message_id),
    ]
    return [csv_safe(value) for value in row], bool(reasons), bool(matches)


def encode_csv_row(row: list[object]) -> bytes:
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerow(row)
    return output.getvalue().encode("utf-8")
