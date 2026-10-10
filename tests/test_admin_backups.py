"""Authenticated dashboard backups use real migrated SQLite data."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from zipfile import ZipFile

import httpx
import pytest

from tg_assistant.admin_api import AdminContext, create_admin_app, dashboard_login_code
from tg_assistant.config import Settings
from tg_assistant.contracts import PublicProfile
from tg_assistant.policy import PolicyEngine
from tg_assistant.services.ollama import OllamaService
from tg_assistant.services.storage import StorageService


@pytest.fixture
async def backup_admin(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path / "profile")

    class NoSecrets:
        def get(self, name):
            pytest.fail("SQLite backup touched a credential")

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
    storage.migrate()
    ollama = OllamaService(settings.ollama_base_url)

    async def handler(*args):
        return 0

    app = create_admin_app(
        AdminContext(
            database=database,
            policy=PolicyEngine(),
            owner_id=123456,
            settings_getter=lambda: settings,
            vectors_getter=lambda: None,
            ollama=ollama,
            ai_switch_handler=handler,
            ollama_activate_handler=handler,
            pause_all_handler=handler,
            resume_all_handler=handler,
            paths={"data": settings.data_dir},
            admin_secret="synthetic-backup-owner",  # noqa: S106 - disposable test authority
            management_admission=lambda: True,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://127.0.0.1:8765",
    ) as client:
        yield client, settings, storage
    await database.close()
    await ollama.close()
    storage.fence.close()


async def login(client):
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "code": dashboard_login_code("synthetic-backup-owner"),
        },
    )
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"], "Origin": "http://127.0.0.1:8765"}


async def test_backup_routes_require_owner_csrf_and_origin(backup_admin):
    client, settings, _ = backup_admin
    assert (await client.get("/api/v1/backups")).status_code == 401
    headers = await login(client)
    assert (await client.post("/api/v1/backups", json={})).status_code == 403
    assert (
        await client.post(
            "/api/v1/backups",
            json={},
            headers={
                **headers,
                "Origin": "https://example.invalid",
            },
        )
    ).status_code == 403
    assert not list((settings.data_dir / "backups").glob("backup-*.zip"))


async def test_dashboard_creates_lists_real_backup_without_paths(backup_admin, tmp_path):
    client, settings, _ = backup_admin
    headers = await login(client)
    response = await client.post("/api/v1/backups", json={}, headers=headers)
    assert response.status_code == 201, response.text
    assert str(settings.data_dir) not in response.text
    archives = list((settings.data_dir / "backups").glob("backup-*.zip"))
    assert len(archives) == 1
    with ZipFile(archives[0]) as archive:
        assert set(archive.namelist()) == {"manifest.json", "database.sqlite3"}
        snapshot = tmp_path / "backup.sqlite3"
        snapshot.write_bytes(archive.read("database.sqlite3"))
        with closing(sqlite3.connect(snapshot)) as database:
            revision = database.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            assert response.json()["manifest"]["schema_revision"] == revision
    listing = await client.get("/api/v1/backups")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["id"] == response.json()["id"]
    assert listing.json()["items"][0]["validation_state"] == "manifest_only"
    assert str(settings.data_dir) not in listing.text


async def test_browser_cannot_choose_backup_filesystem_destination(backup_admin):
    client, settings, _ = backup_admin
    headers = await login(client)
    response = await client.post(
        "/api/v1/backups",
        json={
            "destination": "C:/arbitrary-path/credential-canary.zip",
        },
        headers=headers,
    )
    assert response.status_code == 400
    assert "credential-canary" not in response.text
    assert not list((settings.data_dir / "backups").glob("backup-*.zip"))


async def test_api_backup_refuses_closed_writer_admission(backup_admin):
    client, settings, storage = backup_admin
    headers = await login(client)
    lease = storage.fence.acquire("synthetic-restore", lease_seconds=60)
    try:
        response = await client.post("/api/v1/backups", json={}, headers=headers)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "maintenance_in_progress"
        assert not list((settings.data_dir / "backups").glob("backup-*.zip"))
    finally:
        storage.fence.release(lease)
