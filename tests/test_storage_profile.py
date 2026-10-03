"""Storage startup uses disposable profiles, never the user's config or credentials."""

from __future__ import annotations

import json
import os
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from tg_assistant import config
from tg_assistant import paths as app_paths
from tg_assistant.config import Settings, get_settings, save_settings_env
from tg_assistant.contracts import PublicProfile
from tg_assistant.db.base import Database
from tg_assistant.paths import ensure_runtime_dirs


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("TG_ASSISTANT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local AppData"))
    install = tmp_path / "installed"
    install.mkdir()
    monkeypatch.setattr(config, "project_root", lambda: install)
    monkeypatch.setattr(
        app_paths, "current_user_sid", lambda: "S-1-5-21-100-100-100-1001", raising=False
    )
    monkeypatch.chdir(install)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def profile(backend="sqlite", profile_id="default"):
    return PublicProfile(
        profile_id=profile_id,
        owner_id=None,
        storage_backend=backend,
        setup_stage="welcome",
        version=1,
    )


def storage(settings, store=None):
    from tg_assistant.services import storage as module

    assert hasattr(module, "StorageService"), "StorageService is required for profile startup"
    return module.StorageService(settings, store)


class NoCredentials:
    def get(self, key):
        raise AssertionError(f"SQLite must not request credentials: {key}")


async def test_new_install_uses_sqlite(tmp_path):
    settings = Settings()
    assert getattr(settings, "storage_backend", None) == "sqlite"
    service = storage(settings, NoCredentials())
    db = service.open(profile())
    try:
        assert await db.ping()
        assert db.engine.dialect.name == "sqlite"
        assert Path(db.engine.url.database).is_relative_to(settings.data_dir / "db")
    finally:
        await db.close()
    assert (settings.data_dir / "config" / "settings.json").is_file()
    assert not (config.project_root() / ".env").exists()


def test_existing_mysql_preserved(tmp_path):
    legacy = config.project_root() / ".env"
    legacy.write_text(
        "TG_ASSISTANT_DATABASE_HOST=127.0.0.1\n"
        "TG_ASSISTANT_DATABASE_PORT=13307\n"
        "TG_ASSISTANT_DATABASE_NAME=codex_s01_legacy\n"
        "TG_ASSISTANT_DATABASE_USER=legacy_owner\n"
        "TG_ASSISTANT_DATABASE_PASSWORD=never-import-this\n",
        encoding="utf-8",
    )
    settings = Settings()
    assert getattr(settings, "storage_backend", None) == "mysql"
    assert settings.database_name == "codex_s01_legacy"
    assert settings.database_port == 13307
    save_settings_env({"TG_ASSISTANT_LOG_LEVEL": "DEBUG"})
    persisted = (settings.data_dir / "config" / "settings.json").read_text(encoding="utf-8")
    assert "never-import-this" not in persisted
    assert "password" not in persisted.casefold()
    assert Settings().storage_backend == "mysql"
    assert Settings().database_name == "codex_s01_legacy"
    assert "never-import-this" in legacy.read_text(encoding="utf-8")


