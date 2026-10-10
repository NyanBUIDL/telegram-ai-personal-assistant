"""Real authenticated routes cannot coerce IDs or survive withdrawn admission."""
# ruff: noqa: F811 - shared disposable fixture registration
import json
from dataclasses import replace

import httpx
import pytest
from test_first_value_selection import A, rows, selection, service  # noqa: F401

from tg_assistant.admin_api import AdminContext, create_admin_app
from tg_assistant.admin_api.auth import SESSION_COOKIE
from tg_assistant.config import Settings
from tg_assistant.db.base import Database
from tg_assistant.policy import PolicyEngine
from tg_assistant.services.ollama import OllamaService


@pytest.fixture
async def api(selection, tmp_path):
    # Service creation is intentionally after app construction so route RED is 404.
    database = Database("sqlite+aiosqlite:///" + str(tmp_path / "selected.db"))
    settings = Settings(_env_file=None, data_dir=tmp_path, profile_id="owner-profile",
                        dashboard_dist_path=tmp_path / "absent")
    ollama = OllamaService(settings.ollama_base_url,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"models": []})))
    async def no_jobs(*_args):
        return 0
    secret = "synthetic-admin-secret"
    context = AdminContext(database=database, policy=PolicyEngine(), owner_id=123,
        settings_getter=lambda: settings, vectors_getter=lambda: None, ollama=ollama,
        ai_switch_handler=no_jobs, ollama_activate_handler=no_jobs,
        pause_all_handler=no_jobs, resume_all_handler=no_jobs, paths={"data": tmp_path},
        admin_secret=secret, management_admission=lambda: selection.current[0])
    app = create_admin_app(context)
    auth = app.state.admin_auth
    original = auth.create_session(123)
    login = replace(original, profile_id="owner-profile")
    auth.sessions[login.token] = login
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://127.0.0.1:8765") as client:
        client.cookies.set(SESSION_COOKIE, login.token)
        yield client, context, auth, login, selection
    if selection.runtime.first_value is not None:
        await selection.runtime.first_value.close()
    await ollama.close()
    await database.close()


def headers(login):
    return {"X-CSRF-Token": login.csrf_token, "Origin": "http://127.0.0.1:8765"}


def install(context, selection):
    owner = service(selection)
    context.first_value_getter = lambda: owner
    return owner


async def test_absent_service_is_unavailable(api):
    client, _context, _auth, login, state = api
    response = await client.get("/api/v1/onboarding/first-source")
    assert response.status_code == 503, "First-source route/default-deny getter missing"
    assert rows(state) == {}
    assert (await client.post("/api/v1/onboarding/source-selection", json={"source_id": str(A)},
                              headers=headers(login))).status_code == 503


async def test_valid_selection_roundtrips_and_get_does_not_write(api):
    client, context, _auth, login, state = api
    install(context, state)
    response = await client.post("/api/v1/onboarding/source-selection",
                                 json={"source_id": str(A)}, headers=headers(login))
    assert response.status_code == 200
    assert response.json()["source_id"] == "-1009007199254740993"
    before = rows(state)
    assert (await client.get("/api/v1/onboarding/first-source")).json() == response.json()
    assert rows(state) == before


@pytest.mark.parametrize("payload", [
    {"source_id": A}, {"source_id": 1.5}, {"source_id": True}, {"source_id": None},
    {"source_id": "0"}, {"source_id": "01"}, {"source_id": "+1"},
    {"source_id": " 1"}, {"source_id": "1\n"}, {"source_id": ""}, {},
    {"source_id": str(A), "api_key": "synthetic-sensitive-marker"},
    {"source_id": str(A), "unknown": 1},
])
async def test_invalid_json_ids_and_fields_never_write_or_echo(api, payload):
    client, context, _auth, login, state = api
    install(context, state)
    response = await client.post("/api/v1/onboarding/source-selection", json=payload,
                                 headers=headers(login))
    assert response.status_code == 422
    assert "synthetic-sensitive-marker" not in response.text
    assert rows(state) == {}


@pytest.mark.parametrize("body", [b"{", b"[]", b"null", b"x" * 1025])
async def test_malformed_and_oversize_bodies_are_sanitized(api, body):
    client, context, _auth, login, state = api
    install(context, state)
    response = await client.post("/api/v1/onboarding/source-selection", content=body,
                                 headers=headers(login))
    assert response.status_code == 422
    assert rows(state) == {}


@pytest.mark.parametrize("change", ["anonymous", "setup", "owner", "profile", "csrf", "origin", "missing_origin", "missing_admission", "truthy", "throws"])
async def test_management_boundaries_deny_without_writes(api, change):
    client, context, auth, login, state = api
    install(context, state)
    write_headers = headers(login)
    if change == "anonymous":
        client.cookies.clear()
    elif change in {"setup", "owner", "profile"}:
        changes = {"setup": {"authority": "setup_only"}, "owner": {"owner_id": 456}, "profile": {"profile_id": "foreign"}}
        auth.sessions[login.token] = replace(login, **changes[change])
    elif change == "csrf":
        write_headers.pop("X-CSRF-Token")
    elif change == "origin":
        write_headers["Origin"] = "https://foreign.test"
    elif change == "missing_origin":
        write_headers.pop("Origin")
    elif change == "missing_admission":
        context.management_admission = None
    elif change == "truthy":
        context.management_admission = lambda: 1
    else:
        def throws():
            raise ValueError("synthetic-sensitive-marker")
        context.management_admission = throws
    response = await client.post("/api/v1/onboarding/source-selection",
                                 json={"source_id": str(A)}, headers=write_headers)
    assert response.status_code in {401, 403}
    assert "synthetic-sensitive-marker" not in response.text
    assert rows(state) == {}


async def test_body_await_withdraws_management_before_mutation(api):
    client, context, _auth, login, state = api
    install(context, state)
    async def chunks():
        yield b'{"source_id":'
        state.current[0] = False
        yield json.dumps(str(A)).encode() + b"}"
    response = await client.post("/api/v1/onboarding/source-selection", content=chunks(),
                                 headers=headers(login))
    assert response.status_code == 403
    assert rows(state) == {}
