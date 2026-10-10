from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path
from time import monotonic
from typing import Any, NamedTuple

import structlog
from sqlalchemy import func, select, text, update
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
    TelegramAttachment,
    TelegramChat,
    TelegramChatPolicy,
    TelegramMessage,
    TelegramMessageVersion,
)
from ..policy import PolicyContext, PolicyEngine
from ..security import EncryptedSession, SecretStore, redact
from ..services.revocation import AuthorizationRevoked, require_authorization

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

_HISTORY_CURSOR = (
    "last_message_id",
    "last_message_date",
    "baseline_message_id",
    "catchup_upper_id",
    "catchup_after_id",
    "authorization_epoch",
    "state",
    "error",
)


class HistorySyncPage(NamedTuple):
    read: int
    saved: int
    skipped: int
    completed: bool
    upper_id: int | None
    after_id: int | None
    stop_reason: str | None = None


class StagedHistoryPage(NamedTuple):
    chat_id: int
    actor_id: int
    owner_id: int
    epoch: int
    expected: tuple | None
    baseline: int | None
    upper: int
    after: int
    messages: tuple
    exhausted: bool
    fetched: int | None = None


class HistoryBackfillPage(NamedTuple):
    synced_count: int
    next_before_message_id: int | None
    completed: bool


class _AccountManagementUnavailable(PermissionError):
    def __init__(self):
        super().__init__("owner_pairing_required")


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


