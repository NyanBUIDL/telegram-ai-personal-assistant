"""Advanced CLI uses the same portable, fenced storage service as desktop."""

from __future__ import annotations

import asyncio
import json
from zipfile import ZipFile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from tg_assistant import cli
from tg_assistant.config import Settings
from tg_assistant.contracts import PublicProfile
from tg_assistant.db.models import Task
from tg_assistant.services.storage import StorageService


@pytest.fixture
def backup_cli(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path / "profile", profile_id="backup_cli")

    class NoSecrets:
        def get(self, name):
            pytest.fail("SQLite backup read a credential")

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "SecretStore", NoSecrets)
    storage = StorageService(settings, NoSecrets())
    database = storage.open(
        PublicProfile(
            profile_id=settings.profile_id,
            owner_id=None,
            storage_backend="sqlite",
            setup_stage="storage_ready",
            version=1,
        )
    )
    asyncio.run(database.close())
    storage.migrate()
    engine = create_engine(settings.database_url("", async_driver=False))
    with Session(engine) as session, session.begin():
        session.add(Task(id="keep-task", title="Original task"))
    yield settings, storage, engine, CliRunner()
    storage.fence.close()
    engine.dispose()


def test_sqlite_cli_backup_uses_portable_manifest_without_mysql_tools(
    backup_cli, monkeypatch, tmp_path
):
    settings, _, _, runner = backup_cli

    def forbidden(*args, **kwargs):
        pytest.fail("Portable backup reached a SQL command subprocess")

    monkeypatch.setattr(cli.subprocess, "run", forbidden)
    output = tmp_path / "portable.zip"
    result = runner.invoke(cli.app, ["backup", "--output", str(output)])
    assert result.exit_code == 0, result.output
    with ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["backend"] == "sqlite"
        assert manifest["profile_id"] == settings.profile_id
        assert manifest["schema_revision"] != "0001"
        assert "database.sql" not in archive.namelist()


def test_cli_backup_refuses_overwrite_and_sanitizes_errors(backup_cli, tmp_path):
    _, _, _, runner = backup_cli
    output = tmp_path / "existing.zip"
    output.write_bytes(b"original artifact")
    result = runner.invoke(cli.app, ["backup", "--output", str(output)])
    assert result.exit_code != 0
    assert output.read_bytes() == b"original artifact"
    assert "backup_failed" in result.output
    assert str(output) not in result.output


def test_cli_restore_cancel_does_not_enter_maintenance(backup_cli, tmp_path):
    _, storage, engine, runner = backup_cli
    archive = tmp_path / "portable.zip"
    storage.backup(archive)
    before = storage.fence.state.read_bytes()
    result = runner.invoke(cli.app, ["restore", str(archive)], input="n\n")
    assert result.exit_code != 0
    assert storage.fence.state.read_bytes() == before
    with Session(engine) as session:
        assert session.get(Task, "keep-task").title == "Original task"


def test_cli_restore_refuses_active_writer_without_mutation(backup_cli, tmp_path):
    _, storage, engine, runner = backup_cli
    archive = tmp_path / "portable.zip"
    storage.backup(archive)
    with storage.fence.operation():
        result = runner.invoke(cli.app, ["restore", str(archive)], input="y\n")
    assert result.exit_code != 0
    assert "maintenance_in_progress" in result.output
    with Session(engine) as session:
        assert session.get(Task, "keep-task").title == "Original task"


def test_cli_restore_reports_recovery_and_prebackup(backup_cli, tmp_path):
    settings, storage, engine, runner = backup_cli
    archive = tmp_path / "portable.zip"
    storage.backup(archive)
    with Session(engine) as session, session.begin():
        session.get(Task, "keep-task").title = "Changed task"
    engine.dispose()
    result = runner.invoke(cli.app, ["restore", str(archive)], input="y\n")
    assert result.exit_code == 0, result.output
    assert "cần khôi phục chỉ mục" in result.output
    assert list((settings.data_dir / "backups").glob("before-restore-*.zip"))
    with Session(engine) as session:
        assert session.get(Task, "keep-task").title == "Original task"
