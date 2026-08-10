from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import PermissionName, TelegramMessage
from ..policy import PolicyEngine
from ..security import redact


@dataclass(slots=True)
class SearchResult:
    chat_id: int
    message_id: int
    text: str
    score: float = 1.0
    sender_id: int | None = None
    sent_at: datetime | None = None
    source: str = "keyword"


@dataclass(slots=True)
class SearchQuery:
    text: str
    chat_id: int | None = None
    sender_id: int | None = None
    after: datetime | None = None
    before: datetime | None = None
    has: str | None = None
    content_type: str | None = None


FILTER_RE = re.compile(r"(?P<name>in|from|after|before|has|type):(?P<value>\S+)")


def parse_search_query(raw: str) -> SearchQuery:
    values: dict[str, str] = {}
    for match in FILTER_RE.finditer(raw):
        values[match.group("name")] = match.group("value")
    text = FILTER_RE.sub("", raw).strip()

    def numeric(name: str) -> int | None:
        value = values.get(name)
        if value is None:
            return None
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{name}: cần Telegram numeric ID") from exc

    def day(name: str, *, end: bool = False) -> datetime | None:
        value = values.get(name)
        if value is None:
            return None
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError as exc:
            raise ValueError(f"{name}: dùng YYYY-MM-DD") from exc
        return parsed + timedelta(days=1) if end else parsed

    has = values.get("has")
    if has and has not in {"link", "file", "image", "document", "audio"}:
        raise ValueError("has: chỉ hỗ trợ link|file|image|document|audio")
    content_type = values.get("type")
    if content_type and content_type not in {"task", "decision"}:
        raise ValueError("type: chỉ hỗ trợ task|decision")
    return SearchQuery(
        text=text,
        chat_id=numeric("in"),
        sender_id=numeric("from"),
        after=day("after"),
        before=day("before", end=True),
        has=has,
        content_type=content_type,
    )


class SearchService:
    def __init__(self, policy: PolicyEngine) -> None:
        self.policy = policy

    async def keyword(
        self,
        session: AsyncSession,
        query: str,
        *,
        owner_id: int,
        actor_id: int,
        chat_ids: list[int],
        limit: int = 20,
        preauthorized: bool = False,
        default_after: datetime | None = None,
    ) -> list[SearchResult]:
        parsed = parse_search_query(query)
        requested = [parsed.chat_id] if parsed.chat_id is not None else chat_ids
        if preauthorized:
            authorized = set(chat_ids)
            allowed = [chat_id for chat_id in dict.fromkeys(requested) if chat_id in authorized]
        else:
            allowed = await self.policy.filter_allowed_chat_ids(
                session,
                actor_id=actor_id,
                owner_id=owner_id,
                chat_ids=requested,
                permission=PermissionName.SEARCH_MESSAGES,
            )
        if not allowed:
            return []
        filters = [
            TelegramMessage.chat_id.in_(allowed),
            TelegramMessage.is_deleted.is_(False),
        ]
        if parsed.text:
            terms = [term for term in parsed.text.split() if term]
            filters.append(and_(*(TelegramMessage.text.contains(term) for term in terms)))
        if parsed.sender_id is not None:
            filters.append(TelegramMessage.sender_id == parsed.sender_id)
        effective_after = (
            parsed.after
            if parsed.after is not None or parsed.before is not None
            else default_after
        )
        if effective_after is not None:
            filters.append(TelegramMessage.sent_at >= effective_after)
        if parsed.before is not None:
            filters.append(TelegramMessage.sent_at < parsed.before)
        if parsed.has == "link":
            filters.append(
                or_(
                    TelegramMessage.text.contains("http://"),
                    TelegramMessage.text.contains("https://"),
                )
            )
        elif parsed.has:
            filters.append(TelegramMessage.has_media.is_(True))
        if parsed.content_type == "task":
            filters.append(
                or_(
                    TelegramMessage.text.contains("cần "),
                    TelegramMessage.text.contains("deadline"),
                    TelegramMessage.text.contains("todo"),
                )
            )
        elif parsed.content_type == "decision":
            filters.append(
                or_(
                    TelegramMessage.text.contains("quyết định"),
                    TelegramMessage.text.contains("thống nhất"),
                )
            )
        rows = (
            await session.scalars(
                select(TelegramMessage)
                .where(*filters)
                .order_by(TelegramMessage.sent_at.desc())
                .limit(min(limit, 100))
            )
        ).all()
        return [
            SearchResult(
                row.chat_id,
                row.message_id,
                str(redact(row.text or "")),
                sender_id=row.sender_id,
                sent_at=row.sent_at,
            )
            for row in rows
        ]
