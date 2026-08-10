from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AiMemory, TelegramChatPolicy
from ..security import contains_secret


class MemoryService:
    async def create(
        self,
        session: AsyncSession,
        *,
        content: str,
        memory_type: str,
        scope_type: str,
        scope_id: str | None,
        confirmed: bool,
        source_chat_id: int | None = None,
        source_message_id: int | None = None,
    ) -> AiMemory:
        if not confirmed:
            raise PermissionError("Memory phải được chủ sở hữu xác nhận")
        if contains_secret(content):
            raise ValueError("Không lưu secret vào memory")
        if scope_type not in {"private", "chat", "global"}:
            raise ValueError("Scope không hợp lệ")
        if scope_type == "global" and not confirmed:
            raise PermissionError("Memory global cần xác nhận")
        memory = AiMemory(
            content=content,
            memory_type=memory_type,
            scope_type=scope_type,
            scope_id=scope_id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            confidence=1.0,
            status="active",
        )
        session.add(memory)
        await session.flush()
        return memory

    async def search(
        self, session: AsyncSession, query: str, *, chat_id: int | None = None
    ) -> list[AiMemory]:
        allowed_chat_ids = select(TelegramChatPolicy.chat_id).where(
            TelegramChatPolicy.allowed.is_(True)
        )
        filters = [AiMemory.status == "active", AiMemory.content.contains(query)]
        scope = [AiMemory.scope_type == "private", AiMemory.scope_type == "global"]
        if chat_id is not None:
            scope.append(
                (AiMemory.scope_type == "chat")
                & (AiMemory.scope_id == str(chat_id))
                & (AiMemory.source_chat_id.in_(allowed_chat_ids))
            )
        filters.append(or_(*scope))
        return list((await session.scalars(select(AiMemory).where(*filters).limit(20))).all())
