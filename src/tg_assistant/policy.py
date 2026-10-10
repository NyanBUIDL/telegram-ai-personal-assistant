from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from time import monotonic

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .ai.router import AI_MODES, CLOUD_PROVIDERS, AiRoute
from .db.models import (
    ModerationRule,
    PermissionName,
    TelegramChatPermission,
    TelegramChatPolicy,
)


class DecisionReason(str, Enum):
    ALLOWED = "allowed"
    NOT_OWNER = "not_owner"
    CHAT_BLOCKED = "chat_blocked"
    PERMISSION_DISABLED = "permission_disabled"
    TELEGRAM_RIGHT_MISSING = "telegram_right_missing"
    CONFIRMATION_REQUIRED = "confirmation_required"
    RATE_LIMITED = "rate_limited"
    UNSAFE = "unsafe"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reason: DecisionReason
    requires_confirmation: bool = False


@dataclass(frozen=True, slots=True)
class PolicyContext:
    actor_id: int
    owner_id: int
    chat_id: int | None
    permission: PermissionName | None
    telegram_rights: frozenset[str] = field(default_factory=frozenset)
    confirmed: bool = False
    safe: bool = True


PERMISSION_TEMPLATES: dict[str, set[PermissionName]] = {
    "read_only": {PermissionName.READ_MESSAGES, PermissionName.MONITOR_NEW_MESSAGES},
    "knowledge": {
        PermissionName.READ_MESSAGES,
        PermissionName.SYNC_HISTORY,
        PermissionName.MONITOR_NEW_MESSAGES,
        PermissionName.SEARCH_MESSAGES,
        PermissionName.SUMMARIZE,
        PermissionName.CREATE_MEMORIES,
        PermissionName.AUTO_KNOWLEDGE,
    },
    "task_management": {
        PermissionName.READ_MESSAGES,
        PermissionName.SYNC_HISTORY,
        PermissionName.MONITOR_NEW_MESSAGES,
        PermissionName.SEARCH_MESSAGES,
        PermissionName.SUMMARIZE,
        PermissionName.CREATE_MEMORIES,
        PermissionName.AUTO_KNOWLEDGE,
        PermissionName.EXTRACT_TASKS,
        PermissionName.AUTO_TASK_SUGGESTION,
    },
    "moderation": {
        PermissionName.READ_MESSAGES,
        PermissionName.MONITOR_NEW_MESSAGES,
        PermissionName.MODERATE_MESSAGES,
        PermissionName.DELETE_OWN_MESSAGES,
    },
}

DESTRUCTIVE = {
    PermissionName.DELETE_OWN_MESSAGES,
    PermissionName.DELETE_ANY_MESSAGES,
    PermissionName.MODERATE_MESSAGES,
}

RIGHTS_MAP = {
    PermissionName.SEND_MESSAGES: "send_messages",
    PermissionName.DELETE_ANY_MESSAGES: "delete_messages",
    PermissionName.PIN_MESSAGES: "pin_messages",
    PermissionName.MODERATE_MESSAGES: "delete_messages",
}


class SlidingWindowLimiter:
    def __init__(self, limit: int = 30, seconds: float = 60.0) -> None:
        self.limit, self.seconds = limit, seconds
        self._events: dict[tuple[int, str], list[float]] = {}

    def allow(self, actor_id: int, action: str) -> bool:
        now = monotonic()
        key = (actor_id, action)
        events = [t for t in self._events.get(key, []) if now - t < self.seconds]
        if len(events) >= self.limit:
            self._events[key] = events
            return False
        events.append(now)
        self._events[key] = events
        return True


