"""Profile-bound local storage selection and schema preparation."""

from __future__ import annotations

from sqlalchemy import create_engine, event

from ..config import Settings, save_settings, validate_settings
from ..contracts import MigrationReport, PublicProfile
from ..db.base import Database, configure_sqlite
from ..db.migrations import upgrade_database
from ..paths import ensure_runtime_dirs, resource_path
from ..security import SecretStore


class StorageService:
    def __init__(self, settings: Settings, store: SecretStore | None = None) -> None:
        self.settings = validate_settings(settings)
        self.store = store
        self._opened = False

    def open(self, profile: PublicProfile) -> Database:
        """Open the selected backend; a profile DTO never proves authorization."""
        profile = PublicProfile.model_validate(profile)
        if profile.profile_id != self.settings.profile_id:
            raise ValueError("Storage profile does not match the active profile")
        if profile.storage_backend != self.settings.storage_backend:
            raise ValueError("Storage backend does not match the active profile")
        url = self._url(async_driver=True)
        ensure_runtime_dirs(self.settings.data_dir, profile_id=self.settings.profile_id)
        save_settings(self.settings)
        database = Database(url, pool_size=self.settings.database_pool_size)
        self._opened = True
        return database

    def _url(self, *, async_driver: bool) -> str:
        if self.settings.storage_backend == "sqlite":
            return self.settings.database_url("", async_driver=async_driver)
        store = self.store if self.store is not None else SecretStore()
        password = store.get("database_password")
        if not password:
            raise RuntimeError("Chưa cấu hình MySQL. Chạy tg-assistant reconfigure hoặc start.bat.")
        return self.settings.database_url(password, async_driver=async_driver)

    def migrate(self) -> MigrationReport:
        """Run the reviewed preflight/migration path before application writers start."""
        if not self._opened:
            raise RuntimeError("Open the active storage profile before migration")
        engine = create_engine(self._url(async_driver=False))
        if engine.dialect.name == "sqlite":
            event.listen(engine, "connect", configure_sqlite)
        try:
            with engine.connect() as connection:
                return upgrade_database(
                    connection,
                    script_location=resource_path("alembic"),
                    backup_dir=self.settings.data_dir / "backups",
                )
        finally:
            engine.dispose()
