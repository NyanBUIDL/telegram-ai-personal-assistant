from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tg_assistant.db.models import TaskStatus
from tg_assistant.services.tasks import TaskService
from tg_assistant.services.timeparse import parse_vietnamese_datetime


def test_vietnamese_time_parser() -> None:
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    now = datetime(2026, 7, 23, 10, 30, tzinfo=tz)  # Thursday
    assert parse_vietnamese_datetime("chiều mai lúc 3 giờ", now=now) == datetime(
        2026, 7, 24, 15, 0, tzinfo=tz
    )
    assert parse_vietnamese_datetime("sáng thứ Hai", now=now) == datetime(
        2026, 7, 27, 8, 0, tzinfo=tz
    )
    assert parse_vietnamese_datetime("cuối tháng", now=now).date().isoformat() == "2026-07-31"


@pytest.mark.asyncio
async def test_task_lifecycle(session) -> None:
    service = TaskService()
    task = await service.create(session, "Hoàn thiện báo cáo")
    assert task.status == TaskStatus.INBOX.value
    done = await service.update_status(session, task.id, TaskStatus.DONE)
    assert done.completed_at is not None
    reopened = await service.update_status(session, task.id, TaskStatus.TODO)
    assert reopened.completed_at is None


@pytest.mark.asyncio
async def test_ai_task_requires_confirmation(session) -> None:
    with pytest.raises(PermissionError):
        await TaskService().create(session, "AI suggestion", source="ai")
