"""Independent SQLite transactions exercise digest lifetime, not mock policy."""

from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiogram.filters import CommandObject
from sqlalchemy import delete, event, select, update

from tg_assistant.db.base import Base, Database
from tg_assistant.db.models import (
    AppSetting,
    PermissionName,
    Summary,
    TelegramChat,
    TelegramChatPolicy,
    TelegramMessage,
)
from tg_assistant.policy import PolicyEngine
from tg_assistant.telegram import control_bot
from tg_assistant.telegram.control_bot import ControlBot, digest_window


@pytest_asyncio.fixture
async def digest_case(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{(tmp_path / 'digest.db').as_posix()}")
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    policy = PolicyEngine()
    since, _ = digest_window("today")
    async with db.session() as session:
        for source in (-1, -2):
            session.add(
                TelegramChat(chat_id=source, title=f"Private source {source}", chat_type="group")
            )
        await session.flush()
        for source in (-1, -2):
            await policy.set_allowed(session, source, True)
            await policy.set_permission(session, source, PermissionName.SUMMARIZE, True)
            session.add(
                TelegramMessage(
                    chat_id=source,
                    message_id=1,
                    text="Confidential project launch decision approved by the owner.",
                    sent_at=since + timedelta(minutes=10 if source == -1 else 5),
                )
            )
    admitted = [True]
    bot = ControlBot(
        "123456:synthetic-test-token",
        owner_id=17,
        database=db,
        policy=policy,
        admission=lambda: admitted[0],
    )
    submitted = []

    async def answer(*args, **kwargs):
        if kwargs.get("pre_submit"):
            await kwargs["pre_submit"]()
        submitted.append(args[2])
        return "Confidential project launch [S1]"

    bot.ai = SimpleNamespace(available=True, answer=answer)

    async def revoke(source=-1, regrant=False):
        async with db.session() as session:
            await policy.set_allowed(session, source, False)
            if regrant:
                await policy.set_allowed(session, source, True)
                await policy.set_permission(session, source, PermissionName.SUMMARIZE, True)

    async def summaries():
        async with db.session() as session:
            return list((await session.scalars(select(Summary))).all())

    try:
        yield SimpleNamespace(
            db=db,
            bot=bot,
            revoke=revoke,
            summaries=summaries,
            admitted=admitted,
            submitted=submitted,
            answer=answer,
        )
    finally:
        await bot.close()
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("source,regrant", [(-1, False), (-1, True), (-2, False)])
async def test_revoke_during_provider_prevents_summary_including_unselected_source(
    digest_case, source, regrant
):
    case = digest_case

    async def suspended_provider(*args, **kwargs):
        await case.revoke(source, regrant)
        return "Confidential project launch [S1]"

    case.bot.ai.answer = suspended_provider
    output = await case.bot._digest("today")
    assert not output
    assert await case.summaries() == []
    if regrant:
        case.bot.ai.answer = case.answer
        assert await case.bot._digest("today")
        assert len(await case.summaries()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("at", ["loaded", "pre_submit"])
async def test_loaded_snapshot_cannot_be_refreshed_after_block_regrant(
    digest_case, monkeypatch, at
):
    case = digest_case
    if at == "loaded":
        original = control_bot.load_daily_digest_dataset

        async def load(*args, **kwargs):
            data = await original(*args, **kwargs)
            await case.revoke(-1, True)
            return data

        monkeypatch.setattr(control_bot, "load_daily_digest_dataset", load)
    else:

        async def delayed_provider(*args, **kwargs):
            await case.revoke(-1, True)
            return await case.answer(*args, **kwargs)

        case.bot.ai.answer = delayed_provider
    assert not await case.bot._digest("today")
    assert case.submitted == []
    assert await case.summaries() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("callback", [False, True])
@pytest.mark.parametrize("after_first", [False, True])
async def test_each_digest_chunk_rechecks_original_source_snapshot(
    digest_case, monkeypatch, callback, after_first
):
    case = digest_case
    original = case.bot._digest

    async def generate(period):
        result = await original(period)
        if not after_first:
            await case.revoke(-2)
        return result

    case.bot._digest = generate
    delivered = []

    async def send(*args, **kwargs):
        delivered.append(args[-1])
        await case.revoke(-2)

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(control_bot, "telegram_html_chunks", lambda text: [text, text])
    sender = SimpleNamespace(id=17, is_bot=False)
    message = SimpleNamespace(
        from_user=sender, chat=SimpleNamespace(id=17, type="private"), answer=send
    )
    if callback:
        monkeypatch.setattr(case.bot.bot, "send_message", send)
        monkeypatch.setattr(case.bot.bot, "send_chat_action", noop)
        handler = next(
            h.callback
            for h in case.bot.router.callback_query.handlers
            if h.callback.__name__ == "ai_digest"
        )
        await handler(
            SimpleNamespace(from_user=sender, message=message, data="ai:digest:today", answer=noop),
            SimpleNamespace(clear=noop),
        )
    else:
        handler = next(
            h.callback for h in case.bot.router.message.handlers if h.callback.__name__ == "digest"
        )
        await handler(message, CommandObject(command="digest", args="today"))
    assert len(delivered) == (1 if after_first else 0)


@pytest.mark.asyncio
async def test_pair_withdrawal_after_summary_flush_rolls_back(digest_case):
    case = digest_case
    original = case.db.session

    @asynccontextmanager
    async def session():
        async with original() as value:

            def mark_summary(sync, *_):
                sync.info["summary_written"] = any(isinstance(row, Summary) for row in sync.new)

            def withdraw(sync, *_):
                if sync.info.get("summary_written"):
                    case.admitted[0] = False

            event.listen(value.sync_session, "before_flush", mark_summary)
            event.listen(value.sync_session, "after_flush_postexec", withdraw)
            yield value

    case.db.session = session
    assert not await case.bot._digest("today")
    case.db.session = original
    assert await case.summaries() == []


@pytest.mark.asyncio
async def test_unchanged_summarize_without_search_still_generates(digest_case):
    assert await digest_case.bot._digest("today")
    assert len(await digest_case.summaries()) == 1


@pytest.mark.asyncio
async def test_epoch_is_captured_before_raw_message_read(digest_case):
    case = digest_case
    original = case.db.session
    changed = False

    @asynccontextmanager
    async def session():
        async with original() as value:
            execute = value.execute

            async def read(statement, *args, **kwargs):
                nonlocal changed
                result = await execute(statement, *args, **kwargs)
                if not changed and "telegram_messages" in str(statement):
                    changed = True
                    await case.revoke(-1, True)
                return result

            value.execute = read
            yield value

    case.db.session = session
    assert not await case.bot._digest("today")
    assert changed and case.submitted == []
    case.db.session = original
    assert await case.summaries() == []


@pytest.mark.asyncio
async def test_revocation_just_before_writer_lock_prevents_summary(digest_case):
    case = digest_case
    original = case.db.session
    changed = False

    @asynccontextmanager
    async def session():
        async with original() as value:
            execute = value.execute

            async def begin(statement, *args, **kwargs):
                nonlocal changed
                if str(statement) == "BEGIN IMMEDIATE":
                    changed = True
                    await case.revoke(-1, True)
                return await execute(statement, *args, **kwargs)

            value.execute = begin
            yield value

    case.db.session = session
    assert not await case.bot._digest("today")
    assert changed
    case.db.session = original
    assert await case.summaries() == []


@pytest.mark.asyncio
async def test_source_change_at_flush_is_rechecked_before_actual_commit(digest_case):
    case = digest_case
    original = case.db.session

    @asynccontextmanager
    async def session():
        async with original() as value:

            def revoke(sync, *_):
                if any(isinstance(row, Summary) for row in sync.new):
                    sync.connection().execute(
                        update(TelegramChatPolicy)
                        .where(TelegramChatPolicy.chat_id == -1)
                        .values(authorization_epoch=99)
                    )

            event.listen(value.sync_session, "before_flush", revoke)
            yield value

    case.db.session = session
    assert not await case.bot._digest("today")
    case.db.session = original
    assert await case.summaries() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "unavailable", "empty"])
