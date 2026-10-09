"""Owning bot service integration against disposable SQLite and actual SDK HTTP."""

from __future__ import annotations

import asyncio
import os
import re
import time
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiogram.client.telegram import TelegramAPIServer
from aiohttp import web
from alembic.config import Config
from sqlalchemy import create_engine, event, insert, select, text

from alembic import command
from tg_assistant.db.base import configure_sqlite
from tg_assistant.db.models import AppSetting, TelegramAccount
from tg_assistant.desktop.instance import InstanceGuard
from tg_assistant.paths import current_user_sid, ensure_runtime_dirs
from tg_assistant.services.bot_connection import BotConnectionService, BotConnectionUnavailable
from tg_assistant.services.bot_credentials import ScopedBotCredentials
from tg_assistant.services.bot_pairing import AccountProof, PairingRepository
from tg_assistant.services.maintenance import MaintenanceService
from tg_assistant.telegram.bot_api import BotApiTransport

ROOT = Path(__file__).resolve().parents[2]
OWNER = 9007199254740993
BOT = 900000001
TOKEN = "123456:SYNTHETIC_SERVICE_TOKEN_CANARY_ONLY"


class MemoryCredentials:
    def __init__(self):
        self.values = {}
        self.calls = []
        self.fail_write = False

    def address(self, service, name):
        assert re.fullmatch(r"TelegramAIPersonalAssistant/bot/v1/[a-f0-9]{64}", service)
        assert re.fullmatch(r"telegram_bot_token/[a-f0-9]{32}", name)
        self.calls.append((service, name))
        return service, name

    def set_password(self, service, name, value):
        address = self.address(service, name)
        if self.fail_write:
            raise RuntimeError(TOKEN)
        self.values[address] = value

    def get_password(self, service, name):
        return self.values.get(self.address(service, name))

    def delete_password(self, service, name):
        self.values.pop(self.address(service, name), None)


