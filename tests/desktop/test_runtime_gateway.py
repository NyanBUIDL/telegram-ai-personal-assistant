"""Real application/owner/storage boundaries with synthetic Telegram transports."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet

from tg_assistant import runtime
from tg_assistant.config import Settings
from tg_assistant.contracts import PublicProfile
from tg_assistant.db.models import TelegramAccount
from tg_assistant.desktop.worker import RuntimeGateway
from tg_assistant.paths import ensure_runtime_dirs
from tg_assistant.policy import PolicyEngine
from tg_assistant.security import EncryptedSession
from tg_assistant.services.maintenance import MaintenanceBusy, MaintenanceService
from tg_assistant.services.storage import StorageService
from tg_assistant.telegram import user_client

ACTUAL_UVICORN_SERVER = runtime.uvicorn.Server


class MemoryStore:
    def __init__(self):
        self.values = {
            "telegram_api_id": "123",
            "telegram_api_hash": "synthetic-api-hash",
            "telegram_bot_token": "synthetic-bot-token",
            "session_encryption_key": Fernet.generate_key().decode("ascii"),
            "admin_dashboard_secret": "synthetic-dashboard-secret",
        }

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value


class TelegramTransport:
    def __init__(self, *args):
        self.authorized = True
        self.me = SimpleNamespace(id=123, username="synthetic_owner")
        self.connected = asyncio.Event()
        self.closed = 0

    async def start(self, **kwargs):
        pytest.fail("Native resume must never open terminal authentication")

    async def connect(self):
        self.connected.set()

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        return self.me

    async def disconnect(self):
        self.closed += 1

    async def iter_dialogs(self):
        for item in ():
            yield item

    def on(self, event):
        return lambda handler: handler

    async def run_until_disconnected(self):
        await asyncio.Event().wait()


class BotTransport:
    def __init__(self, *args, **kwargs):
        self.closed = 0

    async def run(self):
        await asyncio.Event().wait()

    async def close(self):
        self.closed += 1


@pytest.fixture
async def native_case(tmp_path, monkeypatch):
    selected = Settings(
        _env_file=None,
        data_dir=tmp_path / "owned",
        ai_provider="off",
        embedding_provider="off",
        enable_embeddings=False,
        admin_api_enabled=True,
        admin_api_port=18765,
    )
    paths = ensure_runtime_dirs(selected.data_dir, profile_id=selected.profile_id)
    store = MemoryStore()
    plain = paths["sessions"] / "account.session"
    encrypted = paths["sessions"] / "account.session.enc"
    plain.write_bytes(b"synthetic session content")
    EncryptedSession(store).encrypt_file(plain, encrypted)
    plain.unlink()
    service = StorageService(selected, store)
    db = service.open(
        PublicProfile(
            profile_id=selected.profile_id,
            owner_id=None,
            storage_backend="sqlite",
            setup_stage="welcome",
            version=1,
        )
    )
    service.migrate()
    async with db.session() as session:
        session.add(TelegramAccount(telegram_user_id=123, is_owner_paired=True, is_active=True))
    await db.close()
    transports = []

    def transport(*args):
        item = TelegramTransport(*args)
        transports.append(item)
        return item

    monkeypatch.setattr(user_client, "TelegramClient", transport)
    monkeypatch.setattr(runtime, "SecretStore", lambda: store)
    monkeypatch.setattr(runtime, "ControlBot", BotTransport)
    monkeypatch.setattr(
        runtime, "get_settings", lambda: pytest.fail("Explicit profile was ignored")
    )
    monkeypatch.setattr(
        runtime.uvicorn, "Server", lambda *args: pytest.fail("Second HTTP listener")
    )
    return selected, paths, store, transports


async def test_native_resume_attaches_protected_api_to_existing_gateway(native_case):
    selected, paths, store, transports = native_case
    gateway = RuntimeGateway(selected.admin_api_port, "1" * 32)
    attached = []

    def attach(admin):
        gateway.admin = admin
        attached.append(admin.state.admin_context.owner_id)
        application.stopping.set()

    application = runtime.Application(settings=selected, admin_app_ready=attach)
    await application.run()
    assert attached == [123]
    assert application.user.owner_id == 123 and transports[0].closed == 1
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway), base_url="http://127.0.0.1:18765"
    ) as client:
        assert (await client.get("/api/v1/overview")).status_code == 401
        response = await client.get("/api/v1/runtime/readiness")
        assert response.json()["capabilities"] == ["management"]
        assert (
            await client.get("/api/v1/overview", headers={"Origin": "https://foreign.invalid"})
        ).status_code == 403
    assert not (paths["sessions"] / "account.session").exists()
    content = Fernet(store.get("session_encryption_key").encode()).decrypt(
        (paths["sessions"] / "account.session.enc").read_bytes()
    )
    assert content == b"synthetic session content"
    await application.close()
    assert transports[0].closed == 1 and application.bot.closed == 1


@pytest.mark.parametrize(
    "reason", ["expired", "missing_identity", "foreign", "inactive", "ambiguous"]
)
async def test_native_unverified_identity_refuses_management_and_cleans_session(
    native_case, monkeypatch, reason
):
    selected, paths, store, transports = native_case
    original = user_client.TelegramClient

    def transport(*args):
        item = original(*args)
        if reason == "expired":
            item.authorized = False
        if reason == "missing_identity":
            item.me = None
        if reason == "foreign":
            item.me.id = 999
        return item

    monkeypatch.setattr(user_client, "TelegramClient", transport)
    if reason in {"inactive", "ambiguous"}:
        db = runtime.make_database(selected, store)
        async with db.session() as session:
            account = await session.get(TelegramAccount, 1)
            if reason == "inactive":
                account.is_active = False
            else:
                session.add(
                    TelegramAccount(telegram_user_id=999, is_owner_paired=True, is_active=True)
                )
        await db.close()
    application = runtime.Application(
        settings=selected,
        admin_app_ready=lambda admin: pytest.fail("Unverified management callback"),
    )
    with pytest.raises(RuntimeError, match="telegram_reconnect_required|owner_pairing_required"):
        await application.run()
    assert transports[0].closed == 1
    assert not (paths["sessions"] / "account.session").exists()
    assert application.ollama.client.is_closed
    assert application.coingecko.client.is_closed


async def test_explicit_runtime_profile_obeys_maintenance_before_resources(
    native_case, monkeypatch
):
    selected, paths, _, transports = native_case
    fence = MaintenanceService(paths["config"], profile_id=selected.profile_id)
    lease = fence.acquire("synthetic-restore")
    try:
        with pytest.raises(MaintenanceBusy):
            await runtime.run_application(settings=selected, admin_app_ready=lambda admin: None)
        assert transports == []
    finally:
        fence.release(lease)


def test_telegram_constructor_failure_preserves_ciphertext_without_plaintext(
    native_case, monkeypatch
):
    selected, paths, store, _ = native_case
    encrypted = paths["sessions"] / "account.session.enc"
    original = encrypted.read_bytes()

    def fail(*args):
        raise OSError("synthetic constructor failure")

    monkeypatch.setattr(user_client, "TelegramClient", fail)
    with pytest.raises(OSError):
        user_client.UserClientAdapter(
            api_id=123,
            api_hash="synthetic",
            store=store,
            policy=PolicyEngine(),
            session_path=paths["sessions"] / "account.session",
            encrypted_path=encrypted,
        )
    assert encrypted.read_bytes() == original
    assert not (paths["sessions"] / "account.session").exists()


async def test_initialization_failure_closes_decrypted_transport(native_case, monkeypatch):
    selected, paths, _, transports = native_case

    def fail(*args):
        raise RuntimeError("synthetic model initialization failure")

    monkeypatch.setattr(runtime, "make_ai_engine", fail)
    application = runtime.Application(settings=selected, admin_app_ready=lambda admin: None)
    with pytest.raises(RuntimeError, match="synthetic model initialization failure"):
        await application.run()
    assert transports[0].closed == 1
    assert not (paths["sessions"] / "account.session").exists()


async def test_cancel_during_native_connect_closes_session(native_case, monkeypatch):
    selected, paths, _, transports = native_case

    async def slow_connect(self):
        self.connected.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(TelegramTransport, "connect", slow_connect)
    application = runtime.Application(
        settings=selected,
        admin_app_ready=lambda admin: pytest.fail("Cancelled authentication callback"),
    )
    task = asyncio.create_task(application.run())
    while not transports:
        await asyncio.sleep(0)
    await transports[0].connected.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert transports[0].closed == 1
    assert not (paths["sessions"] / "account.session").exists()
    assert application.coingecko.client.is_closed


async def test_transport_failure_leaves_ready_runtime_and_closes_resources(
    native_case, monkeypatch
):
    selected, paths, _, transports = native_case
    attached = asyncio.Event()

    async def failed_bot(self):
        raise OSError("synthetic polling failed")

    monkeypatch.setattr(BotTransport, "run", failed_bot)
    application = runtime.Application(
        settings=selected, admin_app_ready=lambda admin: attached.set()
    )
    task = asyncio.create_task(application.run())
    await asyncio.wait_for(attached.wait(), timeout=5)
    try:
        with pytest.raises(RuntimeError, match="runtime_connection_lost"):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert transports[0].closed == 1 and application.bot.closed == 1
    assert not (paths["sessions"] / "account.session").exists()


async def test_vector_close_failure_still_seals_session_and_closes_clients(native_case):
    selected, paths, _, transports = native_case
    application = runtime.Application(settings=selected, admin_app_ready=lambda admin: None)
    application._initialize()

    def broken_close():
        raise OSError("synthetic vector close failure")

    application.rag.vectors = SimpleNamespace(close=broken_close)
    with pytest.raises(RuntimeError, match="runtime_cleanup_failed"):
        await application.close()
    assert transports[0].closed == 1
    assert application.coingecko.client.is_closed and application.ollama.client.is_closed
    assert not (paths["sessions"] / "account.session").exists()


async def test_failed_disconnect_still_seals_working_session(native_case, monkeypatch):
    selected, paths, store, transports = native_case
    application = runtime.Application(settings=selected, admin_app_ready=lambda admin: None)
    application._initialize()

    async def failed_disconnect():
        transports[0].closed += 1
        raise OSError("synthetic disconnect failure")

    monkeypatch.setattr(transports[0], "disconnect", failed_disconnect)
    with pytest.raises(RuntimeError, match="runtime_cleanup_failed"):
        await application.close()
    assert not (paths["sessions"] / "account.session").exists()
    assert (
        Fernet(store.get("session_encryption_key").encode()).decrypt(
            (paths["sessions"] / "account.session.enc").read_bytes()
        )
        == b"synthetic session content"
    )
    assert application.ollama.client.is_closed


def test_partial_decrypt_failure_removes_working_copy(native_case, monkeypatch):
    selected, paths, store, _ = native_case
    encrypted = paths["sessions"] / "account.session.enc"
    original = encrypted.read_bytes()

    def failed_decrypt(self, source, target):
        target.write_bytes(b"partial synthetic plaintext")
        raise OSError("synthetic disk-full decryption")

    monkeypatch.setattr(EncryptedSession, "decrypt_file", failed_decrypt)
    with pytest.raises(OSError):
        user_client.UserClientAdapter(
            api_id=123,
            api_hash="synthetic",
            store=store,
            policy=PolicyEngine(),
            session_path=paths["sessions"] / "account.session",
            encrypted_path=encrypted,
        )
    assert encrypted.read_bytes() == original
    assert not (paths["sessions"] / "account.session").exists()


async def test_failed_seal_preserves_previous_ciphertext_and_removes_working_copy(
    native_case, monkeypatch
):
    from pathlib import Path

    selected, paths, _, _ = native_case
    encrypted = paths["sessions"] / "account.session.enc"
    original = encrypted.read_bytes()
    application = runtime.Application(settings=selected, admin_app_ready=lambda admin: None)
    application._initialize()
    real_write = Path.write_bytes

    def failed_write(path, data):
        if path.parent == encrypted.parent and path.name != "account.session":
            real_write(path, data[:8])
            raise OSError("synthetic disk-full sealing")
        return real_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", failed_write)
    with pytest.raises(RuntimeError, match="runtime_cleanup_failed"):
        await application.close()
    assert encrypted.read_bytes() == original
    assert not (paths["sessions"] / "account.session").exists()


async def test_scheduler_writer_finalizer_finishes_before_clients_close(native_case):
    selected, _, _, transports = native_case
    application = runtime.Application(settings=selected, admin_app_ready=lambda admin: None)
    application._initialize()
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def writer():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release.wait()
            cleanup_finished.set()

    application.scheduler.start()
    application.scheduler.add_job(writer, "date")
    await asyncio.wait_for(started.wait(), timeout=2)
    owned = tuple(application.scheduler._executors["default"]._pending_futures)
    closing = asyncio.create_task(application.close())
    try:
        await asyncio.wait_for(cleanup_started.wait(), timeout=2)
        await asyncio.sleep(0)
        assert not closing.done(), "Writer cleanup outlived application close"
        assert transports[0].closed == 0 and not application.ollama.client.is_closed
        release.set()
        await asyncio.wait_for(closing, timeout=2)
        assert cleanup_finished.is_set() and transports[0].closed == 1
    finally:
        release.set()
        await asyncio.gather(closing, *owned, return_exceptions=True)


@pytest.mark.parametrize("cancelled", [False, True])
async def test_close_cancellation_and_concurrent_callers_wait_for_owned_cleanup(
    native_case, cancelled
):
    selected, paths, _, transports = native_case
    application = runtime.Application(settings=selected, admin_app_ready=lambda admin: None)
    application._initialize()
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowBot:
        closed = 0

        async def close(self):
            self.closed += 1
            started.set()
            await release.wait()

    application.bot = SlowBot()
    first = asyncio.create_task(application.close())
    await started.wait()
    second = asyncio.create_task(application.close())
    if cancelled:
        first.cancel()
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not second.done(), "Concurrent close returned before resource cleanup"
        assert transports[0].closed == 0
        release.set()
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await first
        else:
            await first
        await second
        assert transports[0].closed == application.bot.closed == 1
        assert not (paths["sessions"] / "account.session").exists()
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)


async def test_completed_assistant_removes_management_from_live_gateway(native_case, monkeypatch):
    import json

    from tg_assistant.desktop import worker

    selected, paths, store, transports = native_case
    original_application = runtime.Application
    attached = asyncio.Event()
    servers = []

    class StoppingApplication(original_application):
        def __init__(self, *, settings, admin_app_ready):
            def attach(admin):
                admin_app_ready(admin)
                attached.set()
                self.stopping.set()

            super().__init__(settings=settings, admin_app_ready=attach)

    def server(*args):
        assert not servers, "Native runtime opened a second listener"
        item = ACTUAL_UVICORN_SERVER(*args)
        servers.append(item)
        return item

    monkeypatch.setattr(runtime, "Application", StoppingApplication)
    monkeypatch.setattr(runtime.uvicorn, "Server", server)
    database = runtime.make_database(selected, store)
    with worker.reserve_loopback(0) as listener:
        port = listener.getsockname()[1]
        serving = asyncio.create_task(worker._serve(selected, paths, database, listener))
        try:
            await asyncio.wait_for(attached.wait(), timeout=5)
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", trust_env=False
            ) as client:
                deadline = asyncio.get_running_loop().time() + 2
                status = None
                while asyncio.get_running_loop().time() < deadline:
                    status = (await client.get("/api/v1/runtime/readiness")).json()
                    if status["capabilities"] == ["setup"]:
                        break
                    await asyncio.sleep(0.05)
                assert status["capabilities"] == ["setup"]
                assert status["code"] == "telegram_reconnect_required"
            state = json.loads((paths["config"] / "desktop-runtime.json").read_text())
            assert state["mode"] == "setup" and state["owner_id"] is None
        finally:
            state = json.loads((paths["config"] / "desktop-runtime.json").read_text())
            (paths["data"] / "stop.request").write_text(state["run_id"], encoding="ascii")
            await asyncio.wait_for(serving, timeout=5)
            await database.close()
    assert transports[0].closed == 1 and len(servers) == 1
