"""Restricted audit pages against migrated on-disk SQLite and real Admin auth."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, event, text

from tg_assistant.admin_api import AdminContext, create_admin_app, dashboard_login_code
from tg_assistant.admin_api.auth import SESSION_COOKIE
from tg_assistant.config import Settings
from tg_assistant.contracts import PublicProfile
from tg_assistant.db.models import AuditLog
from tg_assistant.policy import PolicyEngine
from tg_assistant.services.actions import SUPPORTED_ACTIONS
from tg_assistant.services.ollama import OllamaService
from tg_assistant.services.storage import StorageService

TIME = datetime(2026, 10, 10, 3, 4, 5, 123456, tzinfo=UTC)
FIELDS = {"occurred_at", "action", "target_type", "outcome"}


def private_check(passed, message):
    if not passed:
        pytest.fail(message, pytrace=False)


@pytest.fixture
async def support_admin(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "profile")

    class NoSecrets:
        def get(self, _name):
            pytest.fail("Support fixture accessed a credential")

    storage = StorageService(settings, NoSecrets())
    database = storage.open(PublicProfile(
        profile_id=settings.profile_id, owner_id=None, storage_backend="sqlite",
        setup_stage="storage_ready", version=1,
    ))
    storage.migrate()
    ollama = OllamaService(settings.ollama_base_url)

    async def handler(*_args):
        return 0

    secret = uuid4().hex
    app = create_admin_app(AdminContext(
        database=database, policy=PolicyEngine(), owner_id=123456,
        settings_getter=lambda: settings, vectors_getter=lambda: None, ollama=ollama,
        ai_switch_handler=handler, ollama_activate_handler=handler,
        pause_all_handler=handler, resume_all_handler=handler,
        paths={"data": settings.data_dir}, admin_secret=secret,
        management_admission=lambda: True,
    ))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8765",
    ) as client:
        yield client, app, database, secret
    await database.close()
    await ollama.close()
    storage.fence.close()


async def login(fixture):
    client, app, database, secret = fixture
    response = await client.post("/api/v1/auth/login", json={"code": dashboard_login_code(secret)})
    assert response.status_code == 200
    async with database.session() as db:
        await db.execute(delete(AuditLog))
    return app.state.admin_auth.get_session(client.cookies.get(SESSION_COOKIE))


def row(**values):
    return AuditLog(occurred_at=TIME, action="dashboard_login", target_type="dashboard",
                    outcome="success", **values)


async def seed(database, rows):
    async with database.session() as db:
        db.add_all(rows)


async def export(client, **params):
    response = await client.get("/api/v1/audit", params={"support_export": "true", **params})
    assert response.status_code == 200
    return response


async def test_support_omits_every_identifier_and_free_text_canary(support_admin):
    client, _, database, _ = support_admin
    await login(support_admin)
    nonce = uuid4().hex
    canaries = [nonce, f"otp={nonce}", f"password={nonce}", f"sk-{nonce}",
                f"123456789:{nonce}", f"https://user:{nonce}@example.invalid/",
                f"C:\\Users\\{nonce}\\private.session", f"Telegram source {nonce}"]
    await seed(database, [row(actor_id=9007199254740993, target_id=value,
                             reason=value, correlation_id=value,
                             details_redacted={"content": value}) for value in canaries])
    response = await export(client)
    body = response.json()
    assert set(body) == {"items", "page", "page_size", "total"}
    assert body["page"] == 1 and body["page_size"] == 100 and body["total"] == len(canaries)
    assert all(set(item) == FIELDS for item in body["items"])
    private_check(not any(value in response.text for value in [*canaries, "9007199254740993"]),
                  "Support response contains a private canary")
    assert all(datetime.fromisoformat(item["occurred_at"]) == TIME for item in body["items"])
    assert all(item["occurred_at"].endswith(("Z", "+00:00")) for item in body["items"])


@pytest.mark.parametrize("value_kind", ["private", "plausible", "oversized", "unicode", "nul"])
async def test_support_unknown_event_values_are_fixed_safe_literals(support_admin, value_kind):
    client, _, database, _ = support_admin
    await login(support_admin)
    nonce = uuid4().hex
    value = {"private": f"password={nonce}", "plausible": "unrecognized_valid_identifier",
             "oversized": nonce * 40000, "unicode": "đ" * 129,
             "nul": "dashboard_login\x00" + nonce}[value_kind]
    entry = row()
    entry.action = entry.target_type = entry.outcome = value
    await seed(database, [entry])
    body = (await export(client)).json()
    item = body["items"][0]
    assert set(item) == FIELDS
    private_check(item["action"] == "other" and item["target_type"] == "other"
                  and item["outcome"] == "unknown", "Unknown event value crossed projection")


async def test_support_preserves_known_producer_types_results_and_pending_actions(support_admin):
    client, _, database, _ = support_admin
    await login(support_admin)
    triples = [(action, "telegram_message", "success") for action in sorted(SUPPORTED_ACTIONS)]
    triples += [("auto_delete_non_admin_link", "telegram_message", outcome)
                for outcome in ("denied_kept", "uncertain", "failed_safe_kept")]
    triples += [("dashboard_learning_pause", "background_job", "success"),
                ("vector_recovery_pause", "background_job", "pause_requested"),
                ("history_backfill_completed", "telegram_chat", "success"),
                ("vector_coverage_warning", "knowledge_source", "warning")]
    entries = []
    for action, target, outcome in triples:
        entry = row()
        entry.action, entry.target_type, entry.outcome = action, target, outcome
        entries.append(entry)
    await seed(database, entries)
    items = (await export(client)).json()["items"]
    assert {(item["action"], item["target_type"], item["outcome"]) for item in items} == set(triples)


async def test_support_sql_selects_only_bounded_projection_without_loading_json(support_admin):
    client, _, database, _ = support_admin
    await login(support_admin)
    await seed(database, [row()])
    async with database.session() as db:
        await db.execute(text("UPDATE audit_logs SET details_redacted=:payload, reason=:payload, "
                              "target_id=:payload, correlation_id=:payload"),
                         {"payload": "{not-json:" + uuid4().hex * 100000})
    selections = []

    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT") and "audit_logs" in statement:
            selections.append(statement.split("\nFROM", 1)[0])

    def forbidden_load(*_args):
        pytest.fail("Support projection ORM-loaded an audit entity")

    event.listen(database.engine.sync_engine, "before_cursor_execute", record)
    event.listen(AuditLog, "load", forbidden_load)
    try:
        body = (await export(client)).json()
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", record)
        event.remove(AuditLog, "load", forbidden_load)
    assert body["total"] == 1 and set(body["items"][0]) == FIELDS
    projection = next(statement for statement in selections if "count(" not in statement.lower())
    assert not any(f"audit_logs.{name}" in projection for name in
                   ("actor_id", "target_id", "reason", "details_redacted", "correlation_id", "id"))
    assert projection.lower().count("case") == 4
    assert projection.lower().count("length(") == 4 and "BLOB" in projection


async def test_support_keeps_literal_folded_filters_page_scope_and_bounds(support_admin):
    client, _, database, _ = support_admin
    await login(support_admin)
    entries = [row(reason="TỪ KHÓA %_", target_id="filter target") for _ in range(4)]
    entries[0].outcome = "failed"
    await seed(database, entries)
    body = (await export(client, query="từ khóa %_", outcome="success", page=2, page_size=2)).json()
    assert body["total"] == 3 and len(body["items"]) == 1
    assert body["page"] == 2 and body["page_size"] == 2
    assert (await export(client, query="filter target")).json()["total"] == 4
    assert (await export(client, query="dashboard_login")).json()["total"] == 4
    assert (await export(client, outcome="not_known")).json()["total"] == 0
    assert (await export(client, page=100, page_size=500)).json()["items"] == []
    for params in ({"page": 0}, {"page_size": 0}, {"page_size": 501},
                   {"query": "x" * 201}, {"outcome": "x" * 33}, {"support_export": "invalid"}):
        response = await client.get("/api/v1/audit", params={"support_export": "true", **params})
        assert response.status_code == 422


async def test_support_enforces_maximum_materialized_page_and_unknown_target(support_admin):
    client, _, database, _ = support_admin
    await login(support_admin)
    entries = [row() for _ in range(501)]
    entries[0].target_type = None
    await seed(database, entries)
    first = (await export(client, page_size=500)).json()
    second = (await export(client, page_size=500, page=2)).json()
    assert first["total"] == second["total"] == 501
    assert len(first["items"]) == 500 and len(second["items"]) == 1
    assert {item["target_type"] for item in [*first["items"], *second["items"]]} == {"dashboard", "other"}


@pytest.mark.parametrize("denial", ["no_session", "setup_only", "wrong_owner", "expired", "withdrawn"])
async def test_support_denies_before_audit_content_query(support_admin, denial):
    client, app, database, _ = support_admin
    session = await login(support_admin)
    await seed(database, [row(reason=uuid4().hex)])
    if denial == "no_session":
        client.cookies.clear()
    elif denial == "setup_only":
        app.state.admin_auth.sessions[session.token] = replace(session, authority="setup_only")
    elif denial == "wrong_owner":
        app.state.admin_auth.sessions[session.token] = replace(session, owner_id=session.owner_id + 1)
    elif denial == "expired":
        app.state.admin_auth.sessions[session.token] = replace(
            session, expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    else:
        app.state.admin_context.management_admission = lambda: False
    selections = []

    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        if "audit_logs" in statement:
            selections.append(True)

    event.listen(database.engine.sync_engine, "before_cursor_execute", record)
    try:
        response = await client.get("/api/v1/audit", params={"support_export": "true"})
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", record)
    assert response.status_code == (401 if denial in {"no_session", "expired"} else 403)
    assert not selections and "items" not in response.json()


async def test_support_read_needs_no_write_csrf_and_host_boundary_remains(support_admin):
    client, _, database, _ = support_admin
    await login(support_admin)
    await seed(database, [row()])
    assert (await export(client)).json()["total"] == 1
    response = await client.get("/api/v1/audit?support_export=true", headers={"Host": "evil.invalid"})
    assert response.status_code == 400
    assert (await client.post("/api/v1/audit?support_export=true", json={})).status_code == 405


@pytest.mark.parametrize("withdrawal", ["owner", "session"])
async def test_support_withdrawal_during_read_returns_no_support_bytes(support_admin, withdrawal):
    client, app, database, _ = support_admin
    session = await login(support_admin)
    await seed(database, [row()])

    def withdraw(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT") and "audit_logs" in statement:
            if withdrawal == "owner":
                app.state.admin_context.management_admission = lambda: False
            else:
                app.state.admin_auth.revoke_session(session.token)

    event.listen(database.engine.sync_engine, "after_cursor_execute", withdraw)
    try:
        response = await client.get("/api/v1/audit?support_export=true")
    finally:
        event.remove(database.engine.sync_engine, "after_cursor_execute", withdraw)
    assert response.status_code in {401, 403}
    assert "items" not in response.json()


@pytest.mark.parametrize("stored_time", ["2026-10-10 10:04:05.123456+07:00", "invalid", "x" * 100000,
                                       "0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"],
                         ids=["offset", "malformed", "oversized", "utc-underflow", "utc-overflow"])
async def test_support_time_is_canonical_utc_or_unknown(support_admin, stored_time):
    client, _, database, _ = support_admin
    await login(support_admin)
    await seed(database, [row()])
    async with database.session() as db:
        await db.execute(text("UPDATE audit_logs SET occurred_at=:value"), {"value": stored_time})
    item = (await export(client)).json()["items"][0]
    if stored_time.startswith("2026"):
        assert datetime.fromisoformat(item["occurred_at"]) == TIME
        assert item["occurred_at"].endswith(("Z", "+00:00"))
    else:
        assert item["occurred_at"] is None


async def test_normal_audit_keeps_keys_and_reapplies_recognized_redaction(support_admin):
    client, _, database, _ = support_admin
    await login(support_admin)
    token = "sk-" + uuid4().hex
    entry = row(actor_id=123456, reason=token, target_id=token, correlation_id=token,
                details_redacted={"content": token, "nested": [{"otp": uuid4().hex}]})
    entry.action = entry.target_type = entry.outcome = token
    await seed(database, [entry])
    response = await client.get("/api/v1/audit")
    assert response.status_code == 200
    body = response.json()
    assert set(body["items"][0]) == {"id", "occurred_at", "actor_id", "action", "target_type",
                                     "target_id", "outcome", "reason", "details", "correlation_id"}
    private_check(token not in response.text, "Normal audit returned a recognized secret token")
    item = body["items"][0]
    assert item["actor_id"] == "123456"
    private_check(all(item[name] == "[REDACTED]" for name in
                      ("action", "target_type", "outcome", "target_id", "reason", "correlation_id")),
                  "Normal audit string redaction failed")
    private_check(item["details"] == {"content": "[REDACTED]", "nested": [{"otp": "[REDACTED]"}]},
                  "Normal audit recursive redaction failed")


@pytest.mark.parametrize("location", ["page", "page_size", "support_export", "outcome",
                                       "body_key", "path", "body_validator", "malformed_json"])
async def test_validation_rejection_never_echoes_body_query_key_context_or_location(support_admin, location):
    client, _, _, _ = support_admin
    session = await login(support_admin)
    canary = "private-" + uuid4().hex
    headers = {"Origin": "http://127.0.0.1:8765", "X-CSRF-Token": session.csrf_token}
    if location in {"page", "page_size", "support_export", "outcome"}:
        response = await client.get("/api/v1/audit", params={location: canary})
    elif location == "body_key":
        response = await client.post("/api/v1/auth/login", json={"code": {canary: canary}})
    elif location == "path":
        response = await client.get(f"/api/v1/groups/{canary}")
    elif location == "body_validator":
        response = await client.put("/api/v1/groups/123/ai-route", headers=headers,
                                    json={"mode": canary})
    else:
        response = await client.post("/api/v1/auth/login", content='{"code": "' + canary,
                                     headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    private_check(response.json() == {
        "code": "invalid_request", "detail": "Dữ liệu yêu cầu không hợp lệ.",
    }, "Validation response was not the fixed sanitized envelope")
    private_check(canary not in response.text, "Validation response leaked a private input")


async def test_invalid_audit_query_without_authority_still_requires_login(support_admin):
    client, _, _, _ = support_admin
    response = await client.get("/api/v1/audit", params={"page": "invalid"})
    assert response.status_code == 401
