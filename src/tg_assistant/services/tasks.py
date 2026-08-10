from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Task, TaskHistory, TaskPriority, TaskStatus


class TaskService:
    async def create(
        self,
        session: AsyncSession,
        title: str,
        *,
        due_at: datetime | None = None,
        priority: TaskPriority = TaskPriority.MEDIUM,
        source: str = "manual",
        ai_confirmed: bool = False,
    ) -> Task:
        if source == "ai" and not ai_confirmed:
            raise PermissionError("Task do AI phát hiện phải được xác nhận")
        task = Task(
            title=title.strip(),
            due_at=due_at.astimezone(UTC) if due_at else None,
            priority=priority.value,
            source=source,
            ai_confirmed=ai_confirmed,
        )
        session.add(task)
        await session.flush()
        session.add(
            TaskHistory(
                task_id=task.id,
                changed_at=datetime.now(UTC),
                changed_by=source,
                new_values={"status": task.status, "title": task.title},
            )
        )
        return task

    async def update_status(
        self, session: AsyncSession, task_id: str, status: TaskStatus, *, changed_by: str = "owner"
    ) -> Task:
        task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
        if not task:
            raise LookupError("Không tìm thấy task")
        old = task.status
        if old in {TaskStatus.DONE.value, TaskStatus.CANCELLED.value} and status not in {
            TaskStatus.TODO,
            TaskStatus.CANCELLED,
        }:
            raise ValueError("Chuyển trạng thái không hợp lệ")
        task.status = status.value
        task.completed_at = datetime.now(UTC) if status == TaskStatus.DONE else None
        session.add(
            TaskHistory(
                task_id=task.id,
                changed_at=datetime.now(UTC),
                changed_by=changed_by,
                old_values={"status": old},
                new_values={"status": status.value},
            )
        )
        return task

    async def update(
        self,
        session: AsyncSession,
        task_id: str,
        *,
        title: str | None = None,
        due_at: datetime | None = None,
        priority: TaskPriority | None = None,
        changed_by: str = "owner",
    ) -> Task:
        task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
        if not task:
            raise LookupError("Không tìm thấy task")
        old_values = {
            "title": task.title,
            "due_at": task.due_at.isoformat() if task.due_at else None,
            "priority": task.priority,
        }
        if title is not None:
            if not title.strip():
                raise ValueError("Tiêu đề task không được rỗng")
            task.title = title.strip()
        if due_at is not None:
            task.due_at = due_at.astimezone(UTC)
        if priority is not None:
            task.priority = priority.value
        session.add(
            TaskHistory(
                task_id=task.id,
                changed_at=datetime.now(UTC),
                changed_by=changed_by,
                old_values=old_values,
                new_values={
                    "title": task.title,
                    "due_at": task.due_at.isoformat() if task.due_at else None,
                    "priority": task.priority,
                },
            )
        )
        return task

    async def list(self, session: AsyncSession, status: TaskStatus | None = None) -> list[Task]:
        query = select(Task).order_by(Task.due_at.is_(None), Task.due_at, Task.created_at.desc())
        if status:
            query = query.where(Task.status == status.value)
        return list((await session.scalars(query)).all())
