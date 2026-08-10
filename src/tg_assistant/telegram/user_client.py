from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl import types

from ..db.models import (
    AuditLog,
    ModerationRule,
    PermissionName,
    SyncState,
    TelegramChat,
    TelegramMessage,
    TelegramMessageVersion,
)
from ..policy import PolicyContext, PolicyEngine
from ..security import EncryptedSession, SecretStore, redact

log = structlog.get_logger()

EXTERNAL_LINK_RE = re.compile(
    r"""(?ix)
    (?:
        https?://[^\s]+
        | tg://[^\s]+
        | www\.[^\s]+
        | t\.me/[^\s]+
        | telegram\.(?:me|dog)/[^\s]+
        | (?<![@\w])
          (?:[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\.)+
          [a-z]{2,63}
          (?:/[^\s]*)?
    )
    """
)
GroupAskHandler = Callable[[int, int, int, str], Awaitable[None]]


def extract_group_ai_question(text: str | None, username: str | None) -> str | None:
    """Extract an exact ``@username /ask ...`` invocation for the user client."""
    if not text or not username:
        return None
    match = re.fullmatch(
        rf"\s*@{re.escape(username.lstrip('@'))}\s+/ask(?:@\w+)?(?:\s+(.*?))?\s*",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    return (match.group(1) or "").strip()


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    return f"***{phone[-4:]}"


def classify_dialog_entity(entity: Any) -> str:
    """Classify dialogs by their Telegram entity type, not by optional names."""
    if isinstance(entity, (types.User, types.UserEmpty)):
        return "private"
    if isinstance(entity, (types.Channel, types.ChannelForbidden)):
        if getattr(entity, "megagroup", False) or getattr(entity, "gigagroup", False):
            return "supergroup"
        return "channel"
    if isinstance(entity, (types.Chat, types.ChatEmpty, types.ChatForbidden)):
        return "group"
    return "unknown"


def dialog_title(dialog: Any, entity: Any, chat_type: str) -> str | None:
    name = (getattr(dialog, "name", None) or "").strip()
    if name:
        return name
    username = (getattr(entity, "username", None) or "").strip()
    if username:
        return f"@{username}"
    if chat_type == "private" and (
        isinstance(entity, types.UserEmpty) or getattr(entity, "deleted", False)
    ):
        return "Tài khoản đã xóa"
    return None


def is_implicitly_trusted_message(
    message: Any,
    *,
    owner_id: int,
    chat_id: int,
) -> bool:
    """Trust messages that do not require a Telegram participant lookup."""
    sender_id = getattr(message, "sender_id", None)
    return bool(
        getattr(message, "out", False)
        or getattr(message, "action", None) is not None
        or sender_id is None
        or int(sender_id) in {owner_id, chat_id}
        or getattr(message, "post_author", None)
    )


def message_contains_external_link(message: Any) -> bool:
    entities = getattr(message, "entities", None) or []
    if any(
        isinstance(entity, (types.MessageEntityUrl, types.MessageEntityTextUrl))
        for entity in entities
    ):
        return True
    media = getattr(message, "media", None)
    webpage = getattr(media, "webpage", None)
    if getattr(webpage, "url", None):
        return True
    return bool(EXTERNAL_LINK_RE.search(getattr(message, "message", None) or ""))


class UserClientAdapter:
    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        session_path: Path,
        encrypted_path: Path,
        store: SecretStore,
        policy: PolicyEngine,
    ) -> None:
        self.session_path, self.encrypted_path = session_path, encrypted_path
        self.crypto = EncryptedSession(store)
        if encrypted_path.exists() and not session_path.exists():
            self.crypto.decrypt_file(encrypted_path, session_path)
        self.client = TelegramClient(str(session_path), api_id, api_hash)
        self.policy = policy
        self.owner_id: int | None = None
        self.username: str | None = None
        self._trusted_admin_cache: dict[tuple[int, int], float] = {}

    async def authenticate(self, phone: str, *, password_callback: Any = None) -> Any:
        await self.client.start(phone=phone, password=password_callback)
        me = await self.client.get_me()
        self.owner_id = int(me.id)
        self.username = getattr(me, "username", None)
        return me

    async def close(self) -> None:
        await self.client.disconnect()
        if self.session_path.exists():
            self.crypto.encrypt_file(self.session_path, self.encrypted_path)
            self.session_path.unlink(missing_ok=True)
            Path(f"{self.session_path}-journal").unlink(missing_ok=True)

    async def discover_dialogs(self, session: AsyncSession) -> int:
        count = 0
        async for dialog in self.client.iter_dialogs():
            entity = dialog.entity
            chat_type = classify_dialog_entity(entity)
            title = dialog_title(dialog, entity, chat_type)
            row = await session.scalar(
                select(TelegramChat).where(TelegramChat.chat_id == int(dialog.id))
            )
            if not row:
                row = TelegramChat(
                    chat_id=int(dialog.id),
                    title=title,
                    username=getattr(entity, "username", None),
                    chat_type=chat_type,
                    account_rights=self._rights(entity),
                    last_seen_at=getattr(dialog, "date", None),
                )
                session.add(row)
            else:
                (
                    row.title,
                    row.username,
                    row.chat_type,
                    row.account_rights,
                    row.last_seen_at,
                ) = (
                    title,
                    getattr(entity, "username", None),
                    chat_type,
                    self._rights(entity),
                    getattr(dialog, "date", None),
                )
            count += 1
        return count

    @staticmethod
    def _rights(entity: Any) -> dict:
        rights = getattr(entity, "admin_rights", None)
        banned = getattr(entity, "default_banned_rights", None)
        is_private = bool(getattr(entity, "first_name", None))
        is_creator = bool(getattr(entity, "creator", False))
        is_broadcast = bool(getattr(entity, "broadcast", False))
        if is_broadcast:
            can_send = is_creator or bool(getattr(rights, "post_messages", False))
        else:
            can_send = (
                is_private
                or is_creator
                or rights is not None
                or not bool(getattr(banned, "send_messages", False))
            )
        return {
            "is_creator": is_creator,
            "is_admin": is_creator or rights is not None,
            "send_messages": can_send,
            "edit_messages": is_creator or bool(getattr(rights, "edit_messages", False)),
            "delete_messages": is_creator or bool(getattr(rights, "delete_messages", False)),
            "pin_messages": is_private
            or is_creator
            or bool(getattr(rights, "pin_messages", False)),
        }

    async def leave_chat(self, chat_id: int) -> None:
        """Leave a group/channel without deleting history for other participants."""
        await self.client.delete_dialog(chat_id, revoke=False)

    async def get_actual_rights(self, chat_id: int) -> dict[str, bool]:
        entity = await self.client.get_entity(chat_id)
        return self._rights(entity)

    async def _sender_is_trusted_admin(self, chat_id: int, message: Any) -> bool:
        if self.owner_id is None:
            return True
        if is_implicitly_trusted_message(
            message,
            owner_id=self.owner_id,
            chat_id=chat_id,
        ):
            return True
        sender_id = int(message.sender_id)
        cache = getattr(self, "_trusted_admin_cache", {})
        cached_until = cache.get((chat_id, sender_id), 0.0)
        if cached_until > monotonic():
            return True
        permissions = await self.client.get_permissions(chat_id, sender_id)
        trusted = bool(
            getattr(permissions, "is_admin", False) or getattr(permissions, "is_creator", False)
        )
        if trusted:
            cache[(chat_id, sender_id)] = monotonic() + 300
            self._trusted_admin_cache = cache
        return trusted

    async def _auto_moderate_new_message(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        message: Any,
    ) -> bool:
        if self.owner_id is None:
            return False
        rule = await session.scalar(
            select(ModerationRule).where(
                ModerationRule.chat_id == chat_id,
                ModerationRule.name == "non_admin_external_link_auto_delete",
                ModerationRule.mode == "auto_delete",
                ModerationRule.enabled.is_(True),
            )
        )
        if not rule:
            return False
        if not message_contains_external_link(message):
            return False
        automatic = await self.policy.evaluate(
            session,
            PolicyContext(
                self.owner_id,
                self.owner_id,
                chat_id,
                PermissionName.AUTO_MODERATION,
            ),
        )
        if not automatic.allowed:
            return False
        try:
            if await self._sender_is_trusted_admin(chat_id, message):
                return False
        except Exception as exc:
            session.add(
                AuditLog(
                    occurred_at=datetime.now(UTC),
                    actor_id=self.owner_id,
                    action="auto_moderation_role_check",
                    target_type="telegram_message",
                    target_id=f"{chat_id}/{message.id}",
                    outcome="failed_safe_kept",
                    reason=str(redact(str(exc)))[:1000],
                    details_redacted={
                        "sender_id": getattr(message, "sender_id", None),
                        "rule": rule.name,
                    },
                )
            )
            log.warning(
                "auto_moderation_role_check_failed",
                chat_id=chat_id,
                message_id=int(message.id),
            )
            return False
        try:
            rights = await self.get_actual_rights(chat_id)
            decision = await self.policy.evaluate(
                session,
                PolicyContext(
                    self.owner_id,
                    self.owner_id,
                    chat_id,
                    PermissionName.DELETE_ANY_MESSAGES,
                    frozenset(key for key, enabled in rights.items() if enabled),
                    confirmed=True,
                ),
            )
        except Exception as exc:
            session.add(
                AuditLog(
                    occurred_at=datetime.now(UTC),
                    actor_id=self.owner_id,
                    action="auto_moderation_rights_check",
                    target_type="telegram_message",
                    target_id=f"{chat_id}/{message.id}",
                    outcome="failed_safe_kept",
                    reason=str(redact(str(exc)))[:1000],
                    details_redacted={
                        "sender_id": getattr(message, "sender_id", None),
                        "rule": rule.name,
                    },
                )
            )
            return False
        if not decision.allowed:
            session.add(
                AuditLog(
                    occurred_at=datetime.now(UTC),
                    actor_id=self.owner_id,
                    action="auto_delete_non_admin_link",
                    target_type="telegram_message",
                    target_id=f"{chat_id}/{message.id}",
                    outcome="denied_kept",
                    reason=decision.reason.value,
                    details_redacted={
                        "sender_id": getattr(message, "sender_id", None),
                        "rule": rule.name,
                    },
                )
            )
            return False
        try:
            await self.client.delete_messages(chat_id, [int(message.id)])
        except Exception as exc:
            session.add(
                AuditLog(
                    occurred_at=datetime.now(UTC),
                    actor_id=self.owner_id,
                    action="auto_delete_non_admin_link",
                    target_type="telegram_message",
                    target_id=f"{chat_id}/{message.id}",
                    outcome="failed_kept",
                    reason=str(redact(str(exc)))[:1000],
                    details_redacted={
                        "sender_id": getattr(message, "sender_id", None),
                        "rule": rule.name,
                    },
                )
            )
            return False
        stored = await session.scalar(
            select(TelegramMessage).where(
                TelegramMessage.chat_id == chat_id,
                TelegramMessage.message_id == int(message.id),
            )
        )
        if stored:
            stored.is_deleted = True
        session.add(
            AuditLog(
                occurred_at=datetime.now(UTC),
                actor_id=self.owner_id,
                action="auto_delete_non_admin_link",
                target_type="telegram_message",
                target_id=f"{chat_id}/{message.id}",
                outcome="success",
                reason="Tin có external link và sender không phải owner/creator/admin",
                details_redacted={
                    "sender_id": getattr(message, "sender_id", None),
                    "rule": rule.name,
                    "content_preview": str(redact(getattr(message, "message", None) or "[media]"))[
                        :500
                    ],
                },
            )
        )
        return True

    def register_handlers(
        self,
        database: Any,
        *,
        group_ask_handler: GroupAskHandler | None = None,
    ) -> None:
        @self.client.on(events.NewMessage)
        async def new_message(event: Any) -> None:
            if self.owner_id is None:
                return
            chat_id = int(event.chat_id)
            sender_id = getattr(event.message, "sender_id", None)
            question = extract_group_ai_question(
                getattr(event.message, "message", None),
                self.username,
            )
            async with database.session() as session:
                decision = await self.policy.evaluate(
                    session,
                    PolicyContext(
                        self.owner_id, self.owner_id, chat_id, PermissionName.MONITOR_NEW_MESSAGES
                    ),
                )
                if decision.allowed:
                    await self._upsert_message(session, event.message)
                    await session.flush()
                    await self._auto_moderate_new_message(
                        session,
                        chat_id=chat_id,
                        message=event.message,
                    )
            if (
                question is not None
                and sender_id is not None
                and group_ask_handler is not None
            ):
                await group_ask_handler(
                    chat_id,
                    int(sender_id),
                    int(event.message.id),
                    question,
                )

        @self.client.on(events.MessageEdited)
        async def edited(event: Any) -> None:
            if self.owner_id is None:
                return
            async with database.session() as session:
                decision = await self.policy.evaluate(
                    session,
                    PolicyContext(
                        self.owner_id,
                        self.owner_id,
                        int(event.chat_id),
                        PermissionName.MONITOR_NEW_MESSAGES,
                    ),
                )
                if decision.allowed:
                    await self._upsert_message(session, event.message)

        @self.client.on(events.MessageDeleted)
        async def deleted(event: Any) -> None:
            if self.owner_id is None or not event.chat_id:
                return
            async with database.session() as session:
                decision = await self.policy.evaluate(
                    session,
                    PolicyContext(
                        self.owner_id,
                        self.owner_id,
                        int(event.chat_id),
                        PermissionName.MONITOR_NEW_MESSAGES,
                    ),
                )
                if decision.allowed:
                    rows = (
                        await session.scalars(
                            select(TelegramMessage).where(
                                TelegramMessage.chat_id == int(event.chat_id),
                                TelegramMessage.message_id.in_(event.deleted_ids),
                            )
                        )
                    ).all()
                    for row in rows:
                        row.is_deleted = True

    async def _upsert_message(self, session: AsyncSession, message: Any) -> None:
        row = await session.scalar(
            select(TelegramMessage).where(
                TelegramMessage.chat_id == int(message.chat_id),
                TelegramMessage.message_id == int(message.id),
            )
        )
        if row:
            if row.text != message.message:
                version_number = (
                    int(
                        (
                            await session.scalar(
                                select(func.count(TelegramMessageVersion.id)).where(
                                    TelegramMessageVersion.telegram_message_id == row.id
                                )
                            )
                        )
                        or 0
                    )
                    + 1
                )
                session.add(
                    TelegramMessageVersion(
                        telegram_message_id=row.id,
                        text=row.text,
                        version_number=version_number,
                        captured_at=datetime.now(UTC),
                    )
                )
            row.text = message.message
            row.edited_at = message.edit_date
            row.is_deleted = False
            return
        values = {
            "chat_id": int(message.chat_id),
            "message_id": int(message.id),
            "sender_id": int(message.sender_id) if message.sender_id else None,
            "text": message.message,
            "sent_at": message.date.astimezone(UTC),
            "edited_at": message.edit_date,
            "reply_to_message_id": getattr(message.reply_to, "reply_to_msg_id", None),
            "is_outgoing": bool(message.out),
            "is_deleted": False,
            "has_media": message.media is not None,
        }
        update_columns = {
            key: value
            for key, value in values.items()
            if key not in {"chat_id", "message_id"}
        }
        dialect = session.get_bind().dialect.name
        if dialect == "mysql":
            statement = mysql_insert(TelegramMessage).values(**values)
            await session.execute(
                statement.on_duplicate_key_update(
                    **{key: getattr(statement.inserted, key) for key in update_columns}
                )
            )
            return
        if dialect == "sqlite":
            statement = sqlite_insert(TelegramMessage).values(**values)
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["chat_id", "message_id"],
                    set_={key: getattr(statement.excluded, key) for key in update_columns},
                )
            )
            return

        new_row = TelegramMessage(**values)
        try:
            async with session.begin_nested():
                session.add(new_row)
                await session.flush()
        except IntegrityError:
            # History sync and the live listener can see the same message at once.
            # The savepoint keeps the outer transaction usable after that race.
            row = await session.scalar(
                select(TelegramMessage).where(
                    TelegramMessage.chat_id == int(message.chat_id),
                    TelegramMessage.message_id == int(message.id),
                )
            )
            if not row:
                raise
            row.sender_id = int(message.sender_id) if message.sender_id else None
            row.text = message.message
            row.sent_at = message.date.astimezone(UTC)
            row.edited_at = message.edit_date
            row.reply_to_message_id = getattr(message.reply_to, "reply_to_msg_id", None)
            row.is_outgoing = bool(message.out)
            row.is_deleted = False
            row.has_media = message.media is not None

    async def sync_history(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        actor_id: int,
        owner_id: int,
        limit: int = 1000,
    ) -> int:
        decision = await self.policy.evaluate(
            session, PolicyContext(actor_id, owner_id, chat_id, PermissionName.SYNC_HISTORY)
        )
        if not decision.allowed:
            raise PermissionError(decision.reason.value)
        state = await session.scalar(select(SyncState).where(SyncState.chat_id == chat_id))
        min_id = state.last_message_id or 0 if state else 0
        count, highest = 0, min_id
        latest_date = state.last_message_date if state else None
        if latest_date is not None and latest_date.tzinfo is None:
            latest_date = latest_date.replace(tzinfo=UTC)
        try:
            async for message in self.client.iter_messages(
                chat_id,
                min_id=min_id,
                reverse=False,
                limit=limit,
            ):
                await self._upsert_message(session, message)
                highest = max(highest, int(message.id))
                message_date = message.date
                if message_date is not None and message_date.tzinfo is None:
                    message_date = message_date.replace(tzinfo=UTC)
                if message_date and (latest_date is None or message_date > latest_date):
                    latest_date = message_date
                count += 1
        except FloodWaitError as exc:
            await asyncio.sleep(min(exc.seconds, 60))
            raise RuntimeError(f"Telegram FloodWait {exc.seconds}s") from exc
        if not state:
            state = SyncState(chat_id=chat_id)
            session.add(state)
        state.last_message_id, state.last_message_date, state.state = (
            highest,
            latest_date,
            "idle",
        )
        return count

    async def sync_sender_history(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        username: str,
        limit: int = 100,
    ) -> tuple[int, int]:
        """Resolve a username and persist only that sender's newest messages in one chat."""
        clean_username = username.strip().lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", clean_username):
            raise ValueError("Username Telegram không hợp lệ.")
        entity = await self.client.get_entity(f"@{clean_username}")
        if not isinstance(entity, types.User):
            raise ValueError("Username không phải tài khoản Telegram.")
        sender_id = int(entity.id)
        count = 0
        try:
            async for message in self.client.iter_messages(
                chat_id,
                from_user=entity,
                reverse=False,
                limit=min(max(limit, 1), 100),
            ):
                if int(message.sender_id or 0) != sender_id:
                    continue
                await self._upsert_message(session, message)
                count += 1
        except FloodWaitError as exc:
            await asyncio.sleep(min(exc.seconds, 60))
            raise RuntimeError(f"Telegram FloodWait {exc.seconds}s") from exc
        return sender_id, count

    async def send_message(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        text: str,
        actor_id: int,
        owner_id: int,
        telegram_rights: frozenset[str],
    ) -> int:
        decision = await self.policy.evaluate(
            session,
            PolicyContext(
                actor_id, owner_id, chat_id, PermissionName.SEND_MESSAGES, telegram_rights
            ),
        )
        if not decision.allowed:
            raise PermissionError(decision.reason.value)
        message = await self.client.send_message(chat_id, text)
        return int(message.id)

    async def delete_message(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        message_id: int,
        actor_id: int,
        owner_id: int,
        telegram_rights: frozenset[str],
        confirmed: bool,
        delete_any: bool,
    ) -> None:
        permission = (
            PermissionName.DELETE_ANY_MESSAGES if delete_any else PermissionName.DELETE_OWN_MESSAGES
        )
        decision = await self.policy.evaluate(
            session,
            PolicyContext(actor_id, owner_id, chat_id, permission, telegram_rights, confirmed),
        )
        if not decision.allowed:
            raise PermissionError(decision.reason.value)
        await self.client.delete_messages(chat_id, [message_id])

    async def edit_message(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        actor_id: int,
        owner_id: int,
        telegram_rights: frozenset[str],
    ) -> None:
        decision = await self.policy.evaluate(
            session,
            PolicyContext(
                actor_id,
                owner_id,
                chat_id,
                PermissionName.EDIT_OWN_MESSAGES,
                telegram_rights,
            ),
        )
        if not decision.allowed:
            raise PermissionError(decision.reason.value)
        stored = await session.scalar(
            select(TelegramMessage).where(
                TelegramMessage.chat_id == chat_id,
                TelegramMessage.message_id == message_id,
                TelegramMessage.is_outgoing.is_(True),
            )
        )
        if not stored:
            raise PermissionError("Chỉ được sửa tin nhắn do tài khoản này gửi.")
        await self.client.edit_message(chat_id, message_id, text)

    async def pin_message(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        message_id: int,
        actor_id: int,
        owner_id: int,
        telegram_rights: frozenset[str],
    ) -> None:
        decision = await self.policy.evaluate(
            session,
            PolicyContext(
                actor_id,
                owner_id,
                chat_id,
                PermissionName.PIN_MESSAGES,
                telegram_rights,
            ),
        )
        if not decision.allowed:
            raise PermissionError(decision.reason.value)
        await self.client.pin_message(chat_id, message_id, notify=False)