def test_config_independent_of_cwd(tmp_path, monkeypatch):
    save_settings_env({"TG_ASSISTANT_AI_PROVIDER": "off"})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / ".env").write_text("TG_ASSISTANT_AI_PROVIDER=openrouter\n", encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    assert Settings().ai_provider == "off"
    assert not (config.project_root() / ".env").exists()
    document = json.loads((Settings().data_dir / "config" / "settings.json").read_text())
    assert document["version"] == 1


async def test_windows_unicode_paths(tmp_path, monkeypatch):
    root = tmp_path / "Người dùng tiếng Việt" / "Profile có dấu"
    monkeypatch.setenv("TG_ASSISTANT_DATA_DIR", str(root))
    settings = Settings(_env_file=None)
    assert getattr(settings, "storage_backend", None) == "sqlite"
    db = storage(settings, NoCredentials()).open(profile())
    try:
        async with db.engine.begin() as connection:
            await connection.execute(text("CREATE TABLE evidence (text TEXT NOT NULL)"))
            await connection.execute(text("INSERT INTO evidence VALUES ('Tiếng Việt')"))
        async with db.engine.connect() as connection:
            assert await connection.scalar(text("SELECT text FROM evidence")) == "Tiếng Việt"
    finally:
        await db.close()
    assert (root / ".tg-assistant-data").is_file()
    dirs = ensure_runtime_dirs(root)
    assert dirs["sessions"] == root / "sessions"
    assert dirs["config"] == root / "config"
    assert dirs["db"] == root / "db"


async def test_sqlite_pragmas(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'pragmas.db'}")
    try:
        async with db.engine.connect() as first, db.engine.connect() as second:
            for connection in (first, second):
                assert await connection.scalar(text("PRAGMA foreign_keys")) == 1
                assert await connection.scalar(text("PRAGMA journal_mode")) == "wal"
                assert await connection.scalar(text("PRAGMA busy_timeout")) >= 5000
            await first.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY)"))
            await first.execute(
                text("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))")
            )
            await first.commit()
            with pytest.raises(Exception, match="FOREIGN KEY constraint failed"):
                await second.execute(text("INSERT INTO child VALUES (99)"))
    finally:
        await db.close()


def test_aiosqlite_runtime_dependency():
    path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
    assert any(item.startswith("aiosqlite") for item in project["dependencies"])


@pytest.mark.parametrize(
    "key", ["TG_ASSISTANT_DATABASE_PASSWORD", "TG_ASSISTANT_TELEGRAM_API_HASH"]
)
def test_secret_config_rejected_without_creating_file(key):
    with pytest.raises(ValueError, match="non-secret"):
        save_settings_env({key: "sensitive-do-not-write"})
    assert not (Settings(_env_file=None).data_dir / "config" / "settings.json").exists()
    assert not (config.project_root() / ".env").exists()


def test_explicit_no_env_skips_legacy():
    (config.project_root() / ".env").write_text("TG_ASSISTANT_DATABASE_NAME=legacy\n")
    settings = Settings(_env_file=None)
    assert getattr(settings, "storage_backend", None) == "sqlite"


def test_explicit_mysql_settings_preserved():
    settings = Settings(_env_file=None, database_name="codex_s01_explicit")
    assert getattr(settings, "storage_backend", None) == "mysql"
    assert settings.database_url("fixture-only").startswith("mysql+asyncmy://")


def test_unknown_config_version_refused():
    root = Settings(_env_file=None).data_dir
    path = root / "config" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"version": 999, "settings": {}}', encoding="utf-8")
    with pytest.raises(ValueError, match="config"):
        Settings(_env_file=None)
    assert json.loads(path.read_text())["version"] == 999


def test_backend_profile_mismatch_refused():
    settings = Settings(_env_file=None)
    assert getattr(settings, "storage_backend", None) == "sqlite"
    with pytest.raises(ValueError, match="backend"):
        storage(settings, NoCredentials()).open(profile("mysql"))
    assert not settings.data_dir.exists()


def test_marker_profile_mismatch_refused():
    settings = Settings(_env_file=None)
    assert getattr(settings, "storage_backend", None) == "sqlite"
    settings.data_dir.mkdir(parents=True)
    marker = settings.data_dir / ".tg-assistant-data"
    marker.write_text(
        json.dumps({"version": 1, "app": "TelegramAIPersonalAssistant", "profile_id": "other"})
    )
    with pytest.raises(ValueError, match="ownership"):
        storage(settings, NoCredentials()).open(profile())
    assert not (settings.data_dir / "db").exists()


async def test_runtime_new_profile_never_requests_database_secret():
    from tg_assistant.runtime import make_database

    db = make_database(Settings(_env_file=None), NoCredentials())
    try:
        assert await db.ping()
        assert db.engine.dialect.name == "sqlite"
    finally:
        await db.close()