@pytest.fixture
def system(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("TG_ASSISTANT_"):
            monkeypatch.delenv(name)
    profile = "service-owner-profile"
    paths = ensure_runtime_dirs(tmp_path / "profile", profile_id=profile)
    engine = create_engine("sqlite:///" + str(paths["db"] / "assistant.sqlite3"))
    event.listen(engine, "connect", configure_sqlite)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    with engine.begin() as connection:
        connection.execute(insert(TelegramAccount).values(
            telegram_user_id=OWNER, is_active=True, is_owner_paired=False,
        ))
    guard = InstanceGuard(tmp_path / "synthetic-owner").acquire()
    fence = MaintenanceService(paths["config"], profile_id=profile)
    proof = [AccountProof(OWNER, "a" * 64)]
    clock = [datetime.now(UTC) + timedelta(seconds=5)]
    monotonic = [time.monotonic()]
    live = [True]

    def ownership():
        assert live[0] and guard.lock and not guard.lock.closed
        observed = (guard.directory / "runtime.lock").stat()
        handle = os.fstat(guard.lock.file.fileno())
        assert (observed.st_dev, observed.st_ino) == (handle.st_dev, handle.st_ino)

    repo = PairingRepository(
        engine=engine, profile_id=profile, profile_root=paths["data"], fence=fence,
        check_ownership=ownership, account_verifier=lambda: proof[0], now=lambda: clock[0],
    )
    backend = MemoryCredentials()
    credentials = ScopedBotCredentials(
        profile, backend, ownership, repo.current_reference, current_user_sid,
        publication_guard=repo.publication_guard, candidate_unpublished=repo.candidate_unpublished,
    )

    def durable():
        with engine.connect() as connection:
            return (
                connection.scalar(select(AppSetting.value).where(AppSetting.key == repo.key)),
                connection.scalar(select(TelegramAccount.is_owner_paired)),
            )

    try:
        yield SimpleNamespace(repo=repo, credentials=credentials, backend=backend, clock=clock,
                              monotonic=monotonic, proof=proof, live=live, guard=guard,
                              fence=fence, engine=engine, durable=durable)
    finally:
        fence.close()
        engine.dispose()
        guard.close()


@pytest_asyncio.fixture
async def server():
    state = dict(status=200, poll_status=200, reply_status=200, reply_delay=False, identity=dict(id=BOT, is_bot=True, first_name="Synthetic", username="fixture_bot"),
                 updates=[], calls=[], delay=False, entered=asyncio.Event(), reply_entered=asyncio.Event(), release=asyncio.Event())

    async def handle(request):
        method = request.match_info["method"]
        state["calls"].append((method, dict(await request.post())))
        state["entered"].set()
        if method == "sendMessage":
            state["reply_entered"].set()
        if state["delay"] or (method == "sendMessage" and state["reply_delay"]):
            await state["release"].wait()
        status = state["reply_status"] if method == "sendMessage" else state["status"]
        if method == "getUpdates" and status == 200:
            status = state["poll_status"]
        if status != 200:
            return web.json_response(dict(ok=False, error_code=status, description=TOKEN), status=status)
        if method == "sendMessage":
            fields = state["calls"][-1][1]
            result = dict(message_id=3, date=1700000000,
                          chat=dict(id=int(fields["chat_id"]), type="private"), text=fields["text"])
        else:
            result = state["identity"] if method == "getMe" else state["updates"]
        return web.json_response(dict(ok=True, result=result))

    app = web.Application()
    app.router.add_post("/bot{token}/{method}", handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield SimpleNamespace(state=state, api=lambda: TelegramAPIServer.from_base(f"http://127.0.0.1:{port}"))
    finally:
        state["release"].set()
        await runner.cleanup()


@pytest_asyncio.fixture
async def service(system, server):
    value = BotConnectionService(
        system.repo, system.credentials, now=lambda: system.clock[0], monotonic=lambda: system.monotonic[0],
        _client_factory=lambda token: BotApiTransport._for_test(
            token, api_server_factory=server.api, request_timeout=0.2,
        ),
    )
    try:
        yield value
    finally:
        system.live[0] = True
        await value.close()


def wire_update(text_value, *, sender=OWNER, chat_type="private", is_bot=False, update_id=7):
    return dict(update_id=update_id, message=dict(
        message_id=2, date=1700000000, chat=dict(id=sender, type=chat_type),
        **{"from": dict(id=sender, is_bot=is_bot, first_name="Synthetic")}, text=text_value,
    ))


def sanitized(error):
    assert error.__cause__ is None and error.__context__ is None
    assert TOKEN not in "".join(traceback.format_exception(error))
    assert "http://" not in str(error)


@pytest.mark.asyncio
async def test_connect_measures_real_identity_then_current_slot_and_initial_poll(service, system, server):
    assert service.verification() is None
    assert service.connection_status().checked_at is None
    result = await service.connect(TOKEN)
    assert result.bot_id == BOT and service.pairing_username() == "fixture_bot"
    assert [name for name, _ in server.state["calls"]] == ["getMe", "getUpdates"]
    document, paired = system.durable()
    assert document["enrollment"]["bot_id"] == str(BOT) and paired is False
    assert system.credentials.get(document["enrollment"]["credential_slot"]) == TOKEN
    assert service.verification().owner_id == OWNER
    assert service.pairing_verification() is None
    assert service.connection_status().state == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("manual", [False, True])
async def test_actual_private_owner_update_commits_once_before_pair_proof(service, system, server, manual):
    await service.connect(TOKEN)
    invite = await service.issue_pairing()
    assert len(invite.payload) == 48
    assert invite.expires_at == system.clock[0] + timedelta(seconds=300)
    assert invite.payload not in repr(invite) and invite.manual_code not in repr(invite)
    command_text = "/pair " + invite.manual_code if manual else "/start " + invite.payload
    server.state["updates"] = [wire_update(command_text)]
    assert await service.poll_once() is True
    document, paired = system.durable()
    assert document["challenge"]["state"] == "consumed" and paired is True
    assert document["pairing"]["actual_update_id"] == 7
    assert service.pairing_verification().owner_id == OWNER
    revision = document["revision"]
    assert await service.poll_once() is False
    assert system.durable()[0]["revision"] == revision
    public = service.connection_status().model_dump_json()
    assert TOKEN not in public and invite.payload not in public and invite.manual_code not in public
    assert server.state["calls"][-1][1]["offset"] == "8"


@pytest.mark.asyncio
async def test_stranger_noise_batch_cannot_drop_owner_pair_or_success_reply(service, system, server):
    await service.connect(TOKEN)
    invite = await service.issue_pairing()
    value = "/start " + invite.payload
    server.state["updates"] = [wire_update(value, sender=42, update_id=index) for index in range(1, 9)]
    server.state["updates"].append(wire_update(value, update_id=9))
    assert await service.poll_once() is True
    assert system.durable()[1] is True
    replies = [fields for method, fields in server.state["calls"] if method == "sendMessage"]
    assert len(replies) == 5
    assert replies[-1]["chat_id"] == str(OWNER)
    assert replies[-1]["text"] == "Ghép chủ sở hữu thành công."


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["stranger", "group", "bot", "callback", "edited", "bare", "suffix", "oversized"])
async def test_wrong_update_never_consumes_current_challenge(service, system, server, change):
    await service.connect(TOKEN)
    invite = await service.issue_pairing()
    update = wire_update("/start " + invite.payload)
    if change == "stranger":
        update = wire_update("/start " + invite.payload, sender=42)
    elif change == "group":
        update = wire_update("/start " + invite.payload, chat_type="group")
    elif change == "bot":
        update = wire_update("/start " + invite.payload, is_bot=True)
    elif change == "callback":
        update = dict(update_id=7)
    elif change == "edited":
        update["edited_message"] = update.pop("message")
    elif change == "bare":
        update["message"]["text"] = "/start"
    elif change == "suffix":
        update["message"]["text"] += " more"
    else:
        update["message"]["text"] = "/start " + "x" * 400
    server.state["updates"] = [update]
    assert await service.poll_once() is False
    assert system.durable()[0]["challenge"]["state"] == "pending"
    assert service.pairing_verification() is None


@pytest.mark.asyncio
async def test_sql_failure_cannot_publish_pair_success(service, system, server):
    await service.connect(TOKEN)
    invite = await service.issue_pairing()
    with system.engine.begin() as connection:
        connection.execute(text("CREATE TRIGGER fail_pair BEFORE UPDATE ON telegram_accounts BEGIN SELECT RAISE(ABORT, 'synthetic'); END"))
    server.state["updates"] = [wire_update("/start " + invite.payload)]
    with pytest.raises(BotConnectionUnavailable) as caught:
        await service.poll_once()
    sanitized(caught.value)
    assert system.durable()[1] is False
    assert system.durable()[0]["challenge"]["state"] == "pending"
    assert service.pairing_verification() is None


@pytest.mark.asyncio
async def test_invalid_getme_allocates_no_credentials_or_enrollment(service, system, server):
    server.state["identity"]["id"] = True
    with pytest.raises(BotConnectionUnavailable) as caught:
        await service.connect(TOKEN)
    sanitized(caught.value)
    assert system.backend.calls == [] and system.durable()[0] is None
    assert service.verification() is None


@pytest.mark.asyncio
async def test_failed_candidate_write_preserves_prior_durable_enrollment(service, system):
    await service.connect(TOKEN)
    before = system.durable()
    system.backend.fail_write = True
    with pytest.raises(BotConnectionUnavailable) as caught:
        await service.connect("654321:SECOND_SYNTHETIC_SERVICE_TOKEN_ONLY")
    sanitized(caught.value)
    assert system.durable() == before
    assert list(system.backend.values.values()) == [TOKEN]
    assert service.verification() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(401, "bot_token_revoked"), (409, "bot_polling_conflict"), (500, "bot_unavailable")])
