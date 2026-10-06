"""SQLite product boundaries use private profiles and never real credentials."""

from __future__ import annotations

import asyncio
import builtins
import importlib
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine

from tg_assistant.config import Settings, config_path, get_settings, save_settings
from tg_assistant.contracts import PublicProfile, StorageBackend
from tg_assistant.db.base import Base
from tg_assistant.services.maintenance import MaintenanceService
from tg_assistant.services.storage import StorageService


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import os

    for name in tuple(os.environ):
        if name.startswith("TG_ASSISTANT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected profile, process, network or credential access")


@pytest.mark.parametrize("source", ["init", "env", "profile"])
def test_mysql_configuration_rejected_without_writes(tmp_path, monkeypatch, source):
    root = tmp_path / "private"
    values = {"data_dir": root}
    if source == "init":
        values["storage_backend"] = "mysql"
    elif source == "env":
        monkeypatch.setenv("TG_ASSISTANT_STORAGE_BACKEND", "mysql")
    else:
        path = config_path(root)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"version": 1, "settings": {"storage_backend": "mysql"}}))
    before = set(root.rglob("*"))
    with pytest.raises((ValueError, ValidationError)):
        Settings(**values)
    assert set(root.rglob("*")) == before


def test_connection_fields_never_infer_mysql(tmp_path):
    settings = Settings(data_dir=tmp_path / "private", database_name="old_unused_name")
    assert settings.storage_backend == "sqlite"
    assert settings.database_url("synthetic-unused-password").startswith("sqlite+aiosqlite:")


def test_existing_v1_sqlite_profile_remains_readable(tmp_path):
    root = tmp_path / "private"
    path = config_path(root)
    path.parent.mkdir(parents=True)
    original = {"version": 1, "settings": {
        "storage_backend": "sqlite", "database_host": "localhost", "database_port": 3306,
        "database_name": "old_unused_name", "database_user": "old_unused_user",
        "profile_id": "existing", "media_retention_hours": 37,
    }}
    path.write_text(json.dumps(original))
    settings = Settings(data_dir=root)
    assert settings.media_retention_hours == 37
    assert settings.profile_id == "existing"
    assert settings.database_url("unused", async_driver=False).startswith("sqlite:")
    assert json.loads(path.read_text()) == original


def test_legacy_profile_cannot_be_silently_overridden_to_sqlite(tmp_path):
    root = tmp_path / "private"
    path = config_path(root)
    path.parent.mkdir(parents=True)
    payload = json.dumps({"version": 1, "settings": {"storage_backend": "mysql"}})
    path.write_text(payload)
    with pytest.raises(ValueError):
        Settings(data_dir=root, storage_backend="sqlite")
    assert path.read_text() == payload


def test_native_provider_rejects_non_sqlite_engine_before_ownership_access(tmp_path):
    from tg_assistant.desktop.provider_context import ProviderContext

    settings = Settings(data_dir=tmp_path / "private")
    engine = SimpleNamespace(dialect=SimpleNamespace(name="mysql"), connect=forbidden)
    with pytest.raises(ValueError, match="storage_sqlite_required"):
        ProviderContext(settings, engine, forbidden, forbidden, current_sid=forbidden)


