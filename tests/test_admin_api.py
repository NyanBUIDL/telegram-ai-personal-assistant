from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from fastapi import HTTPException

from tg_assistant.admin_api import AdminContext, create_admin_app, dashboard_login_code
from tg_assistant.admin_api.app import _job_json, _update_job_state
from tg_assistant.config import Settings
from tg_assistant.db.base import Base, Database
from tg_assistant.db.models import BackgroundJob, TelegramChat, TelegramMessage
from tg_assistant.policy import PolicyEngine
from tg_assistant.services.ollama import OllamaService


@pytest.fixture
async def admin_client(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'admin.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with database.session() as session:
        session.add(
            TelegramChat(
                chat_id=-1001315055119,
                title="Coin68 Community | Chat",
                username="Group_68Trading",
                chat_type="supergroup",
            )
        )

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        dashboard_dist_path=tmp_path / "missing-dashboard",
    )
    ollama = OllamaService(
        settings.ollama_base_url,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"models": []})),
    )

    async def switch_provider(provider: str) -> str:
        return provider

    async def activate_ollama(chat_model: str, embedding_model: str, dimension: int) -> str:
        return f"{chat_model}:{embedding_model}:{dimension}"

    async def no_jobs(_session) -> int:
        return 0

    async def filter_history_posts(_session, _chat_id, _instruction, posts):
        return [
            {
                "message_id": post["message_id"],
                "match": True,
                "confidence": 91,
                "reason": "Khớp tiêu chí kiểm thử.",
            }
            for post in posts
        ]

    secret = "dashboard-test-secret"
    application = create_admin_app(
        AdminContext(
            database=database,
            policy=PolicyEngine(),
            owner_id=123456,
            settings_getter=lambda: settings,
            vectors_getter=lambda: None,
            ollama=ollama,
            ai_switch_handler=switch_provider,
            ollama_activate_handler=activate_ollama,
            pause_all_handler=no_jobs,
            resume_all_handler=no_jobs,
            paths={"data": tmp_path, "downloads": tmp_path / "downloads"},
            admin_secret=secret,
            history_ai_filter_handler=filter_history_posts,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://127.0.0.1:8765",
    ) as client:
        yield client, secret
    await ollama.close()
    await database.close()


async def test_admin_api_requires_login_and_csrf(admin_client) -> None:
    client, secret = admin_client
    health = await client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["loopback_only"] is True
    assert (await client.get("/api/v1/overview")).status_code == 401

    login = await client.post(
        "/api/v1/auth/login",
        json={"code": dashboard_login_code(secret)},
    )
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]

    groups = await client.get("/api/v1/groups")
    assert groups.status_code == 200
    assert groups.json()["items"][0]["title"] == "Coin68 Community | Chat"

    action_body = {
        "action_type": "set_chat_allowed",
        "payload": {"allowed": True, "memory_action": "keep"},
        "preview": "Cho phép group",
    }
    missing_csrf = await client.post(
        "/api/v1/groups/-1001315055119/actions",
        json=action_body,
    )
    assert missing_csrf.status_code == 403

    created = await client.post(
        "/api/v1/groups/-1001315055119/actions",
        json=action_body,
        headers={
            "X-CSRF-Token": csrf,
            "Referer": "http://127.0.0.1:8765/groups/-1001315055119",
        },
    )
    assert created.status_code == 201
    assert created.json()["status"] == "pending"

    confirmed = await client.post(
        f"/api/v1/pending-actions/{created.json()['action_id']}/confirm",
        headers={"X-CSRF-Token": csrf},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"


async def test_admin_api_rejects_invalid_login_code(admin_client) -> None:
    client, _secret = admin_client
    response = await client.post("/api/v1/auth/login", json={"code": "00000000"})
    assert response.status_code == 401


async def test_admin_api_creates_backfill_preview_and_exports_history(admin_client) -> None:
    client, secret = admin_client
    login = await client.post(
        "/api/v1/auth/login",
        json={"code": dashboard_login_code(secret)},
    )
    csrf = login.json()["csrf_token"]
    chat_id = -1001315055119

    created = await client.post(
        f"/api/v1/groups/{chat_id}/actions",
        json={
            "action_type": "backfill_chat_history",
            "payload": {"batch_size": 500},
            "preview": "Quét toàn bộ lịch sử.",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    assert created.json()["action_type"] == "backfill_chat_history"

    exported = await client.get(
        f"/api/v1/groups/{chat_id}/history-export",
        params={"search_terms": "SOL, ETF"},
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    assert "is_promotion" in exported.text
    assert "matches_search_terms" in exported.text

    jobs = await client.get("/api/v1/history-backfill-jobs", params={"chat_id": chat_id})
    assert jobs.status_code == 200
    assert jobs.json()["items"] == []


async def test_admin_api_lists_and_previews_selected_history_link_posts(admin_client) -> None:
    client, secret = admin_client
    chat_id = -1001315055119
    async with client._transport.app.state.admin_context.database.session() as session:
        session.add_all(
            [
                TelegramMessage(
                    chat_id=chat_id,
                    message_id=101,
                    text="Bài thường có link https://example.com",
                    sent_at=datetime.now(UTC),
                ),
                TelegramMessage(
                    chat_id=chat_id,
                    message_id=102,
                    text="Airdrop promotion https://example.com",
                    sent_at=datetime.now(UTC),
                ),
            ]
        )
    login = await client.post(
        "/api/v1/auth/login", json={"code": dashboard_login_code(secret)}
    )
    csrf = login.json()["csrf_token"]

    candidates = await client.get(
        f"/api/v1/groups/{chat_id}/history-delete-candidates",
        params={"mode": "all_links", "page": 1, "page_size": 50},
    )
    assert candidates.status_code == 200
    assert candidates.json()["total"] == 2
    assert candidates.json()["items"][0]["message_id"] == 102

    action = await client.post(
        f"/api/v1/groups/{chat_id}/history-delete-preview",
        json={"mode": "all_links", "selected_message_ids": [102]},
        headers={"X-CSRF-Token": csrf},
    )
    assert action.status_code == 201
    assert action.json()["payload"]["candidate_count"] == 1
    assert action.json()["payload"]["selected_message_ids"] == [102]

    ai_filter = await client.post(
        f"/api/v1/groups/{chat_id}/history-ai-delete-filter",
        json={
            "mode": "all_links",
            "instruction": "Tìm quảng cáo cần xóa",
            "message_ids": [102],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert ai_filter.status_code == 200
    assert ai_filter.json()["items"][0]["confidence"] == 91


async def test_admin_api_knowledge_coverage_export_and_enable_all_preview(
    admin_client,
) -> None:
    client, secret = admin_client
    login = await client.post(
        "/api/v1/auth/login",
        json={"code": dashboard_login_code(secret)},
    )
    csrf = login.json()["csrf_token"]

    search = await client.get("/api/v1/groups", params={"query": "@Group_68Trading"})
    assert search.status_code == 200
    assert search.json()["total"] == 1

    sources = await client.get("/api/v1/knowledge/sources")
    assert sources.status_code == 200
    assert sources.json()["summary"] == {
        "total_sources": 1,
        "learned_sources": 0,
        "auto_knowledge_sources": 0,
        "coverage_percent": 0.0,
        "mysql_messages": 0,
        "vectors": 0,
        "last_learned_at": None,
        "counts": {"not_learned": 1},
    }
    assert sources.json()["items"][0]["status_reason"]

    exported = await client.get("/api/v1/knowledge/sources/export")
    assert exported.status_code == 200
    assert "Coin68 Community | Chat" in exported.content.decode("utf-8-sig")

    action = await client.post(
        "/api/v1/knowledge/enable-all-action",
        headers={"X-CSRF-Token": csrf},
    )
    assert action.status_code == 201
    assert action.json()["status"] == "pending"
    assert action.json()["payload"]["chat_ids"] == [-1001315055119]
    assert "1 group/channel" in action.json()["preview"]


async def test_admin_api_operational_filters_selection_preferences_and_docs(
    admin_client,
) -> None:
    client, secret = admin_client
    login = await client.post(
        "/api/v1/auth/login",
        json={"code": dashboard_login_code(secret)},
    )
    csrf = login.json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf}

    groups = await client.get(
        "/api/v1/groups",
        params={
            "chat_type": "supergroup",
            "policy_status": "block",
            "sort_by": "title",
            "sort_dir": "asc",
            "page_size": 25,
        },
    )
    assert groups.status_code == 200
    assert groups.json()["page_size"] == 25
    assert groups.json()["items"][0]["activity"]["state_60d"] == "unknown"

    preferences = {
        "density": "compact",
        "group_page_size": 25,
        "knowledge_page_size": 50,
        "visible_group_columns": ["policy", "learning"],
        "saved_views": [{"name": "Supergroup", "page": "groups", "state": {}}],
        "always_keep_chat_ids": [-1001315055119],
        "ignored_recommendation_chat_ids": [],
    }
    saved = await client.put("/api/v1/preferences", json=preferences, headers=headers)
    assert saved.status_code == 200
    assert (await client.get("/api/v1/preferences")).json()["density"] == "compact"

    selection = await client.post(
        "/api/v1/knowledge/enable-selection-action",
        json={"chat_ids": [-1001315055119], "limit": 500},
        headers=headers,
    )
    assert selection.status_code == 201
    assert selection.json()["payload"]["chat_ids"] == [-1001315055119]

    note = await client.put(
        "/api/v1/knowledge/sources/-1001315055119/note",
        json={"note": "Nguồn cần ưu tiên"},
        headers=headers,
    )
    assert note.status_code == 200
    assert note.json()["owner_note"] == "Nguồn cần ưu tiên"

    delete_preview = await client.post(
        "/api/v1/knowledge/sources/-1001315055119/delete-preview",
        json={"scope": "vectors_only"},
        headers=headers,
    )
    assert delete_preview.status_code == 201
    assert delete_preview.json()["action_type"] == "delete_learned_data"

    docs = await client.get("/api/v1/docs")
    assert docs.status_code == 200
    assert any(item["id"] == "user-guide" and item["available"] for item in docs.json()["items"])
    guide = await client.get("/api/v1/docs/user-guide")
    assert guide.status_code == 200
    assert "bộ não chung" in guide.json()["content"]


def test_retry_is_only_available_for_failed_learning_jobs() -> None:
    completed = BackgroundJob(
        job_type="learn_group",
        status="completed",
        payload={"chat_id": 1},
    )
    with pytest.raises(HTTPException) as error:
        _update_job_state(completed, "retry")
    assert error.value.status_code == 409

    failed = BackgroundJob(
        job_type="learn_group",
        status="failed",
        attempts=3,
        payload={"chat_id": 1},
        last_error="embedding timeout",
    )
    _update_job_state(failed, "retry")
    assert failed.status == "queued"
    assert failed.attempts == 0
    assert failed.last_error is None


def test_learning_job_json_normalizes_progress_contract() -> None:
    queued = BackgroundJob(
        job_type="learn_group",
        status="queued",
        payload={"phase": "embedding", "progress": 77},
    )
    completed = BackgroundJob(
        job_type="learn_group",
        status="completed",
        payload={"phase": "completed", "progress": 0, "indexed_count": 12},
    )
    unknown = BackgroundJob(
        job_type="learn_group",
        status="legacy",
        payload={"phase": "unknown"},
    )

    queued_json = _job_json(queued)
    completed_json = _job_json(completed)
    unknown_json = _job_json(unknown)

    assert queued_json["phase"] == "queued"
    assert queued_json["progress"] == 0
    # Status alone is not evidence that an old/incomplete payload reached 100%.
    assert completed_json["progress"] == 0
    assert completed_json["vectors_created"] == 12
    assert unknown_json["progress"] is None


def test_learning_job_json_preserves_reconciliation_warning_contract() -> None:
    warning = BackgroundJob(
        job_type="learn_group",
        status="completed_with_warning",
        payload={
            "phase": "reconciliation_required",
            "progress": 100,
            "evaluated": 10,
            "indexed_new": 4,
            "reused_existing": 2,
            "filtered": 1,
            "duplicate": 1,
            "skipped": 2,
            "failed": 0,
            "vectors_before": 257,
            "vectors_after": 41,
            "invariant_ok": True,
        },
    )

    payload = _job_json(warning)

    assert payload["status"] == "completed_with_warning"
    assert payload["phase"] == "reconciliation_required"
    assert payload["evaluated"] == 10
    assert payload["vectors_before"] == 257
    assert payload["vectors_after"] == 41
