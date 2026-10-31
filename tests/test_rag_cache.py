from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tg_assistant.ai import rag as rag_module
from tg_assistant.ai.rag import RagService
from tg_assistant.db.base import Base
from tg_assistant.db.models import AiQueryCache, AppSetting, TelegramChat, TelegramMessage
from tg_assistant.policy import PolicyEngine


class AnswerAi:
    available = False  # No embeddings or external requests in cache regressions.
    provider = "ollama"
    model = "test-model"

    def __init__(self, barrier=None):
        self.contexts = []
        self.barrier = barrier

    async def answer(self, session, question, context, **kwargs):
        self.contexts.append(context)
        number = len(self.contexts)
        if self.barrier:
            await self.barrier.wait()
            session.add(AppSetting(key=f"caller-write:{number}", value=True))
        return f"Answer {number}, {len(context)} sources [S1]."


async def prepare_corpus(session):
    session.add(TelegramChat(chat_id=100, title="Cache source", chat_type="group"))
    await session.flush()
    policy = PolicyEngine()
    await policy.apply_template(session, 100, "knowledge")
    session.add_all(
        TelegramMessage(
            chat_id=100,
            message_id=message_id,
            text="cache " + "evidence " * 180,
            sent_at=datetime.now(UTC),
        )
        for message_id in (1, 2)
    )
    await session.commit()
    return policy


async def ask(rag, session, *, include_citations=True):
    return await rag.answer(
        session,
        "cache",
        actor_id=1,
        owner_id=1,
        chat_ids=[100],
        include_citations=include_citations,
    )


