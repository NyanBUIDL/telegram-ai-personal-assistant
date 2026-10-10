"""Guided preview uses disposable SQLite and the real action/application path."""
# ruff: noqa: F811 - shared fixtures
import asyncio
import importlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select, update
from test_first_value_api import api, headers, install  # noqa: F401
from test_first_value_selection import A, B, selection, service  # noqa: F401

from tg_assistant.ai.engine import AiEngine
from tg_assistant.ai.router import AiRouter
from tg_assistant.config import Settings
from tg_assistant.db.base import Database
from tg_assistant.db.models import (
    AppSetting,
    AuditLog,
    BackgroundJob,
    KnowledgeSource,
    PendingAction,
    PermissionName,
    TelegramChat,
    TelegramChatPermission,
    TelegramChatPolicy,
)
from tg_assistant.policy import PERMISSION_TEMPLATES, PolicyEngine
from tg_assistant.runtime import Application
from tg_assistant.services.actions import PendingActionService


@pytest.fixture
async def guided(selection, tmp_path):
    runtime = Application.__new__(Application)
    runtime.__dict__.update(selection.runtime.__dict__)
    selection.runtime = runtime
    runtime.bot_runtime._runtime = runtime
    runtime.settings = Settings(_env_file=None, data_dir=tmp_path, profile_id="owner-profile",
                               ai_provider="ollama", embedding_provider="ollama")
    runtime.database = Database("sqlite+aiosqlite:///" + str(tmp_path / "selected.db"))
    runtime.database.management_admission = lambda: selection.current[0]
    runtime.policy = PolicyEngine()
    runtime.user = SimpleNamespace(owner_id=123)
    runtime.stopping = asyncio.Event()
    runtime.bot = None
    runtime.ai = AiEngine(api_key=None, model="qwen3:8b", embedding_model="nomic-embed-text:latest",
                          provider="ollama", base_url="http://127.0.0.1:11434/v1", budget=None, max_output_tokens=1000)
    runtime.embedding_ai = runtime.ai
    runtime.ai_router = AiRouter({"ollama": runtime.ai}, default_provider="ollama")
    pair = ["pair-one"]
    runtime.bot_runtime.pairing_verification = lambda: SimpleNamespace(owner_id=123, fingerprint=pair[0])
    owner = service(selection)
    await owner.select_source(A)
    async with runtime.database.session() as db:
        db.add_all([TelegramChat(chat_id=chat, chat_type="supergroup", title=str(chat)) for chat in (A, B)])
        await runtime.policy.set_allowed(db, A, True)
        await runtime.policy.set_permission(db, A, PermissionName.SEND_MESSAGES, True)
        await runtime.policy.set_permission(db, A, PermissionName.GROUP_AI_ASK, True)
        await runtime.policy.set_allowed(db, B, True)
        await runtime.policy.set_permission(db, B, PermissionName.SEND_MESSAGES, True)
    yield SimpleNamespace(owner=owner, runtime=runtime, state=selection, pair=pair)
    await owner.close()
    await runtime.ai.close()
    await runtime.database.close()


def preview(guided):
    try:
        module = importlib.import_module("tg_assistant.services.first_source_preview")
    except ModuleNotFoundError:
        pytest.fail("Source-bound preview implementation missing")
    assert hasattr(guided.owner, "preview"), "Actual FirstValueService preview owner missing"
    assert isinstance(guided.owner.preview, module.FirstSourcePreviewService)
    return guided.owner.preview


async def create(guided):
    async with guided.runtime.database.session() as db:
        return await preview(guided).create(db, A, 1000)


def actions(guided):
    assert hasattr(guided.runtime, "_validate_first_source_action"), "Current Application guided validator missing"
    return PendingActionService(first_source_validator=guided.runtime._validate_first_source_action)


async def effects(guided):
    async with guided.runtime.database.session() as db:
        permissions = (await db.execute(select(TelegramChatPermission.chat_id, TelegramChatPermission.permission,
                                               TelegramChatPermission.enabled).order_by(TelegramChatPermission.id))).all()
        jobs = (await db.scalars(select(BackgroundJob))).all()
        return permissions, jobs


