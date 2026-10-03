from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext
from datetime import datetime

from sqlalchemy import DateTime, MetaData, event, func
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Database:
    def __init__(self, url: str, *, echo: bool = False, pool_size: int = 5) -> None:
        kwargs: dict = {"echo": echo, "pool_pre_ping": True}
        if not url.startswith("sqlite"):
            kwargs.update(
                pool_size=pool_size,
                max_overflow=5,
                pool_recycle=1800,
                connect_args={"init_command": "SET time_zone = '+00:00'"},
            )
        self.engine: AsyncEngine = create_async_engine(url, **kwargs)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine.sync_engine, "connect", configure_sqlite)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.fence = None

    def operation(self):
        return self.fence.operation() if self.fence else nullcontext()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        with self.operation():
            async with self.sessions() as session:
                from ..services.jobs import (
                    active_job_lease,
                    fence_runtime_writes,
                    track_runtime_statement,
                )

                lease = active_job_lease.get()
                if lease:
                    event.listen(
                        session.sync_session,
                        "before_flush",
                        lambda sync, *_: fence_runtime_writes(sync, lease, phase="flush"),
                    )
                    event.listen(
                        session.sync_session,
                        "before_commit",
                        lambda sync: fence_runtime_writes(sync, lease),
                    )
                    event.listen(
                        session.sync_session,
                        "do_orm_execute",
                        lambda state: track_runtime_statement(state, lease),
                    )

                    def clear_guard(sync):
                        sync.info.pop("job_lease_fenced", None)
                        sync.info.pop("job_core_write", None)

                    event.listen(session.sync_session, "after_commit", clear_guard)
                    event.listen(session.sync_session, "after_rollback", clear_guard)
                try:
                    yield session
                    await session.commit()
                except BaseException:
                    await session.rollback()
                    raise

    async def ping(self) -> bool:
        from sqlalchemy import text

        with self.operation():
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        return True

    async def close(self) -> None:
        await self.engine.dispose()


def configure_sqlite(connection, _record, *, busy_timeout: int = 5000) -> None:
    """Enforce each SQLite connection's concurrency and referential settings."""
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={busy_timeout}")
        # SQLite journal-mode changes can return BUSY immediately even with a
        # busy handler. Two first connections must not fail a startup race.
        deadline = time.monotonic() + min(busy_timeout / 1000, 0.5)
        while True:
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                break
            except Exception as exc:
                if (
                    getattr(exc, "sqlite_errorcode", None) not in {5, 6}
                    or time.monotonic() >= deadline
                ):
                    raise
                time.sleep(0.01)
    finally:
        cursor.close()
