"""Unsupported URLs are rejected before a driver, secret or network is touched."""

from types import SimpleNamespace

import pytest

from tg_assistant.db import base
from tg_assistant.services import jobs
from tg_assistant.services.backup import BackupService


def test_backup_refuses_legacy_backend_before_reading_target():
    def forbidden(**kwargs):
        pytest.fail("An unsupported backup target was read")

    storage = SimpleNamespace(settings=SimpleNamespace(storage_backend="mysql"), _url=forbidden)
    with pytest.raises(ValueError, match="storage_sqlite_required"):
        BackupService(storage)


def test_backup_refuses_external_url_without_exposing_credentials():
    storage = SimpleNamespace(
        settings=SimpleNamespace(storage_backend="sqlite"),
        _url=lambda **kwargs: "mysql+pymysql://owner:synthetic-private@localhost/fixture",
    )
    with pytest.raises(ValueError, match="storage_sqlite_required") as error:
        BackupService(storage)
    assert "synthetic-private" not in str(error.value)


@pytest.mark.parametrize("driver", ["mysql+asyncmy", "postgresql+asyncpg"])
def test_async_database_refuses_external_backend_before_engine(monkeypatch, driver):
    def forbidden(*args, **kwargs):
        pytest.fail("An unsupported database driver was constructed")

    monkeypatch.setattr(base, "create_async_engine", forbidden)
    with pytest.raises(ValueError, match="storage_sqlite_required") as error:
        base.Database(f"{driver}://owner:synthetic-private@localhost/fixture")
    assert "synthetic-private" not in str(error.value)


@pytest.mark.parametrize("driver", ["mysql+asyncmy", "postgresql+asyncpg"])
def test_job_repository_refuses_external_backend_before_engine(monkeypatch, driver):
    def forbidden(*args, **kwargs):
        pytest.fail("An unsupported job database driver was constructed")

    monkeypatch.setattr(jobs, "create_engine", forbidden)
    with pytest.raises(ValueError, match="storage_sqlite_required") as error:
        jobs.JobRepository(
            f"{driver}://owner:synthetic-private@localhost/fixture", profile_id="default"
        )
    assert "synthetic-private" not in str(error.value)


async def test_supported_file_has_actual_wal_and_foreign_keys(tmp_path):
    from sqlalchemy import text

    database = base.Database(f"sqlite+aiosqlite:///{tmp_path / 'database.sqlite3'}")
    try:
        assert await database.ping()
        async with database.engine.connect() as connection:
            assert await connection.scalar(text("PRAGMA journal_mode")) == "wal"
            assert await connection.scalar(text("PRAGMA foreign_keys")) == 1
        repository = jobs.JobRepository(database.engine.url, profile_id="default")
        try:
            with repository.engine.connect() as connection:
                assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
                assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        finally:
            repository.close()
    finally:
        await database.close()
