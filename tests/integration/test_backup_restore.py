"""Disposable backup drills; no user profile, secrets or Telegram account."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import traceback
from contextlib import closing
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from zipfile import ZIP_STORED, ZipFile

import pytest
import sqlalchemy as sa

from tg_assistant.config import Settings
from tg_assistant.contracts import MaintenanceLease, PublicProfile
from tg_assistant.db.models import (
    AiBudgetLock,
    AiBudgetReservation,
    AiUsage,
    AppSetting,
    BackgroundJob,
    PendingAction,
    TelegramAccount,
    TelegramChat,
    TelegramChatPermission,
    TelegramChatPolicy,
    TelegramMessage,
)
from tg_assistant.services.storage import StorageService


@pytest.fixture
def service(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("TG_ASSISTANT_"):
            monkeypatch.delenv(key)
    settings = Settings(_env_file=None, data_dir=tmp_path / "profile", storage_backend="sqlite")
    service = StorageService(settings)
    service.open(
        PublicProfile(
            profile_id="default",
            owner_id=None,
            storage_backend="sqlite",
            setup_stage="welcome",
            version=1,
        )
    )
    service.migrate()
    engine = sa.create_engine(service._url(async_driver=False))
    with engine.begin() as connection:
        connection.execute(
            sa.insert(TelegramChat).values(chat_id=-123, title="drill", chat_type="group")
        )
        connection.execute(
            sa.insert(TelegramMessage).values(
                chat_id=-123,
                message_id=1,
                text="first",
                sent_at=datetime.now(UTC),
                vector_status="indexed",
            )
        )
    engine.dispose()
    yield service
    service.fence.close()


def read_text(service):
    with (
        closing(
            sqlite3.connect(service.settings.data_dir / "db" / "assistant.sqlite3")
        ) as connection,
        connection,
    ):
        return connection.execute(
            "SELECT text FROM telegram_messages WHERE message_id=1"
        ).fetchone()[0]


def test_live_sqlite_backup_consistent(service, tmp_path):
    stop, started = Event(), Event()
    path = service.settings.data_dir / "db" / "assistant.sqlite3"

    def write():
        with closing(sqlite3.connect(path)) as connection, connection:
            while not stop.is_set():
                connection.execute("UPDATE telegram_messages SET text='changed' WHERE message_id=1")
                connection.commit()
                started.set()

    thread = Thread(target=write)
    thread.start()
    started.wait(5)
    try:
        archive = tmp_path / "backup.zip"
        manifest = service.backup(archive)
    finally:
        stop.set()
        thread.join(5)
    with ZipFile(archive) as saved:
        assert set(saved.namelist()) == {"manifest.json", "database.sqlite3"}
        payload = saved.read("database.sqlite3")
        assert manifest.checksums == {"database.sqlite3": hashlib.sha256(payload).hexdigest()}
        target = tmp_path / "snapshot.sqlite3"
        target.write_bytes(payload)
    with closing(sqlite3.connect(target)) as connection, connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == manifest.schema_revision
        )
    assert manifest.backend == "sqlite" and manifest.profile_id == "default"


def test_corrupt_checksum_no_mutation(service, tmp_path):
    archive = tmp_path / "bad.zip"
    service.backup(archive)
    with ZipFile(archive) as saved:
        manifest = saved.read("manifest.json")
        payload = saved.read("database.sqlite3")
    with ZipFile(archive, "w", ZIP_STORED) as saved:
        saved.writestr("manifest.json", manifest)
        saved.writestr("database.sqlite3", payload + b"corrupt")
    lease = service.fence.acquire("restore")
    with pytest.raises(RuntimeError, match="checksum"):
        service.restore(archive, lease)
    assert read_text(service) == "first"
    service.fence.release(lease)


def test_vector_absent_not_marked_ready(service, tmp_path):
    archive = tmp_path / "saved.zip"
    service.backup(archive)
    with (
        closing(
            sqlite3.connect(service.settings.data_dir / "db" / "assistant.sqlite3")
        ) as connection,
        connection,
    ):
        connection.execute("UPDATE telegram_messages SET text='newer'")
    lease = service.fence.acquire("restore")
    report = service.restore(archive, lease)
    assert read_text(service) == "first"
    assert report.vector_state == "degraded" and report.recovery_required
    assert report.prebackup_path.is_file()
    with (
        closing(
            sqlite3.connect(service.settings.data_dir / "db" / "assistant.sqlite3")
        ) as connection,
        connection,
    ):
        assert (
            connection.execute("SELECT vector_status FROM telegram_messages").fetchone()[0]
            != "indexed"
        )
    service.fence.release(lease)


@pytest.mark.parametrize("kind", ["fake", "expired", "foreign"])
def test_restore_requires_owned_unexpired_lease(service, tmp_path, kind):
    archive = tmp_path / "saved.zip"
    service.backup(archive)
    if kind == "fake":
        lease = MaintenanceLease(
            profile_id="default",
            generation=1,
            holder="restore",
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )
    else:
        lease = service.fence.acquire("restore", lease_seconds=-1 if kind == "expired" else 300)
        if kind == "foreign":
            lease = lease.model_copy(update={"profile_id": "foreign"})
    with pytest.raises(RuntimeError, match="maintenance"):
        service.restore(archive, lease)
    assert read_text(service) == "first"


def test_restore_failure_keeps_original(service, tmp_path, monkeypatch):
    archive = tmp_path / "saved.zip"
    service.backup(archive)
    with (
        closing(
            sqlite3.connect(service.settings.data_dir / "db" / "assistant.sqlite3")
        ) as connection,
        connection,
    ):
        connection.execute("UPDATE telegram_messages SET text='original'")
    lease = service.fence.acquire("restore")
    from tg_assistant.services import backup

    def fail(*args, **kwargs):
        raise RuntimeError("migration_drill_failure")

    monkeypatch.setattr(backup, "upgrade_database", fail)
    with pytest.raises(RuntimeError, match="restore_failed") as error:
        service.restore(archive, lease)
    assert error.value.original_preserved
    assert read_text(service) == "original"
    service.fence.release(lease)


def test_portable_backup_excludes_credentials_sessions(service, tmp_path):
    (service.settings.data_dir / "sessions" / "raw.session").write_bytes(b"RAW-SESSION-SECRET")
    with (
        closing(
            sqlite3.connect(service.settings.data_dir / "db" / "assistant.sqlite3")
        ) as connection,
        connection,
    ):
        connection.execute(
            "INSERT INTO app_settings (key,value) VALUES ('api_key', '\"CREDENTIAL-SECRET\"')"
        )
    archive = tmp_path / "saved.zip"
    service.backup(archive)
    with ZipFile(archive) as saved:
        assert b"RAW-SESSION-SECRET" not in b"".join(saved.read(name) for name in saved.namelist())
        assert b"CREDENTIAL-SECRET" not in saved.read("database.sqlite3")


def test_wrong_profile_manifest_no_mutation(service, tmp_path):
    archive = tmp_path / "wrong.zip"
    service.backup(archive)
    with ZipFile(archive) as saved:
        manifest = json.loads(saved.read("manifest.json"))
        payload = saved.read("database.sqlite3")
    manifest["profile_id"] = "foreign"
    with ZipFile(archive, "w") as saved:
        saved.writestr("manifest.json", json.dumps(manifest))
        saved.writestr("database.sqlite3", payload)
    lease = service.fence.acquire("restore")
    with pytest.raises(RuntimeError, match="profile"):
        service.restore(archive, lease)
    assert read_text(service) == "first"
    service.fence.release(lease)


def _security_restore_drill(instance, tmp_path):
    engine = sa.create_engine(instance._url(async_driver=False))
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            sa.insert(TelegramChatPolicy).values(chat_id=-123, allowed=True, authorization_epoch=3)
        )
        connection.execute(
            sa.insert(TelegramChatPermission).values(
                chat_id=-123, permission="search_messages", enabled=True
            )
        )
        connection.execute(
            sa.insert(TelegramAccount).values(telegram_user_id=111, is_owner_paired=True)
        )
        connection.execute(
            sa.insert(BackgroundJob).values(
                id="stale-job",
                job_type="index",
                status="running",
                claim_token="old-lease",  # noqa: S106 - synthetic lease
            )
        )
        connection.execute(
            sa.insert(BackgroundJob).values(
                id="uncertain-job",
                job_type="send",
                status="running",
                payload={"external_effect_started": True},
                claim_token="old-effect",  # noqa: S106 - synthetic lease
            )
        )
        connection.execute(
            sa.insert(PendingAction).values(
                action_id="stale-action",
                action_type="delete",
                requested_by=111,
                payload={},
                status="pending",
                expires_at=now + timedelta(hours=1),
            )
        )
        connection.execute(sa.insert(AiBudgetLock).values(profile_id="default"))
        connection.execute(
            sa.insert(AiBudgetReservation).values(
                request_id="old-reservation",
                profile_id="default",
                occurred_at=now,
                provider="cloud",
                model="test",
                pricing_version="fixture",
                pricing_rates={},
                operation="chat",
                feature="ask",
                reserved_input_tokens=10,
                reserved_output_tokens=10,
                reserved_cost_usd=1,
                state="reserved",
            )
        )
        connection.execute(
            sa.insert(AiUsage).values(
                occurred_at=now,
                model="test",
                operation="chat",
                estimated_cost_usd=3,
                reservation_id="old-reservation",
            )
        )
    archive = tmp_path / "security.zip"
    instance.backup(archive)
    with engine.begin() as connection:
        connection.execute(
            sa.update(TelegramChatPolicy).values(allowed=False, authorization_epoch=8)
        )
        connection.execute(sa.update(TelegramChatPermission).values(enabled=False))
        connection.execute(
            sa.update(TelegramAccount).values(telegram_user_id=222, is_owner_paired=False)
        )
        connection.execute(sa.insert(AppSetting).values(key="cloud_consent", value=False))
        connection.execute(
            sa.insert(AiUsage).values(
                occurred_at=now, model="test", operation="chat", estimated_cost_usd=7
            )
        )
        connection.execute(
            sa.insert(AiBudgetReservation).values(
                request_id="new-reservation",
                profile_id="default",
                occurred_at=now,
                provider="cloud",
                model="test",
                pricing_version="fixture",
                pricing_rates={},
                operation="chat",
                feature="ask",
                reserved_input_tokens=10,
                reserved_output_tokens=10,
                reserved_cost_usd=2,
                state="submitted",
            )
        )
    engine.dispose()
    lease = instance.fence.acquire("restore", lease_seconds=1800)
    report = instance.restore(archive, lease)
    assert report.recovery_required and report.prebackup_path.is_file()
    with engine.connect() as connection:
        assert connection.scalar(sa.select(TelegramChatPolicy.allowed)) is False
        assert connection.scalar(sa.select(TelegramChatPolicy.authorization_epoch)) > 8
        assert connection.scalar(sa.select(TelegramChatPermission.enabled)) is False
        assert connection.scalar(sa.select(TelegramAccount.telegram_user_id)) == 222
        assert (
            connection.scalar(sa.select(AppSetting.value).where(AppSetting.key == "cloud_consent"))
            is False
        )
        assert connection.scalar(sa.select(sa.func.sum(AiUsage.estimated_cost_usd))) == 10
        assert connection.scalar(sa.select(sa.func.count()).select_from(AiBudgetReservation)) == 2
        assert (
            connection.scalar(
                sa.select(BackgroundJob.status).where(BackgroundJob.id == "stale-job")
            )
            == "cancelled"
        )
        assert (
            connection.scalar(
                sa.select(BackgroundJob.status).where(BackgroundJob.id == "uncertain-job")
            )
            == "uncertain"
        )
        assert (
            connection.scalar(
                sa.select(BackgroundJob.claim_token).where(BackgroundJob.id == "stale-job")
            )
            is None
        )
        assert connection.scalar(sa.select(PendingAction.status)) == "expired"
        assert connection.scalar(sa.select(TelegramMessage.vector_status)) != "indexed"
    engine.dispose()
    instance.fence.release(lease)


def test_sqlite_restore_preserves_revocation_budget_consent_identity(service, tmp_path):
    _security_restore_drill(service, tmp_path)


def test_restore_retains_live_uncertain_work(service, tmp_path):
    archive = tmp_path / "saved.zip"
    service.backup(archive)
    engine = sa.create_engine(service._url(async_driver=False))
    with engine.begin() as connection:
        connection.execute(
            sa.insert(BackgroundJob).values(
                id="live-uncertain",
                job_type="send",
                status="uncertain",
                payload={"external_effect_started": True},
            )
        )
        connection.execute(
            sa.insert(PendingAction).values(
                action_id="live-action",
                action_type="send",
                requested_by=111,
                payload={},
                status="uncertain",
                expires_at=datetime.now(UTC),
            )
        )
    engine.dispose()
    lease = service.fence.acquire("restore")
    service.restore(archive, lease)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                sa.select(BackgroundJob.status).where(BackgroundJob.id == "live-uncertain")
            )
            == "uncertain"
        )
        assert (
            connection.scalar(
                sa.select(PendingAction.status).where(PendingAction.action_id == "live-action")
            )
            == "uncertain"
        )
    engine.dispose()
    service.fence.release(lease)


@pytest.mark.parametrize("backend", ["sqlite"])
def test_post_swap_failure_rolls_back_original(backend, request, tmp_path, monkeypatch):
    instance = request.getfixturevalue("service" if backend == "sqlite" else "mysql_service")
    archive = tmp_path / "saved.zip"
    instance.backup(archive)
    engine = sa.create_engine(instance._url(async_driver=False))
    with engine.begin() as connection:
        connection.execute(sa.update(TelegramMessage).values(text="original-before-swap"))
    engine.dispose()
    from tg_assistant.services.backup import BackupError, BackupService

    original_validator = BackupService._validate_db

    def fail_after_swap(self, connection, revision=None):
        if revision and connection.engine.url.database == self.url.database:
            text = connection.scalar(sa.select(TelegramMessage.text))
            if text == "first":
                raise RuntimeError("post_swap_drill_failure")
        return original_validator(self, connection, revision)

    monkeypatch.setattr(BackupService, "_validate_db", fail_after_swap)
    lease = instance.fence.acquire("restore", lease_seconds=1800)
    with pytest.raises(BackupError, match="restore_failed") as error:
        instance.restore(archive, lease)
    assert error.value.original_preserved
    with engine.connect() as connection:
        assert connection.scalar(sa.select(TelegramMessage.text)) == "original-before-swap"
    engine.dispose()
    instance.fence.release(lease)


def test_restore_disables_restored_only_sources(service, tmp_path):
    engine = sa.create_engine(service._url(async_driver=False))
    with engine.begin() as connection:
        connection.execute(
            sa.insert(TelegramChatPolicy).values(chat_id=-123, allowed=True, authorization_epoch=3)
        )
        connection.execute(
            sa.insert(TelegramChatPermission).values(
                chat_id=-123, permission="search_messages", enabled=True
            )
        )
    archive = tmp_path / "saved.zip"
    service.backup(archive)
    with engine.begin() as connection:
        connection.execute(sa.delete(TelegramChatPolicy))
        connection.execute(sa.delete(TelegramChatPermission))
    engine.dispose()
    lease = service.fence.acquire("restore")
    service.restore(archive, lease)
    with engine.connect() as connection:
        assert connection.scalar(sa.select(TelegramChatPolicy.allowed)) is False
        assert connection.scalar(sa.select(TelegramChatPolicy.authorization_epoch)) > 3
        assert (
            connection.scalar(sa.select(sa.func.count()).select_from(TelegramChatPermission)) == 0
        )
    engine.dispose()
    service.fence.release(lease)


@pytest.mark.parametrize("mutation", ["duplicate", "path", "unknown_revision", "wrong_backend"])
def test_archive_boundary_rejects_without_mutating(service, tmp_path, mutation):
    from tg_assistant.services.backup import BackupError

    archive = tmp_path / "invalid.zip"
    service.backup(archive)
    with ZipFile(archive) as saved:
        manifest = json.loads(saved.read("manifest.json"))
        payload = saved.read("database.sqlite3")
    if mutation == "unknown_revision":
        manifest["schema_revision"] = "not_a_real_revision"
    if mutation == "wrong_backend":
        manifest["backend"] = "mysql"
    with ZipFile(archive, "w") as saved:
        saved.writestr("manifest.json", json.dumps(manifest))
        saved.writestr("database.sqlite3", payload)
        if mutation == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate name"):
                saved.writestr("database.sqlite3", payload)
        if mutation == "path":
            saved.writestr("../escape", b"must not extract")
    lease = service.fence.acquire("restore")
    with pytest.raises(BackupError) as error:
        service.restore(archive, lease)
    assert error.value.original_preserved
    assert read_text(service) == "first"
    assert not (tmp_path / "escape").exists()
    service.fence.release(lease)


def test_fence_blocks_new_operations_and_does_not_stop_at_dto(service, tmp_path):
    from tg_assistant.services.maintenance import MaintenanceBusy, MaintenanceService

    archive = tmp_path / "saved.zip"
    service.backup(archive)
    independent = MaintenanceService(service.settings.data_dir / "config", profile_id="default")
    with independent.operation():
        with pytest.raises(MaintenanceBusy):
            service.fence.acquire("restore", timeout=0)
    # Failed draining must be recovered, never silently reopened.
    service.fence.recover()
    lease = service.fence.acquire("restore")
    with pytest.raises(MaintenanceBusy):
        with independent.operation():
            pytest.fail("Maintenance admitted a new writer")
    service.restore(archive, lease)
    with pytest.raises(MaintenanceBusy):
        with independent.operation():
            pytest.fail("Restore must leave lease release to its owner")
    service.fence.release(lease)
    with independent.operation():
        assert read_text(service) == "first"


def test_restore_failure_code_never_echoes_dependency_input(service, tmp_path, monkeypatch):
    from tg_assistant.services import backup

    archive = tmp_path / "saved.zip"
    service.backup(archive)

    def dependency_failure(*args, **kwargs):
        raise RuntimeError("provider rejected secret-input-marker")

    monkeypatch.setattr(backup, "upgrade_database", dependency_failure)
    lease = service.fence.acquire("restore")
    with pytest.raises(backup.BackupError) as error:
        service.restore(archive, lease)
    assert error.value.code == "restore_failed"
    assert error.value.original_preserved
    assert "secret-input-marker" not in str(error.value)
    assert "secret-input-marker" not in "".join(traceback.format_exception(error.value))
    service.fence.release(lease)


def test_historical_revision_migrates_only_in_stage(service, tmp_path):
    from alembic.config import Config

    from alembic import command
    from tg_assistant.paths import resource_path

    old_path = tmp_path / "old.sqlite3"
    engine = sa.create_engine(sa.URL.create("sqlite", database=str(old_path)))
    config = Config()
    config.set_main_option("script_location", str(resource_path("alembic")))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0005")
        chat = sa.Table("telegram_chats", sa.MetaData(), autoload_with=connection)
        connection.execute(
            chat.insert().values(chat_id=-321, title="historical", chat_type="group")
        )
        connection.commit()
    engine.dispose()
    payload = old_path.read_bytes()
    manifest = {
        "format_version": 1,
        "schema_revision": "0005",
        "backend": "sqlite",
        "profile_id": "default",
        "checksums": {"database.sqlite3": hashlib.sha256(payload).hexdigest()},
        "vector_state": "excluded",
    }
    archive = tmp_path / "historical.zip"
    with ZipFile(archive, "w") as saved:
        saved.writestr("manifest.json", json.dumps(manifest))
        saved.writestr("database.sqlite3", payload)
    lease = service.fence.acquire("restore")
    report = service.restore(archive, lease)
    assert report.schema_revision != "0005"
    assert report.recovery_required
    with closing(sqlite3.connect(old_path)) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0005"
    with closing(
        sqlite3.connect(service.settings.data_dir / "db" / "assistant.sqlite3")
    ) as connection:
        assert (
            connection.execute("SELECT title FROM telegram_chats WHERE chat_id=-321").fetchone()[0]
            == "historical"
        )
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == report.schema_revision
        )
    service.fence.release(lease)


@pytest.mark.parametrize("backend", ["sqlite"])
def test_portable_backup_excludes_live_job_claim_authority(backend, request, tmp_path):
    service = request.getfixturevalue("service" if backend == "sqlite" else "mysql_service")
    engine = sa.create_engine(service._url(async_driver=False))
    live_claim = "LIVE-CLAIM-SECRET"
    with engine.begin() as connection:
        connection.execute(
            sa.insert(BackgroundJob).values(
                id="claimed",
                job_type="index",
                status="running",
                claim_token=live_claim,
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
    archive = tmp_path / "claims.zip"
    service.backup(archive)
    with ZipFile(archive) as saved:
        assert live_claim.encode() not in saved.read(
            "database.sqlite3" if backend == "sqlite" else "database.json"
        )
    with engine.connect() as connection:
        assert (
            connection.scalar(
                sa.select(BackgroundJob.claim_token).where(BackgroundJob.id == "claimed")
            )
            == live_claim
        )
    engine.dispose()