async def execute(guided):
    task = asyncio.create_task(guided.runtime._execute_actions())
    try:
        for _ in range(100):
            async with guided.runtime.database.session() as db:
                states = (await db.scalars(select(PendingAction.status))).all()
            if states and all(state not in {"confirmed", "executing"} for state in states):
                break
            await asyncio.sleep(.01)
        else:
            pytest.fail("Action did not finish")
    finally:
        guided.runtime.stopping.set()
        await asyncio.wait_for(task, 3)


async def test_preview_only_and_truthful_permissions(guided):
    before = await effects(guided)
    action = await create(guided)
    assert action.status == "pending" and action.chat_id == A
    assert action.action_type == "enable_group_learning"
    assert action.action_id.startswith("fv1-")
    assert await effects(guided) == before
    assert set(action.payload) == {"limit", "first_source_preview", "authorization_epochs"}
    assert "1000" in action.preview and str(A) in action.preview
    assert "send_messages" in action.preview and "group_ai_ask" in action.preview
    assert all(p.value in action.preview for p in PERMISSION_TEMPLATES["knowledge"])
    assert "http" not in action.preview and "qwen3:8b" in action.preview
    assert "chưa" in action.preview.casefold()
    assert not guided.owner.selection_status().test_available


async def test_confirm_then_real_application_queues_one_selected_source(guided):
    action = await create(guided)
    async with guided.runtime.database.session() as db:
        await actions(guided).confirm(db, action.action_id, 123)
    await execute(guided)
    permissions, jobs = await effects(guided)
    assert len(jobs) == 1 and jobs[0].job_type == "learn_group"
    assert jobs[0].payload["chat_id"] == A and jobs[0].payload["limit"] == 1000
    enabled = {name for chat, name, on in permissions if chat == A and on}
    assert enabled == {p.value for p in PERMISSION_TEMPLATES["knowledge"]} | {"group_ai_ask"}
    assert [(name, on) for chat, name, on in permissions if chat == B] == [("send_messages", True)]
    async with guided.runtime.database.session() as db:
        with pytest.raises(ValueError):
            await actions(guided).confirm(db, action.action_id, 123)
    assert len((await effects(guided))[1]) == 1


@pytest.mark.parametrize("change", ["selection", "block_regrant", "policy", "model", "endpoint", "consent", "pair", "restore", "payload", "reference", "owner"])
@pytest.mark.parametrize("boundary", ["confirm", "execute"])
async def test_changed_capture_cannot_grant_or_queue(guided, change, boundary):
    action = await create(guided)
    if boundary == "execute":
        async with guided.runtime.database.session() as db:
            await actions(guided).confirm(db, action.action_id, 123)
    if change == "selection":
        await guided.owner.select_source(B)
        await guided.owner.select_source(A)
    elif change == "block_regrant":
        async with guided.runtime.database.session() as db:
            await guided.runtime.policy.set_allowed(db, A, False)
            await guided.runtime.policy.set_allowed(db, A, True)
    elif change == "policy":
        async with guided.runtime.database.session() as db:
            await guided.runtime.policy.set_ai_route(db, A, mode="local_only")
    elif change == "model":
        guided.runtime.ai.model = "changed-model"
    elif change == "endpoint":
        guided.runtime.ai.client.base_url = "http://127.0.0.1:11435/v1"
    elif change == "consent":
        guided.runtime.settings.cloud_consent = True
    elif change == "pair":
        guided.pair[0] = "new-pair"
    elif change == "restore":
        async with guided.runtime.database.session() as db:
            row = await db.get(AppSetting, guided.owner._key)
            row.value = {**row.value, "restore_epoch": "new-restore"}
    elif change in {"payload", "reference"}:
        async with guided.runtime.database.session() as db:
            row = await db.get(PendingAction, action.action_id)
            value = dict(row.payload)
            if change == "payload":
                value.pop("first_source_preview")
            else:
                value["first_source_preview"] = "forged"
            row.payload = value
    else:
        guided.runtime.first_value = None
    before = await effects(guided)
    if boundary == "confirm":
        async with guided.runtime.database.session() as db:
            with pytest.raises((PermissionError, ValueError)):
                await actions(guided).confirm(db, action.action_id, 123)
    else:
        await execute(guided)
    assert await effects(guided) == before


