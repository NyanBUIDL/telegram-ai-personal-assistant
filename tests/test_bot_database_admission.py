"""A private runtime withdrawal must roll back in-flight database writes."""

import pytest
from sqlalchemy import insert, select

from tg_assistant.db.base import Base, Database
from tg_assistant.db.models import (
    AppSetting,
    PermissionName,
    TelegramChat,
    TelegramChatPermission,
    TelegramChatPolicy,
)


async def test_runtime_bot_revocation_after_flush_rolls_back_transaction(tmp_path):
    database = Database("sqlite+aiosqlite:///" + str(tmp_path / "fenced.sqlite3"))
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    admitted = [True]
    database.management_admission = lambda: admitted[0]
    try:
        with pytest.raises(PermissionError, match="owner_pairing_required"):
            async with database.session() as session:
                session.add(AppSetting(key="pending_write", value={"state": "pending"}))
                await session.flush()
                admitted[0] = False
        async with database.engine.connect() as connection:
            assert await connection.scalar(select(AppSetting.key)) is None
    finally:
        await database.close()


async def test_private_withdrawal_after_actual_job_commit_fence_rolls_back(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from tg_assistant.contracts import JobLease
    from tg_assistant.db.models import BackgroundJob
    from tg_assistant.services import jobs

    database = Database("sqlite+aiosqlite:///" + str(tmp_path / "listener.sqlite3"))
    admitted = [True]
    identity, claim = uuid4().hex, uuid4().hex
    expiry = datetime.now(UTC) + timedelta(minutes=5)
    payload = {"chat_id": 100, "authorization_epoch": 0}
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with database.session() as session:
            session.add(TelegramChatPolicy(chat_id=100, allowed=True, authorization_epoch=0))
            session.add(BackgroundJob(
                id=identity, job_type="history_backfill", status="running",
                claim_token=claim, lease_expires_at=expiry, payload=payload,
            ))
        database.management_admission = lambda: admitted[0]
        original = jobs.fence_runtime_writes

        def withdraw_after_actual_fence(session, lease, *, phase="commit"):
            original(session, lease, phase=phase)
            if phase == "commit":
                admitted[0] = False

        monkeypatch.setattr(jobs, "fence_runtime_writes", withdraw_after_actual_fence)
        lease = JobLease(
            id=identity, claim_token=claim, payload=payload, expires_at=expiry,
            authorization_epoch=0,
        )
        with pytest.raises(PermissionError, match="owner_pairing_required"):
            with jobs.worker_lease(lease):
                async with database.session() as session:
                    await session.execute(insert(AppSetting).values(key="late_private_write", value={}))
        async with database.engine.connect() as connection:
            assert await connection.scalar(select(AppSetting.key)) is None
    finally:
        await database.close()


async def test_runtime_bot_withdrawal_blocks_direct_core_insert(tmp_path):
    database = Database("sqlite+aiosqlite:///" + str(tmp_path / "fenced.sqlite3"))
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    admitted = [True]
    database.management_admission = lambda: admitted[0]
    try:
        with pytest.raises(PermissionError, match="owner_pairing_required"):
            async with database.session() as session:
                admitted[0] = False
                await session.execute(insert(AppSetting).values(key="core_write", value={}))
        async with database.engine.connect() as connection:
            assert await connection.scalar(select(AppSetting.key)) is None
    finally:
        await database.close()


@pytest.mark.parametrize("boundary", ["source", "model"])
async def test_runtime_fence_rechecks_bot_after_actual_authorization_transaction(tmp_path, boundary):
    from contextlib import asynccontextmanager

    from tg_assistant.config import Settings
    from tg_assistant.runtime import Application

    database = Database("sqlite+aiosqlite:///" + str(tmp_path / "fenced.sqlite3"))
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with database.session() as session:
        session.add(TelegramChat(chat_id=100, chat_type="group"))
        await session.flush()
        session.add_all([
            TelegramChatPolicy(chat_id=100, allowed=True, authorization_epoch=2, ai_mode="local_only"),
            TelegramChatPermission(chat_id=100, permission=PermissionName.GROUP_AI_ASK.value, enabled=True),
        ])
    admitted = [True]
    database.management_admission = lambda: admitted[0]
    actual_session = database.session

    @asynccontextmanager
    async def withdrawing_session():
        async with actual_session() as session:
            yield session
        # The source/permission transaction really completed before the owning
        # bot proof was withdrawn; those source grants alone cannot authorize I/O.
        admitted[0] = False

    database.session = withdrawing_session
    application = object.__new__(Application)
    application.database = database
    application.settings = Settings(_env_file=None)
    try:
        with pytest.raises(PermissionError, match="owner_pairing_required"):
            if boundary == "source":
                await application._source_fence(100, PermissionName.GROUP_AI_ASK, 2)
            else:
                await application._model_fence({100: 2}, PermissionName.GROUP_AI_ASK, cloud=False)
    finally:
        await database.close()