async def test_actual_http_error_withdraws_proof_and_revocation_is_durable(service, system, server, status, code):
    await service.connect(TOKEN)
    invite = await service.issue_pairing()
    server.state["updates"] = [wire_update("/start " + invite.payload)]
    await service.poll_once()
    slot = system.durable()[0]["enrollment"]["credential_slot"]
    server.state["status"] = status
    with pytest.raises(BotConnectionUnavailable) as caught:
        await service.poll_once()
    assert caught.value.code == code
    sanitized(caught.value)
    assert service.verification() is None and service.pairing_verification() is None
    assert service.connection_status().state != "ready"
    if status == 401:
        assert system.durable()[0]["enrollment"] is None and system.durable()[1] is False
    else:
        assert system.durable()[0]["enrollment"]["credential_slot"] == slot
    assert TOKEN in system.backend.values.values()
    assert system.repo.candidate_unpublished(slot) is False


@pytest.mark.asyncio
async def test_original_age_and_owning_account_are_required_on_every_read(service, system):
    await service.connect(TOKEN)
    checked = service.connection_status().checked_at
    system.monotonic[0] += 30
    assert service.verification() is None and service.pairing_verification() is None
    assert service.connection_status().checked_at == checked
    system.monotonic[0] -= 30
    system.proof[0] = None
    assert service.verification() is None
    assert service.connection_status().state != "ready"


