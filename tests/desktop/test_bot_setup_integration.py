"""Actual native setup stages with migrated SQLite and installed Bot API SDK.

Only the existing account SDK and credential backend are synthetic. The Bot API
uses aiohttp loopback HTTP on the same retained native account loop.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.client.telegram import TelegramAPIServer
from aiohttp import web
from sqlalchemy import select

from tg_assistant.config import Settings
from tg_assistant.db.models import AppSetting, TelegramAccount
from tg_assistant.desktop.bot_context import open_bot_context
from tg_assistant.desktop.instance import AlreadyRunning, InstanceGuard
from tg_assistant.desktop.setup_context import open_setup_context
from tg_assistant.services.bot_connection import BotConnectionService
from tg_assistant.services.telegram_publication import EncryptedSessionPublication
from tg_assistant.telegram.bot_api import BotApiTransport

_spec = importlib.util.spec_from_file_location(
    "o04_setup_bot_context_fixtures", Path(__file__).with_name("test_bot_context.py"),
)
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
migrated_system, account = _fixtures.system, _fixtures.account
OWNER = _fixtures._fixtures.OWNER
BOT = 900000001
TOKEN = "123456:SYNTHETIC_NATIVE_STAGE_TOKEN_ONLY"


@pytest.fixture
def system(migrated_system):
    """Open the real setup first so account and bot borrow its exact objects."""
    base = migrated_system
    settings = Settings(_env_file=None, data_dir=base.root, profile_id="publication-profile")
    setup = open_setup_context(settings, secret_store_factory=lambda: base.store)
    publications = []

    def make():
        publication = EncryptedSessionPublication(
            engine=setup.engine, profile_root=base.root, profile_id=settings.profile_id,
            maintenance=setup.fence, store=base.store,
            control_directory=base.root.parent / "native-control",
        )
        publications.append(publication)
        return publication

    try:
        yield SimpleNamespace(
            settings=settings, setup=setup, engine=setup.engine, fence=setup.fence,
            root=base.root, store=base.store, make=make,
        )
    finally:
        setup.close()
        for publication in publications:
            publication.close()


async def start_server():
    state = SimpleNamespace(updates=[], calls=[], loop=asyncio.get_running_loop())

    async def handle(request):
        assert asyncio.get_running_loop() is state.loop
        method = request.match_info["method"]
        form = dict(await request.post())
        state.calls.append((method, form))
        if method == "getMe":
            result = dict(id=BOT, is_bot=True, first_name="Fixture", username="fixture_bot")
        elif method == "getUpdates":
            offset = int(form.get("offset", "0"))
            result = [item for item in state.updates if item["update_id"] >= offset]
        elif method == "sendMessage":
            result = dict(message_id=9, date=1700000000,
                          chat=dict(id=int(form["chat_id"]), type="private"), text=form["text"])
        else:
            pytest.fail("Unexpected Bot API method in native setup integration")
        return web.json_response(dict(ok=True, result=result))

    app = web.Application()
    app.router.add_post("/bot{token}/{method}", handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    state.api = lambda: TelegramAPIServer.from_base(f"http://127.0.0.1:{port}")
    state.close = runner.cleanup
    return state


def test_native_bot_pairing_advances_only_actual_verified_setup_stages(account):
    value = account.system
    setup, native = value.setup, account.context
    assert native.engine is setup.engine and native.fence is setup.fence
    assert setup.begin().profile.setup_stage == "storage_ready"
    skipped = setup.skip_ai()
    assert skipped.profile.setup_stage == "ai_configured"
    assert skipped.profile.owner_id is None
    native.refresh()
    setup.install_telegram(native)
    assert native.verification() is not None
    server = native.runner.submit(start_server()).result(5)
    clients = []

    def transport(token):
        assert asyncio.get_running_loop() is server.loop is native.runner.loop
        assert token == TOKEN
        client = BotApiTransport._for_test(token, api_server_factory=server.api)
        clients.append(client)
        return client

    def service(repository, credentials, **options):
        return BotConnectionService(repository, credentials, _client_factory=transport, **options)

    bot = open_bot_context(
        value.settings, engine=setup.engine, fence=setup.fence, account_context=native,
        _credential_backend=_fixtures.MemoryCredentials(), _service_factory=service,
    )
    setup.install_bot(bot)
    guard = native.publication._guard
    try:
        assert bot.runner is native.runner
        assert bot.service.repository.engine is setup.engine
        with pytest.raises(AlreadyRunning):
            InstanceGuard(guard.directory).acquire()
        native.runner.submit(bot.service.connect(TOKEN)).result(10)
        checked = setup.advance_verified()
        assert checked.profile.setup_stage == "bot_verified"
        assert checked.profile.owner_id == OWNER
        assert "owner_paired" not in checked.stage_evidence_ids
        invitation = native.runner.submit(bot.service.issue_pairing()).result(10)

        async def pair_owner():
            server.updates = [dict(
                update_id=7,
                message={
                    "message_id": 3, "date": 1700000000,
                    "chat": dict(id=OWNER, type="private"),
                    "from": dict(id=OWNER, is_bot=False, first_name="Owner"),
                    "text": "/start " + invitation.payload,
                },
            )]
            assert await bot.service.poll_once() is True

        native.runner.submit(pair_owner()).result(10)
        result = setup.advance_verified()
        assert result.profile.setup_stage == "owner_paired"
        assert result.profile.owner_id == OWNER
        assert set(result.stage_evidence_ids) == {
            "welcome", "storage_ready", "ai_configured", "telegram_verified", "bot_verified", "owner_paired",
        }
        assert not {"source_selected", "first_answer", "ready"} & set(result.stage_evidence_ids)
        assert bot.service.pairing_verification() is not None
        repository = bot.service.repository
        with setup.engine.connect() as connection:
            state = connection.scalar(select(AppSetting.value).where(AppSetting.key == repository.key))
            onboarding = connection.scalar(select(AppSetting.value).where(
                AppSetting.key == setup.coordinator._key,
            ))
            rows = connection.execute(select(TelegramAccount)).mappings().all()
            all_settings = connection.scalars(select(AppSetting.value)).all()
        assert len(rows) == 1 and rows[0]["telegram_user_id"] == OWNER
        assert rows[0]["is_active"] is True and rows[0]["is_owner_paired"] is True
        assert state["challenge"]["state"] == "consumed"
        assert state["pairing"]["owner_id"] == str(OWNER)
        assert onboarding["profile_id"] == value.settings.profile_id
        pair_evidence = onboarding["evidence"][onboarding["completed"]["owner_paired"]]
        assert pair_evidence["verification"]["owner_id"] == str(OWNER)
        persisted = json.dumps(all_settings, ensure_ascii=False)
        assert all(secret not in persisted for secret in (TOKEN, invitation.payload, invitation.manual_code))
        assert len(state["challenge"]["nonce_hash"]) == len(state["challenge"]["manual_hash"]) == 64
        assert [method for method, _ in server.calls] == ["getMe", "getUpdates", "getUpdates", "sendMessage"]
        assert server.calls[-1][1]["text"] == "Ghép chủ sở hữu thành công."
        assert len(clients) == 1
    finally:
        setup.detach_bot()
        try:
            assert not guard.lock.closed and native.runner.thread.is_alive()
            assert native.service._client.is_connected() is True
            assert all(client._session._session.closed is True for client in clients)
        finally:
            native.runner.submit(server.close()).result(5)
            assert not guard.lock.closed
            setup.detach_telegram()
    assert setup.bot is None and setup.telegram is None
    assert native._released is True and not native.runner.thread.is_alive()