class PolicyEngine:
    def __init__(self, limiter: SlidingWindowLimiter | None = None) -> None:
        self.limiter = limiter or SlidingWindowLimiter()

    async def evaluate(self, session: AsyncSession, context: PolicyContext) -> PolicyDecision:
        if context.actor_id != context.owner_id:
            return PolicyDecision(False, DecisionReason.NOT_OWNER)
        if not context.safe:
            return PolicyDecision(False, DecisionReason.UNSAFE)
        if context.chat_id is None or context.permission is None:
            return PolicyDecision(True, DecisionReason.ALLOWED)
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == context.chat_id)
        )
        if not policy or not policy.allowed:
            return PolicyDecision(False, DecisionReason.CHAT_BLOCKED)
        permission = await session.scalar(
            select(TelegramChatPermission).where(
                TelegramChatPermission.chat_id == context.chat_id,
                TelegramChatPermission.permission == context.permission.value,
                TelegramChatPermission.enabled.is_(True),
            )
        )
        if not permission:
            return PolicyDecision(False, DecisionReason.PERMISSION_DISABLED)
        required_right = RIGHTS_MAP.get(context.permission)
        if required_right and required_right not in context.telegram_rights:
            return PolicyDecision(False, DecisionReason.TELEGRAM_RIGHT_MISSING)
        if not self.limiter.allow(context.actor_id, context.permission.value):
            return PolicyDecision(False, DecisionReason.RATE_LIMITED)
        if context.permission in DESTRUCTIVE and not context.confirmed:
            return PolicyDecision(False, DecisionReason.CONFIRMATION_REQUIRED, True)
        return PolicyDecision(True, DecisionReason.ALLOWED)

    async def filter_allowed_chat_ids(
        self,
        session: AsyncSession,
        *,
        actor_id: int,
        owner_id: int,
        chat_ids: list[int],
        permission: PermissionName,
        consume_rate_limit: bool = True,
    ) -> list[int]:
        """Authorize one read request across a shared multi-chat corpus.

        Rate limiting applies to the user request, not to every source inspected
        while answering that request. Destructive or Telegram-right-dependent
        permissions must continue to use ``evaluate`` per action.
        """
        if permission in DESTRUCTIVE or permission in RIGHTS_MAP:
            raise ValueError("Quyền ghi hoặc phá hủy phải được kiểm tra theo từng hành động.")
        if actor_id != owner_id:
            return []
        unique_chat_ids = list(dict.fromkeys(chat_ids))
        if not unique_chat_ids:
            return []
        if consume_rate_limit and not self.limiter.allow(actor_id, permission.value):
            return []
        permitted = set(
            (
                await session.scalars(
                    select(TelegramChatPolicy.chat_id)
                    .join(
                        TelegramChatPermission,
                        TelegramChatPermission.chat_id == TelegramChatPolicy.chat_id,
                    )
                    .where(
                        TelegramChatPolicy.chat_id.in_(unique_chat_ids),
                        TelegramChatPolicy.allowed.is_(True),
                        TelegramChatPermission.permission == permission.value,
                        TelegramChatPermission.enabled.is_(True),
                    )
                )
            ).all()
        )
        return [chat_id for chat_id in unique_chat_ids if chat_id in permitted]

    async def set_allowed(
        self, session: AsyncSession, chat_id: int, allowed: bool, *, expected_epoch: int | None = None
    ) -> TelegramChatPolicy:
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
        )
        if expected_epoch is not None:
            from .services.revocation import AuthorizationRevoked, source_epoch
            if await source_epoch(session, chat_id) != expected_epoch:
                raise AuthorizationRevoked("stale_grant_authorization")
        if not policy:
            policy = TelegramChatPolicy(chat_id=chat_id, allowed=allowed, authorization_epoch=0)
            session.add(policy)
            await session.flush()
        else:
            if allowed and expected_epoch is not None:
                with session.no_autoflush:
                    result = await session.execute(update(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id, TelegramChatPolicy.authorization_epoch == expected_epoch).values(allowed=True, revoked_at=None))
                if result.rowcount != 1:
                    from .services.revocation import AuthorizationRevoked
                    raise AuthorizationRevoked("stale_grant_authorization")
                await session.refresh(policy)
            else:
                policy.allowed = allowed
                policy.revoked_at = None if allowed else datetime.now(UTC)
        if not allowed:
            await session.execute(update(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id).values(authorization_epoch=TelegramChatPolicy.authorization_epoch + 1, allowed=False, revoked_at=datetime.now(UTC)))
            await session.refresh(policy)
            permissions = (
                await session.scalars(
                    select(TelegramChatPermission).where(TelegramChatPermission.chat_id == chat_id)
                )
            ).all()
            for permission in permissions:
                permission.enabled = False
            from .services.revocation import fence_source_work
            session.info["revocation_cancelled_jobs"] = await fence_source_work(session, chat_id)
        return policy

    async def ai_route(
        self,
        session: AsyncSession,
        chat_ids: list[int],
    ) -> AiRoute:
        """Return a source route only when the request belongs to one policy scope."""
        unique = list(dict.fromkeys(chat_ids))
        if len(unique) != 1:
            return AiRoute()
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == unique[0])
        )
        if not policy:
            return AiRoute()
        return AiRoute(
            mode=policy.ai_mode,
            preferred_cloud_provider=policy.preferred_cloud_provider,
            cloud_fallback=policy.cloud_fallback,
        )

    async def set_ai_route(
        self,
        session: AsyncSession,
        chat_id: int,
        *,
        mode: str,
        preferred_cloud_provider: str | None = None,
        cloud_fallback: bool = False,
    ) -> TelegramChatPolicy:
        if mode not in AI_MODES:
            raise ValueError("AI mode không hợp lệ.")
        if preferred_cloud_provider not in CLOUD_PROVIDERS | {None}:
            raise ValueError("Cloud provider không hợp lệ.")
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
        )
        if not policy or not policy.allowed:
            raise PermissionError("Chat chưa được ALLOW.")
        policy.ai_mode = mode
        policy.preferred_cloud_provider = preferred_cloud_provider
        policy.cloud_fallback = cloud_fallback
        return policy

    async def set_source_limits(
        self,
        session: AsyncSession,
        chat_id: int,
        *,
        retention_days: int | None,
        max_messages: int | None,
        max_storage_mb: int | None,
        max_vectors: int | None,
    ) -> TelegramChatPolicy:
        values = (retention_days, max_messages, max_storage_mb, max_vectors)
        if any(value is not None and value <= 0 for value in values):
            raise ValueError("Retention và quota phải lớn hơn 0 hoặc để trống.")
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
        )
        if not policy or not policy.allowed:
            raise PermissionError("Chat chưa được ALLOW.")
        policy.retention_days = retention_days
        policy.max_messages = max_messages
        policy.max_storage_mb = max_storage_mb
        policy.max_vectors = max_vectors
        return policy

    async def set_ai_efficiency(
        self,
        session: AsyncSession,
        chat_id: int,
        *,
        preset: str | None = None,
        filtering_level: str = "standard",
        rag_top_k: int | None = None,
        rag_max_context_tokens: int | None = None,
    ) -> TelegramChatPolicy:
        if preset not in {None, "saving", "balanced", "quality"}:
            raise ValueError("Preset hiệu quả AI không hợp lệ.")
        if filtering_level not in {"relaxed", "standard", "strict"}:
            raise ValueError("Mức lọc nội dung không hợp lệ.")
        if rag_top_k is not None and not 1 <= rag_top_k <= 50:
            raise ValueError("RAG top_k phải nằm trong khoảng 1–50.")
        if rag_max_context_tokens is not None and not 500 <= rag_max_context_tokens <= 50_000:
            raise ValueError("RAG context phải nằm trong khoảng 500–50.000 token.")
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
        )
        if not policy or not policy.allowed:
            raise PermissionError("Chat chưa được ALLOW.")
        policy.ai_efficiency_preset = preset
        policy.filtering_level = filtering_level
        policy.rag_top_k = rag_top_k
        policy.rag_max_context_tokens = rag_max_context_tokens
        return policy

    async def set_permission(
        self, session: AsyncSession, chat_id: int, name: PermissionName, enabled: bool
    ) -> None:
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
        )
        if enabled and (not policy or not policy.allowed):
            raise PermissionError("Chat chưa được allow")
        row = await session.scalar(
            select(TelegramChatPermission).where(
                TelegramChatPermission.chat_id == chat_id,
                TelegramChatPermission.permission == name.value,
            )
        )
        if row:
            if row.enabled and not enabled and policy:
                await session.execute(update(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id).values(authorization_epoch=TelegramChatPolicy.authorization_epoch + 1))
                await session.refresh(policy)
                from .services.revocation import fence_source_work
                await fence_source_work(session, chat_id)
            row.enabled = enabled
        else:
            session.add(
                TelegramChatPermission(chat_id=chat_id, permission=name.value, enabled=enabled)
            )

    async def apply_template(
        self,
        session: AsyncSession,
        chat_id: int,
        template: str,
        *,
        enable_send: bool = False,
        enable_delete_any: bool = False,
    ) -> None:
        if template not in PERMISSION_TEMPLATES:
            raise ValueError("Mẫu quyền không hợp lệ")
        await self.set_allowed(session, chat_id, True)
        enabled = set(PERMISSION_TEMPLATES[template])
        if enable_send and template == "task_management":
            enabled.add(PermissionName.SEND_MESSAGES)
        if enable_delete_any and template == "moderation":
            enabled.add(PermissionName.DELETE_ANY_MESSAGES)
        for permission in PermissionName:
            if permission is PermissionName.GROUP_AI_ASK:
                # This is an explicit standing authorization for group members.
                # General permission templates must not silently enable or revoke it.
                continue
            await self.set_permission(session, chat_id, permission, permission in enabled)

    async def set_group_ai_ask(
        self,
        session: AsyncSession,
        chat_id: int,
        *,
        enabled: bool,
    ) -> None:
        """Allow an explicitly approved group to invoke the owner's AI assistant."""
        if enabled:
            policy = await session.scalar(
                select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
            )
            if not policy or not policy.allowed:
                raise PermissionError("Group phải ở ALLOW trước khi bật hỏi AI.")
            for permission in (
                PermissionName.READ_MESSAGES,
                PermissionName.SYNC_HISTORY,
                PermissionName.MONITOR_NEW_MESSAGES,
                PermissionName.SEARCH_MESSAGES,
            ):
                await self.set_permission(session, chat_id, permission, True)
        await self.set_permission(
            session,
            chat_id,
            PermissionName.GROUP_AI_ASK,
            enabled,
        )

    async def apply_moderation_bundle(self, session: AsyncSession, chat_id: int) -> None:
        """Enable review-first moderation without automatic destructive actions."""
        await self.apply_template(
            session,
            chat_id,
            "moderation",
            enable_delete_any=True,
        )
        for permission in (
            PermissionName.SYNC_HISTORY,
            PermissionName.SEARCH_MESSAGES,
        ):
            await self.set_permission(session, chat_id, permission, True)

    async def set_link_spam_auto_moderation(
        self,
        session: AsyncSession,
        chat_id: int,
        *,
        enabled: bool,
    ) -> None:
        """Auto-delete only new linked posts from non-admin senders."""
        if enabled:
            await self.apply_moderation_bundle(session, chat_id)
        await self.set_permission(
            session,
            chat_id,
            PermissionName.AUTO_MODERATION,
            enabled,
        )
        rule = await session.scalar(
            select(ModerationRule).where(
                ModerationRule.chat_id == chat_id,
                ModerationRule.name == "non_admin_external_link_auto_delete",
            )
        )
        rule_json = {
            "scope": "new_messages_only",
            "delete_when": "contains_external_link_and_sender_is_not_admin",
            "link_types": [
                "http",
                "https",
                "www",
                "bare_domain",
                "telegram_link",
                "telegram_text_url_entity",
            ],
            "trusted": ["owner", "creator", "admin", "anonymous_admin"],
            "fail_safe": "keep_message_when_role_check_fails",
        }
        if rule:
            rule.rule_json = rule_json
            rule.mode = "auto_delete"
            rule.enabled = enabled
        else:
            session.add(
                ModerationRule(
                    chat_id=chat_id,
                    name="non_admin_external_link_auto_delete",
                    rule_json=rule_json,
                    mode="auto_delete",
                    enabled=enabled,
                )
            )
        old_rule = await session.scalar(
            select(ModerationRule).where(
                ModerationRule.chat_id == chat_id,
                ModerationRule.name == "admin_only_auto_delete",
            )
        )
        if old_rule:
            old_rule.enabled = False
