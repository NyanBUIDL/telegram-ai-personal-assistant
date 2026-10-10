"""Portable database snapshots and fenced, staged restore.

Archives contain no configuration, credential store, raw Telegram sessions or
vector files. Derived vectors require owner-approved recovery after every restore.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

import sqlalchemy as sa
from alembic.script import ScriptDirectory

from ..contracts import BackupManifest, MaintenanceLease
from ..db.migrations import actual_revision, classify_schema, upgrade_database
from ..paths import resource_path


@dataclass(frozen=True)
class RestoreReport:
    code: str
    backend: str
    profile_id: str
    schema_revision: str
    prebackup_path: Path
    vector_state: str = "degraded"
    recovery_required: bool = True
    next_action: str = "Đối soát vector, xem trước và xác nhận khôi phục chỉ mục từng nguồn."


class BackupError(RuntimeError):
    """Sanitized internal failure with proof of original database preservation."""

    def __init__(self, code, *, original_preserved=False):
        safe_codes = {
            "backup_invalid",
            "backup_destination_exists",
            "backup_integrity_failed",
            "backup_requires_innodb",
            "backup_members_invalid",
            "backup_size_limit",
            "backup_manifest_invalid",
            "backup_format_invalid",
            "backup_profile_mismatch",
            "backup_backend_mismatch",
            "backup_checksum_mismatch",
            "backup_value_invalid",
            "backup_schema_revision_invalid",
            "backup_tables_invalid",
            "backup_columns_invalid",
            "restore_failed",
            "restore_foreign_key_invalid",
            "restore_integrity_invalid",
            "restore_database_busy",
            "invalid_maintenance_lease",
            "unknown_schema",
        }
        code = code if code in safe_codes else "restore_failed"
        super().__init__(code)
        self.code = code
        self.original_preserved = original_preserved
        self.needs_maintenance_recovery = not original_preserved


def _scrub_settings(connection):
    table = sa.Table("app_settings", sa.MetaData(), autoload_with=connection)
    # These arbitrary JSON values are not a portable typed settings contract.
    # Keep current settings on restore; export none rather than guess safe keys.
    connection.execute(table.delete())
    jobs = sa.Table("background_jobs", sa.MetaData(), autoload_with=connection)
    # An online backup must not export still-valid execution capabilities.
    # Scrub only the copied snapshot; live workers retain their own claims.
    fields = {
        key: None
        for key in ("claim_token", "lease_expires_at", "locked_by", "locked_at")
        if key in jobs.c
    }
    if fields:
        connection.execute(jobs.update().values(**fields))


class BackupService:
    def __init__(self, storage):
        self.storage = storage
        self.settings = storage.settings
        if self.settings.storage_backend != "sqlite":
            raise ValueError("storage_sqlite_required")
        try:
            self.url = sa.engine.make_url(storage._url(async_driver=False))
        except (sa.exc.ArgumentError, TypeError, ValueError):
            raise ValueError("storage_sqlite_required") from None
        if self.url.drivername != "sqlite":
            raise ValueError("storage_sqlite_required")
        self.scripts = resource_path("alembic")

    def _lease(self, lease):
        if not isinstance(lease, MaintenanceLease) or not self.storage.fence.validate(lease):
            raise RuntimeError("invalid_maintenance_lease")

    def _validate_db(self, connection, revision=None):
        state = classify_schema(connection, str(self.scripts))
        actual = actual_revision(connection)
        if state.kind != "revision" or not actual or (revision and actual != revision):
            raise RuntimeError("backup_schema_revision_invalid")
        return actual

    def backup(self, destination) -> BackupManifest:
        destination = Path(destination).resolve()
        if destination.exists():
            raise RuntimeError("backup_destination_exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(".backup-" + uuid4().hex + ".tmp")
        try:
            with TemporaryDirectory(
                prefix="backup-", dir=self.settings.data_dir / "backups"
            ) as root:
                root = Path(root)
                name = "database.sqlite3"
                target = root / name
                with (
                    closing(sqlite3.connect(self.url.database)) as source,
                    closing(sqlite3.connect(target)) as output,
                ):
                    source.backup(output)
                    if output.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("backup_integrity_failed")
                    output.execute("PRAGMA secure_delete=ON")
                engine = sa.create_engine(sa.URL.create("sqlite", database=str(target)))
                try:
                    with engine.begin() as connection:
                        revision = self._validate_db(connection)
                        _scrub_settings(connection)
                    with engine.connect() as connection:
                        connection.exec_driver_sql("VACUUM")
                finally:
                    engine.dispose()
                payload = target.read_bytes()
                manifest = BackupManifest(
                    format_version=1,
                    schema_revision=revision,
                    backend=self.settings.storage_backend,
                    profile_id=self.settings.profile_id,
                    checksums={name: hashlib.sha256(payload).hexdigest()},
                    vector_state="excluded",
                )
                with ZipFile(temporary, "x", ZIP_DEFLATED) as archive:
                    archive.writestr("manifest.json", manifest.model_dump_json())
                    archive.writestr(name, payload)
                with temporary.open("r+b") as archive:
                    os.fsync(archive.fileno())
                os.replace(temporary, destination)
                return manifest
        finally:
            temporary.unlink(missing_ok=True)

    def _read(self, source):
        try:
            with ZipFile(source) as archive:
                infos = archive.infolist()
                if len(infos) != 2 or len({info.filename for info in infos}) != 2:
                    raise RuntimeError("backup_members_invalid")
                if any(info.file_size > 512 * 1024 * 1024 for info in infos):
                    raise RuntimeError("backup_size_limit")
                # No extractall: filenames cannot escape a stage directory.
                if archive.getinfo("manifest.json").file_size > 65536:
                    raise RuntimeError("backup_manifest_invalid")
                manifest = BackupManifest.model_validate_json(archive.read("manifest.json"))
                name = "database.sqlite3"
                if manifest.format_version != 1 or manifest.vector_state != "excluded":
                    raise RuntimeError("backup_format_invalid")
                if manifest.profile_id != self.settings.profile_id:
                    raise RuntimeError("backup_profile_mismatch")
                if manifest.backend != self.settings.storage_backend:
                    raise RuntimeError("backup_backend_mismatch")
                if set(archive.namelist()) != {"manifest.json", name} or set(
                    manifest.checksums
                ) != {name}:
                    raise RuntimeError("backup_members_invalid")
                payload = archive.read(name)
                if hashlib.sha256(payload).hexdigest() != manifest.checksums[name]:
                    raise RuntimeError("backup_checksum_mismatch")
                if manifest.schema_revision not in {
                    revision.revision
                    for revision in ScriptDirectory(str(self.scripts)).walk_revisions()
                }:
                    raise RuntimeError("backup_schema_revision_invalid")
                return manifest, payload
        except (BadZipFile, KeyError, ValueError):
            raise RuntimeError("backup_invalid") from None

    def _preserve_security(self, live, staged):
        metadata = sa.MetaData()
        metadata.reflect(bind=staged)
        # Consent settings and billing history are current live authority. Their
        # old archive values never restore consent or erase newer reserved spend.
        retained = {}
        retention_order = (
            "app_settings",
            "telegram_accounts",
            "ai_budget_locks",
            "ai_budget_reservations",
            "ai_usage",
        )
        for name in retention_order:
            if name not in metadata.tables:
                continue
            original = sa.Table(name, sa.MetaData(), autoload_with=live)
            retained[name] = [dict(row) for row in live.execute(sa.select(original)).mappings()]
        for name in reversed(retention_order):
            if name in retained:
                staged.execute(metadata.tables[name].delete())
        for name in retention_order:
            if retained.get(name):
                staged.execute(metadata.tables[name].insert(), retained[name])
        policies = metadata.tables["telegram_chat_policies"]
        permissions = metadata.tables["telegram_chat_permissions"]
        live_policies = sa.Table("telegram_chat_policies", sa.MetaData(), autoload_with=live)
        current = {
            row["chat_id"]: dict(row) for row in live.execute(sa.select(live_policies)).mappings()
        }
        epochs = {
            row.chat_id: row.authorization_epoch
            for row in staged.execute(sa.select(policies.c.chat_id, policies.c.authorization_epoch))
        }
        staged.execute(
            policies.update().values(
                allowed=False,
                cloud_fallback=False,
                authorization_epoch=policies.c.authorization_epoch + 1,
            )
        )
        staged.execute(permissions.delete())
        for chat_id, row in current.items():
            row["authorization_epoch"] = max(row["authorization_epoch"], epochs.get(chat_id, 0)) + 1
            staged.execute(policies.delete().where(policies.c.chat_id == chat_id))
            row.pop("id", None)
            staged.execute(policies.insert().values(**row))
        live_permissions = sa.Table("telegram_chat_permissions", sa.MetaData(), autoload_with=live)
        for row in live.execute(sa.select(live_permissions)).mappings():
            values = dict(row)
            values.pop("id", None)
            staged.execute(permissions.insert().values(**values))
        for name, key in (("background_jobs", "id"), ("pending_actions", "action_id")):
            original = sa.Table(name, sa.MetaData(), autoload_with=live)
            target = metadata.tables[name]
            for row in live.execute(sa.select(original)).mappings():
                values = dict(row)
                started = bool((values.get("payload") or {}).get("external_effect_started"))
                started = started or bool(values.get("execution_started_at"))
                if values["status"] not in {"uncertain", "completed", "executed"} and not started:
                    continue
                if started and values["status"] not in {"completed", "executed"}:
                    values["status"] = "uncertain"
                if name == "background_jobs":
                    values.update(
                        claim_token=None, lease_expires_at=None, locked_by=None, locked_at=None
                    )
                staged.execute(target.delete().where(target.c[key] == values[key]))
                staged.execute(target.insert().values(**values))
        jobs = metadata.tables["background_jobs"]
        for row in staged.execute(
            sa.select(jobs.c.id, jobs.c.payload).where(
                jobs.c.status.in_(["queued", "running", "paused"])
            )
        ):
            if isinstance(row.payload, dict) and row.payload.get("external_effect_started"):
                staged.execute(
                    jobs.update()
                    .where(jobs.c.id == row.id)
                    .values(
                        status="uncertain",
                        claim_token=None,
                        lease_expires_at=None,
                        locked_by=None,
                        locked_at=None,
                        last_error="restore_requires_reconciliation",
                    )
                )
        staged.execute(
            jobs.update()
            .where(jobs.c.status.in_(["queued", "running", "paused"]))
            .values(
                status="cancelled",
                claim_token=None,
                lease_expires_at=None,
                locked_by=None,
                locked_at=None,
                last_error="restore_requires_fresh_job",
            )
        )
        actions = metadata.tables["pending_actions"]
        staged.execute(
            actions.update()
            .where(actions.c.status.in_(["pending", "confirmed"]))
            .values(status="expired", expires_at=datetime.now(UTC))
        )
        staged.execute(
            actions.update()
            .where(actions.c.status.in_(["executing", "running"]))
            .values(status="uncertain", error="restore_requires_reconciliation")
        )
        # Cached answers may contain revoked source content, never reactivate them.
        if "ai_query_cache" in metadata.tables:
            staged.execute(metadata.tables["ai_query_cache"].delete())
        from .vector_reliability import invalidate_restored_index

        invalidate_restored_index(staged)

    def _prepare(self, stage, live, manifest):
        with stage.connect() as connection:
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            self._validate_db(connection, manifest.schema_revision)
            upgrade_database(
                connection,
                script_location=self.scripts,
                backup_dir=self.settings.data_dir / "backups",
            )
            connection.commit()
            with connection.begin():
                with live.connect() as original:
                    self._preserve_security(original, connection)
            revision = self._validate_db(connection)
            if connection.dialect.name == "sqlite":
                if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
                    raise RuntimeError("restore_foreign_key_invalid")
                if connection.exec_driver_sql("PRAGMA integrity_check").scalar() != "ok":
                    raise RuntimeError("restore_integrity_invalid")
                connection.commit()
                connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
            return revision

    def restore(self, source, lease):
        self._lease(lease)
        try:
            manifest, payload = self._read(source)
        except Exception as error:
            code = str(error) if isinstance(error, RuntimeError) else "backup_invalid"
            raise BackupError(code, original_preserved=True) from None
        return self._restore_sqlite(manifest, payload, lease)

    def _restore_sqlite(self, manifest, payload, lease):
        path = Path(self.url.database)
        stage_path = path.with_name(".restore-stage-" + uuid4().hex + ".sqlite3")
        rollback = path.with_name(".restore-original-" + uuid4().hex + ".sqlite3")
        stage_path.write_bytes(payload)
        stage = sa.create_engine(sa.URL.create("sqlite", database=str(stage_path)))
        live = sa.create_engine(self.url)
        swapped = False
        original_preserved = True
        try:
            revision = self._prepare(stage, live, manifest)
            self._lease(lease)
            prebackup = (
                self.settings.data_dir / "backups" / ("before-restore-" + uuid4().hex + ".zip")
            )
            self.backup(prebackup)
            with live.connect() as connection:
                result = connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)").one()
                if result[0]:
                    raise RuntimeError("restore_database_busy")
            live.dispose()
            stage.dispose()
            self._lease(lease)
            # Windows refuses replacement when an uncooperative handle is open;
            # the original remains intact and admission stays fenced on failure.
            os.replace(path, rollback)
            original_preserved = False
            try:
                os.replace(stage_path, path)
                swapped = True
                self._lease(lease)
                with live.connect() as connection:
                    self._validate_db(connection, revision)
                live.dispose()
            except BaseException:
                live.dispose()
                if swapped:
                    path.unlink(missing_ok=True)
                os.replace(rollback, path)
                original_preserved = True
                raise
            rollback.unlink()
            return RestoreReport(
                "restored_recovery_required",
                "sqlite",
                self.settings.profile_id,
                revision,
                prebackup,
            )
        except Exception as error:
            code = str(error) if isinstance(error, RuntimeError) else "restore_failed"
            raise BackupError(code, original_preserved=original_preserved) from None
        finally:
            live.dispose()
            stage.dispose()
            stage_path.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm"):
                Path(str(stage_path) + suffix).unlink(missing_ok=True)