async def test_storage_migrates_empty_profile_to_real_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    settings = Settings(_env_file=None)
    assert getattr(settings, "storage_backend", None) == "sqlite"
    service = storage(settings, NoCredentials())
    db = service.open(profile())
    report = service.migrate()
    root = Path(__file__).resolve().parents[1]
    alembic = Config(str(root / "alembic.ini"))
    alembic.set_main_option("script_location", str(root / "alembic"))
    head = ScriptDirectory.from_config(alembic).get_current_head()
    try:
        async with db.engine.connect() as connection:
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == head
        assert report.current_revision == head
        assert service.migrate().changed is False
    finally:
        await db.close()


def test_same_profile_foreign_sid_marker_refused():
    settings = Settings(_env_file=None)
    settings.data_dir.mkdir(parents=True)
    marker = settings.data_dir / ".tg-assistant-data"
    foreign = {
        "version": 1,
        "app": "TelegramAIPersonalAssistant",
        "profile_id": "default",
        "sid": "S-1-5-21-100-100-100-1002",
    }
    marker.write_text(json.dumps(foreign))
    with pytest.raises(ValueError, match="ownership"):
        storage(settings, NoCredentials()).open(profile())
    assert json.loads(marker.read_text()) == foreign
    assert not (settings.data_dir / "db").exists()


def test_nonempty_unowned_root_refused(tmp_path):
    root = tmp_path / "unowned"
    root.mkdir()
    payload = root / "keep.txt"
    payload.write_text("foreign data")
    with pytest.raises(ValueError, match="ownership"):
        ensure_runtime_dirs(root)
    assert payload.read_text() == "foreign data"
    assert list(root.iterdir()) == [payload]


def test_new_marker_binds_current_sid():
    settings = Settings(_env_file=None)
    dirs = ensure_runtime_dirs(settings.data_dir)
    marker = json.loads((dirs["data"] / ".tg-assistant-data").read_text())
    assert marker["sid"] == "S-1-5-21-100-100-100-1001"


def test_empty_legacy_marker_only_adopted_on_current_per_user_root(tmp_path):
    root = Settings(_env_file=None).data_dir
    root.mkdir(parents=True)
    (root / ".tg-assistant-data").touch()
    (root / "legacy-session-placeholder").write_text("synthetic historical data")
    ensure_runtime_dirs()
    assert (root / "legacy-session-placeholder").read_text() == "synthetic historical data"
    assert json.loads((root / ".tg-assistant-data").read_text())["sid"].endswith("1001")
    foreign = tmp_path / "foreign-marker"
    foreign.mkdir()
    marker = foreign / ".tg-assistant-data"
    marker.touch()
    with pytest.raises(ValueError, match="ownership"):
        ensure_runtime_dirs(foreign)
    assert marker.read_text() == ""


def test_data_override_does_not_prove_legacy_directory_ownership(tmp_path, monkeypatch):
    foreign = tmp_path / "foreign-empty-marker"
    foreign.mkdir()
    marker = foreign / ".tg-assistant-data"
    marker.touch()
    monkeypatch.setenv("TG_ASSISTANT_DATA_DIR", str(foreign))
    with pytest.raises(ValueError, match="ownership"):
        ensure_runtime_dirs()
    assert marker.read_text() == ""


def test_resource_resolver_source_and_frozen_unicode_paths(tmp_path, monkeypatch):
    source = tmp_path / "Cài đặt nguồn"
    bundle = tmp_path / "Gói phát hành"
    monkeypatch.setattr(app_paths, "project_root", lambda: source)
    assert app_paths.resource_path("assets", "font.ttf") == source / "assets" / "font.ttf"
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    assert app_paths.resource_path("assets", "font.ttf") == bundle / "assets" / "font.ttf"
    assert (
        Settings(_env_file=None).resolved_dashboard_dist_path
        == bundle / "dashboard-prototype" / "dist" / "client"
    )
    assert not Settings(_env_file=None).data_dir.is_relative_to(bundle)
    with pytest.raises(ValueError):
        app_paths.resource_path("..", "mutable")


