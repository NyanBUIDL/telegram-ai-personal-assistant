from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AppSetting, PendingAction
from ..security import contains_secret, redact
from .revocation import source_epoch, source_ids, validate_action_epoch

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
    "backfill_chat_history",
    "delete_history_link_posts",
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
                "backfill_chat_history",
                "delete_history_link_posts",
                "enable_group_learning",
                "leave_telegram_chat",
                "delete_learned_data",
                "recover_source_index",
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
        if action_type == "recover_source_index":
            if not preview or set(payload) != {"plan_id"} or not isinstance(payload.get("plan_id"), str):
                raise ValueError("Recovery requires a server-issued preview.")
            private = await session.get(AppSetting, f"recovery_plan:{payload['plan_id']}")
            if (private is None or not isinstance(private.value, dict)
                    or private.value.get("owner_id") != requested_by
                    or private.value.get("state") != "preview"
                    or (private.value.get("plan") or {}).get("chat_id") != str(chat_id)):
                raise ValueError("Recovery requires a server-issued preview.")
        if action_type == "leave_telegram_chat" and not preview:
            raise ValueError("Rời group/channel bắt buộc phải có preview.")
        if action_type == "create_memory":
            content = str(payload.get("content", ""))
            if not content.strip() or contains_secret(content):
                raise ValueError("Memory rỗng hoặc chứa nội dung giống secret.")
        if action_type == "delete_message" and not preview:
            raise ValueError("Xóa tin nhắn bắt buộc phải có preview.")
        snapshots = {
            str(source): await source_epoch(session, source)
            for source in source_ids(payload, chat_id)
        }
        action = PendingAction(
            action_type=action_type,
            requested_by=requested_by,
            payload={**payload, "authorization_epochs": snapshots},
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
        await validate_action_epoch(session, action)
        consumed = await session.execute(
            update(PendingAction)
            .where(
                PendingAction.action_id == action_id,
                PendingAction.status == "pending",
                PendingAction.requested_by == actor_id,
                PendingAction.expires_at > now,
            )
            .values(status="confirmed", confirmed_at=now)
            .execution_options(synchronize_session=False)
        )
        if consumed.rowcount != 1:
            raise ValueError("Action đã được sử dụng")
        action.status = "confirmed"
        action.confirmed_at = now
        return action

    async def cancel(self, session: AsyncSession, action_id: str, actor_id: int) -> PendingAction:
        action = await session.scalar(
            select(PendingAction).where(PendingAction.action_id == action_id).with_for_update()
        )
        if not action or action.requested_by != actor_id or action.status != "pending":
            raise ValueError("Action không thể hủy")
        changed = await session.execute(
            update(PendingAction)
            .where(
                PendingAction.action_id == action_id,
                PendingAction.status == "pending",
                PendingAction.requested_by == actor_id,
            )
            .values(status="cancelled")
        )
        if changed.rowcount != 1:
            raise ValueError("Action không thể hủy")
        action.status = "cancelled"
        return action

    async def claim_execution(self, session: AsyncSession, action_id: str, actor_id: int) -> bool:
        action = await session.scalar(
            select(PendingAction).where(PendingAction.action_id == action_id).with_for_update()
        )
        if not action or action.requested_by != actor_id or action.status != "confirmed":
            return False
        await validate_action_epoch(session, action)
        now = datetime.now(UTC)
        claimed = await session.execute(
            update(PendingAction)
            .where(
                PendingAction.action_id == action_id,
                PendingAction.status == "confirmed",
                PendingAction.requested_by == actor_id,
                PendingAction.expires_at > now,
            )
            .values(status="executing", execution_started_at=now)
            .execution_options(synchronize_session=False)
        )
        return claimed.rowcount == 1

    async def recover_interrupted(self, session: AsyncSession) -> int:
        cutoff = datetime.now(UTC) - timedelta(minutes=15)
        actions = list(
            (
                await session.scalars(
                    select(PendingAction)
                    .where(
                        PendingAction.status == "executing",
                        (PendingAction.execution_started_at.is_(None))
                        | (PendingAction.execution_started_at <= cutoff),
                    )
                    .with_for_update()
                )
            ).all()
        )
        recovered = 0
        for action in actions:
            external = bool((action.payload or {}).get("external_effect_started"))
            changed = await session.execute(
                update(PendingAction)
                .where(
                    PendingAction.action_id == action.action_id,
                    PendingAction.status == "executing",
                    (PendingAction.execution_started_at.is_(None))
                    | (PendingAction.execution_started_at <= cutoff),
                )
                .values(
                    status="uncertain" if external else "cancelled",
                    error="requires_reconciliation"
                    if external
                    else "interrupted_requires_new_confirmation",
                )
                .execution_options(synchronize_session=False)
            )
            recovered += changed.rowcount
        return recovered
