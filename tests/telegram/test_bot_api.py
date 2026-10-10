"""PRIVATE actual installed-SDK loopback HTTP transport regressions."""

import asyncio
import traceback
from datetime import UTC
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import Update
from aiohttp import web

from tg_assistant.telegram import bot_api as api

TOKEN = "123456:PRIVATE_TOKEN_CANARY_SYNTHETIC_ONLY"  # noqa: S105 - synthetic loopback fixture only


@pytest_asyncio.fixture
async def server():
    state = {
        "status": 200, "success_status": 200, "delay": False, "malformed": False,
        "redirect": False, "destination_calls": [],
        "identity": {"id": 900000001, "is_bot": True, "first_name": "Synthetic", "username": "fixture_bot"},
        "updates": [], "calls": [], "entered": asyncio.Event(), "release": asyncio.Event(),
    }

    async def destination(request):
        state["destination_calls"].append(request.method)
        return web.json_response({"ok": True, "result": state["identity"]})

    destination_app = web.Application()
    destination_app.router.add_route("*", "/target", destination)
    destination_runner = web.AppRunner(destination_app, access_log=None)
    await destination_runner.setup()
    destination_site = web.TCPSite(destination_runner, "127.0.0.1", 0)
    await destination_site.start()
    destination_port = destination_site._server.sockets[0].getsockname()[1]

    async def handle(request):
        method = request.match_info["method"]
        state["calls"].append((method, dict(await request.post())))
        state["entered"].set()
        if state["redirect"]:
            return web.Response(status=302, headers={
                "Location": f"http://127.0.0.1:{destination_port}/target",
            })
        if state["delay"]:
            await state["release"].wait()
        if state["malformed"]:
            return web.Response(text="PRIVATE_TOKEN_CANARY_SYNTHETIC_ONLY malformed")
        if state["status"] != 200:
            return web.json_response({
                "ok": False, "error_code": state["status"],
                "description": f"PRIVATE_TOKEN_CANARY_SYNTHETIC_ONLY {request.url}",
            }, status=state["status"])
        return web.json_response({
            "ok": True, "result": state["identity"] if method == "getMe" else state["updates"],
        }, status=state["success_status"])

    app = web.Application()
    app.router.add_post("/bot{token}/{method}", handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    value = SimpleNamespace(
        state=state, factory=lambda: TelegramAPIServer.from_base(f"http://127.0.0.1:{port}"),
    )
    try:
        yield value
    finally:
        state["release"].set()
        await runner.cleanup()
        await destination_runner.cleanup()


@pytest_asyncio.fixture
async def transport(server):
    value = api.BotApiTransport._for_test(TOKEN, api_server_factory=server.factory, request_timeout=0.15)
    try:
        yield value
    finally:
        await value.close()


@pytest.mark.asyncio
async def test_get_me_uses_actual_sdk_response_identity_and_success_timestamp(transport, server):
    assert transport.identity_checked_at is None
    identity = await transport.verify_identity()
    assert identity == api.BotIdentity(900000001, "fixture_bot")
    assert identity.bot_id != 123456  # Token prefix is not returned identity evidence.
    assert transport.identity_checked_at.tzinfo is UTC
    assert [name for name, _ in server.state["calls"]] == ["getMe"]
    assert TOKEN not in repr(transport)


@pytest.mark.asyncio
async def test_empty_poll_is_actual_success_without_automatic_cursor(transport, server):
    assert transport.poll_checked_at is None
    assert await transport.poll_once() == []
    assert transport.poll_checked_at.tzinfo is UTC
    name, fields = server.state["calls"][0]
    assert name == "getUpdates"
    assert fields == {"limit": "100", "timeout": "0", "allowed_updates": '["message"]'}


@pytest.mark.asyncio
async def test_management_poll_uses_same_owned_sdk_with_explicit_callback_allowlist(transport, server):
    assert transport.bot is transport._bot
    assert await transport.poll_once(allowed_updates=("message", "callback_query")) == []
    assert server.state["calls"][0][1]["allowed_updates"] == '["message", "callback_query"]'


@pytest.mark.parametrize("allowed", [
    ["message"], (), ("callback_query",), ("message", "message"),
    ("message", "edited_message"), ("message", []), (True,), True,
])
@pytest.mark.asyncio
async def test_poll_rejects_noncanonical_allowlists_without_http(transport, server, allowed):
    with pytest.raises(api.BotApiUnavailable):
        await transport.poll_once(allowed_updates=allowed)
    assert server.state["calls"] == []


@pytest.mark.asyncio
async def test_borrowed_sdk_unavailable_after_close(transport):
    await transport.close()
    with pytest.raises(api.BotApiUnavailable):
        _ = transport.bot


@pytest.mark.asyncio
async def test_poll_returns_actual_sdk_update_and_passes_exact_cursor(transport, server):
    server.state["updates"] = [{
        "update_id": 17, "message": {
            "message_id": 2, "date": 1700000000, "chat": {"id": 999, "type": "private"},
            "from": {"id": 999, "is_bot": False, "first_name": "Synthetic"}, "text": "/start synthetic",
        },
    }]
    values = await transport.poll_once(cursor=18)
    assert len(values) == 1 and isinstance(values[0], Update)
    assert values[0].update_id == 17
    assert values[0].message.from_user.id == 999
    assert server.state["calls"][0][1]["offset"] == "18"
    assert len(server.state["calls"]) == 1


@pytest.mark.parametrize("change", [
    {"id": True}, {"id": "123"}, {"id": 0}, {"id": -2}, {"is_bot": False},
    {"is_bot": "true"}, {"username": None}, {"username": ""}, {"username": "x/y"},
    {"username": "á_bot"}, {"username": "x" * 33}, {"username": "@fixture_bot"},
])
@pytest.mark.asyncio
async def test_invalid_wire_identity_never_qualifies(transport, server, change):
    server.state["identity"].update(change)
    with pytest.raises(api.BotApiUnavailable) as caught:
        await transport.verify_identity()
    assert caught.value.code == "bot_unavailable"
    assert transport.identity is None and transport.identity_checked_at is None


@pytest.mark.parametrize("status,code", [(401, "bot_token_revoked"), (409, "bot_polling_conflict"), (500, "bot_unavailable")])
@pytest.mark.parametrize("operation", ["verify_identity", "poll_once"])
@pytest.mark.asyncio
async def test_http_failure_is_fixed_and_drops_measurement(transport, server, status, code, operation):
    await transport.verify_identity()
    await transport.poll_once()
    server.state["status"] = status
    with pytest.raises(api.BotApiUnavailable) as caught:
        await getattr(transport, operation)()
    assert caught.value.code == code
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    reporter = "".join(traceback.format_exception(caught.value))
    assert TOKEN not in reporter and "http://" not in reporter
    if operation == "verify_identity" or status == 401:
        assert transport.identity is None and transport.identity_checked_at is None
    if operation == "poll_once" or status in {401, 409}:
        assert transport.poll_checked_at is None


@pytest.mark.parametrize("operation", ["verify_identity", "poll_once"])
@pytest.mark.asyncio
async def test_timeout_is_bounded_and_never_stamps_success(transport, server, operation):
    server.state["delay"] = True
    with pytest.raises(api.BotApiUnavailable) as caught:
        await asyncio.wait_for(getattr(transport, operation)(), 1)
    assert caught.value.code == "bot_timeout"
    assert transport.identity_checked_at is None and transport.poll_checked_at is None
    assert len(server.state["calls"]) == 1


@pytest.mark.parametrize("operation", ["verify_identity", "poll_once"])
@pytest.mark.asyncio
async def test_malformed_response_is_sanitized(transport, server, operation):
    server.state["malformed"] = True
    with pytest.raises(api.BotApiUnavailable) as caught:
        await getattr(transport, operation)()
    assert caught.value.code == "bot_unavailable"
    assert caught.value.__context__ is None
    assert TOKEN not in "".join(traceback.format_exception(caught.value))


@pytest.mark.asyncio
async def test_borrowed_sdk_reply_parser_error_is_sanitized_at_session_boundary(transport, server):
    server.state["malformed"] = True
    with pytest.raises(api.BotApiUnavailable) as caught:
        await transport.bot.send_message(chat_id=999, text="Sanitized setup guidance")
    assert caught.value.code == "bot_unavailable"
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert "PRIVATE_TOKEN_CANARY_SYNTHETIC_ONLY" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("status,code", [(401, "bot_token_revoked"), (409, "bot_polling_conflict"), (500, "bot_unavailable")])
@pytest.mark.asyncio
async def test_borrowed_sdk_reply_http_error_is_fixed(transport, server, status, code):
    server.state["status"] = status
    with pytest.raises(api.BotApiUnavailable) as caught:
        await transport.bot.send_message(chat_id=999, text="Sanitized setup guidance")
    assert caught.value.code == code
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert "PRIVATE_TOKEN_CANARY_SYNTHETIC_ONLY" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.asyncio
async def test_borrowed_sdk_reply_timeout_is_fixed(transport, server):
    server.state["delay"] = True
    with pytest.raises(api.BotApiUnavailable) as caught:
        await transport.bot.send_message(chat_id=999, text="Sanitized setup guidance")
    assert caught.value.code == "bot_timeout"
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize("cursor", [True, -1, "0", 0.0])
@pytest.mark.asyncio
async def test_invalid_cursor_is_rejected_without_http(transport, server, cursor):
    with pytest.raises(api.BotApiUnavailable):
        await transport.poll_once(cursor)
    assert server.state["calls"] == []


@pytest.mark.parametrize("timeout", [
    True, 0, -1, 6, float("inf"), float("nan"), "1",
    pytest.param(10**1000, id="huge_integer"),
])
def test_invalid_request_budget_fails_without_creation(timeout):
    with pytest.raises(api.BotApiUnavailable):
        api.BotApiTransport(TOKEN, request_timeout=timeout)


@pytest.mark.asyncio
async def test_close_cancels_and_drains_owned_sdk_session(transport, server):
    await transport.verify_identity()
    session = transport._session._session
    server.state["entered"].clear()
    server.state["delay"] = True
    polling = asyncio.create_task(transport.poll_once())
    await asyncio.wait_for(server.state["entered"].wait(), 1)
    await transport.close()
    with pytest.raises(asyncio.CancelledError):
        await polling
    assert session.closed and not transport._calls
    assert transport.identity_checked_at is None and transport.poll_checked_at is None
    calls = len(server.state["calls"])
    with pytest.raises(api.BotApiUnavailable):
        await transport.verify_identity()
    assert len(server.state["calls"]) == calls
    await transport.close()


@pytest.mark.asyncio
async def test_cancellation_invalidates_previous_poll_measurement(transport, server):
    await transport.poll_once()
    assert transport.poll_checked_at is not None
    server.state["entered"].clear()
    server.state["delay"] = True
    polling = asyncio.create_task(transport.poll_once())
    await asyncio.wait_for(server.state["entered"].wait(), 1)
    polling.cancel()
    with pytest.raises(asyncio.CancelledError):
        await polling
    assert transport.poll_checked_at is None
    await transport.close()
    assert not transport._calls


@pytest.mark.asyncio
async def test_nonstandard_success_cannot_bypass_strict_wire_identity(transport, server):
    server.state["success_status"] = 201
    server.state["identity"]["id"] = True
    with pytest.raises(api.BotApiUnavailable):
        await transport.verify_identity()
    assert transport.identity_checked_at is None


@pytest.mark.asyncio
async def test_token_bearing_request_never_follows_redirect(transport, server):
    server.state["redirect"] = True
    with pytest.raises(api.BotApiUnavailable) as caught:
        await transport.verify_identity()
    assert caught.value.code == "bot_unavailable"
    assert server.state["destination_calls"] == []
    assert transport.identity_checked_at is None


@pytest.mark.parametrize("operation", ["verify_identity", "poll_once"])
@pytest.mark.asyncio
async def test_completed_sdk_response_cannot_publish_after_close(transport, server, monkeypatch, operation):
    completed = asyncio.Event()
    publish = asyncio.Event()
    original = transport._request

    async def pause_after_real_response(*args, **kwargs):
        value = await original(*args, **kwargs)
        completed.set()
        await publish.wait()
        return value

    monkeypatch.setattr(transport, "_request", pause_after_real_response)
    pending = asyncio.create_task(getattr(transport, operation)())
    await asyncio.wait_for(completed.wait(), 1)
    await transport.close()
    publish.set()
    with pytest.raises(api.BotApiUnavailable):
        await pending
    assert len(server.state["calls"]) == 1
    assert transport.identity is None and transport.identity_checked_at is None
    assert transport.poll_checked_at is None


def test_public_constructor_cannot_select_arbitrary_api_endpoint():
    with pytest.raises(TypeError):
        api.BotApiTransport(TOKEN, base_url_factory=lambda: None)


def test_default_transport_preserves_official_https_session():
    value = api.BotApiTransport(TOKEN)
    assert value._session.api is api.PRODUCTION
    assert value._session._connector_init["ssl"] is not False


@pytest.mark.asyncio
async def test_timeout_retains_sdk_task_until_uncertain_cancellation_finishes(transport, server, monkeypatch):
    original_bot = transport._bot
    release = asyncio.Event()
    stopped = asyncio.Event()
    server.state["delay"] = True

    async def resistant_sdk_request(*args, **kwargs):
        kwargs["request_timeout"] = 5
        try:
            return await original_bot(*args, **kwargs)
        except asyncio.CancelledError:
            stopped.set()
            await release.wait()
            return []

    monkeypatch.setattr(transport, "_bot", resistant_sdk_request)
    try:
        with pytest.raises(api.BotApiUnavailable) as caught:
            await asyncio.wait_for(transport.poll_once(), 0.4)
        assert caught.value.code == "bot_timeout"
        await asyncio.wait_for(stopped.wait(), 0.4)
        assert transport._calls
        assert transport.poll_checked_at is None
        with pytest.raises(api.BotApiUnavailable) as conflict:
            await transport.poll_once()
        assert conflict.value.code == "bot_polling_conflict"
        assert len(server.state["calls"]) == 1
    finally:
        release.set()
        await transport.close()
    assert not transport._calls


@pytest.mark.asyncio
async def test_close_cancellation_keeps_drain_owner_and_refuses_new_requests(transport, monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    actual_close = transport._session.close

    async def delayed_close():
        entered.set()
        await release.wait()
        await actual_close()

    monkeypatch.setattr(transport._session, "close", delayed_close)
    closing = asyncio.create_task(transport.close())
    await asyncio.wait_for(entered.wait(), 1)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert transport._close_task is not None and not transport._close_task.done()
    with pytest.raises(api.BotApiUnavailable):
        await transport.poll_once()
    release.set()
    await transport.close()
    assert transport._close_task.done()


@pytest.mark.asyncio
async def test_failed_actual_session_close_can_retry_without_retrying_requests(server, monkeypatch):
    value = api.BotApiTransport._for_test(TOKEN, api_server_factory=server.factory)
    await value.verify_identity()
    sdk_session = value._session._session
    actual_close = value._session.close
    attempts = 0

    async def fail_once():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("PRIVATE_TOKEN_CANARY_SYNTHETIC_ONLY")
        await actual_close()

    monkeypatch.setattr(value._session, "close", fail_once)
    try:
        with pytest.raises(api.BotApiUnavailable) as caught:
            await value.close()
        assert caught.value.code == "bot_unavailable"
        assert caught.value.__cause__ is None and caught.value.__context__ is None
        failed_drain = value._close_task
        assert failed_drain.done() and not sdk_session.closed
        await value.close()
        assert value._close_task is not failed_drain
        assert sdk_session.closed and attempts == 2
        assert [method for method, _ in server.state["calls"]] == ["getMe"]
        assert value.identity is None and value.identity_checked_at is None
    finally:
        await actual_close()


def test_borrowed_sdk_requires_running_owning_loop():
    value = api.BotApiTransport(TOKEN)
    with pytest.raises(api.BotApiUnavailable) as caught:
        _ = value.bot
    assert caught.value.__context__ is None


@pytest.mark.asyncio
async def test_foreign_loop_cannot_use_or_close_owned_sdk(transport, server):
    await transport.verify_identity()
    calls = len(server.state["calls"])

    def foreign_loop():
        async def attempt():
            for operation in (transport.verify_identity, transport.poll_once, transport.close):
                with pytest.raises(api.BotApiUnavailable):
                    await operation()
            with pytest.raises(api.BotApiUnavailable):
                _ = transport.bot

        asyncio.run(attempt())

    await asyncio.to_thread(foreign_loop)
    assert len(server.state["calls"]) == calls
    assert transport.identity == api.BotIdentity(900000001, "fixture_bot")
    assert not transport._closed
