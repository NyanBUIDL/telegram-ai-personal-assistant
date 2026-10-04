from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tg_assistant.db.base import Base


@pytest.fixture(scope="session")
def qt_application():
    from PySide6.QtWidgets import QApplication

    # Qt owns process-wide native font/style objects. Keep one application alive
    # across desktop modules rather than destroying/recreating it between tests.
    application = QApplication.instance() or QApplication([])
    if os.environ.get("ART_PHASE") != "before" and os.environ.get("ART_NATIVE_UNTHEMED") != "1":
        from tg_assistant.desktop.theme import apply_theme
        from tg_assistant.paths import resource_path

        apply_theme(application, resource_path("dashboard-prototype", "public", "fonts"))
    return application


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as value:
        yield value
        await value.rollback()
    await engine.dispose()