@pytest.mark.parametrize("revoke", [False, True])
async def test_digest_nonprovider_paths_keep_source_fence(digest_case, monkeypatch, mode, revoke):
    case = digest_case
    if mode == "off":
        async with case.db.session() as session:
            session.add(AppSetting(key="ai_enabled", value=False))
    elif mode == "unavailable":
        case.bot.ai = None
    else:
        async with case.db.session() as session:
            await session.execute(delete(TelegramMessage))
    if revoke:
        original = control_bot.load_daily_digest_dataset

        async def load(*args, **kwargs):
            dataset = await original(*args, **kwargs)
            await case.revoke(-2)
            return dataset

        monkeypatch.setattr(control_bot, "load_daily_digest_dataset", load)
    result = await case.bot._digest("today")
    assert bool(result) is (not revoke)
    assert len(await case.summaries()) == (0 if revoke else 1)
    assert case.submitted == []


@pytest.mark.asyncio
async def test_summarize_permission_revocation_is_not_swallowed_as_provider_error(digest_case):
    case = digest_case

    async def provider(*args, **kwargs):
        async with case.db.session() as session:
            await case.bot.policy.set_permission(session, -1, PermissionName.SUMMARIZE, False)
        raise RuntimeError("synthetic provider failure")

    case.bot.ai.answer = provider
    assert not await case.bot._digest("today")
    assert await case.summaries() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["reserve", "submitted", "inflight", "cloud", "unchanged"])
