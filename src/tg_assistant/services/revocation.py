"""Durable source revocation and authorization snapshots.

No task may refresh its authorization snapshot after an awaited operation.
Reads use scalar columns and locking current reads, avoiding ORM identity caches
and MySQL repeatable-read snapshots. Callers commit revocation before reporting it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import exists, select

from ..contracts import RevocationReport
from ..db.models import (
    AiMemory,
    AuditLog,
    BackgroundJob,
    KnowledgeSource,
    PendingAction,
    PermissionName,
    TelegramChatPermission,
    TelegramChatPolicy,
)


class AuthorizationRevoked(PermissionError):
    """An operation's durable authorization generation is no longer current."""


class AuthorizedAnswer(str):
    """Internal text carrying source generations to the final delivery boundary."""

    def __new__(cls, text: str, epochs: dict[int, int]):
        value = super().__new__(cls, text)
        value.authorization_epochs = dict(epochs)
        return value


async def validate_answer(session, answer: str) -> None:
    if isinstance(answer, AuthorizedAnswer):
        for chat_id, epoch in answer.authorization_epochs.items():
            await require_authorization(session, chat_id, PermissionName.SEARCH_MESSAGES, epoch)


async def source_epoch(session, chat_id: int) -> int:
    with session.no_autoflush:
        epoch = await session.scalar(
            select(TelegramChatPolicy.authorization_epoch)
            .where(TelegramChatPolicy.chat_id == chat_id)
            .with_for_update()
        )
    return int(epoch or 0)


async def require_authorization(
    session, chat_id: int, permission: PermissionName, epoch: int | None = None
) -> int:
    with session.no_autoflush:
        policy_query = select(
            TelegramChatPolicy.authorization_epoch, TelegramChatPolicy.allowed
        ).where(TelegramChatPolicy.chat_id == chat_id)
        permission_query = select(TelegramChatPermission.enabled).where(
            TelegramChatPermission.chat_id == chat_id,
            TelegramChatPermission.permission == permission.value,
        )
        if epoch is not None:
            # MySQL consistent reads keep the transaction's old snapshot. A
            # locking current read is required when rechecking a captured epoch.
            policy_query = policy_query.with_for_update()
            permission_query = permission_query.with_for_update()
        row = (await session.execute(policy_query)).first()
        enabled = await session.scalar(permission_query)
    if (
        not row
        or not row.allowed
        or not enabled
        or (epoch is not None and row.authorization_epoch != epoch)
    ):
        raise AuthorizationRevoked("source_authorization_revoked")
    return int(row.authorization_epoch)


async def require_fresh_authorization(session, chat_id, permission, epoch=None, lease=None):
    """One statement in a fresh transaction: current committed state, no self-lock.

    This helper must only be used with a newly opened session before any read.
    Write transactions still lock/recheck epochs at their commit boundary.
    """
    query = (
        select(
            TelegramChatPolicy.authorization_epoch,
            TelegramChatPolicy.allowed,
            TelegramChatPermission.enabled,
        )
        .outerjoin(
            TelegramChatPermission,
            (TelegramChatPermission.chat_id == TelegramChatPolicy.chat_id)
            & (TelegramChatPermission.permission == permission.value),
        )
        .where(TelegramChatPolicy.chat_id == chat_id)
    )
    if lease:
        query = query.where(
            exists(
                select(BackgroundJob.id).where(
                    BackgroundJob.id == lease.id,
                    BackgroundJob.claim_token == lease.claim_token,
                    BackgroundJob.status == "running",
                    BackgroundJob.lease_expires_at > datetime.now(UTC),
                )
            )
        )
    row = (await session.execute(query)).first()
    if (
        not row
        or not row.allowed
        or not row.enabled
        or (epoch is not None and epoch != row.authorization_epoch)
    ):
        raise AuthorizationRevoked("source_authorization_revoked")
    return int(row.authorization_epoch)


def source_ids(payload: dict, chat_id: int | None = None) -> list[int]:
    ids = [chat_id] if chat_id is not None else []
    ids += [int(value) for value in payload.get("chat_ids", [])]
    if payload.get("chat_id") is not None:
        ids.append(int(payload["chat_id"]))
    return list(dict.fromkeys(ids))


async def validate_action_epoch(session, action: PendingAction) -> None:
    snapshots = (action.payload or {}).get("authorization_epochs", {})
    for chat_id in source_ids(action.payload or {}, action.chat_id):
        if snapshots.get(str(chat_id)) != await source_epoch(session, chat_id):
            raise AuthorizationRevoked("stale_action_authorization")


async def fence_source_work(session, chat_id: int) -> int:
    jobs = list(
        (
            await session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.status.in_(("queued", "running", "paused", "pause_requested"))
                )
            )
        ).all()
    )
    cancelled = 0
    for job in jobs:
        if chat_id in source_ids(job.payload or {}):
            job.status = (
                "uncertain" if (job.payload or {}).get("external_effect_started") else "cancelled"
            )
            job.last_error = "source_authorization_revoked"
            job.locked_by = None
            job.locked_at = None
            cancelled += 1
    actions = list(
        (
            await session.scalars(
                select(PendingAction).where(
                    PendingAction.status.in_(("pending", "confirmed", "executing"))
                )
            )
        ).all()
    )
    for action in actions:
        if chat_id in source_ids(action.payload or {}, action.chat_id):
            action.status = (
                "uncertain"
                if action.status == "executing"
                and (action.payload or {}).get("external_effect_started")
                else "cancelled"
            )
            action.error = "source_authorization_revoked"
    source = await session.get(KnowledgeSource, chat_id)
    if source:
        source.requested_for_learning = False
        source.status = "revoked"
    return cancelled


class RevocationService:
    def __init__(self, database, owner_id: int):
        self.database = database
        self.owner_id = owner_id

    async def revoke_source(
        self, chat_id: int, actor_id: int, memory_action: str
    ) -> RevocationReport:
        async with self.database.session() as session:
            return await self.revoke_in_session(session, chat_id, actor_id, memory_action)

    async def revoke_in_session(
        self, session, chat_id: int, actor_id: int, memory_action: str
    ) -> RevocationReport:
        if actor_id != self.owner_id:
            raise PermissionError("not_owner")
        if memory_action not in {"keep", "archive", "delete"}:
            raise ValueError("invalid_memory_action")
        from ..policy import PolicyEngine

        policy = await PolicyEngine().set_allowed(session, chat_id, False)
        policy.revocation_memory_action = memory_action
        cancelled = session.info.pop("revocation_cancelled_jobs", 0)
        if memory_action != "keep":
            memories = (
                await session.scalars(
                    select(AiMemory).where(
                        AiMemory.source_chat_id == chat_id, AiMemory.status == "active"
                    )
                )
            ).all()
            for memory in memories:
                memory.status = "archived" if memory_action == "archive" else "deleted"
                if memory_action == "delete":
                    memory.content = "[deleted]"
        session.add(
            AuditLog(
                occurred_at=datetime.now(UTC),
                actor_id=actor_id,
                action="revoke_source",
                target_type="telegram_chat",
                target_id=str(chat_id),
                outcome="success",
                details_redacted={
                    "authorization_epoch": policy.authorization_epoch,
                    "cancelled_jobs": cancelled,
                    "memory_action": memory_action,
                },
            )
        )
        return RevocationReport(
            chat_id=str(chat_id),
            authorization_epoch=policy.authorization_epoch,
            cancelled_jobs=cancelled,
            memory_action=memory_action,
            operation_id=None,
        )
