from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any

from ..security import contains_secret

EMBEDDING_VERSION = "local-v1"
SKIP_REASONS = {
    "empty_content",
    "too_short",
    "duplicate",
    "service_message",
    "spam",
    "retention_excluded",
    "unsupported_content",
    "other",
}
SOCIAL_REPLIES = {
    "ok",
    "okay",
    "thanks",
    "thank you",
    "tks",
    "up",
    "done",
    "ừ",
    "uh",
    "vâng",
    "dạ",
    "cảm ơn",
    "cam on",
    "chuẩn",
    "đúng",
    "haha",
    "hehe",
    "+1",
}
SPACE_RE = re.compile(r"\s+")
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
PRICE_RE = re.compile(
    r"\b(?:giá|price)\s+(?:của\s+)?(?:coin\s+)?[A-Za-z0-9$._-]{2,20}\b",
    re.IGNORECASE,
)
USERNAME_RE = re.compile(r"(?<!\w)@[A-Za-z0-9_]{5,32}")


class QueryRoute(StrEnum):
    DIRECT_DATABASE = "direct_database"
    COINGECKO = "coingecko"
    TELEGRAM_SEARCH = "telegram_search"
    MEMBER_HISTORY = "member_history"
    GROUP_TOPIC = "group_topic"
    LOCAL_RAG = "local_rag"
    CLOUD_RAG = "cloud_rag"
    DAILY_SUMMARY = "daily_summary"
    SEVEN_DAY_SUMMARY = "seven_day_summary"
    SYSTEM_OPERATION = "system_operation"


@dataclass(frozen=True, slots=True)
class RagPreset:
    name: str
    top_k: int
    max_chunks_per_source: int
    max_sources: int
    max_context_tokens: int
    max_chunk_tokens: int
    cloud_fallback: bool


PRESETS: dict[str, RagPreset] = {
    "saving": RagPreset("saving", 5, 2, 5, 3_500, 350, False),
    "balanced": RagPreset("balanced", 8, 3, 8, 6_000, 500, True),
    "quality": RagPreset("quality", 12, 4, 10, 10_000, 700, True),
}