@pytest.mark.asyncio
async def test_monotonic_expiry_wins_over_wall_clock_rollback(service, system, server):
    await service.connect(TOKEN)
    invite = await service.issue_pairing()
    system.monotonic[0] += 300
    system.clock[0] += timedelta(seconds=10)
    await service.resume()  # Measured refresh does not extend an invitation.
    server.state["updates"] = [wire_update("/start " + invite.payload)]
    assert await service.poll_once() is False
    assert system.durable()[1] is False
    assert service.pairing_verification() is None


@pytest.mark.asyncio
async def test_periodic_resume_preserves_same_client_cursor_and_live_invitation(service, system, server):
    await service.connect(TOKEN)
    invite = await service.issue_pairing()
    client = service._client
    server.state["updates"] = [wire_update("/start", update_id=10)]
    await service.poll_once()
    system.monotonic[0] += 20
    system.clock[0] = datetime.now(UTC) + timedelta(seconds=5)
    await service.resume()
    assert service._client is client
    assert system.durable()[0]["challenge"]["state"] == "pending"
    assert system.durable()[0]["challenge"]["generation"] == invite.generation
    assert server.state["calls"][-1][1]["offset"] == "11"
    server.state["updates"] = [wire_update("/start " + invite.payload, update_id=11)]
    assert await service.poll_once() is True


@pytest.mark.asyncio
async def test_first_resume_explicitly_abandons_prior_process_pending(service, system, server):
    await service.connect(TOKEN)
    invite = await service.issue_pairing()
    await service.close()
    restarted = BotConnectionService(
        system.repo, system.credentials, now=lambda: system.clock[0], monotonic=lambda: system.monotonic[0],
        _client_factory=lambda token: BotApiTransport._for_test(token, api_server_factory=server.api),
    )
    try:
        await restarted.resume()
        assert system.durable()[0]["challenge"]["state"] == "abandoned"
        server.state["updates"] = [wire_update("/start " + invite.payload)]
        assert await restarted.poll_once() is False
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_cancel_invalidates_exact_current_invitation(service, system, server):
    await service.connect(TOKEN)
    invite = await service.issue_pairing()
    await service.cancel_pairing()
    assert system.durable()[0]["challenge"]["state"] == "cancelled"
    server.state["updates"] = [wire_update("/start " + invite.payload)]
    assert await service.poll_once() is False


@pytest.mark.asyncio
async def test_close_cancels_poll_drains_sdk_and_preserves_borrowed_owner(service, system, server):
    await service.connect(TOKEN)
    sdk_session = service._client._session._session
    server.state["entered"].clear()
    server.state["delay"] = True
    polling = asyncio.create_task(service.poll_once())
    await asyncio.wait_for(server.state["entered"].wait(), 1)
    await service.close()
    with pytest.raises(asyncio.CancelledError):
        await polling
    assert sdk_session.closed and service.verification() is None
    assert system.guard.lock and not system.guard.lock.closed
    calls = len(server.state["calls"])
    with pytest.raises(BotConnectionUnavailable):
        await service.resume()
    assert len(server.state["calls"]) == calls


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "unavailable", "revoked"])
async def test_committed_pair_reply_failure_advances_cursor_and_never_resends(service, system, server, failure):
    await service.connect(TOKEN)
    invite = await service.issue_pairing()
    server.state["updates"] = [wire_update("/start " + invite.payload)]
    if failure == "timeout":
        server.state["reply_delay"] = True
    else:
        server.state["reply_status"] = 401 if failure == "revoked" else 500
    assert await service.poll_once() is True
    assert service._cursor == 8
    assert service.verification() is None and service.pairing_verification() is None
    assert len([name for name, _ in server.state["calls"] if name == "sendMessage"]) == 1
    if failure == "revoked":
        assert system.durable()[0]["enrollment"] is None and system.durable()[1] is False
    else:
        assert system.durable()[0]["challenge"]["state"] == "consumed" and system.durable()[1] is True
        server.state["reply_delay"], server.state["reply_status"] = False, 200
        await service.resume()
        assert service.pairing_verification() is not None
        assert len([name for name, _ in server.state["calls"] if name == "sendMessage"]) == 1


