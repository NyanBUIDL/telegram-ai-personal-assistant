"""Native setup uses actual migrated selected storage and its writer fence."""

from __future__ import annotations

import asyncio
import importlib

import pytest

from tg_assistant.config import Settings
from tg_assistant.contracts import PublicProfile
from tg_assistant.services.maintenance import MaintenanceBusy
from tg_assistant.services.storage import StorageService


def context_module():
    assert importlib.util.find_spec("tg_assistant.desktop.setup_context"), (
        "Native storage wiring missing"
    )
    return importlib.import_module("tg_assistant.desktop.setup_context")


@pytest.fixture(params=["sqlite"])
def migrated_settings(tmp_path, request):
    values = dict(_env_file=None, data_dir=tmp_path / "profile", profile_id="setup_context")
    store = None
    settings = Settings(**values)
    service = StorageService(settings, store)
    database = service.open(
        PublicProfile(
            profile_id="setup_context",
            owner_id=None,
            storage_backend=request.param,
            setup_stage="welcome",
            version=1,
        )
    )
    asyncio.run(database.close())
    service.migrate()
    try:
        yield settings, service
    finally:
        service.fence.close()


def test_native_context_uses_actual_storage_without_accessing_sqlite_credentials(migrated_settings):
    module = context_module()
    settings, _ = migrated_settings

    def forbidden_secrets():
        pytest.fail("Default SQLite setup must not access Credential Manager")

    secrets = (
        forbidden_secrets
        if settings.storage_backend == "sqlite"
        else lambda: migrated_settings[1].store
    )
    with module.open_setup_context(settings, secret_store_factory=secrets) as context:
        result = context.begin()
        assert result.profile.setup_stage == "storage_ready"
        assert result.profile.owner_id is None
        assert {row.service.value: row.state.value for row in result.connections}[
            "storage"
        ] == "ready"
        assert "telegram_verified" not in result.stage_evidence_ids
        assert "management" in result.disabled_capabilities
    with module.open_setup_context(settings, secret_store_factory=secrets) as resumed:
        assert resumed.coordinator.resume().profile.setup_stage == "storage_ready"


def test_native_context_refuses_unprepared_database_without_creating_it(tmp_path):
    module = context_module()
    settings = Settings(_env_file=None, data_dir=tmp_path / "empty")
    with pytest.raises(ValueError, match="setup_storage_unavailable"):
        module.open_setup_context(settings)
    assert not (settings.data_dir / "db" / "assistant.sqlite3").exists()


def test_native_context_cannot_advance_during_real_maintenance(migrated_settings):
    module = context_module()
    settings, storage = migrated_settings
    with module.open_setup_context(settings, secret_store_factory=lambda: storage.store) as context:
        lease = storage.fence.acquire("fixture-maintenance", lease_seconds=30)
        try:
            with pytest.raises(MaintenanceBusy):
                context.begin()
            assert not context.coordinator.status().stage_evidence_ids
        finally:
            storage.fence.release(lease)