@pytest.mark.asyncio
async def test_expired_answer_cache_refreshes_without_losing_caller_writes(session):
    policy = await prepare_corpus(session)
    ai = AnswerAi()
    rag = RagService(policy, ai)
    first = await ask(rag, session)
    await session.commit()
    cached = await session.scalar(select(AiQueryCache))
    cached.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    session.add(AppSetting(key="caller-write", value=True))

    refreshed = await ask(rag, session)
    await session.commit()

    assert refreshed != first
    assert len(ai.contexts) == 2
    assert await session.scalar(select(func.count()).select_from(AiQueryCache)) == 1
    assert (await session.get(AppSetting, "caller-write")).value is True
    assert await ask(rag, session) == refreshed
    assert len(ai.contexts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("first_has_citations", [True, False])
async def test_answer_cache_separates_citation_modes(session, first_has_citations):
    policy = await prepare_corpus(session)
    ai = AnswerAi()
    rag = RagService(policy, ai)

    first = await ask(rag, session, include_citations=first_has_citations)
    await session.commit()
    second = await ask(rag, session, include_citations=not first_has_citations)
    await session.commit()

    assert ("[S1]" in first) is first_has_citations
    assert ("[S1]" in second) is not first_has_citations
    assert len(ai.contexts) == 2
    assert await ask(rag, session, include_citations=first_has_citations) == first
    assert await ask(rag, session, include_citations=not first_has_citations) == second
    assert len(ai.contexts) == 2


@pytest.mark.asyncio
async def test_lower_context_limit_does_not_reuse_larger_answer(session):
    policy = await prepare_corpus(session)
    ai = AnswerAi()
    rag = RagService(policy, ai)
    first = await ask(rag, session)
    await session.commit()
    await policy.set_ai_efficiency(session, 100, rag_max_context_tokens=500)
    await session.commit()

    second = await ask(rag, session)
    await session.commit()

    assert "2 sources" in first
    assert "1 sources" in second
    assert len(ai.contexts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("deletion", ["hard", "soft", "replace"])
async def test_answer_cache_invalidates_when_older_evidence_is_removed(session, deletion):
    policy = await prepare_corpus(session)
    older, latest = (await session.scalars(select(TelegramMessage).order_by(TelegramMessage.id))).all()
    latest_update = datetime.now(UTC)
    older_update = latest_update - timedelta(hours=1)
    older.text = "cache removed-evidence"
    older.updated_at = older_update
    latest.updated_at = latest_update
    await session.commit()
    ai = AnswerAi()
    rag = RagService(policy, ai)
    first = await ask(rag, session)
    await session.commit()

    if deletion == "soft":
        # Explicit SQL keeps the historical timestamp; ORM no-op assignment triggers onupdate.
        await session.execute(
            update(TelegramMessage)
            .where(TelegramMessage.id == older.id)
            .values(is_deleted=True, updated_at=older_update)
        )
    else:
        await session.delete(older)
    if deletion == "replace":
        session.add(
            TelegramMessage(
                chat_id=100,
                message_id=3,
                text="cache replacement-evidence",
                sent_at=latest_update,
                updated_at=older_update,
            )
        )
    await session.commit()
    assert await session.scalar(select(func.max(TelegramMessage.updated_at))) == latest_update.replace(
        tzinfo=None
    )

    refreshed = await ask(rag, session)
    await session.commit()

    assert refreshed != first
    assert len(ai.contexts) == 2
    assert all("removed-evidence" not in context for context in ai.contexts[-1])
    assert await ask(rag, session) == refreshed
    assert len(ai.contexts) == 2


@pytest.mark.asyncio
async def test_answer_cache_invalidates_when_evidence_ages_out_of_window(session, monkeypatch):
    policy = await prepare_corpus(session)
    older, latest = (await session.scalars(select(TelegramMessage).order_by(TelegramMessage.id))).all()
    asked_at = datetime(2026, 10, 31, 12, tzinfo=UTC)
    older.sent_at = asked_at - timedelta(days=7) + timedelta(minutes=1)
    latest.sent_at = asked_at - timedelta(hours=1)
    await session.commit()
    monkeypatch.setattr(
        rag_module, "rag_time_window", lambda: (asked_at - timedelta(days=7), asked_at)
    )
    ai = AnswerAi()
    rag = RagService(policy, ai)
    first = await ask(rag, session)
    await session.commit()
    later = asked_at + timedelta(minutes=2)
    monkeypatch.setattr(rag_module, "rag_time_window", lambda: (later - timedelta(days=7), later))

    refreshed = await ask(rag, session)
    await session.commit()

    assert "2 sources" in first
    assert "1 sources" in refreshed
    assert len(ai.contexts) == 2
    assert await ask(rag, session) == refreshed
    assert len(ai.contexts) == 2


@pytest.mark.asyncio
async def test_concurrent_cache_misses_preserve_both_caller_transactions(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cache.sqlite'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            policy = await prepare_corpus(session)
        ai = AnswerAi(asyncio.Barrier(2))
        rag = RagService(policy, ai)

        async def request():
            async with factory() as session:
                answer = await ask(rag, session)
                await session.commit()
                return answer

        answers = await asyncio.wait_for(asyncio.gather(request(), request()), timeout=10)

        assert answers[0] == answers[1]
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(AiQueryCache)) == 1
            for number in (1, 2):
                assert (await session.get(AppSetting, f"caller-write:{number}")).value is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("include_citations", [True, False])
@pytest.mark.parametrize("answer", ["", " \n", "[S1]", "[S1].", "Unknown [S99].", "Mixed [S1] [S99]."])
async def test_unusable_rag_answers_are_not_cached(session, answer, include_citations):
    policy = await prepare_corpus(session)
    ai = AnswerAi()

    async def invalid_answer(*_args, **_kwargs):
        ai.contexts.append([])
        return answer

    ai.answer = invalid_answer
    rag = RagService(policy, ai)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await ask(rag, session, include_citations=include_citations)
        await session.commit()
    assert len(ai.contexts) == 2
    assert await session.scalar(select(func.count()).select_from(AiQueryCache)) == 0


@pytest.mark.asyncio
async def test_uncited_rag_answer_requires_grounding_only_in_citation_mode(session):
    policy = await prepare_corpus(session)
    ai = AnswerAi()

    async def uncited_answer(*_args, **_kwargs):
        return "An answer without source references."

    ai.answer = uncited_answer
    rag = RagService(policy, ai)
    with pytest.raises(RuntimeError):
        await ask(rag, session)
    assert await session.scalar(select(func.count()).select_from(AiQueryCache)) == 0
    assert await ask(rag, session, include_citations=False) == "An answer without source references."


@pytest.mark.asyncio
async def test_answer_cache_ignores_entries_from_old_grounding_version(session, monkeypatch):
    policy = await prepare_corpus(session)
    ai = AnswerAi()
    rag = RagService(policy, ai)
    cache_key = rag_module.query_cache_key

    def old_cache_key(question, **kwargs):
        kwargs["rag_config_version"] = kwargs["rag_config_version"].replace(":grounding=2", "")
        return cache_key(question, **kwargs)

    monkeypatch.setattr(rag_module, "query_cache_key", old_cache_key)
    await ask(rag, session)
    await session.commit()
    cached = await session.scalar(select(AiQueryCache))
    cached.response = "Unsafe old cached answer [S99]."
    await session.commit()
    monkeypatch.setattr(rag_module, "query_cache_key", cache_key)

    answer = await ask(rag, session)

    assert "Unsafe old cached answer" not in answer
    assert len(ai.contexts) == 2