@pytest.mark.asyncio
async def test_reply_admission_withdrawn_during_first_http_reply_blocks_following_send(service, system, server):
    await service.connect(TOKEN)
    server.state["updates"] = [wire_update("/help", update_id=10), wire_update("/help", update_id=11)]
    server.state["reply_delay"] = True
    polling = asyncio.create_task(service.poll_once())
    try:
        await asyncio.wait_for(server.state["reply_entered"].wait(), 1)
        system.proof[0] = None
        server.state["reply_delay"] = False
        server.state["release"].set()
        with pytest.raises(BotConnectionUnavailable) as caught:
            await polling
        assert caught.value.code == "bot_account_required"
        sanitized(caught.value)
        assert len([name for name, _ in server.state["calls"] if name == "sendMessage"]) == 1
        assert service.verification() is None and service.pairing_verification() is None
        assert system.durable()[1] is False
    finally:
        server.state["release"].set()
        if not polling.done():
            polling.cancel()
        await asyncio.gather(polling, return_exceptions=True)


@pytest.mark.asyncio
async def test_reply_admission_withdrawn_after_pair_commit_preserves_sql_cursor_without_send(service, system, server, monkeypatch):
    await service.connect(TOKEN)
    invite = await service.issue_pairing()
    server.state["updates"] = [wire_update("/start " + invite.payload)]
    original = system.repo.consume

    def committed_then_withdrawn(*args, **kwargs):
        consumed = original(*args, **kwargs)
        if consumed:
            system.proof[0] = None
        return consumed

    monkeypatch.setattr(system.repo, "consume", committed_then_withdrawn)
    with pytest.raises(BotConnectionUnavailable) as caught:
        await service.poll_once()
    assert caught.value.code == "bot_account_required"
    sanitized(caught.value)
    assert system.durable()[0]["challenge"]["state"] == "consumed" and system.durable()[1] is True
    assert service._cursor == 8
    assert not any(name == "sendMessage" for name, _ in server.state["calls"])
    assert service.pairing_verification() is None
    system.proof[0] = AccountProof(OWNER, "a" * 64)
    await service.resume()
    assert service.pairing_verification() is not None
    assert not any(name == "sendMessage" for name, _ in server.state["calls"])


@pytest.mark.asyncio
async def test_publication_uncertainty_never_deletes_historically_published_slot(service, system, monkeypatch):
    original = system.repo.publish_enrollment

    def committed_then_failed(**kwargs):
        original(**kwargs)
        raise RuntimeError(TOKEN)

    monkeypatch.setattr(system.repo, "publish_enrollment", committed_then_failed)
    with pytest.raises(BotConnectionUnavailable) as caught:
        await service.connect(TOKEN)
    sanitized(caught.value)
    reference = system.durable()[0]["enrollment"]["credential_slot"]
    assert system.repo.candidate_unpublished(reference) is False
    assert system.credentials.get(reference) == TOKEN
    await service.close()
    assert TOKEN in system.backend.values.values()


@pytest.mark.asyncio
async def test_initial_poll_failure_never_produces_bot_stage_proof(service, system, server):
    server.state["poll_status"] = 409
    with pytest.raises(BotConnectionUnavailable) as caught:
        await service.connect(TOKEN)
    assert caught.value.code == "bot_polling_conflict"
    sanitized(caught.value)
    assert system.durable()[0]["enrollment"] is not None
    assert service.verification() is None and service.pairing_verification() is None