def message_media_kind(message: Any) -> str:
    """Classify Telegram media narrowly so image deletion never includes videos/documents."""
    if getattr(message, "photo", None) is not None:
        return "image"
    document = getattr(message, "document", None)
    mime_type = str(getattr(document, "mime_type", "") or "").lower()
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/") or getattr(message, "video", None) is not None:
        return "video"
    if document is not None:
        return "document"
    return "none"


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
        decrypted = False
        try:
            if encrypted_path.exists() and not session_path.exists():
                decrypted = True
                self.crypto.decrypt_file(encrypted_path, session_path)
            self.client = TelegramClient(str(session_path), api_id, api_hash)
        except BaseException:
            # The original ciphertext is still intact. Remove only the working
            # copy created by this constructor when transport creation fails.
            if decrypted:
                session_path.unlink(missing_ok=True)
                Path(f"{session_path}-journal").unlink(missing_ok=True)
            raise
        self.policy = policy
        self.owner_id: int | None = None
        self.username: str | None = None
        self.management_admission: Callable[[], bool] | None = None
        self._trusted_admin_cache: dict[tuple[int, int], float] = {}

    def _check_management(self) -> None:
        # Setup and isolated storage callers install no management callback.
        # The owning runtime installs this only after actual bot preparation.
        callback = getattr(self, "management_admission", None)
        if callback is None:
            return
        admitted = False
        try:
            admitted = callback() is True
        except Exception:
            admitted = False
        if not admitted:
            raise _AccountManagementUnavailable() from None

    def _guard_management_handler(self, handler):
        @wraps(handler)
        async def guarded(event):
            try:
                self._check_management()
                await handler(event)
            except _AccountManagementUnavailable:
                # Withdrawal is silent and contains no private callback error.
                return

        return guarded

    async def authenticate(self, phone: str, *, password_callback: Any = None) -> Any:
        await self.client.start(phone=phone, password=password_callback)
        me = await self.client.get_me()
        self.owner_id = int(me.id)
        self.username = getattr(me, "username", None)
        return me

    async def resume_existing(self) -> Any:
        """Resume a stored session without Telethon's interactive login path."""
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError("telegram_reconnect_required")
        me = await self.client.get_me()
        if me is None or type(me.id) is not int or me.id <= 0:
            raise RuntimeError("telegram_reconnect_required")
        self.owner_id = me.id
        self.username = getattr(me, "username", None)
        return me

    async def close(self) -> None:
        try:
            await self.client.disconnect()
        finally:
            try:
                session = getattr(self.client, "session", None)
                if session is not None:
                    session.close()
            finally:
                if self.session_path.exists():
                    try:
                        self.crypto.encrypt_file(self.session_path, self.encrypted_path)
                    finally:
                        # Atomic sealing preserves previous ciphertext on error.
                        # Reconnect is required if the new working copy cannot seal.
                        self.session_path.unlink(missing_ok=True)
                        Path(f"{self.session_path}-journal").unlink(missing_ok=True)

    async def discover_dialogs(self, session: AsyncSession) -> int:
        count = 0
        self._check_management()
        async for dialog in self.client.iter_dialogs():
            self._check_management()
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

    async def resolve_sender_identity(self, reference: str) -> dict[str, object]:
        """Resolve a Telegram @handle (including a channel) into its sender ID and label."""
        query = reference.strip()
        if not query:
            raise ValueError("Nhập @handle hoặc ID người đăng.")
        self._check_management()
        try:
            entity = await self.client.get_entity(
                int(query) if re.fullmatch(r"-?\d+", query) else f"@{query.lstrip('@')}"
            )
        except Exception as exc:
            raise ValueError("Không tìm thấy tài khoản/channel Telegram theo handle này.") from exc
        sender_id = getattr(entity, "id", None)
        self._check_management()
        if sender_id is None:
            raise ValueError("Telegram không trả về định danh người đăng hợp lệ.")
        username = getattr(entity, "username", None)
        display_name = (
            getattr(entity, "title", None)
            or " ".join(
                value
                for value in (
                    getattr(entity, "first_name", None),
                    getattr(entity, "last_name", None),
                )
                if value
            )
            or (f"@{username}" if username else None)
            or f"Sender {sender_id}"
        )
        return {
            "sender_id": int(sender_id),
            "display_name": str(display_name),
            "username": str(username) if username else None,
            "kind": "channel" if isinstance(entity, types.Channel) else "account",
        }

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
        self._check_management()
        await self.client.delete_dialog(chat_id, revoke=False)

    async def get_actual_rights(self, chat_id: int) -> dict[str, bool]:
        self._check_management()
        entity = await self.client.get_entity(chat_id)
        self._check_management()
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
        self._check_management()
        permissions = await self.client.get_permissions(chat_id, sender_id)
        self._check_management()
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
        self._check_management()
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
            epoch = await self._authorization_fence(
                session, chat_id, PermissionName.AUTO_MODERATION
            )
        except AuthorizationRevoked:
            return False
        try:
            if await self._sender_is_trusted_admin(chat_id, message):
                return False
        except _AccountManagementUnavailable:
            raise
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
        except _AccountManagementUnavailable:
            raise
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
            await self._authorization_fence(session, chat_id, PermissionName.AUTO_MODERATION, epoch)
            await self._authorization_fence(
                session, chat_id, PermissionName.DELETE_ANY_MESSAGES, epoch
            )
        except AuthorizationRevoked:
            return False
        try:
            self._check_management()
            await self.client.delete_messages(chat_id, [int(message.id)])
        except _AccountManagementUnavailable:
            raise
        except Exception as exc:
            session.add(
                AuditLog(
                    occurred_at=datetime.now(UTC),
                    actor_id=self.owner_id,
                    action="auto_delete_non_admin_link",
                    target_type="telegram_message",
                    target_id=f"{chat_id}/{message.id}",
                    outcome="uncertain",
                    reason=str(redact(str(exc)))[:1000],
                    details_redacted={
                        "sender_id": getattr(message, "sender_id", None),
                        "rule": rule.name,
                        "requires_reconciliation": True,
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
        self._check_management()
        if stored:
            stored.is_deleted = True
            await self._dirty_message(session, stored)
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
        self.database = database

        @self.client.on(events.NewMessage)
        @self._guard_management_handler
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
                self._check_management()
                if decision.allowed:
                    await self._upsert_message(session, event.message)
                    await session.flush()
                    await self._auto_moderate_new_message(
                        session,
                        chat_id=chat_id,
                        message=event.message,
                    )
            if question is not None and sender_id is not None and group_ask_handler is not None:
                self._check_management()
                await group_ask_handler(
                    chat_id,
                    int(sender_id),
                    int(event.message.id),
                    question,
                )

        @self.client.on(events.MessageEdited)
        @self._guard_management_handler
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
                self._check_management()
                if decision.allowed:
                    await self._upsert_message(session, event.message)

        @self.client.on(events.MessageDeleted)
        @self._guard_management_handler
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
                self._check_management()
                if decision.allowed:
                    rows = (
                        await session.scalars(
                            select(TelegramMessage).where(
                                TelegramMessage.chat_id == int(event.chat_id),
                                TelegramMessage.message_id.in_(event.deleted_ids),
                            )
                        )
                    ).all()
                    self._check_management()
                    for row in rows:
                        row.is_deleted = True
                        await self._dirty_message(session, row)

    async def _dirty_message(self, session: AsyncSession, row: TelegramMessage) -> None:
        """Coalesce invalidations transactionally with the authoritative edit/delete."""
        row.vector_dirty = True
        row.content_hash = None  # invalidate any captured in-flight embedding fence
        # An edited/deleted canonical may need a surviving duplicate promoted.
        duplicates = (
            await session.scalars(
                select(TelegramMessage).where(
                    TelegramMessage.chat_id == row.chat_id,
                    TelegramMessage.embedding_skip_reason == "duplicate",
                )
            )
        ).all()
        for duplicate in duplicates:
            duplicate.vector_dirty = True

    async def _upsert_message(self, session: AsyncSession, message: Any) -> None:
        row = await session.scalar(
            select(TelegramMessage).where(
                TelegramMessage.chat_id == int(message.chat_id),
                TelegramMessage.message_id == int(message.id),
            )
        )
        if row:
            if row.text != message.message or row.is_deleted:
                await self._dirty_message(session, row)
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
            row.has_media = message.media is not None
            row.metadata_json = {
                **(row.metadata_json or {}),
                "media_kind": message_media_kind(message),
            }
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
            "metadata_json": {"media_kind": message_media_kind(message)},
        }
        update_columns = {
            key: value
            for key, value in values.items()
            if key not in {"chat_id", "message_id", "metadata_json"}
        }
        dialect = session.get_bind().dialect.name
        if dialect == "mysql":
            statement = mysql_insert(TelegramMessage).values(**values)
            await session.execute(
                statement.on_duplicate_key_update(
                    **{key: getattr(statement.inserted, key) for key in update_columns},
                    vector_dirty=True,
                    content_hash=None,
                )
            )
            return
        if dialect == "sqlite":
            statement = sqlite_insert(TelegramMessage).values(**values)
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["chat_id", "message_id"],
                    set_={
                        **{key: getattr(statement.excluded, key) for key in update_columns},
                        "vector_dirty": True,
                        "content_hash": None,
                    },
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
            row.metadata_json = {
                **(row.metadata_json or {}),
                "media_kind": message_media_kind(message),
            }

    async def _history_read_stage(self, session):
        # SQLAlchemy autobegin after SELECT is not a SQLite writer transaction.
        with session.no_autoflush:
            connection = await session.connection()
            raw = await connection.get_raw_connection()
            if (
                session.new
                or session.dirty
                or session.deleted
                or raw.driver_connection.in_transaction
            ):
                raise RuntimeError("history_sync_requires_read_stage")

    async def stage_history_page(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        actor_id: int,
        owner_id: int,
        limit: int = 1000,
        check_exhaustion: bool = False,
        continuation_cursor: tuple[int, int, int] | None = None,
        fetch_budget: int | None = None,
    ) -> StagedHistoryPage:
        if type(limit) is not int or not 1 <= limit <= 100000:
            raise ValueError("invalid_history_limit")
        if fetch_budget is not None and (type(fetch_budget) is not int or not 1 <= fetch_budget <= 1000):
            raise ValueError("invalid_history_fetch_budget")
        self._check_management()
        if actor_id != owner_id or getattr(self, "owner_id", owner_id) not in (None, owner_id):
            raise PermissionError("not_owner")
        with session.no_autoflush:
            epoch = await self._authorization_fence(session, chat_id, PermissionName.SYNC_HISTORY)
            await self._history_read_stage(session)
            row = (
                await session.execute(
                    select(*(getattr(SyncState, key) for key in _HISTORY_CURSOR)).where(
                        SyncState.chat_id == chat_id
                    )
                )
            ).first()
            expected = tuple(row) if row else None
            previous = dict(zip(_HISTORY_CURSOR, expected, strict=True)) if expected else {}
            baseline = previous.get("baseline_message_id")
            upper = previous.get("catchup_upper_id")
            after = previous.get("catchup_after_id")
            if upper is not None and previous.get("authorization_epoch") != epoch:
                raise AuthorizationRevoked("source_authorization_revoked")
            if continuation_cursor is not None:
                # An internal committed cursor is continuity evidence, never authorization.
                if upper is None or continuation_cursor != (epoch, upper, after):
                    raise RuntimeError("history_sync_conflict")
            else:
                decision = await self.policy.evaluate(
                    session, PolicyContext(actor_id, owner_id, chat_id, PermissionName.SYNC_HISTORY)
                )
                if not decision.allowed:
                    raise PermissionError(decision.reason.value)
            page_size = min(limit, 1000)
            fetched, fetch_exhausted = 0, False

            async def fetch(**kwargs):
                nonlocal fetched, fetch_exhausted
                if fetch_budget is not None:
                    kwargs["limit"] = min(kwargs["limit"], fetch_budget - fetched)
                if kwargs["limit"] <= 0:
                    fetch_exhausted = False
                    return []
                messages = []
                async for message in self.client.iter_messages(chat_id, **kwargs):
                    messages.append(message)
                fetched += len(messages)
                fetch_exhausted = len(messages) < kwargs["limit"]
                await self._authorization_fence(
                    session, chat_id, PermissionName.SYNC_HISTORY, epoch
                )
                return messages

            try:
                if upper is None and previous.get("last_message_id") is None and baseline is None:
                    if limit <= 1000 or fetch_budget is not None:
                        messages = await fetch(reverse=False, limit=limit)
                        messages.sort(key=lambda message: int(message.id))
                        baseline = int(messages[0].id) if messages else 0
                        upper = int(messages[-1].id) if messages else 0
                        after = max(0, baseline - 1)
                        exhausted = fetch_exhausted
                    else:
                        # Count actual yielded messages: server offsets include MessageEmpty entries.
                        upper, baseline, count = 0, 0, 0
                        async for message in self.client.iter_messages(
                            chat_id, reverse=False, limit=limit
                        ):
                            count += 1
                            if count == 1:
                                upper = int(message.id)
                            baseline = int(message.id)
                            if count % 100 == 0:
                                await self._authorization_fence(
                                    session, chat_id, PermissionName.SYNC_HISTORY, epoch
                                )
                        await self._authorization_fence(
                            session, chat_id, PermissionName.SYNC_HISTORY, epoch
                        )
                        after = max(0, baseline - 1)
                        messages = await fetch(
                            min_id=after, max_id=upper + 1, reverse=True, limit=page_size
                        )
                        exhausted = len(messages) < page_size
                else:
                    if upper is None:
                        after = previous.get("last_message_id")
                        if after is None:
                            after = max(0, (baseline or 0) - 1)
                        newest = await fetch(min_id=after, reverse=False, limit=1)
                        upper = int(newest[0].id) if newest else after
                    messages = await fetch(
                        min_id=after, max_id=upper + 1, reverse=True, limit=page_size
                    )
                    exhausted = fetch_exhausted
                if fetch_budget is not None and messages and int(messages[-1].id) == upper:
                    exhausted = True
                if check_exhaustion and not exhausted and messages:
                    lookahead = await fetch(
                        min_id=int(messages[-1].id), max_id=upper + 1, reverse=True, limit=1
                    )
                    exhausted = fetch_exhausted and not lookahead
            except FloodWaitError as exc:
                await asyncio.sleep(min(exc.seconds, 60))
                raise RuntimeError(f"Telegram FloodWait {exc.seconds}s") from exc
            await self._authorization_fence(session, chat_id, PermissionName.SYNC_HISTORY, epoch)
            return StagedHistoryPage(
                chat_id,
                actor_id,
                owner_id,
                epoch,
                expected,
                baseline,
                upper,
                after,
                tuple(messages),
                exhausted,
                fetched if fetch_budget is not None else None,
            )

    async def apply_history_page(
        self, session: AsyncSession, page: StagedHistoryPage
    ) -> HistorySyncPage:
        connection = await session.connection()
        raw = await connection.get_raw_connection()
        if not raw.driver_connection.in_transaction:
            await session.execute(text("BEGIN IMMEDIATE"))
        self._check_management()
        if page.actor_id != page.owner_id or getattr(self, "owner_id", page.owner_id) not in (
            None,
            page.owner_id,
        ):
            raise PermissionError("not_owner")
        await require_authorization(session, page.chat_id, PermissionName.SYNC_HISTORY, page.epoch)
        values = dict(
            baseline_message_id=page.baseline,
            catchup_upper_id=page.upper,
            catchup_after_id=page.after,
            authorization_epoch=page.epoch,
            state="syncing",
            error=None,
        )
        if page.expected is None:
            statement = sqlite_insert(SyncState).values(chat_id=page.chat_id, **values)
            result = await session.execute(
                statement.on_conflict_do_nothing(index_elements=["chat_id"])
            )
        else:
            statement = update(SyncState).where(SyncState.chat_id == page.chat_id)
            for key, value in zip(_HISTORY_CURSOR, page.expected, strict=True):
                statement = statement.where(getattr(SyncState, key) == value)
            result = await session.execute(
                statement.values(**values).execution_options(synchronize_session=False)
            )
        if result.rowcount != 1:
            raise RuntimeError("history_sync_conflict")
        policy = await session.scalar(
            select(TelegramChatPolicy)
            .where(TelegramChatPolicy.chat_id == page.chat_id)
            .execution_options(populate_existing=True)
        )
        if any(
            value is not None and value < 0
            for value in (policy.max_messages, policy.max_storage_mb)
        ):
            raise ValueError("invalid_history_quota")
        count, size = (
            await session.execute(
                select(
                    func.count(TelegramMessage.id),
                    func.coalesce(func.sum(func.length(TelegramMessage.text)), 0),
                ).where(
                    TelegramMessage.chat_id == page.chat_id, TelegramMessage.is_deleted.is_(False)
                )
            )
        ).one()
        size += (
            await session.scalar(
                select(func.sum(TelegramAttachment.size_bytes))
                .join(TelegramMessage, TelegramMessage.id == TelegramAttachment.telegram_message_id)
                .where(TelegramMessage.chat_id == page.chat_id)
            )
        ) or 0
        cutoff = (
            datetime.now(UTC) - timedelta(days=policy.retention_days)
            if policy.retention_days and policy.retention_days > 0
            else None
        )
        saved, skipped, after, quota = 0, 0, page.after, False
        for message in page.messages:
            date = message.date
            if date is not None and date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            if cutoff and date and date < cutoff:
                skipped += 1
                after = int(message.id)
                continue
            old = (
                await session.execute(
                    select(TelegramMessage.is_deleted, func.length(TelegramMessage.text)).where(
                        TelegramMessage.chat_id == page.chat_id,
                        TelegramMessage.message_id == int(message.id),
                    )
                )
            ).first()
            count_delta = int(old is None or old[0])
            text_size = (await session.scalar(select(func.length(message.message)))) or 0
            size_delta = text_size - ((old[1] or 0) if old and not old[0] else 0)
            if (
                policy.max_messages is not None
                and count_delta > 0
                and count + count_delta > policy.max_messages
            ) or (
                policy.max_storage_mb is not None
                and size_delta > 0
                and size + size_delta > policy.max_storage_mb * 1024 * 1024
            ):
                quota = True
                break
            await self._upsert_message(session, message)
            count += count_delta
            size += size_delta
            saved += 1
            after = int(message.id)
        completed = page.exhausted and not quota
        values.update(catchup_after_id=after, state="paused" if quota else "syncing")
        if completed:
            await session.flush()
            latest_date = await session.scalar(
                select(func.max(TelegramMessage.sent_at)).where(
                    TelegramMessage.chat_id == page.chat_id,
                    TelegramMessage.is_deleted.is_(False),
                    TelegramMessage.message_id <= page.upper,
                    TelegramMessage.message_id
                    > (
                        (page.expected[0] if page.expected else None)
                        or max(0, (page.baseline or 0) - 1)
                    ),
                )
            )
            previous_date = page.expected[1] if page.expected else None
            dates = [
                date.replace(tzinfo=UTC) if date.tzinfo is None else date
                for date in (latest_date, previous_date)
                if date
            ]
            values.update(
                last_message_id=page.upper,
                last_message_date=max(dates) if dates else None,
                catchup_upper_id=None,
                catchup_after_id=None,
                state="idle",
            )
        await session.execute(
            update(SyncState)
            .where(SyncState.chat_id == page.chat_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await require_authorization(session, page.chat_id, PermissionName.SYNC_HISTORY, page.epoch)
        self._check_management()
        return HistorySyncPage(
            page.fetched if page.fetched is not None else len(page.messages),
            saved,
            skipped,
            completed,
            page.upper,
            after,
            "quota" if quota else (None if completed else "page_limit"),
        )

    async def sync_history_page(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        actor_id: int,
        owner_id: int,
        limit: int = 1000,
    ) -> HistorySyncPage:
        page = await self.stage_history_page(
            session, chat_id=chat_id, actor_id=actor_id, owner_id=owner_id, limit=limit
        )
        return await self.apply_history_page(session, page)

    async def sync_history(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        actor_id: int,
        owner_id: int,
        limit: int = 1000,
    ) -> int:
        return (
            await self.sync_history_page(
                session, chat_id=chat_id, actor_id=actor_id, owner_id=owner_id, limit=limit
            )
        ).saved

    async def backfill_history_page(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        actor_id: int,
        owner_id: int,
        before_message_id: int | None,
        limit: int = 500,
    ) -> HistoryBackfillPage:
        """Append one older-history page without moving the live-sync cursor.

        Telegram returns newest-to-oldest for ``max_id``.  The background job keeps
        the oldest ID from each page as its next exclusive cursor, which makes the
        operation restartable and harmless when it overlaps with normal sync.
        """
        decision = await self.policy.evaluate(
            session, PolicyContext(actor_id, owner_id, chat_id, PermissionName.SYNC_HISTORY)
        )
        if not decision.allowed:
            raise PermissionError(decision.reason.value)
        epoch = await self._authorization_fence(session, chat_id, PermissionName.SYNC_HISTORY)
        page_size = min(max(int(limit), 1), 1000)
        cursor = max(int(before_message_id or 0), 0)
        count = 0
        oldest_id: int | None = None
        try:
            messages = []
            self._check_management()
            async for message in self.client.iter_messages(
                chat_id,
                max_id=cursor,
                limit=page_size,
            ):
                await self._authorization_fence(
                    session, chat_id, PermissionName.SYNC_HISTORY, epoch
                )
                messages.append(message)
            for message in messages:
                await self._authorization_fence(
                    session, chat_id, PermissionName.SYNC_HISTORY, epoch
                )
                await self._upsert_message(session, message)
                message_id = int(message.id)
                oldest_id = message_id if oldest_id is None else min(oldest_id, message_id)
                count += 1
        except FloodWaitError as exc:
            await asyncio.sleep(min(exc.seconds, 60))
            raise RuntimeError(f"Telegram FloodWait {exc.seconds}s") from exc
        await self._authorization_fence(session, chat_id, PermissionName.SYNC_HISTORY, epoch)
        return HistoryBackfillPage(
            synced_count=count,
            next_before_message_id=oldest_id,
            completed=count < page_size or oldest_id is None,
        )

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
        epoch = await self._authorization_fence(session, chat_id, PermissionName.SYNC_HISTORY)
        self._check_management()
        entity = await self.client.get_entity(f"@{clean_username}")
        await self._authorization_fence(session, chat_id, PermissionName.SYNC_HISTORY, epoch)
        if not isinstance(entity, types.User):
            raise ValueError("Username không phải tài khoản Telegram.")
        sender_id = int(entity.id)
        count = 0
        try:
            messages = []
            self._check_management()
            async for message in self.client.iter_messages(
                chat_id,
                from_user=entity,
                reverse=False,
                limit=min(max(limit, 1), 100),
            ):
                await self._authorization_fence(
                    session, chat_id, PermissionName.SYNC_HISTORY, epoch
                )
                if int(message.sender_id or 0) != sender_id:
                    continue
                messages.append(message)
            for message in messages:
                await self._authorization_fence(
                    session, chat_id, PermissionName.SYNC_HISTORY, epoch
                )
                await self._upsert_message(session, message)
                count += 1
        except FloodWaitError as exc:
            await asyncio.sleep(min(exc.seconds, 60))
            raise RuntimeError(f"Telegram FloodWait {exc.seconds}s") from exc
        await self._authorization_fence(session, chat_id, PermissionName.SYNC_HISTORY, epoch)
        return sender_id, count

    async def _authorization_fence(self, session, chat_id, permission, epoch=None):
        self._check_management()
        database = getattr(self, "database", None)
        if database:
            async with database.session() as current:
                result = await require_authorization(current, chat_id, permission, epoch)
        else:
            result = await require_authorization(session, chat_id, permission, epoch)
        self._check_management()
        return result

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
        epoch = await self._authorization_fence(session, chat_id, PermissionName.SEND_MESSAGES)
        self._check_management()
        message = await self.client.send_message(chat_id, text)
        await self._authorization_fence(session, chat_id, PermissionName.SEND_MESSAGES, epoch)
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
        epoch = await self._authorization_fence(session, chat_id, permission)
        self._check_management()
        await self.client.delete_messages(chat_id, [message_id])
        await self._authorization_fence(session, chat_id, permission, epoch)

    async def delete_messages_bulk(
        self,
        session: AsyncSession,
        *,
        chat_id: int,
        message_ids: list[int],
        actor_id: int,
        owner_id: int,
        telegram_rights: frozenset[str],
    ) -> None:
        if not message_ids:
            return
        decision = await self.policy.evaluate(
            session,
            PolicyContext(
                actor_id,
                owner_id,
                chat_id,
                PermissionName.DELETE_ANY_MESSAGES,
                telegram_rights,
                True,
            ),
        )
        if not decision.allowed:
            raise PermissionError(decision.reason.value)
        epoch = await self._authorization_fence(
            session, chat_id, PermissionName.DELETE_ANY_MESSAGES
        )
        self._check_management()
        await self.client.delete_messages(chat_id, message_ids)
        await self._authorization_fence(session, chat_id, PermissionName.DELETE_ANY_MESSAGES, epoch)

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
        epoch = await self._authorization_fence(session, chat_id, PermissionName.EDIT_OWN_MESSAGES)
        stored = await session.scalar(
            select(TelegramMessage).where(
                TelegramMessage.chat_id == chat_id,
                TelegramMessage.message_id == message_id,
                TelegramMessage.is_outgoing.is_(True),
            )
        )
        if not stored:
            raise PermissionError("Chỉ được sửa tin nhắn do tài khoản này gửi.")
        await self._authorization_fence(session, chat_id, PermissionName.EDIT_OWN_MESSAGES, epoch)
        self._check_management()
        await self.client.edit_message(chat_id, message_id, text)
        await self._authorization_fence(session, chat_id, PermissionName.EDIT_OWN_MESSAGES, epoch)

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
        epoch = await self._authorization_fence(session, chat_id, PermissionName.PIN_MESSAGES)
        self._check_management()
        await self.client.pin_message(chat_id, message_id, notify=False)
        await self._authorization_fence(session, chat_id, PermissionName.PIN_MESSAGES, epoch)
