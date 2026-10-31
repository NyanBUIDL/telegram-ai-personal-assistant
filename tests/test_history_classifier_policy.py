from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import event, select

from tg_assistant.admin_api import AdminContext, create_admin_app, dashboard_login_code
from tg_assistant.ai.router import AiRouter
from tg_assistant.config import Settings
from tg_assistant.db.models import AppSetting, PermissionName, TelegramChat, TelegramMessage
from tg_assistant.policy import PolicyEngine, SlidingWindowLimiter
from tg_assistant.runtime import Application


@pytest.fixture
async def classifier_context(session, tmp_path):
    calls = []

    async def classify(_session, **kwargs):
        calls.append(kwargs)
        return [{"message_id": 1, "match": True, "confidence": 90, "reason": "matches"}]

    engine = SimpleNamespace(available=True, classify_history_delete_candidates=classify)
    application = object.__new__(Application)
    application.settings = Settings(_env_file=None, data_dir=tmp_path)
    application.user = SimpleNamespace(owner_id=1)
    application.policy = PolicyEngine(SlidingWindowLimiter(limit=1))
    application.ai_router = AiRouter(
        {"openai": engine, "openrouter": engine, "ollama": engine}, default_provider="openai"
    )
    session.add(TelegramChat(chat_id=44, title="Synthetic source", chat_type="group"))
    session.add(
        TelegramMessage(
            chat_id=44,
            message_id=1,
            text="https://example.invalid selected post",
            sent_at=datetime.now(UTC),
        )
    )
    await session.flush()

    @asynccontextmanager
    async def database_session():
        yield session

    secret = "synthetic"
    api = create_admin_app(
        AdminContext(
            database=SimpleNamespace(session=database_session),
            policy=application.policy,
            owner_id=1,
            settings_getter=lambda: application.settings,
            vectors_getter=lambda: None,
            ollama=SimpleNamespace(),
            ai_switch_handler=None,
            ollama_activate_handler=None,
            pause_all_handler=None,
            resume_all_handler=None,
            paths={},
            admin_secret=secret,
            history_ai_filter_handler=application._classify_history_delete_with_openai,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://127.0.0.1:8765"
    ) as client:
        login = await client.post("/api/v1/auth/login", json={"code": dashboard_login_code(secret)})
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        yield application, client, calls


async def set_boundary(session, application, boundary):
    if boundary != "missing_policy":
        await application.policy.set_allowed(session, 44, True)
        await application.policy.set_permission(
            session, 44, PermissionName.READ_MESSAGES, boundary != "read_disabled"
        )
        if boundary == "blocked":
            await application.policy.set_allowed(session, 44, False)
    if boundary == "provider_off":
        application.settings = application.settings.model_copy(update={"ai_provider": "off"})
    elif boundary == "setting_off":
        session.add(AppSetting(key="ai_enabled", value=False))
    elif boundary == "router_off":
        application.ai_router.enabled = False
    elif boundary in {"off", "local_only", "local_first"}:
        await application.policy.set_ai_route(session, 44, mode=boundary)
    elif boundary == "inherit_local":
        application.settings = application.settings.model_copy(update={"ai_provider": "ollama"})
        application.ai_router.default_provider = "ollama"
    elif boundary == "prefer_openrouter":
        await application.policy.set_ai_route(
            session, 44, mode="cloud_only", preferred_cloud_provider="openrouter"
        )
    await session.flush()


@pytest.mark.parametrize(
    "boundary",
    [
        "missing_policy",
        "blocked",
        "read_disabled",
        "provider_off",
        "setting_off",
        "off",
        "local_only",
        "local_first",
        "inherit_local",
        "prefer_openrouter",
    ],
)
async def test_classifier_api_denies_without_cloud_call(
    session,
    classifier_context,
    boundary,
):
    application, client, calls = classifier_context
    await set_boundary(session, application, boundary)
    text_reads = []

    def record_read(_connection, _cursor, statement, _parameters, _context, _many):
        if (
            statement.lstrip().upper().startswith("SELECT")
            and "FROM telegram_messages" in statement
        ):
            text_reads.append(statement)

    event.listen(session.bind.sync_engine, "before_cursor_execute", record_read)
    try:
        response = await client.post(
            "/api/v1/groups/44/history-ai-delete-filter",
            json={
                "mode": "all_links",
                "instruction": "find selected posts",
                "message_ids": [1],
            },
        )
    finally:
        event.remove(session.bind.sync_engine, "before_cursor_execute", record_read)
    assert response.status_code == 403
    assert response.json()["detail"]
    if boundary in {"missing_policy", "blocked", "read_disabled", "provider_off", "setting_off"}:
        assert text_reads == []
    assert calls == []


@pytest.mark.parametrize(
    "boundary",
    [
        "missing_policy",
        "blocked",
        "read_disabled",
        "provider_off",
        "setting_off",
        "router_off",
        "off",
        "local_only",
        "local_first",
        "inherit_local",
        "prefer_openrouter",
    ],
)
async def test_classifier_runtime_rechecks_policy_for_alternate_callers(
    session,
    classifier_context,
    boundary,
):
    application, _client, calls = classifier_context
    await set_boundary(session, application, boundary)
    with pytest.raises(PermissionError):
        await application._classify_history_delete_with_openai(
            session, 44, "find selected posts", [{"message_id": 1, "text": "selected post"}]
        )
    assert calls == []


@pytest.mark.parametrize(
    "mode,fallback",
    [("inherit", False), ("cloud_only", False), ("cloud_first", False), ("local_first", True)],
)
async def test_classifier_allowed_request_consumes_one_limit_and_preserves_source(
    session,
    classifier_context,
    mode,
    fallback,
):
    application, client, calls = classifier_context
    await set_boundary(session, application, "allowed")
    await application.policy.set_ai_route(session, 44, mode=mode, cloud_fallback=fallback)
    await session.flush()
    response = await client.post(
        "/api/v1/groups/44/history-ai-delete-filter",
        json={
            "mode": "all_links",
            "instruction": "find selected posts",
            "message_ids": [1],
        },
    )
    assert response.status_code == 200
    assert len(calls) == 1
    assert (
        await session.scalar(select(TelegramMessage))
    ).text == "https://example.invalid selected post"
    second = await client.post(
        "/api/v1/groups/44/history-ai-delete-filter",
        json={
            "mode": "all_links",
            "instruction": "find selected posts",
            "message_ids": [1],
        },
    )
    assert second.status_code == 403
    assert len(calls) == 1


async def test_classifier_rejects_secret_instruction_at_both_boundaries(
    session, classifier_context
):
    application, client, calls = classifier_context
    await set_boundary(session, application, "allowed")
    instruction = "token=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    response = await client.post(
        "/api/v1/groups/44/history-ai-delete-filter",
        json={
            "mode": "all_links",
            "instruction": instruction,
            "message_ids": [1],
        },
    )
    assert response.status_code == 422
    with pytest.raises(ValueError, match="secret"):
        await application._classify_history_delete_with_openai(
            session, 44, instruction, [{"message_id": 1, "text": "selected post"}]
        )
    assert calls == []


async def test_classifier_checks_route_before_model_availability(session, classifier_context):
    application, client, calls = classifier_context
    await set_boundary(session, application, "allowed")
    application.ai_router.engines["openai"].available = False
    await application.policy.set_ai_route(session, 44, mode="local_only")
    await session.flush()
    payload = {"mode": "all_links", "instruction": "find selected posts", "message_ids": [1]}
    assert (
        await client.post("/api/v1/groups/44/history-ai-delete-filter", json=payload)
    ).status_code == 403
    application.policy.limiter = SlidingWindowLimiter()
    await application.policy.set_ai_route(session, 44, mode="cloud_only")
    await session.flush()
    assert (
        await client.post("/api/v1/groups/44/history-ai-delete-filter", json=payload)
    ).status_code == 503
    assert calls == []


async def test_classifier_preserves_router_provider_fallback(session, classifier_context):
    application, client, calls = classifier_context
    await set_boundary(session, application, "prefer_openrouter")
    del application.ai_router.engines["openrouter"]
    response = await client.post(
        "/api/v1/groups/44/history-ai-delete-filter",
        json={
            "mode": "all_links",
            "instruction": "find selected posts",
            "message_ids": [1],
        },
    )
    assert response.status_code == 200
    assert len(calls) == 1
