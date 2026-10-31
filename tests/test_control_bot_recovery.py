from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import select

from tg_assistant.db.models import PendingAction, Task, TelegramChat, TelegramMessage
from tg_assistant.policy import PolicyEngine
from tg_assistant.telegram import control_bot
from tg_assistant.telegram.control_bot import (
    AiFlowState,
    ControlBot,
    GroupSearchState,
    OllamaFlowState,
)


@pytest_asyncio.fixture
async def bot_flow(session, monkeypatch):
    """Run real dispatcher/services with only Telegram's transport replaced."""

    @asynccontextmanager
    async def database_session():
        yield session

    bot = ControlBot(
        "123456:offline-test-token",
        owner_id=42,
        database=SimpleNamespace(session=database_session),
        policy=PolicyEngine(),
    )
    sent = []
    owner = User(id=42, is_bot=False, first_name="Owner")
    chat = Chat(id=42, type="private")

    async def transport(_bot, method, **_kwargs):
        if isinstance(method, SendMessage):
            sent.append(method)
            return Message(
                message_id=len(sent), date=datetime.now(UTC), chat=chat, text=method.text
            )
        return True

    monkeypatch.setattr(bot.bot.session, "make_request", transport)
    state = bot.dp.fsm.get_context(bot=bot.bot, chat_id=42, user_id=42)
    update_id = 0

    async def send(text):
        nonlocal update_id
        update_id += 1
        await bot.dp.feed_update(
            bot.bot,
            Update(
                update_id=update_id,
                message=Message(
                    message_id=update_id,
                    date=datetime.now(UTC),
                    chat=chat,
                    from_user=owner,
                    text=text,
                ),
            ),
        )

    async def click(data):
        nonlocal update_id
        update_id += 1
        await bot.dp.feed_update(
            bot.bot,
            Update(
                update_id=update_id,
                callback_query=CallbackQuery(
                    id=str(update_id),
                    from_user=owner,
                    chat_instance="offline",
                    data=data,
                    message=Message(message_id=1, date=datetime.now(UTC), chat=chat),
                ),
            ),
        )

    yield SimpleNamespace(bot=bot, sent=sent, state=state, send=send, click=click)
    await bot.close()


@pytest.mark.parametrize(
    "waiting_state",
    [
        AiFlowState.waiting_question,
        AiFlowState.waiting_price,
        AiFlowState.waiting_search,
        AiFlowState.waiting_learning_search,
        AiFlowState.waiting_learning_audit_file,
        AiFlowState.waiting_source_limits,
        GroupSearchState.waiting_query,
        OllamaFlowState.waiting_pull_model,
    ],
)
async def test_groups_command_escapes_every_text_prompt(bot_flow, waiting_state):
    await bot_flow.state.set_state(waiting_state)
    await bot_flow.send("/groups")

    assert await bot_flow.state.get_state() is None
    callbacks = {
        button.callback_data
        for row in bot_flow.sent[-1].reply_markup.inline_keyboard
        for button in row
    }
    assert "groups:list:all" in callbacks


async def test_help_exits_waiting_prompt(bot_flow):
    await bot_flow.state.set_state(AiFlowState.waiting_question)
    await bot_flow.send("/help")

    assert await bot_flow.state.get_state() is None


async def test_ask_menu_exits_unrelated_model_prompt(bot_flow):
    await bot_flow.state.set_state(OllamaFlowState.waiting_pull_model)
    await bot_flow.send("/ask")

    assert await bot_flow.state.get_state() is None
    assert bot_flow.sent[-1].reply_markup is not None


@pytest.mark.parametrize(
    "destination",
    [
        "ai:menu",
        "groups:show",
        "groups:list:all",
        "groups:page:0",
        "group:open:-1001",
    ],
)
async def test_menu_navigation_discards_stale_prompt(bot_flow, session, destination):
    session.add(TelegramChat(chat_id=-1001, title="Test group", chat_type="supergroup"))
    await session.flush()
    await bot_flow.state.set_state(AiFlowState.waiting_question)
    await bot_flow.click(destination)

    assert await bot_flow.state.get_state() is None


async def test_cancel_without_id_exits_flow_without_creating_action(bot_flow, session):
    await bot_flow.state.set_state(AiFlowState.waiting_search)
    await bot_flow.state.update_data(learning_chat_ids=[-1001])
    await bot_flow.send("/cancel")

    assert await bot_flow.state.get_state() is None
    assert await bot_flow.state.get_data() == {}
    assert list((await session.scalars(select(PendingAction))).all()) == []
    assert "hủy" in bot_flow.sent[-1].text


@pytest.mark.parametrize("command", ["/confirm missing", "/cancel missing"])
async def test_unknown_action_returns_recoverable_response(bot_flow, command):
    await bot_flow.send(command)

    assert bot_flow.sent
    assert "missing" not in bot_flow.sent[-1].text


async def test_expired_confirmation_explains_error(bot_flow, session):
    action = await bot_flow.bot.actions.create(
        session,
        action_type="send_message",
        requested_by=42,
        chat_id=-1001,
        payload={"text": "hello"},
    )
    action.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    await bot_flow.send(f"/confirm {action.action_id}")

    assert "hết hạn" in bot_flow.sent[-1].text
    assert action.status != "confirmed"


async def test_direct_search_invalid_filter_returns_guidance(bot_flow):
    await bot_flow.send("/search after:not-a-date")

    assert "YYYY-MM-DD" in bot_flow.sent[-1].text


async def test_direct_search_splits_long_results(bot_flow, session):
    session.add(TelegramChat(chat_id=-1001, title="Test group", chat_type="supergroup"))
    await session.flush()
    await bot_flow.bot.policy.apply_template(session, -1001, "knowledge")
    session.add_all(
        [
            TelegramMessage(
                chat_id=-1001,
                message_id=index,
                text="needle " + "x" * 600,
                sent_at=datetime.now(UTC) - timedelta(minutes=index),
            )
            for index in range(1, 11)
        ]
    )
    await session.flush()
    await bot_flow.send("/search needle")

    assert len(bot_flow.sent) > 1
    assert all(len(message.text) <= 4096 for message in bot_flow.sent)
    assert "-1001/10" in "".join(message.text for message in bot_flow.sent)


async def test_today_uses_vietnam_calendar_day(bot_flow, session, monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 31, 18, 0, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(control_bot, "datetime", FixedDatetime)
    session.add_all(
        [
            Task(title="Due today in Vietnam", due_at=datetime(2026, 10, 31, 18, 30, tzinfo=UTC)),
            Task(
                title="Due yesterday in Vietnam", due_at=datetime(2026, 10, 31, 10, 0, tzinfo=UTC)
            ),
            Task(
                title="Due later today in Vietnam", due_at=datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
            ),
        ]
    )
    await session.flush()
    await bot_flow.send("/today")

    text = "".join(message.text for message in bot_flow.sent)
    assert "Due today in Vietnam" in text
    assert "Due later today in Vietnam" in text
    assert "Due yesterday in Vietnam" not in text