@pytest.mark.skipif(os.name != "nt", reason="Windows drive-relative path semantics")
def test_resource_resolver_rejects_drive_relative_path():
    with pytest.raises(ValueError):
        app_paths.resource_path("C:asset")


def test_runtime_dirs_follow_legacy_selected_data_root(tmp_path):
    selected = tmp_path / "Hồ sơ cũ"
    legacy = config.project_root() / ".env"
    legacy.write_text(
        f"TG_ASSISTANT_DATA_DIR={selected}\nTG_ASSISTANT_DATABASE_NAME=legacy\n", encoding="utf-8"
    )
    assert get_settings().data_dir == selected
    assert ensure_runtime_dirs()["data"] == selected


def test_runtime_dirs_follow_selected_profile_identifier(monkeypatch):
    monkeypatch.setenv("TG_ASSISTANT_PROFILE_ID", "active-profile")
    settings = get_settings()
    ensure_runtime_dirs(settings.data_dir, profile_id=settings.profile_id)
    assert ensure_runtime_dirs()["data"] == settings.data_dir
    assert (
        json.loads((settings.data_dir / ".tg-assistant-data").read_text())["profile_id"]
        == "active-profile"
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_concurrent_profiles_cannot_overwrite_winning_ownership(tmp_path, monkeypatch, legacy):
    root = app_paths.default_user_data_root() if legacy else tmp_path / "claim-race"
    root.mkdir(parents=True)
    if legacy:
        (root / ".tg-assistant-data").touch()
    starting = Barrier(2)
    original_iterdir = Path.iterdir

    def slow_directory_listing(path):
        entries = tuple(original_iterdir(path))
        if path == root:
            # A slow filesystem exposes the gap between inspection and claim.
            time.sleep(0.05)
        return iter(entries)

    monkeypatch.setattr(Path, "iterdir", slow_directory_listing)

    def claim(identifier):
        starting.wait(timeout=10)
        try:
            ensure_runtime_dirs(root, profile_id=identifier)
            return identifier
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(claim, ["profile-a", "profile-b"]))
    winners = [item for item in results if item is not None]
    assert len(winners) == 1
    assert json.loads((root / ".tg-assistant-data").read_text())["profile_id"] == winners[0]


def test_mysql_wizard_adapter_writes_per_user_json():
    from tg_assistant.setup.wizard import _save_database_config

    _save_database_config(
        config.project_root() / ".env",
        host="127.0.0.1",
        port=13307,
        database="codex_s01_wizard",
        app_user="fixture",
    )
    settings = Settings()
    assert settings.storage_backend == "mysql"
    assert settings.database_port == 13307
    assert settings.database_name == "codex_s01_wizard"
    assert not (config.project_root() / ".env").exists()


def test_doctor_default_does_not_require_mysql(monkeypatch, capsys):
    from tg_assistant import cli

    settings = Settings(_env_file=None, ai_provider="off", admin_api_enabled=False)

    class AvailabilityStore:
        def get(self, key):
            if key == "database_password":
                raise AssertionError("SQLite doctor must not request a database password")
            return None

    def unexpected_mysql(*args, **kwargs):
        raise AssertionError("SQLite doctor must not inspect a MySQL service")

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "SecretStore", AvailabilityStore)
    monkeypatch.setattr(cli, "detect_mysql", unexpected_mysql)
    cli.doctor()
    output = capsys.readouterr().out
    assert "Database (sqlite): OK" in output
    assert "MySQL" not in output


def test_prepare_sqlite_cli_uses_real_migrations():
    from tg_assistant.cli import _prepare_sqlite_storage

    settings = Settings(_env_file=None)
    _prepare_sqlite_storage(settings, NoCredentials())
    import sqlite3

    with sqlite3.connect(settings.data_dir / "db" / "assistant.sqlite3") as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0006",)