@pytest.mark.asyncio
async def test_monotonic_expiry_rechecked_inside_real_pair_commit(service, system, server, monkeypatch):
    await service.connect(TOKEN)
    invite = await service.issue_pairing()
    server.state["updates"] = [wire_update("/start " + invite.payload)]
    original = system.repo.consume

    def advance_before_commit(*args, **kwargs):
        check = kwargs["_commit_check"]

        def delayed_check():
            system.monotonic[0] += 300
            check()

        kwargs["_commit_check"] = delayed_check
        return original(*args, **kwargs)

    monkeypatch.setattr(system.repo, "consume", advance_before_commit)
    with pytest.raises(BotConnectionUnavailable):
        await service.poll_once()
    assert system.durable()[0]["challenge"]["state"] == "pending"
    assert system.durable()[1] is False
    assert not any(name == "sendMessage" for name, _ in server.state["calls"])


@pytest.mark.asyncio
async def test_candidate_change_cancels_pending_invitation_without_owner_adoption(service, system, server):
    await service.connect(TOKEN)
    invite = await service.issue_pairing()
    system.proof[0] = AccountProof(OWNER, "b" * 64)
    assert service.verification() is None
    await service.resume()
    assert system.durable()[0]["challenge"]["state"] == "cancelled"
    server.state["updates"] = [wire_update("/start " + invite.payload)]
    assert await service.poll_once() is False
    assert system.durable()[1] is False


@pytest.mark.asyncio
async def test_durable_generation_change_immediately_denies_metadata(service, system):
    await service.connect(TOKEN)
    old = system.durable()[0]["enrollment"]["generation"]
    system.repo.invalidate_enrollment(old, reason="bot_disconnected")
    assert service.verification() is None and service.pairing_verification() is None
    assert service.connection_status().state != "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["withdraw", "client", "time"])
async def test_bot_proof_rechecks_current_observation_after_real_durable_read(service, system, server, monkeypatch, mutation):
    await service.connect(TOKEN)
    assert service.verification() is not None
    original_read, original_client = system.repo.read_admission, service._client
    replacement = BotApiTransport._for_test(TOKEN, api_server_factory=server.api)

    def read_then_change_observation():
        document = original_read()
        if mutation == "withdraw":
            service._withdraw("bot_unavailable")
        elif mutation == "client":
            service._client = replacement
        else:
            system.monotonic[0] += 30
        return document

    monkeypatch.setattr(system.repo, "read_admission", read_then_change_observation)
    try:
        assert service.verification() is None
    finally:
        service._client = original_client
        await replacement.close()


@pytest.mark.asyncio
async def test_service_foreign_loop_and_lost_owner_cannot_start_http(service, system, server):
    await service.connect(TOKEN)
    count = len(server.state["calls"])

    def foreign_loop():
        async def attempt():
            with pytest.raises(BotConnectionUnavailable):
                await service.poll_once()
            with pytest.raises(BotConnectionUnavailable):
                await service.close()

        asyncio.run(attempt())

    await asyncio.to_thread(foreign_loop)
    system.live[0] = False
    with pytest.raises(BotConnectionUnavailable) as caught:
        await service.poll_once()
    assert caught.value.code == "bot_account_required"
    sanitized(caught.value)
    assert len(server.state["calls"]) == count


async def management_service(native, system, server):
    await native.connect(TOKEN)
    invite = await native.issue_pairing()
    server.state["updates"] = [wire_update("/start " + invite.payload)]
    assert await native.poll_once()
    await native.close()
    return BotConnectionService(
        system.repo, system.credentials, now=lambda: system.clock[0], monotonic=lambda: system.monotonic[0],
        _management_mode=True, _client_factory=lambda token: BotApiTransport._for_test(
            token, api_server_factory=server.api, request_timeout=0.2,
        ),
    )