@pytest.mark.parametrize("change", ["foreign", "expired", "absent_validator", "admission"])
async def test_owner_one_use_expiry_and_default_deny(guided, change):
    action = await create(guided)
    if change == "expired":
        async with guided.runtime.database.session() as db:
            await db.execute(update(PendingAction).where(PendingAction.action_id == action.action_id)
                             .values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    before = await effects(guided)
    if change == "admission":
        guided.state.current[0] = False
    try:
        async with guided.runtime.database.sessions() as db:
            with pytest.raises((PermissionError, ValueError, TimeoutError)):
                validator = PendingActionService() if change == "absent_validator" else actions(guided)
                await validator.confirm(db, action.action_id, 456 if change == "foreign" else 123)
    finally:
        guided.state.current[0] = True
    assert await effects(guided) == before


@pytest.mark.parametrize("chat", [B, 999])
async def test_unselected_or_missing_source_denied(guided, chat):
    before = await effects(guided)
    async with guided.runtime.database.session() as db:
        with pytest.raises((PermissionError, ValueError)):
            await preview(guided).create(db, chat, 1000)
    assert await effects(guided) == before


@pytest.mark.parametrize("body", [{"source_id": A}, {"source_id": True}, {"source_id": "0"},
                                   {"source_id": "01"}, {"source_id": str(A), "limit": 1}])
async def test_preview_route_strict_body_before_owner_work(api, body):
    client, context, _auth, login, state = api
    install(context, state)
    response = await client.post("/api/v1/onboarding/first-source/preview", json=body, headers=headers(login))
    assert response.status_code == 422, "Strict preview route missing"


@pytest.mark.parametrize("change", ["anonymous", "setup", "profile", "csrf", "origin", "missing_origin"])
async def test_preview_management_boundaries(api, change):
    from dataclasses import replace
    client, context, auth, login, state = api
    install(context, state)
    write_headers = headers(login)
    if change == "anonymous":
        client.cookies.clear()
    elif change == "setup":
        auth.sessions[login.token] = replace(login, authority="setup_only")
    elif change == "profile":
        auth.sessions[login.token] = replace(login, profile_id="foreign")
    elif change == "csrf":
        write_headers.pop("X-CSRF-Token")
    elif change == "origin":
        write_headers["Origin"] = "https://foreign.test"
    else:
        write_headers.pop("Origin")
    response = await client.post("/api/v1/onboarding/first-source/preview", json={"source_id": str(A)}, headers=write_headers)
    assert response.status_code in {401, 403}


async def test_guided_claim_without_current_validator_denied(guided):
    action = await create(guided)
    async with guided.runtime.database.session() as db:
        await actions(guided).confirm(db, action.action_id, 123)
    async with guided.runtime.database.session() as db:
        with pytest.raises(PermissionError):
            await PendingActionService().claim_execution(db, action.action_id, 123)
    assert not (await effects(guided))[1]


async def test_authenticated_preview_and_existing_confirm_route(api, guided):
    client, context, _auth, login, _state = api
    context.first_value_getter = lambda: guided.owner
    before = await effects(guided)
    response = await client.post("/api/v1/onboarding/first-source/preview",
                                 json={"source_id": str(A)}, headers=headers(login))
    assert response.status_code == 201
    data = response.json()
    assert data["chat_id"] == str(A) and data["status"] == "pending"
    assert "http" not in response.text and "api_key" not in response.text
    assert await effects(guided) == before
    confirmed = await client.post(f"/api/v1/pending-actions/{data['action_id']}/confirm", headers=headers(login))
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed"
    assert await effects(guided) == before
    repeated = await client.post(f"/api/v1/pending-actions/{data['action_id']}/confirm", headers=headers(login))
    assert repeated.status_code == 409


async def test_invalid_current_settings_never_echo_private_input(api, guided):
    from tg_assistant.config import config_path
    client, context, _auth, login, _state = api
    context.first_value_getter = lambda: guided.owner
    action = await create(guided)
    path = config_path(guided.runtime.settings.data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"version":1,"settings":{"ai_provider":"synthetic-sensitive-marker"}}', encoding="utf-8")
    response = await client.post(f"/api/v1/pending-actions/{action.action_id}/confirm", headers=headers(login))
    assert response.status_code in {403, 404, 409}
    assert "synthetic-sensitive-marker" not in response.text
    assert not (await effects(guided))[1]


async def test_preview_route_body_await_withdraws_admission(api, guided):
    client, context, _auth, login, state = api
    context.first_value_getter = lambda: guided.owner
    async def chunks():
        yield b'{"source_id":'
        state.current[0] = False
        yield ('"' + str(A) + '"}').encode()
    try:
        response = await client.post("/api/v1/onboarding/first-source/preview", content=chunks(), headers=headers(login))
        assert response.status_code == 403
    finally:
        state.current[0] = True
    async with guided.runtime.database.session() as db:
        assert (await db.scalars(select(PendingAction))).all() == []


async def test_admission_loss_after_preset_rolls_back_all_effects(guided):
    action = await create(guided)
    async with guided.runtime.database.session() as db:
        await actions(guided).confirm(db, action.action_id, 123)
    before = await effects(guided)
    original = guided.runtime.policy.apply_template
    async def withdrawn(*args, **kwargs):
        await original(*args, **kwargs)
        guided.state.current[0] = False
    guided.runtime.policy.apply_template = withdrawn
    try:
        with pytest.raises(PermissionError):
            await guided.runtime._execute_actions()
    finally:
        guided.state.current[0] = True
    assert await effects(guided) == before


@pytest.mark.parametrize("statement_prefix", ["INSERT INTO background_jobs", "INSERT INTO audit_logs"])
async def test_model_change_at_final_commit_rolls_back_effects(guided, statement_prefix):
    action = await create(guided)
    async with guided.runtime.database.session() as db:
        await actions(guided).confirm(db, action.action_id, 123)
    before = await effects(guided)
    def change(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith(statement_prefix):
            guided.runtime.ai.model = "changed-at-commit"
    event.listen(guided.runtime.database.engine.sync_engine, "after_cursor_execute", change)
    try:
        await execute(guided)
    finally:
        event.remove(guided.runtime.database.engine.sync_engine, "after_cursor_execute", change)
    assert await effects(guided) == before


async def test_restart_requires_new_preview_and_monotonic_expiry(guided, monkeypatch):
    action = await create(guided)
    module = importlib.import_module("tg_assistant.services.first_source_preview")
    monkeypatch.setattr(module, "monotonic", lambda: 10**20)
    async with guided.runtime.database.session() as db:
        with pytest.raises(PermissionError):
            await actions(guided).confirm(db, action.action_id, 123)
    monkeypatch.undo()
    guided.owner.preview = module.FirstSourcePreviewService(guided.owner)
    async with guided.runtime.database.session() as db:
        with pytest.raises(PermissionError):
            await actions(guided).confirm(db, action.action_id, 123)
    assert not (await effects(guided))[1]


async def test_embedding_off_does_not_fabricate_readiness(guided):
    guided.runtime.embedding_ai = AiEngine(api_key=None, budget=None, provider="off", model=None,
        embedding_model="nomic-embed-text:latest", base_url="http://127.0.0.1:11434/v1", max_output_tokens=0)
    guided.runtime.settings.embedding_provider = "off"
    try:
        action = await create(guided)
        assert "off" in action.preview and not guided.owner.selection_status().test_available
    finally:
        await guided.runtime.embedding_ai.close()


@pytest.mark.parametrize("following", ["advanced", "guided"])
async def test_stale_guided_action_preserves_batch_and_failure_bookkeeping(guided, following):
    first = await create(guided)
    async with guided.runtime.database.session() as db:
        await actions(guided).confirm(db, first.action_id, 123)
    guided.runtime.ai.model = "changed-after-confirm"
    if following == "guided":
        second = await create(guided)
        async with guided.runtime.database.session() as db:
            await actions(guided).confirm(db, second.action_id, 123)
    else:
        async with guided.runtime.database.session() as db:
            second = await PendingActionService().create(db, action_type="enable_group_learning",
                requested_by=123, chat_id=B, payload={"limit": 1000}, preview="advanced")
            await PendingActionService().confirm(db, second.action_id, 123)
    await execute(guided)
    async with guided.runtime.database.session() as db:
        states = dict((await db.execute(select(PendingAction.action_id, PendingAction.status))).all())
        assert states[first.action_id] == "cancelled"
        assert states[second.action_id] == "executed"
    _, jobs = await effects(guided)
    assert len(jobs) == 1
    assert jobs[0].payload["chat_id"] == (A if following == "guided" else B)


@pytest.mark.parametrize("guided_action", [True, False])
async def test_bot_notice_matches_guided_queue_scope_and_preserves_advanced(guided, guided_action):
    notices = []
    async def send_message(owner_id, content):
        notices.append((owner_id, content))
    guided.runtime.bot = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
    if guided_action:
        action = await create(guided)
        action_service = actions(guided)
    else:
        async with guided.runtime.database.session() as db:
            action = await PendingActionService().create(db, action_type="enable_group_learning",
                requested_by=123, chat_id=B, payload={"limit": 1000}, preview="advanced")
        action_service = PendingActionService()
    async with guided.runtime.database.session() as db:
        await action_service.confirm(db, action.action_id, 123)
    await execute(guided)
    _, jobs = await effects(guided)
    assert len(jobs) == 1 and jobs[0].status == "queued"
    if guided_action:
        assert notices == [], "Guided queue creation cannot claim learning completion or AI readiness"
        assert not guided.owner.selection_status().test_available
    else:
        assert len(notices) == 1 and notices[0][0] == 123
        assert "ĐÃ HỌC XONG GROUP" in notices[0][1]


@pytest.mark.parametrize("status, identity", [
    ("queued", "advanced"), ("running", "advanced"), ("paused", "advanced"),
    ("pause_requested", "advanced"), ("cancel_requested", "advanced"),
    ("queued", "old_action"), ("queued", "wrong_owner"),
    ("queued", "stale_epoch"), ("queued", "same_action"),
    ("queued", "whitespace_id"), ("queued", "padded_id"),
    ("queued", "source_list"), ("queued", "malformed_mapping"),
    ("queued", "malformed_id"), ("queued", "malformed_source_list"),
    ("queued", "absent_source"),
])
async def test_guided_conflict_preserves_existing_learning_and_grants(guided, status, identity):
    action = await create(guided)
    async with guided.runtime.database.session() as db:
        await actions(guided).confirm(db, action.action_id, 123)
        epoch = await db.scalar(select(TelegramChatPolicy.authorization_epoch)
                                .where(TelegramChatPolicy.chat_id == A))
        payload = {"chat_id": A, "owner_id": 123, "authorization_epoch": epoch, "limit": 17}
        if identity != "advanced":
            payload["action_id"] = action.action_id if identity == "same_action" else "fv1-old"
        if identity == "wrong_owner":
            payload["owner_id"] = 456
        if identity == "stale_epoch":
            payload["authorization_epoch"] = epoch + 1
        if identity == "whitespace_id":
            payload["chat_id"] = f" {A} "
        if identity == "padded_id":
            payload["chat_id"] = "-0" + str(A)[1:]
        if identity == "source_list":
            payload["chat_id"], payload["chat_ids"] = B, [str(A)]
        if identity == "malformed_mapping":
            payload = [A]
        if identity == "malformed_id":
            payload["chat_id"] = "not-an-id"
        if identity == "malformed_source_list":
            payload["chat_ids"] = None
        if identity == "absent_source":
            payload.pop("chat_id")
        db.add(BackgroundJob(job_type="learn_group", status=status, payload=payload))
        db.add(KnowledgeSource(chat_id=A, status="paused", requested_for_learning=False,
                               last_error="existing_source_state"))

    async def stored_state():
        async with guided.runtime.database.session() as db:
            return [(await db.execute(select(*model.__table__.columns))).all() for model in (
                BackgroundJob, KnowledgeSource, TelegramChatPolicy, TelegramChatPermission,
            )]

    before = await stored_state()
    mutations = []
    def record(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith(("INSERT", "UPDATE", "DELETE")) and any(
            name in statement for name in ("background_jobs", "knowledge_sources", "telegram_chat_policies", "telegram_chat_permissions")
        ):
            mutations.append(statement)
    event.listen(guided.runtime.database.engine.sync_engine, "before_cursor_execute", record)
    try:
        await execute(guided)
    finally:
        event.remove(guided.runtime.database.engine.sync_engine, "before_cursor_execute", record)
    async with guided.runtime.database.session() as db:
        rejected = await db.get(PendingAction, action.action_id)
        assert rejected.status == "failed"
        assert rejected.error == "source_learning_in_progress"
        assert "job_id" not in rejected.payload and "deduplicated" not in rejected.payload
        audits = (await db.scalars(select(AuditLog).where(AuditLog.correlation_id == action.action_id))).all()
        assert len(audits) == 1 and audits[0].outcome == "failed"
        assert audits[0].reason == "source_learning_in_progress"
    assert await stored_state() == before
    assert mutations == [], "Reject the conflicting job before any tentative preset/job/source write"
    assert not guided.runtime.settings.cloud_consent
    assert not guided.owner.selection_status().test_available


async def test_guided_other_source_job_is_not_a_conflict(guided):
    action = await create(guided)
    async with guided.runtime.database.session() as db:
        await actions(guided).confirm(db, action.action_id, 123)
        other = BackgroundJob(job_type="learn_group", status="running", payload={"chat_id": B})
        db.add(other)
        await db.flush()
        other_id = other.id
    await execute(guided)
    async with guided.runtime.database.session() as db:
        finished = await db.get(PendingAction, action.action_id)
        assert finished.status == "executed" and not finished.payload["deduplicated"]
        job = await db.get(BackgroundJob, finished.payload["job_id"])
        assert job.payload["chat_id"] == A and job.payload["action_id"] == action.action_id
        assert job.payload["owner_id"] == 123 and job.status == "queued"
        assert (await db.get(BackgroundJob, other_id)).status == "running"


@pytest.mark.parametrize("changes_permissions", [False, True])
async def test_advanced_learning_retains_preset_before_job_dedup(guided, changes_permissions):
    async with guided.runtime.database.session() as db:
        if not changes_permissions:
            await guided.runtime.policy.apply_template(db, A, "knowledge")
        action = await PendingActionService().create(db, action_type="enable_group_learning",
            requested_by=123, chat_id=A, payload={"limit": 1000}, preview="advanced")
        await PendingActionService().confirm(db, action.action_id, 123)
        existing = BackgroundJob(job_type="learn_group", status="running", payload={"chat_id": A})
        db.add(existing)
        await db.flush()
        existing_id = existing.id
    await execute(guided)
    async with guided.runtime.database.session() as db:
        finished = await db.get(PendingAction, action.action_id)
        assert finished.status == "executed"
        if changes_permissions:
            assert not finished.payload["deduplicated"]
            assert finished.payload["job_id"] != existing_id
            assert (await db.get(BackgroundJob, existing_id)).status == "cancelled"
            assert len((await db.scalars(select(BackgroundJob))).all()) == 2
        else:
            assert finished.payload["deduplicated"] and finished.payload["job_id"] == existing_id
            assert (await db.get(BackgroundJob, existing_id)).status == "running"
            assert len((await db.scalars(select(BackgroundJob))).all()) == 1
