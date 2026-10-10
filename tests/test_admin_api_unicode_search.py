from __future__ import annotations

import unicodedata
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tg_assistant.admin_api import AdminContext, create_admin_app, dashboard_login_code
from tg_assistant.config import Settings
from tg_assistant.db.base import Base, Database
from tg_assistant.db.models import AuditLog, TelegramChat
from tg_assistant.policy import PolicyEngine
from tg_assistant.services.ollama import OllamaService

BASE = datetime(2026, 1, 1, tzinfo=UTC)
NFD_TITLE = unicodedata.normalize("NFD", "Nhóm đầu tư")

CHATS = [
    (-1001, "Nhóm ĐẦU TƯ", "grp_one"),
    (-1002, NFD_TITLE, None),
    (-1003, "Nhom dau tu", None),
    (-1004, "a_b", None),
    (-1005, "axb", None),
    (-1006, "giảm 100% hôm nay", None),
    (-1007, "giảm 1005 hôm nay", None),
]

# (action, target_id, reason); occurred_at increases with list position.
AUDITS = [
    ("search", None, "Hợp đồng ĐẦU TƯ"),
    ("search", None, unicodedata.normalize("NFD", "hợp đồng đầu tư")),
    ("search", None, "hop dong dau tu"),
    ("probe", "-1009876", None),
    ("search", None, "giảm 50% hôm nay"),
    ("search", None, "giảm 505 hôm nay"),
    ("search", None, "file_a"),
    ("search", None, "fileXa"),
]


@pytest.fixture
async def admin_client(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'admin-search.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with database.session() as session:
        for chat_id, title, username in CHATS:
            session.add(
                TelegramChat(
                    chat_id=chat_id, title=title, username=username, chat_type="supergroup"
                )
            )
        for index, (action, target_id, reason) in enumerate(AUDITS, start=1):
            session.add(
                AuditLog(
                    occurred_at=BASE + timedelta(minutes=index),
                    action=action,
                    target_id=target_id,
                    outcome="success",
                    reason=reason,
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
            management_admission=lambda: True,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://127.0.0.1:8765",
    ) as client:
        login = await client.post(
            "/api/v1/auth/login", json={"code": dashboard_login_code(secret)}
        )
        assert login.status_code == 200
        yield client
    await ollama.close()
    await database.close()


async def group_ids(client, query, **params):
    response = await client.get("/api/v1/groups", params={"query": query, **params})
    assert response.status_code == 200
    body = response.json()
    return [int(item["chat_id"]) for item in body["items"]], body["total"]


async def audit_reasons(client, query, **params):
    response = await client.get("/api/v1/audit", params={"query": query, **params})
    assert response.status_code == 200
    body = response.json()
    return [item["reason"] or item["action"] for item in body["items"]], body["total"]


async def test_groups_search_unicode_uppercase_and_nfd(admin_client):
    ids, total = await group_ids(admin_client, "đầu tư", sort_by="title", sort_dir="asc")
    assert sorted(ids) == [-1002, -1001]
    assert total == 2
    ids, _ = await group_ids(admin_client, unicodedata.normalize("NFD", "ĐẦU TƯ"))
    assert sorted(ids) == [-1002, -1001]
    ids, _ = await group_ids(admin_client, "dau tu")
    assert ids == [-1003]  # accent-stripped query does not match accented titles


async def test_groups_search_literal_wildcards_username_null_and_chat_id(admin_client):
    assert (await group_ids(admin_client, "a_b"))[0] == [-1004]
    assert (await group_ids(admin_client, "100%"))[0] == [-1006]  # not "1005"
    assert (await group_ids(admin_client, "@GRP_ONE"))[0] == [-1001]  # NULL usernames tolerated
    assert (await group_ids(admin_client, "-1003"))[0] == [-1003]  # chat_id cast
    ids, total = await group_ids(admin_client, "1005")  # chat_id -1005 and title "1005"
    assert sorted(ids) == [-1007, -1005]
    assert total == 2


async def test_groups_search_pages_and_total(admin_client):
    first, total = await group_ids(
        admin_client, "đầu tư", sort_by="title", sort_dir="asc", page=1, page_size=1
    )
    second, second_total = await group_ids(
        admin_client, "đầu tư", sort_by="title", sort_dir="asc", page=2, page_size=1
    )
    assert total == second_total == 2
    assert sorted(first + second) == [-1002, -1001]
    assert first != second


async def test_audit_search_unicode_literal_null_and_target_id(admin_client):
    reasons, total = await audit_reasons(admin_client, "ĐẦU TƯ")
    assert total == 2
    assert reasons == [  # occurred_at desc
        unicodedata.normalize("NFD", "hợp đồng đầu tư"),
        "Hợp đồng ĐẦU TƯ",
    ]
    assert (await audit_reasons(admin_client, unicodedata.normalize("NFD", "hợp đồng")))[1] == 2
    assert (await audit_reasons(admin_client, "hop dong"))[0] == ["hop dong dau tu"]
    assert (await audit_reasons(admin_client, "50%"))[0] == ["giảm 50% hôm nay"]
    assert (await audit_reasons(admin_client, "file_a"))[0] == ["file_a"]
    # Row with NULL reason matched through target_id; NULL columns do not break others.
    assert (await audit_reasons(admin_client, "9876"))[0] == ["probe"]


async def test_audit_search_pages_limit_and_total(admin_client):
    first, total = await audit_reasons(admin_client, "ĐẦU TƯ", page=1, page_size=1)
    second, second_total = await audit_reasons(admin_client, "ĐẦU TƯ", page=2, page_size=1)
    assert total == second_total == 2
    assert first == [unicodedata.normalize("NFD", "hợp đồng đầu tư")]
    assert second == ["Hợp đồng ĐẦU TƯ"]


async def test_search_endpoints_keep_owner_management_denial(admin_client):
    application = admin_client._transport.app
    application.state.admin_context.management_admission = lambda: False
    for path in ("/api/v1/groups", "/api/v1/audit"):
        response = await admin_client.get(path, params={"query": "đầu tư"})
        assert response.status_code == 403
