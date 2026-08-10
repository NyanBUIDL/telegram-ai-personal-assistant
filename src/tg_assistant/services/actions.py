from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import PendingAction
from ..security import contains_secret, redact

SUPPORTED_ACTIONS = {
    "send_message",
    "edit_message",
    "delete_message",
    "pin_message",
    "set_chat_allowed",
    "set_chat_permission",
    "apply_permission_template",
    "setup_moderation",
    "set_admin_only_auto_moderation",
    "set_link_spam_auto_moderation",
    "set_group_ai_ask",
    "sync_chat_history",
    "enable_group_learning",
    "enable_group_learning_bulk",
    "create_memory",
    "forget_memory",
    "create_task",
    "delete_task",
    "storage_cleanup",
    "delete_ollama_model",
    "leave_telegram_chat",
    "delete_learned_data",
    "recover_source_index",
}

MAX_BULK_LEARNING_SOURCES = 500


class PendingActionService:
    def __init__(self, ttl_seconds: int = 300) -> None:
        self.ttl_seconds = ttl_seconds

    async def create(
        self,
        session: AsyncSession,
        *,
        action_type: str,
        requested_by: int,
        payload: dict,
        chat_id: int | None = None,
        message_id: int | None = None,
        preview: str | None = None,
        reason: str | None = None,
    ) -> PendingAction:
        if action_type not in SUPPORTED_ACTIONS:
            raise ValueError("Loại pending action không được hỗ trợ.")
        if (
            action_type
            in {
                "send_message",
                "edit_message",
                "delete_message",
                "pin_message",
                "set_chat_allowed",
                "set_chat_permission",
                "apply_permission_template",
                "setup_moderation",
                "set_admin_only_auto_moderation",
                "set_link_spam_auto_moderation",
                "set_group_ai_ask",
                "sync_chat_history",
                "enable_group_learning",
                "leave_telegram_chat",
                "delete_learned_data",
            }
            and chat_id is None
        ):
            raise ValueError("Hành động này cần Chat ID cụ thể.")
        if action_type in {"edit_message", "delete_message", "pin_message"} and message_id is None:
            raise ValueError("Hành động này cần Message ID cụ thể.")
        if action_type in {"send_message", "edit_message"}:
            text = str(payload.get("text", ""))
            if not text.strip():
                raise ValueError("Nội dung tin nhắn không được rỗng.")
            if contains_secret(text):
                raise ValueError(
                    "Phát hiện nội dung giống secret; không tạo hành động. "
                    "Dùng tg-assistant reconfigure trong terminal."
                )
        if action_type == "enable_group_learning_bulk":
            chat_ids = payload.get("chat_ids")
            if not isinstance(chat_ids, list) or not chat_ids:
                raise ValueError("Học nhiều group cần danh sách Chat ID.")
            if len(chat_ids) > MAX_BULK_LEARNING_SOURCES or any(
                not isinstance(chat_id, int) for chat_id in chat_ids
            ):
                raise ValueError(
                    f"Mỗi đợt chỉ hỗ trợ tối đa {MAX_BULK_LEARNING_SOURCES} Chat ID hợp lệ."
                )
        if action_type == "delete_learned_data":
            scope = payload.get("scope")
            if scope not in {
                "vectors_only",
                "search_index",
                "mysql_content",
                "media_only",
                "all",
                "reset_checkpoint",
            }:
                raise ValueError("Phạm vi xóa dữ liệu học không hợp lệ.")
            if not preview:
                raise ValueError("Xóa dữ liệu học bắt buộc phải có preview.")
        if action_type == "recover_source_index" and not preview:
            raise ValueError("Recovery index requires a preview.")
        if action_type == "leave_telegram_chat" and not preview:
            raise ValueError("Rời group/channel bắt buộc phải có preview.")
        if action_type == "create_memory":
            content = str(payload.get("content", ""))
            if not content.strip() or contains_secret(content):
                raise ValueError("Memory rỗng hoặc chứa nội dung giống secret.")
        if action_type == "delete_message" and not preview:
            raise ValueError("Xóa tin nhắn bắt buộc phải có preview.")
        action = PendingAction(
            action_type=action_type,
            requested_by=requested_by,
            payload=payload,
            chat_id=chat_id,
            message_id=message_id,
            preview=str(redact(preview)) if preview is not None else None,
            reason=str(redact(reason)) if reason is not None else None,
            expires_at=datetime.now(UTC) + timedelta(seconds=self.ttl_seconds),
        )
        session.add(action)
        await session.flush()
        # MySQL populates created_at with a server default. Load generated
        # values while the instance is still attached so API callers can
        # safely serialize the pending action after the transaction closes.
        await session.refresh(action)
        return action

    async def confirm(self, session: AsyncSession, action_id: str, actor_id: int) -> PendingAction:
        action = await session.scalar(
            select(PendingAction).where(PendingAction.action_id == action_id).with_for_update()
        )
        if not action or action.requested_by != actor_id:
            raise PermissionError("Action không tồn tại hoặc không thuộc chủ sở hữu")
        now = datetime.now(UTC)
        expiry = (
            action.expires_at.replace(tzinfo=UTC)
            if action.expires_at.tzinfo is None
            else action.expires_at
        )
        if action.status != "pending":
            raise ValueError("Action đã được sử dụng")
        if expiry <= now:
            action.status = "expired"
            raise TimeoutError("Action đã hết hạn")
        action.status = "confirmed"
        action.confirmed_at = now
        return action

    async def cancel(self, session: AsyncSession, action_id: str, actor_id: int) -> PendingAction:
        action = await session.scalar(
            select(PendingAction).where(PendingAction.action_id == action_id).with_for_update()
        )
        if not action or action.requested_by != actor_id or action.status != "pending":
            raise ValueError("Action không thể hủy")
        action.status = "cancelled"
        return action
