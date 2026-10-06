"""Profile-bound local storage selection and schema preparation."""

from __future__ import annotations

from sqlalchemy import create_engine, event

from ..config import Settings, save_settings, validate_settings
from ..contracts import BackupManifest, MaintenanceLease, MigrationReport, PublicProfile
from ..db.base import Database, configure_sqlite
from ..db.migrations import upgrade_database
from ..paths import ensure_runtime_dirs, resource_path
from ..security import SecretStore
from .maintenance import MaintenanceService


class StorageService:
    def __init__(self, settings: Settings, store: SecretStore | None = None) -> None:
        self.settings = validate_settings(settings)
        self.store = store
        self._opened = False
        self.fence = None

    def open(self, profile: PublicProfile) -> Database:
        """Open the selected backend; a profile DTO never proves authorization."""
        profile = PublicProfile.model_validate(profile)
        if profile.profile_id != self.settings.profile_id:
            raise ValueError("Storage profile does not match the active profile")
        if profile.storage_backend != self.settings.storage_backend:
            raise ValueError("Storage backend does not match the active profile")
        ensure_runtime_dirs(self.settings.data_dir, profile_id=self.settings.profile_id)
        self.fence = MaintenanceService(
            self.settings.data_dir / "config", profile_id=self.settings.profile_id
        )
        with self.fence.operation():
            url = self._url(async_driver=True)
            save_settings(self.settings)
            database = Database(url, pool_size=self.settings.database_pool_size)
            database.fence = self.fence
        self._opened = True
        return database

    def _url(self, *, async_driver: bool) -> str:
        return self.settings.database_url("", async_driver=async_driver)

    def migrate(self) -> MigrationReport:
        """Run the reviewed preflight/migration path before application writers start."""
        if not self._opened:
            raise RuntimeError("Open the active storage profile before migration")
        engine = create_engine(self._url(async_driver=False))
        if engine.dialect.name == "sqlite":
            event.listen(engine, "connect", configure_sqlite)
        try:
            lease = self.fence.acquire("schema-migration", lease_seconds=1800, timeout=0)
            with engine.connect() as connection:
                report = upgrade_database(
                    connection,
                    script_location=resource_path("alembic"),
                    backup_dir=self.settings.data_dir / "backups",
                )
            self.fence.release(lease)
            return report
        finally:
            self.fence.close()
            engine.dispose()

    def backup(self, destination) -> BackupManifest:
        from .backup import BackupService

        if not self._opened:
            raise RuntimeError("storage_not_open")
        with self.fence.operation():
            return BackupService(self).backup(destination)

    def restore(self, source, maintenance_lease: MaintenanceLease):
        from .backup import BackupService

        if not self._opened:
            raise RuntimeError("storage_not_open")
        return BackupService(self).restore(source, maintenance_lease)
