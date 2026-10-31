from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

from tg_assistant import cli
from tg_assistant.ai.engine import AiEngine
from tg_assistant.ai.vector import LocalVectorStore
from tg_assistant.config import Settings
from tg_assistant.db.models import (
    TelegramChat,
    TelegramChatPermission,
    TelegramChatPolicy,
    TelegramMessage,
)


async def test_reindex_uses_configured_budget_and_indexes_allowed_messages(session, tmp_path, monkeypatch) -> None:
    settings = Settings(
        _env_file=None, data_dir=tmp_path, ollama_vector_size=3,
        daily_ai_budget_usd=2.0, monthly_ai_budget_usd=30.0,
        daily_token_limit=1234, monthly_token_limit=12345,
        group_daily_token_limit=2345, feature_daily_token_limit=3456,
        provider_daily_token_limit=4567, max_cloud_fallbacks_per_day=7,
    )
    session.add_all([
        TelegramChat(chat_id=-1001, title="Allowed", chat_type="group"),
        TelegramChatPolicy(chat_id=-1001, allowed=True),
        TelegramChatPermission(chat_id=-1001, permission="search_messages", enabled=True),
        TelegramMessage(id=1, chat_id=-1001, message_id=1, text="Useful source content", sent_at=datetime.now(UTC)),
    ])
    await session.flush()

    class Database:
        @asynccontextmanager
        async def session(self):
            yield session

        async def close(self):
            pass

    async def embed(engine, current_session, text):
        budget = engine.budget
        assert (budget.daily_limit, budget.monthly_limit) == (2.0, 30.0)
        assert (budget.daily_token_limit, budget.monthly_token_limit) == (1234, 12345)
        assert (budget.group_daily_token_limit, budget.feature_daily_token_limit) == (2345, 3456)
        assert (budget.provider_daily_token_limit, budget.max_cloud_fallbacks_per_day) == (4567, 7)
        return [1.0, 0.0, 0.0]

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "SecretStore", lambda: SimpleNamespace(get=lambda key: None))
    monkeypatch.setattr(cli, "make_database", lambda settings, store: Database())
    monkeypatch.setattr(AiEngine, "embed", embed)
    # The CLI owns its event loop; this fixture already has one.
    pending = []
    monkeypatch.setattr(cli.asyncio, "run", pending.append)
    cli.reindex()
    await pending.pop()

    vectors = LocalVectorStore(settings.resolved_semantic_vector_path, vector_size=3)
    try:
        assert vectors.reference_ids(chat_id=-1001) == [1]
    finally:
        vectors.close()
