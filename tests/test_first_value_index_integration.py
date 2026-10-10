"""Integration coverage for the current-index reader: cloud ledger and the actual Application.

Reuses the Env/run harness of test_first_value_index. No provider, network, account or key is
used. The cloud ledger rows are synthetic durable fixtures, never a provider receipt or invoice.
The BotShell pairing context stays a fixture: this is not ControlBot/startup/live-owner proof.
"""

from datetime import UTC, datetime, timedelta

import pytest
from test_first_value_index import (
    CHAT,
    PROFILE_ID,
    Env,
    current_policy_version,
    run,
)

from tg_assistant.ai.budget import pricing_for
from tg_assistant.ai.engine import AiEngine
from tg_assistant.ai.rag import RagService
from tg_assistant.db.models import AiBudgetReservation, TelegramChatPolicy, TelegramMessage
from tg_assistant.runtime import Application, make_budget
from tg_assistant.services.first_value import FirstValueService
from tg_assistant.services.first_value_index import CurrentIndexReader

PROVIDERS = ["openai", "openrouter"]


def cloud(provider):
    return dict(ai_provider=provider, embedding_provider=provider, cloud_embedding_dimension=3,
                cloud_consent=True)


def cloud_ledger(env, row, **changes):
    """Rewrite the local-zero fixture ledger as a coherent settled cloud embedding row."""
    snapshot = pricing_for(env.profile.provider, env.profile.model)
    assert snapshot.cost(10, 0) > 0
    stamp = datetime.now(UTC) - timedelta(seconds=1)
    values = dict(
        is_local=False, pricing_version=snapshot.version, pricing_rates=snapshot.as_dict(),
        reserved_cost_usd=snapshot.reserve_cost(10, 0), actual_cost_usd=snapshot.cost(10, 0),
        occurred_at=stamp, submitted_at=stamp, settled_at=stamp,
    )
    env.update(AiBudgetReservation, AiBudgetReservation.request_id == row.request_id,
               **{**values, **changes})


# --------------------------------------------------------------------------- A: cloud ledger


@pytest.mark.parametrize("provider", PROVIDERS)
def test_coherent_settled_cloud_embedding_ledger_qualifies(tmp_path, provider):
    async def body(env):
        assert env.profile.provider == provider and env.profile.dimension == 3
        row = env.add_indexed(10)
        assert await env.reader.read_source_binding(CHAT) is None  # local-zero ledger is wrong
        cloud_ledger(env, row)
        _, binding = await env.bound()
        checked = await env.checked(binding, row)
        assert checked is not None
        assert env.current(binding, None) is True
        assert env.current(binding, checked) is True

    run(tmp_path, body, **cloud(provider))


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("values", [
    {"is_local": True},
    {"actual_cost_usd": 0},
    {"actual_cost_usd": 1},
], ids=["wrong_locality", "cost_zero", "cost_too_high"])
def test_cloud_embedding_with_wrong_locality_or_actual_cost_is_refused(tmp_path, provider, values):
    async def body(env):
        row = env.add_indexed(10)
        cloud_ledger(env, row)
        _, binding = await env.bound()
        checked = await env.checked(binding, row)
        assert checked is not None
        cloud_ledger(env, row, **values)
        assert env.current(binding, checked) is False
        assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body, **cloud(provider))


@pytest.mark.parametrize("provider", PROVIDERS)
def test_local_only_source_excludes_cloud_embedding_with_matching_metadata(tmp_path, provider):
    async def body(env):
        row = env.add_indexed(10)
        cloud_ledger(env, row)
        assert await env.reader.read_source_binding(CHAT) is not None
        env.update(TelegramChatPolicy, TelegramChatPolicy.chat_id == CHAT, ai_mode="local_only")
        env.update(TelegramMessage, TelegramMessage.id == row.row_id, metadata_json={
            "embedding_store_id": env.profile.store_id, "embedding_content_hash": row.digest,
            "embedding_request_id": row.request_id,
            "embedding_policy_version": current_policy_version(env)})
        assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body, **cloud(provider))


# --------------------------------------------------------------------------- B: Application


async def adopt_application(env):
    """Replace the RuntimeShell owner with the actual Application, AiEngine and RagService."""
    shell, bot = env.runtime, env.runtime.bot_runtime
    await env.reader.close()
    await env.first_value.close()
    settings = env.settings
    app = Application(settings=settings)  # explicit settings: no get_settings/profile open
    app.database, app.policy, app.user = shell.database, shell.policy, shell.user
    app._knowledge_lock = shell._knowledge_lock
    app.budget = make_budget(settings)
    app.ai = AiEngine(
        api_key=None, budget=app.budget, provider=settings.ai_provider,
        base_url=settings.active_ai_base_url, model=settings.active_ai_model,
        embedding_model=settings.provider_embedding_model,
        max_output_tokens=settings.max_output_tokens_per_request,
    )
    env.application = app
    app.rag = RagService(app.policy, app.ai, shell.rag.vectors)
    app.rag.database = app.database
    bot._runtime = app
    app.bot_runtime = bot
    app.admitted = True  # read solely by BotShell.management_admitted
    app.first_value = FirstValueService(runtime=app, coordinator=env.coordinator,
                                        windows_sid=env.sid)
    env.runtime, env.first_value = app, app.first_value
    env.reader = CurrentIndexReader(runtime=app, first_value=app.first_value, windows_sid=env.sid)
    return app


def run_application(tmp_path, body):
    import asyncio

    async def main():
        env = await Env.create(tmp_path)
        env.application = None
        try:
            await adopt_application(env)
            await body(env, env.application)
        finally:
            await env.aclose()
            if env.application is not None:
                await env.application.ai.close()  # the borrowed AsyncOpenAI client

    asyncio.run(main())


def test_actual_application_and_rag_service_own_the_current_index(tmp_path):
    async def body(env, app):
        row = env.add_indexed(10)
        reader, client = env.reader, app.ai.client
        assert type(app) is Application and env.runtime is app
        assert type(app.rag) is RagService and type(app.ai) is AiEngine
        assert app.rag.ai is app.ai is app.rag.embedding_ai and client is not None
        assert app.rag.vectors is env.vectors and app.rag.database is app.database
        assert app.bot_runtime._runtime is app and app.first_value._bot is app.bot_runtime
        assert app.management_admitted() is True
        _, binding = await env.bound()
        checked = await env.checked(binding, row)
        assert checked is not None
        assert env.current(binding, None) is True
        assert env.current(binding, checked) is True
        assert env.reader is reader and app.ai.client is client and app.rag.ai is app.ai
        app.stopping.set()
        assert app.management_admitted() is False
        assert env.current(binding, checked) is False
        assert env.current(binding, None) is False
        assert await env.checked(binding, row) is None
        assert await env.reader.read_source_binding(CHAT) is None
        assert PROFILE_ID == app.settings.profile_id

    run_application(tmp_path, body)