@dataclass(frozen=True, slots=True)
class EmbeddingDecision:
    eligible: bool
    normalized_text: str
    content_hash: str | None
    reason: str | None = None


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value).replace("\x00", " ")
    return SPACE_RE.sub(" ", normalized).strip()


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def estimate_tokens(value: str) -> int:
    return max(1, len(value) // 4)


def _only_symbols(value: str) -> bool:
    meaningful = [
        character
        for character in value
        if not character.isspace()
        and unicodedata.category(character)[0] not in {"P", "S", "M"}
    ]
    return not meaningful


def embedding_decision(
    text: str | None,
    *,
    metadata: dict[str, Any] | None = None,
    filtering_level: str = "standard",
    duplicate_hashes: set[str] | None = None,
    retention_excluded: bool = False,
) -> EmbeddingDecision:
    normalized = normalize_text(text)
    if retention_excluded:
        return EmbeddingDecision(False, normalized, None, "retention_excluded")
    if not normalized:
        return EmbeddingDecision(False, "", None, "empty_content")
    metadata = metadata or {}
    if metadata.get("service_message"):
        return EmbeddingDecision(False, normalized, None, "service_message")
    if metadata.get("spam_deleted") or metadata.get("moderation_action") == "deleted":
        return EmbeddingDecision(False, normalized, None, "spam")
    if contains_secret(normalized):
        return EmbeddingDecision(False, normalized, None, "unsupported_content")
    if _only_symbols(normalized):
        return EmbeddingDecision(False, normalized, None, "unsupported_content")
    folded = normalized.casefold()
    min_length = {"relaxed": 2, "standard": 8, "strict": 16}.get(filtering_level, 8)
    if len(normalized) < min_length or folded in SOCIAL_REPLIES:
        return EmbeddingDecision(False, normalized, None, "too_short")
    digest = text_hash(folded)
    if duplicate_hashes is not None and digest in duplicate_hashes:
        return EmbeddingDecision(False, normalized, digest, "duplicate")
    return EmbeddingDecision(True, normalized, digest)


def classify_query(question: str, *, in_group: bool = False) -> QueryRoute:
    normalized = normalize_text(question).casefold()
    if PRICE_RE.search(normalized):
        return QueryRoute.COINGECKO
    if any(
        marker in normalized
        for marker in (
            "trạng thái học",
            "tiến độ học",
            "danh sách group",
            "danh sách nhóm",
            "learning job",
            "pending action",
        )
    ):
        return QueryRoute.DIRECT_DATABASE
    if any(marker in normalized for marker in ("tìm link", "tìm tin", "search ", "has:link")):
        return QueryRoute.TELEGRAM_SEARCH
    if USERNAME_RE.search(question) and any(
        marker in normalized
        for marker in ("đã nói", "nói về", "thảo luận", "lịch sử", "nhắn gì")
    ):
        return QueryRoute.MEMBER_HISTORY
    if in_group and any(
        marker in normalized
        for marker in (
            "group đang",
            "nhóm đang",
            "mọi người đang",
            "chủ đề đang",
            "cuộc trò chuyện",
        )
    ):
        return QueryRoute.GROUP_TOPIC
    if any(marker in normalized for marker in ("hôm nay", "trong ngày", "bản tin ngày")):
        return QueryRoute.DAILY_SUMMARY
    if any(marker in normalized for marker in ("7 ngày", "bảy ngày", "tuần qua")):
        return QueryRoute.SEVEN_DAY_SUMMARY
    if any(
        marker in normalized
        for marker in (
            "phân tích sâu",
            "so sánh toàn bộ",
            "tổng hợp nhiều nguồn",
            "đánh giá chiến lược",
        )
    ):
        return QueryRoute.CLOUD_RAG
    return QueryRoute.LOCAL_RAG


def route_feature(route: QueryRoute) -> str:
    return {
        QueryRoute.COINGECKO: "price",
        QueryRoute.MEMBER_HISTORY: "member_history",
        QueryRoute.GROUP_TOPIC: "group_topic",
        QueryRoute.DAILY_SUMMARY: "daily_summary",
        QueryRoute.SEVEN_DAY_SUMMARY: "seven_day_summary",
        QueryRoute.CLOUD_RAG: "cloud_rag",
        QueryRoute.LOCAL_RAG: "normal_ask",
        QueryRoute.TELEGRAM_SEARCH: "telegram_search",
        QueryRoute.SYSTEM_OPERATION: "system_operation",
        QueryRoute.DIRECT_DATABASE: "direct_database",
    }[route]


def query_cache_ttl(route: QueryRoute) -> timedelta:
    return {
        QueryRoute.COINGECKO: timedelta(seconds=45),
        QueryRoute.GROUP_TOPIC: timedelta(minutes=10),
        QueryRoute.DAILY_SUMMARY: timedelta(minutes=30),
        QueryRoute.SEVEN_DAY_SUMMARY: timedelta(hours=2),
        QueryRoute.MEMBER_HISTORY: timedelta(minutes=30),
    }.get(route, timedelta(hours=24))


def query_cache_key(
    question: str,
    *,
    chat_ids: list[int],
    sender_id: int | None,
    route: QueryRoute,
    provider: str,
    model: str,
    knowledge_version: str,
    rag_config_version: str,
) -> tuple[str, str]:
    normalized_hash = text_hash(normalize_text(question).casefold())
    payload = {
        "query": normalized_hash,
        "chat_ids": sorted(set(chat_ids)),
        "sender_id": sender_id,
        "route": route.value,
        "provider": provider,
        "model": model,
        "knowledge_version": knowledge_version,
        "rag_config_version": rag_config_version,
    }
    cache_key = text_hash(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return cache_key, normalized_hash


def trim_contexts(
    contexts: list[tuple[int, str]],
    *,
    preset: RagPreset,
) -> list[str]:
    selected: list[str] = []
    per_source: dict[int, int] = {}
    used_sources: set[int] = set()
    token_total = 0
    seen: set[str] = set()
    for chat_id, context in contexts:
        if per_source.get(chat_id, 0) >= preset.max_chunks_per_source:
            continue
        if chat_id not in used_sources and len(used_sources) >= preset.max_sources:
            continue
        compact = context[: preset.max_chunk_tokens * 4]
        digest = text_hash(normalize_text(compact).casefold())
        if digest in seen:
            continue
        tokens = estimate_tokens(compact)
        if selected and token_total + tokens > preset.max_context_tokens:
            break
        selected.append(compact)
        token_total += tokens
        seen.add(digest)
        used_sources.add(chat_id)
        per_source[chat_id] = per_source.get(chat_id, 0) + 1
        if len(selected) >= preset.top_k:
            break
    return selected