@pytest.mark.asyncio
async def test_management_initial_batch_preserves_callback_without_dispatch_or_ack(service, system, server):
    managed = await management_service(service, system, server)
    server.state["updates"] = [wire_update("/ask synthetic", update_id=100), dict(
        update_id=101, callback_query=dict(id="fixture", chat_instance="fixture",
                                        **{"from": dict(id=OWNER, is_bot=False, first_name="Synthetic")},
                                        data="synthetic_action"),
    )]
    start = len(server.state["calls"])
    try:
        await managed.resume()
        assert [name for name, _ in server.state["calls"][start:]] == ["getMe", "getUpdates"]
        assert server.state["calls"][-1][1]["allowed_updates"] == '["message", "callback_query"]'
        assert managed._cursor is None
        sdk = managed.bot
        checked = managed.connection_status().checked_at
        system.monotonic[0] += 20
        await managed.resume()
        assert managed.bot is sdk and managed.connection_status().checked_at == checked
        assert [name for name, _ in server.state["calls"][start:]] == ["getMe", "getUpdates", "getMe"]
        pending = await managed.poll_management_once()
        assert [value.update_id for value in pending] == [100, 101]
        assert pending[1].callback_query.data == "synthetic_action"
        for invalid in (True, 101, 999):
            with pytest.raises(BotConnectionUnavailable):
                managed.acknowledge_update(invalid)
        assert managed._cursor is None
        managed.acknowledge_update(100)
        managed.acknowledge_update(101)
        with pytest.raises(BotConnectionUnavailable):
            managed.acknowledge_update(101)
        server.state["updates"] = []
        assert await managed.poll_management_once() == []
        assert server.state["calls"][-1][1]["offset"] == "102"
        assert not any(name == "sendMessage" for name, _ in server.state["calls"][start:])
    finally:
        await managed.close()


@pytest.mark.asyncio
async def test_management_unacknowledged_batch_blocks_another_sdk_poll(service, system, server):
    managed = await management_service(service, system, server)
    server.state["updates"] = [wire_update("/ask synthetic", update_id=100)]
    try:
        await managed.resume()
        await managed.poll_management_once()
        before = len(server.state["calls"])
        with pytest.raises(BotConnectionUnavailable) as caught:
            await managed.poll_management_once()
        assert caught.value.code == "bot_polling_conflict"
        assert len(server.state["calls"]) == before and managed._cursor is None
        assert managed._ack_pending == [100]
    finally:
        await managed.close()


@pytest.mark.asyncio
async def test_same_offset_health_repoll_preserves_unacked_batch_and_queues_only_new_ids(service, system, server):
    managed = await management_service(service, system, server)
    server.state["updates"] = [wire_update("/ask original", update_id=100)]
    try:
        await managed.resume()
        issued = await managed.poll_management_once()
        checked = managed.connection_status().checked_at
        system.monotonic[0] += 20
        await managed.resume()
        server.state["updates"] = [wire_update("/ask duplicate", update_id=100), wire_update("/ask next", update_id=101)]
        await managed.refresh_pending_poll()
        assert issued[0].message.text == "/ask original"
        assert managed._ack_pending == [100] and managed._cursor is None
        assert [value.update_id for value in managed._pending_updates] == [101]
        assert "offset" not in server.state["calls"][-1][1]
        assert managed.connection_status().checked_at > checked
        before = len(server.state["calls"])
        managed.acknowledge_update(100)
        assert [value.update_id for value in await managed.poll_management_once()] == [101]
        assert len(server.state["calls"]) == before
        managed.acknowledge_update(101)
        assert managed._cursor == 102
    finally:
        await managed.close()


@pytest.mark.asyncio
async def test_same_offset_health_queue_overflow_fails_closed_without_ack_or_drop(service, system, server):
    managed = await management_service(service, system, server)
    server.state["updates"] = [wire_update("/ask original", update_id=value) for value in range(100, 200)]
    try:
        await managed.resume()
        issued = await managed.poll_management_once()
        assert len(issued) == 100
        server.state["updates"] = [wire_update("/ask excess", update_id=200)]
        with pytest.raises(BotConnectionUnavailable):
            await managed.refresh_pending_poll()
        assert managed._ack_pending == list(range(100, 200))
        assert managed._pending_updates == [] and managed._cursor is None
        assert managed.verification() is None
    finally:
        await managed.close()


@pytest.mark.asyncio
async def test_management_mode_cannot_load_unpaired_token_or_construct_new_enrollment(service, system, server):
    await service.connect(TOKEN)
    managed = BotConnectionService(system.repo, system.credentials, _management_mode=True)
    try:
        before = len(server.state["calls"])
        with pytest.raises(BotConnectionUnavailable):
            await managed.resume()
        with pytest.raises(BotConnectionUnavailable):
            await managed.connect(TOKEN)
        assert len(server.state["calls"]) == before and managed._client is None
    finally:
        await managed.close()