async def test_real_engine_budget_presubmit_and_cloud_denial(digest_case, monkeypatch, boundary):
    from tg_assistant.ai.budget import BudgetService
    from tg_assistant.ai.engine import AiEngine

    case = digest_case
    budget = BudgetService(daily_limit=1, monthly_limit=10)
    engine = AiEngine(
        api_key=None,
        budget=budget,
        provider="openai" if boundary == "cloud" else "ollama",
        base_url="https://api.openai.com/v1"
        if boundary == "cloud"
        else "http://127.0.0.1:11434/v1",
        model="gpt-5.6-terra" if boundary == "cloud" else "synthetic-local",
        embedding_model="synthetic-embedding",
        max_output_tokens=100,
        max_input_tokens=100_000,
        cloud_consent=True,
    )
    if engine.client:
        await engine.client.close()
    calls = []

    async def submit(**kwargs):
        calls.append(kwargs)
        if boundary == "inflight":
            await case.revoke(-2, True)
        return SimpleNamespace(
            output_text="Confidential project launch [S1]",
            usage=SimpleNamespace(input_tokens=30, output_tokens=10),
        )

    engine.client = SimpleNamespace(responses=SimpleNamespace(create=submit))
    if boundary in {"reserve", "submitted"}:
        method = "reserve" if boundary == "reserve" else "mark_submitted"
        original = getattr(budget, method)

        async def withdraw(*args, **kwargs):
            result = await original(*args, **kwargs)
            await case.revoke(-1, True)
            return result

        monkeypatch.setattr(budget, method, withdraw)
    case.bot.ai = engine
    result = await case.bot._digest("today")
    if boundary == "unchanged":
        assert result and len(calls) == 1
        assert len(await case.summaries()) == 1
    elif boundary == "cloud":
        assert calls == []
        # Existing policy denial remains owner-visible; source metadata remains fenced.
        assert result and "policy" in result.text.lower()
    elif boundary == "inflight":
        assert not result and len(calls) == 1
        assert await case.summaries() == []
    else:
        assert not result and calls == []
        assert await case.summaries() == []
