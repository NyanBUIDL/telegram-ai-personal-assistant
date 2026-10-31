from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiogram.types import Chat, Message, Update, User

from tg_assistant.policy import PolicyEngine
from tg_assistant.telegram.control_bot import ControlBot


@pytest_asyncio.fixture
async def polling_bot(monkeypatch):
    before = asyncio.all_tasks()
    bot = ControlBot(
        "123456:offline-test-token", owner_id=42,
        database=object(), policy=PolicyEngine(),
    )
    events, polling_tasks = [], []
    started = asyncio.Event()

    async def polling(**_kwargs):
        polling_tasks.append(asyncio.current_task())
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            events.append("polling:finished")

    async def close_session():
        events.append("session:closed")

    # Keep Dispatcher.start_polling real to reproduce its child-task ownership.
    monkeypatch.setattr(bot.dp, "_polling", polling)
    monkeypatch.setattr(bot.bot.session, "close", close_session)
    yield SimpleNamespace(bot=bot, events=events, tasks=polling_tasks, started=started)
    await asyncio.gather(bot.close(), return_exceptions=True)
    pending = asyncio.all_tasks() - before - {asyncio.current_task()}
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.parametrize("wait_for_started", [False, True])
async def test_run_cancellation_drains_sdk_children_before_session_close(
    polling_bot, wait_for_started
):
    flow = polling_bot
    running = asyncio.create_task(flow.bot.run())
    if wait_for_started:
        await asyncio.wait_for(flow.started.wait(), timeout=1)
    else:
        await asyncio.sleep(0)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(running, timeout=1)
    await flow.bot.close()
    assert all(task.done() for task in flow.tasks)
    assert flow.events.count("session:closed") == 1
    if flow.tasks:
        assert flow.events.index("polling:finished") < flow.events.index("session:closed")


def update():
    return Update(update_id=1, message=Message(
        message_id=1, date=datetime.now(UTC), text="offline lifecycle",
        chat=Chat(id=42, type="private"),
        from_user=User(id=42, is_bot=False, first_name="Owner"),
    ))


async def test_close_drains_live_update_and_background_finalizers(polling_bot):
    flow = polling_bot
    handler_started, background_started = asyncio.Event(), asyncio.Event()

    async def handler(_message):
        handler_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            flow.events.append("update:finished")

    async def background():
        background_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            flow.events.append("background:finished")

    flow.bot.dp.message.register(handler)
    handling = asyncio.create_task(flow.bot.dp.feed_update(flow.bot.bot, update()))
    flow.bot._run_background_task(background())
    await asyncio.wait_for(handler_started.wait(), timeout=1)
    await asyncio.wait_for(background_started.wait(), timeout=1)
    await flow.bot.close()
    assert handling.done()
    assert flow.events.index("update:finished") < flow.events.index("session:closed")
    assert flow.events.index("background:finished") < flow.events.index("session:closed")


async def test_close_from_update_does_not_await_itself(polling_bot):
    flow = polling_bot

    async def handler(_message):
        await flow.bot.close()
        flow.events.append("handler:returned")

    flow.bot.dp.message.register(handler)
    await asyncio.wait_for(flow.bot.dp.feed_update(flow.bot.bot, update()), timeout=1)
    assert "handler:returned" in flow.events


async def test_close_before_run_does_not_start_polling(polling_bot):
    flow = polling_bot
    await flow.bot.close()
    assert not flow.tasks
    assert flow.events == ["session:closed"]
