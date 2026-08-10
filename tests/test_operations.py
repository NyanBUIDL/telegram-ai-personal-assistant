from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from tg_assistant.db.models import BackgroundJob, TelegramChat, TelegramChatPolicy, TelegramMessage
from tg_assistant.runtime import pause_learning_jobs, resume_learning_jobs
from tg_assistant.services.operations import cleanup_storage


class FakeVectors:
    def __init__(self, ids: list[int]) -> None:
        self.ids = ids
        self.deleted: list[int] = []

    def reference_ids(self, *, chat_id=None) -> list[int]:
        return list(self.ids)

    def delete_reference_ids(self, ids: list[int]) -> int:
        self.deleted.extend(ids)
        return len(ids)


@pytest.mark.asyncio
async def test_pause_and_resume_learning_queue(session) -> None:
    queued = BackgroundJob(job_type="learn_group", status="queued")
    running = BackgroundJob(job_type="learn_group", status="running")
    session.add_all([queued, running])
    await session.flush()

    assert await pause_learning_jobs(session) == 2
    assert queued.status == "paused"
    assert running.status == "pause_requested"

    running.status = "paused"
    assert await resume_learning_jobs(session) == 2
    assert queued.status == "queued"
    assert running.status == "queued"


@pytest.mark.asyncio
async def test_cleanup_removes_expired_messages_and_orphan_vectors(session, tmp_path) -> None:
    chat_id = -1001
    session.add(TelegramChat(chat_id=chat_id, title="Source", chat_type="group"))
    session.add(
        TelegramChatPolicy(
            chat_id=chat_id,
            allowed=True,
            retention_days=7,
        )
    )
    old = TelegramMessage(
        chat_id=chat_id,
        message_id=1,
        text="old",
        sent_at=datetime.now(UTC) - timedelta(days=8),
    )
    recent = TelegramMessage(
        chat_id=chat_id,
        message_id=2,
        text="recent",
        sent_at=datetime.now(UTC),
    )
    session.add_all([old, recent])
    await session.flush()
    vectors = FakeVectors([old.id, recent.id, 999_999])

    preview = await cleanup_storage(
        session,
        vectors,
        media_root=tmp_path,
        dry_run=True,
    )
    assert preview.expired_messages == 1
    assert preview.orphan_vectors == 1
    assert not vectors.deleted

    report = await cleanup_storage(
        session,
        vectors,
        media_root=tmp_path,
        dry_run=False,
    )
    assert report.expired_messages == 1
    assert old.id in vectors.deleted
    assert 999_999 in vectors.deleted
    remaining = list((await session.scalars(select(TelegramMessage))).all())
    assert [row.id for row in remaining] == [recent.id]