@pytest.mark.parametrize(
    "url",
    [
        "http://user:sensitive@localhost:11434/v1",
        "http://localhost:11434/v1?token=sensitive",
        "http://localhost:11434/v1#sensitive",
    ],
)
def test_credential_bearing_local_endpoint_rejected(url):
    with pytest.raises(ValueError):
        Settings(_env_file=None, ollama_base_url=url)


def test_unchecked_settings_revalidated_before_persistence():
    invalid = Settings(_env_file=None).model_copy(
        update={"ollama_base_url": "http://user:sensitive@localhost:11434/v1"}
    )
    with pytest.raises(ValueError, match="non-secret") as caught:
        config.save_settings(invalid)
    assert "sensitive" not in str(caught.value)
    assert not invalid.data_dir.exists()


def test_unchecked_storage_settings_rejected_before_directory_claim():
    invalid = Settings(_env_file=None).model_copy(
        update={"ollama_base_url": "http://user:sensitive@localhost:11434/v1"}
    )
    with pytest.raises(ValueError, match="non-secret") as caught:
        storage(invalid, NoCredentials()).open(profile())
    assert "sensitive" not in str(caught.value)
    assert not invalid.data_dir.exists()


def test_relative_data_override_does_not_follow_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_ASSISTANT_DATA_DIR", "Hồ sơ tương đối")
    first = Settings(_env_file=None)
    elsewhere = tmp_path / "unrelated"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    second = Settings(_env_file=None)
    assert first.data_dir == second.data_dir
    assert (
        first.data_dir
        == tmp_path / "Local AppData" / "TelegramAIPersonalAssistant" / "Hồ sơ tương đối"
    )


def test_pool_tuning_does_not_select_mysql():
    assert Settings(_env_file=None, database_pool_size=8).storage_backend == "sqlite"


async def test_existing_mysql_real_data_remains_mysql(tmp_path):
    raw = os.environ.get("TG_TEST_MYSQL_URL")
    if not raw:
        pytest.skip("TG_TEST_MYSQL_URL disposable MySQL schema not configured")
    url = sa.engine.make_url(raw)
    assert url.host == "127.0.0.1" and url.port == 13307
    name = "codex_s01_" + uuid4().hex
    admin = sa.create_engine(url.set(database=None))
    with admin.begin() as connection:
        connection.exec_driver_sql(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")

    class FixtureStore:
        def get(self, key):
            assert key == "database_password"
            return url.password

    db = None
    try:
        settings = Settings(
            _env_file=None,
            data_dir=tmp_path / "mysql-profile",
            database_host=url.host,
            database_port=url.port,
            database_user=url.username,
            database_name=name,
        )
        assert settings.storage_backend == "mysql"
        service = storage(settings, FixtureStore())
        db = service.open(profile("mysql"))
        assert db.engine.url.database == name
        assert db.engine.dialect.name == "mysql"
        service.migrate()
        async with db.session() as session:
            await session.execute(
                text(
                    "INSERT INTO runtime_metrics (collected_at, process_id, process_name) "
                    "VALUES (CURRENT_TIMESTAMP, 123, 'Giữ dữ liệu')"
                )
            )
        await db.close()
        db = None
        reopened_settings = Settings(_env_file=None, data_dir=settings.data_dir)
        assert reopened_settings.storage_backend == "mysql"
        reopened = storage(reopened_settings, FixtureStore())
        db = reopened.open(profile("mysql"))
        assert reopened.migrate().changed is False
        async with db.session() as session:
            assert (
                await session.scalar(text("SELECT process_name FROM runtime_metrics"))
                == "Giữ dữ liệu"
            )
        assert not (settings.data_dir / "db" / "assistant.sqlite3").exists()
    finally:
        if db is not None:
            await db.close()
        with admin.begin() as connection:
            connection.exec_driver_sql(f"DROP DATABASE `{name}`")
        admin.dispose()