def test_installation_dotenv_is_not_an_implicit_or_explicit_product_source(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("TG_ASSISTANT_STORAGE_BACKEND=mysql\nTG_ASSISTANT_CLOUD_CONSENT=true\n")
    settings = Settings(_env_file=dotenv, data_dir=tmp_path / "private")
    assert settings.storage_backend == "sqlite"
    assert settings.cloud_consent is False


def test_public_storage_backend_is_sqlite_only():
    assert list(StorageBackend) == [StorageBackend.SQLITE]
    with pytest.raises(ValidationError):
        PublicProfile(profile_id="private", owner_id=None, storage_backend="mysql",
                      setup_stage="welcome", version=1)


def test_invalid_copy_rejected_before_storage_side_effects(tmp_path):
    root = tmp_path / "private"
    invalid = Settings(data_dir=root).model_copy(update={"storage_backend": "mysql"})
    with pytest.raises(ValueError):
        StorageService(invalid, forbidden)
    with pytest.raises(ValueError):
        invalid.database_url("synthetic-unused-password")
    assert not root.exists()


def test_worker_rejects_invalid_copy_before_instance_files(tmp_path, monkeypatch):
    worker = importlib.import_module("tg_assistant.desktop.worker")
    monkeypatch.setattr(worker, "InstanceGuard", forbidden)
    invalid = Settings(data_dir=tmp_path / "private").model_copy(update={"storage_backend": "mysql"})
    with pytest.raises(ValueError):
        worker.serve_worker(invalid, instance_directory=tmp_path / "instance")
    assert not (tmp_path / "private").exists()


def test_legacy_cli_rejects_mysql_before_secret_or_directory_access(tmp_path, monkeypatch):
    cli = importlib.import_module("tg_assistant.cli")
    invalid = Settings(data_dir=tmp_path / "private").model_copy(update={"storage_backend": "mysql"})
    monkeypatch.setattr(cli, "get_settings", lambda: invalid)
    monkeypatch.setattr(cli, "SecretStore", forbidden)
    monkeypatch.setattr(cli, "ensure_runtime_dirs", forbidden)
    with pytest.raises(ValueError):
        cli._ensure_ready()


def test_retired_mysql_setup_imports_no_driver_and_cannot_provision(monkeypatch):
    original = builtins.__import__

    def no_mysql_driver(name, *args, **kwargs):
        if name.split(".")[0] in {"pymysql", "asyncmy"}:
            raise AssertionError("SQLite product imported a MySQL driver")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_mysql_driver)
    module = importlib.reload(importlib.import_module("tg_assistant.setup.wizard"))
    monkeypatch.setattr(module, "detect_mysql", forbidden, raising=False)
    monkeypatch.setattr(module, "SecretStore", forbidden, raising=False)
    monkeypatch.setattr(module, "get_settings", forbidden, raising=False)
    with pytest.raises(RuntimeError, match="mysql_setup_unsupported"):
        module.run_setup()


def test_fresh_sqlite_storage_and_setup_do_not_access_store(tmp_path):
    from tg_assistant.desktop.setup_context import open_setup_context

    settings = Settings(data_dir=tmp_path / "private", profile_id="fresh")
    storage = StorageService(settings, forbidden)
    database = storage.open(PublicProfile(profile_id="fresh", owner_id=None,
        storage_backend="sqlite", setup_stage="welcome", version=1))
    asyncio.run(database.close())
    storage.migrate()
    with open_setup_context(settings, secret_store_factory=forbidden) as context:
        status = context.begin()
        assert status.profile.owner_id is None
        assert status.profile.storage_backend == "sqlite"
        assert "telegram_verified" not in status.stage_evidence_ids
    storage.fence.close()


@pytest.mark.parametrize("action", ["refresh", "check", "save_empty", "disconnect"])
def test_fresh_cloud_choices_cannot_adopt_global_credentials(tmp_path, action):
    from tg_assistant.desktop.provider_context import ProviderContext

    settings = Settings(data_dir=tmp_path / "private", profile_id="fresh_cloud",
                        ai_provider="openai", embedding_provider="off", cloud_consent=True)
    save_settings(settings)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    fence = MaintenanceService(settings.data_dir / "config", profile_id=settings.profile_id)
    accesses = []

    def rejected_store():
        accesses.append("store")
        return forbidden()

    context = ProviderContext(settings, engine, fence, rejected_store,
        current_sid=lambda: "synthetic-sid", transport=httpx.MockTransport(forbidden))
    options = {"service": "chat_ai", "model": settings.active_ai_model,
               "cloud_consent": True, "endpoint": settings.openai_base_url}
    try:
        if action == "refresh":
            context.refresh_saved_health()
            assert context.configuration_verification() is None
        elif action == "check":
            assert context.service.test_connection("openai", options).code == "credential_required"
        elif action == "save_empty":
            assert context.service.validate_and_save("openai", "", options).code == "credential_required"
        else:
            assert context.service.disconnect("openai").code == "disconnected"
        assert accesses == []
    finally:
        context.close()
        fence.close()
        engine.dispose()
