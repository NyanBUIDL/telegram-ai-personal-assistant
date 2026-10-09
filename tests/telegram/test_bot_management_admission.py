"""Actual SDK updates/dispatch and session boundary, synthetic HTTP only.

These cases establish no live Telegram health. Private admission is supplied by
the owning runtime in production; this fixture exercises its consumption.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.filters import CommandObject
from aiogram.methods import AnswerCallbackQuery, GetMe, GetUpdates, SendMessage
from aiogram.types import Chat, Message, Update, User

from tg_assistant.policy import PolicyEngine
from tg_assistant.telegram.control_bot import AiFlowState, ControlBot
from tg_assistant.telegram.pairing import PairingCode

TOKEN = "123456:synthetic-only-management-test-token"  # noqa: S105
OWNER = 17


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.delivered = []
        self.closed = False
        self.send_entered = asyncio.Event()
        self.send_gate = None

    async def close(self):
        self.closed = True

    async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109 - SDK signature
        if isinstance(method, SendMessage):
            self.send_entered.set()
            if self.send_gate is not None:
                await self.send_gate.wait()
            self.delivered.append(method.text)
            return Message(
                message_id=len(self.delivered),
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text,
            )
        if isinstance(method, AnswerCallbackQuery):
            return True
        if isinstance(method, GetMe):
            return User(id=123456, is_bot=True, first_name="Synthetic", username="synthetic_bot")
        if isinstance(method, GetUpdates):
            return []
        raise AssertionError("Unexpected SDK operation in management admission fixture")

    async def stream_content(self, *args, **kwargs):
        yield b""


class ForbiddenDatabase:
    def session(self):
        raise AssertionError("Unadmitted update reached management persistence")


@pytest_asyncio.fixture
async def sdk():
    session = RecordingSession()
    bot = Bot(TOKEN, session=session)
    try:
        yield SimpleNamespace(bot=bot, session=session)
    finally:
        await session.close()


def message_update(text="/help", *, sender=OWNER, sender_bot=False, update_id=1):
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": 1700000000,
                "chat": {"id": sender, "type": "private"},
                "from": {"id": sender, "is_bot": sender_bot, "first_name": "Synthetic"},
                "text": text,
                "entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}],
            },
        }
    )


def callback_update(*, sender=OWNER):
    return Update.model_validate(
        {
            "update_id": 2,
            "callback_query": {
                "id": "synthetic-callback",
                "chat_instance": "synthetic-chat-instance",
                "from": {"id": sender, "is_bot": False, "first_name": "Synthetic"},
                "data": "ai:ask",
            },
        }
    )


def control(sdk, *, admission=None, polling_runner=None, pairing=None):
    return ControlBot(
        TOKEN,
        owner_id=OWNER,
        database=ForbiddenDatabase(),
        policy=PolicyEngine(),
        pairing=pairing,
        bot_instance=sdk.bot,
        admission=admission,
        polling_runner=polling_runner,
    )


@pytest.mark.asyncio
async def test_token_and_matching_owner_without_private_admission_cannot_manage():
    value = ControlBot(TOKEN, owner_id=OWNER, database=ForbiddenDatabase(), policy=PolicyEngine())
    try:
        assert value._owner(message_update().message) is False
        assert value._owner_callback(callback_update().callback_query) is False
    finally:
        await value.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("admitted", [False, None, 1, "true"])
async def test_owner_updates_require_exact_current_private_true(sdk, admitted):
    value = control(sdk, admission=lambda: admitted)
    try:
        await value.dp.feed_update(sdk.bot, message_update())
        await value.dp.feed_update(sdk.bot, callback_update())
        assert sdk.session.delivered == []
    finally:
        await value.close()


@pytest.mark.asyncio
async def test_private_reader_exception_is_silent_and_denies_actual_dispatch(sdk):
    def unavailable():
        raise RuntimeError("synthetic-private-exception-canary")

    value = control(sdk, admission=unavailable)
    try:
        await value.dp.feed_update(sdk.bot, message_update())
        assert sdk.session.delivered == []
    finally:
        await value.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("sender,sender_bot", [(18, False), (OWNER, True)])
async def test_verified_admission_preserves_exact_nonbot_owner_check(sdk, sender, sender_bot):
    value = control(sdk, admission=lambda: True)
    try:
        await value.dp.feed_update(sdk.bot, message_update(sender=sender, sender_bot=sender_bot))
        assert sdk.session.delivered == []
    finally:
        await value.close()


@pytest.mark.asyncio
async def test_fresh_help_dispatch_works_then_stale_admission_denies_fsm_and_callback(sdk):
    state = {"fresh": True}
    value = control(sdk, admission=lambda: state["fresh"])
    try:
        await value.dp.feed_update(sdk.bot, message_update())
        assert sdk.session.delivered and "TRỢ LÝ" in sdk.session.delivered[0]
        original = list(sdk.session.delivered)
        fsm = value.dp.fsm.get_context(bot=sdk.bot, chat_id=OWNER, user_id=OWNER)
        await fsm.set_state(AiFlowState.waiting_question)
        state["fresh"] = False
        await value.dp.feed_update(sdk.bot, message_update("private question", update_id=3))
        await value.dp.feed_update(sdk.bot, callback_update())
        assert sdk.session.delivered == original
        assert await fsm.get_state() == AiFlowState.waiting_question.state
    finally:
        await value.close()


@pytest.mark.asyncio
async def test_direct_handler_call_cannot_bypass_private_admission(sdk):
    value = control(sdk, admission=lambda: False)
    try:
        handler = next(
            entry.callback
            for entry in value.router.message.handlers
            if entry.callback.__name__ == "help_command"
        )
        await handler(message_update().message.as_(sdk.bot))
        assert sdk.session.delivered == []
    finally:
        await value.close()


@pytest.mark.asyncio
async def test_legacy_pairing_code_never_publishes_management_authority(sdk):
    pairing = PairingCode.create(OWNER)
    value = control(sdk, admission=lambda: True, pairing=pairing)
    try:
        handler = next(
            entry.callback
            for entry in value.router.message.handlers
            if entry.callback.__name__ == "pair"
        )
        await handler(
            message_update().message.as_(sdk.bot),
            CommandObject(command="pair", args=pairing.value),
        )
        assert pairing.used is False
        assert all("Đã ghép nối" not in text for text in sdk.session.delivered)
    finally:
        await value.close()


@pytest.mark.asyncio
async def test_outbound_sdk_boundary_withdraws_direct_helper_reply_but_keeps_health_reads(sdk):
    state = {"fresh": True}
    value = control(sdk, admission=lambda: state["fresh"])
    try:
        state["fresh"] = False
        with pytest.raises(RuntimeError, match="^bot_management_unavailable$"):
            await value._send_groups_hub(OWNER)
        assert sdk.session.delivered == []
        assert (await sdk.bot.get_me()).is_bot is True
        assert await sdk.bot.get_updates() == []
    finally:
        await value.close()
    # The borrowed SDK's owner can still use it after this wrapper's drain.
    assert sdk.session.closed is False
    await sdk.bot.send_message(OWNER, "owner transport remains usable")
    assert sdk.session.delivered == ["owner transport remains usable"]


@pytest.mark.asyncio
async def test_controlled_polling_reuses_same_dispatcher_and_sdk_and_borrowed_close(sdk):
    async def poll(dispatcher, bot):
        assert bot is sdk.bot
        await dispatcher.feed_update(bot, message_update())

    value = control(sdk, admission=lambda: True, polling_runner=poll)
    try:
        await value.run()
        assert sdk.session.delivered
    finally:
        await value.close()
    assert sdk.session.closed is False


@pytest.mark.asyncio
async def test_borrowed_sdk_without_owned_poller_never_starts_second_polling(sdk):
    value = control(sdk, admission=lambda: True)
    try:
        with pytest.raises(RuntimeError, match="^bot_management_unavailable$"):
            await value.run()
    finally:
        await value.close()


@pytest.mark.asyncio
async def test_borrowed_close_drains_actual_dispatched_handler_before_removing_request_gate(sdk):
    value = control(sdk, admission=lambda: True)
    sdk.session.send_gate = asyncio.Event()
    dispatch = asyncio.create_task(value.dp.feed_update(sdk.bot, message_update()))
    try:
        await asyncio.wait_for(sdk.session.send_entered.wait(), 1)
        await value.close()
        sdk.session.send_gate.set()
        await asyncio.gather(dispatch, return_exceptions=True)
        assert dispatch.done()
        assert sdk.session.delivered == []
        assert sdk.session.closed is False
    finally:
        sdk.session.send_gate.set()
        dispatch.cancel()
        await asyncio.gather(dispatch, return_exceptions=True)
        await value.close()
