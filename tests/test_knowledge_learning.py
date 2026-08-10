from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tg_assistant.db.models import BackgroundJob, TelegramChat, TelegramMessage
from tg_assistant.runtime import (
    embedding_batches,
    knowledge_rows_query,
    learning_backlog_size,
    recover_interrupted_learning_jobs,
    requeue_interrupted_learning_job,
)


def _message(row_id: int, text: str) -> TelegramMessage:
    return TelegramMessage(
        id=row_id,
        chat_id=-1001,
        message_id=row_id,
        text=text,
        sent_at=datetime.now(UTC),
    )


def test_embedding_batches_are_bounded_and_skip_secrets() -> None:
    safe_one = _message(1, "a" * 24)
    secret = _message(2, "token=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
    safe_two = _message(3, "b" * 24)
    oversized = _message(4, "c" * 100)

    batches = embedding_batches(
        [safe_one, secret, safe_two, oversized],
        max_input_tokens=10,
        max_items=100,
    )

    assert [[row.id for row in batch] for batch in batches] == [[1], [3]]
    assert all(sum(max(1, len(row.text or "") // 4) for row in batch) <= 10 for batch in batches)


def test_embedding_batches_obey_item_limit() -> None:
    rows = [_message(index, f"tin {index}") for index in range(1, 6)]

    batches = embedding_batches(rows, max_input_tokens=100, max_items=2)

    assert [len(batch) for batch in batches] == [2, 2, 1]


def test_interrupted_learning_job_returns_to_queue() -> None:
    locked_at = datetime.now(UTC)
    job = BackgroundJob(
        job_type="learn_group",
        status="running",
        attempts=2,
        max_attempts=3,
        locked_by="worker",
        locked_at=locked_at,
        last_error="old",
    )

    requeue_interrupted_learning_job(job, now=locked_at)

    assert job.status == "queued"
    assert job.attempts == 1
    assert job.run_after == locked_at
    assert job.locked_by is None
    assert job.locked_at is None
    assert job.last_error is None


@pytest.mark.asyncio
async def test_learning_backlog_counts_only_queued_and_running(session) -> None:
    session.add_all(
        [
            BackgroundJob(job_type="learn_group", status="queued"),
            BackgroundJob(job_type="learn_group", status="running"),
            BackgroundJob(job_type="learn_group", status="completed"),
            BackgroundJob(job_type="other", status="queued"),
        ]
    )
    await session.flush()

    assert await learning_backlog_size(session) == 2


@pytest.mark.asyncio
async def test_startup_recovers_interrupted_learning_jobs(session) -> None:
    session.add_all(
        [
            BackgroundJob(
                job_type="learn_group",
                status="running",
                attempts=1,
                locked_by="old-worker",
                locked_at=datetime.now(UTC),
            ),
            BackgroundJob(job_type="learn_group", status="completed"),
        ]
    )
    await session.flush()

    assert await recover_interrupted_learning_jobs(session) == 1
    assert await learning_backlog_size(session) == 1


@pytest.mark.asyncio
async def test_continued_learning_selects_only_rows_after_checkpoint(session) -> None:
    chat_id = -1001
    session.add(TelegramChat(chat_id=chat_id, title="Kho chung", chat_type="group"))
    await session.flush()
    now = datetime.now(UTC)
    session.add_all(
        [
            TelegramMessage(
                id=row_id,
                chat_id=chat_id,
                message_id=row_id,
                text=f"tin {row_id}",
                sent_at=now,
            )
            for row_id in range(1, 6)
        ]
    )
    await session.flush()

    rows = list(
        (await session.scalars(knowledge_rows_query(chat_id, limit=1000, checkpoint_id=3))).all()
    )

    assert [row.id for row in rows] == [4, 5]
