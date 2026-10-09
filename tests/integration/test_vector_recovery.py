"""Real SQL/ledger/Qdrant recovery; the provider transport is synthetic."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from tg_assistant.ai.budget import BudgetService
from tg_assistant.ai.vector import LocalVectorStore
from tg_assistant.config import Settings, save_settings
from tg_assistant.db.base import Base, Database
from tg_assistant.db.models import (
    AiBudgetReservation,
    AppSetting,
    AuditLog,
    BackgroundJob,
    KnowledgeSource,
    PendingAction,
    TelegramAccount,
    TelegramChat,
    TelegramChatPolicy,
    TelegramMessage,
    VectorStore,
)
from tg_assistant.policy import PolicyEngine
from tg_assistant.runtime import Application, make_local_embedding_engine
from tg_assistant.services.actions import PendingActionService
from tg_assistant.services.jobs import claim_runtime_job, worker_lease


class SyntheticSecrets:
    def get(self, key):
        return "synthetic-recovery-test-key"


@pytest_asyncio.fixture(params=["sqlite"])
async def recovery_case(tmp_path, monkeypatch, request):
    # Qdrant embeds a SQLite filename below the generation directory. Keep the
    # disposable root short enough for Windows' SQLite path limit.
    tmp_path = tmp_path.parent / ("r" + uuid4().hex[:8])
    tmp_path.mkdir()
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "profile",
        ai_provider="openai",
        embedding_provider="openai",
        cloud_consent=True,
        cloud_embedding_dimension=3,
    )
    save_settings(settings)
    monkeypatch.setenv("TG_ASSISTANT_DATA_DIR", str(settings.data_dir))
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    app = Application.__new__(Application)
    app.settings, app.database, app.policy = settings, database, PolicyEngine()
    app.user = SimpleNamespace(owner_id=7)
    app.bot = None
    app._knowledge_lock = asyncio.Lock()
    app.stopping = asyncio.Event()
    app.budget = BudgetService(1, 10)
    app.embedding_ai = make_local_embedding_engine(settings, SyntheticSecrets(), app.budget)
    app.rag = SimpleNamespace(
        vectors=LocalVectorStore(
            settings.resolved_semantic_vector_path,
            vector_size=3,
            profile=settings.embedding_profile,
        )
    )
    requests = []
    handler = None

    def transport(request):
        body = json.loads(request.content)
        requests.append(body)
        if handler:
            return handler(request)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "text-embedding-3-small",
                "data": [
                    {"object": "embedding", "index": i, "embedding": [0.1, 0.2, 0.3]}
                    for i, _ in enumerate(body["input"])
                ],
                "usage": {"prompt_tokens": 10, "total_tokens": 10},
            },
        )

    await app.embedding_ai.client._client.aclose()
    app.embedding_ai.client._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    async with database.session() as session:
        for chat_id in (20, 30):
            session.add(TelegramChat(chat_id=chat_id, chat_type="group"))
            await session.flush()
            await app.policy.apply_template(session, chat_id, "knowledge")
            await app.policy.set_ai_route(session, chat_id, mode="cloud_only")
        for chat_id, message_id, text in (
            (20, 1, "First useful synthetic release plan preserved"),
            (20, 2, "Second useful synthetic release plan recovered"),
            (30, 3, "Other source useful synthetic knowledge preserved"),
        ):
            session.add(
                TelegramMessage(
                    chat_id=chat_id, message_id=message_id, text=text, sent_at=datetime.now(UTC)
                )
            )
    async with database.session() as session:
        rows = list(
            (
                await session.scalars(
                    select(TelegramMessage).where(TelegramMessage.message_id.in_([1, 3]))
                )
            ).all()
        )
        await app._index_knowledge_rows(session, rows)
    requests.clear()

    def set_handler(value):
        nonlocal handler
        handler = value

    try:
        yield app, requests, set_handler
    finally:
        app.rag.vectors.close()
        await app.embedding_ai.close()
        await database.close()


def service(app):
    assert importlib.util.find_spec("tg_assistant.services.vector_recovery"), (
        "Confirmed recovery has no durable service/build/verified activation implementation"
    )
    from tg_assistant.services.vector_recovery import VectorRecoveryService

    return VectorRecoveryService.for_application(app)


async def build(app, recovery, plan):
    operation = await recovery.enqueue(plan.plan_id, 7)
    claimed = await claim_runtime_job(app.database, ("vector_recovery",))
    assert claimed and claimed[0].id == operation
    with worker_lease(claimed[0]):
        result = await recovery.run(claimed[0])
    return operation, result


async def snapshot(app):
    async with app.database.session() as session:
        rows = list(
            (await session.scalars(select(TelegramMessage).order_by(TelegramMessage.id))).all()
        )
        store = await session.get(VectorStore, app.settings.embedding_profile.store_id)
        return (
            [(row.id, row.vector_status, row.vector_dirty, row.metadata_json) for row in rows],
            (store.path, store.state, store.role, store.last_reconciled_at),
            sorted(app.rag.vectors.reference_ids()),
        )


async def test_existing_confirmed_path_must_enqueue_real_job(recovery_case, monkeypatch):
    """Removing actual runtime recovery dispatch recreates this behavioral RED."""
    app, _, _ = recovery_case
    async with app.database.session() as session:
        payload = {}
        if importlib.util.find_spec("tg_assistant.services.vector_recovery"):
            plan = await service(app).preview(20, app.settings.embedding_profile.store_id)
            payload = {"plan_id": plan.plan_id}
        action = await PendingActionService().create(
            session,
            action_type="recover_source_index",
            requested_by=7,
            chat_id=20,
            payload=payload,
            preview="Recover one missing eligible reference",
        )
        await PendingActionService().confirm(session, action.action_id, 7)

    async def stop_after_cycle(seconds):
        app.stopping.set()

    monkeypatch.setattr("tg_assistant.runtime.asyncio.sleep", stop_after_cycle)
    await app._execute_actions()
    async with app.database.session() as session:
        jobs = list(
            (
                await session.scalars(
                    select(BackgroundJob).where(BackgroundJob.job_type == "vector_recovery")
                )
            ).all()
        )
        assert len(jobs) == 1, "Owner confirmation only records approved_not_executed"
        operation = jobs[0].id
        persisted = await session.get(PendingAction, action.action_id)
        assert persisted.status == "executed" and persisted.payload["operation_id"] == operation
    await app._process_admin_jobs()
    async with app.database.session() as session:
        assert (await session.get(BackgroundJob, operation)).status == "completed"
        assert await session.scalar(
            select(AuditLog.id).where(
                AuditLog.action == "vector_recovery_verified", AuditLog.correlation_id == operation
            )
        )
        from tg_assistant.services.vector_reliability import ensure_vector_store_registry

        registered = await ensure_vector_store_registry(session, app.settings)
        assert registered.path == str(Path(app.rag.vectors.client._client.location).resolve())
        assert registered.role == "semantic_active" and registered.state == "active"
    assert sorted(app.rag.vectors.reference_ids()) == [1, 2, 3]
    assert app.rag.vectors.search([0.1, 0.2, 0.3], allowed_chat_ids=[30])[0][0] == 3


async def test_preview_readonly(recovery_case):
    app, requests, _ = recovery_case
    before = await snapshot(app)
    plan = await service(app).preview(20, app.settings.embedding_profile.store_id)
    assert plan.expected_count == 2
    assert await snapshot(app) == before
    assert requests == []


async def test_approved_recovery_fills_missing_and_preserves_other_source(recovery_case):
    app, requests, _ = recovery_case
    recovery = service(app)
    old_path = app.settings.resolved_semantic_vector_path
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    operation, result = await build(app, recovery, plan)
    assert result.state.value == "completed"
    assert app.rag.vectors.reference_ids(chat_id=20) == [1, 2]
    assert app.rag.vectors.reference_ids(chat_id=30) == [3]
    assert [text for request in requests for text in request["input"]] == [
        "Second useful synthetic release plan recovered"
    ]
    original = LocalVectorStore(old_path, vector_size=3, profile=app.settings.embedding_profile)
    try:
        assert sorted(original.reference_ids()) == [1, 3]
    finally:
        original.close()
    async with app.database.session() as session:
        assert (await session.get(BackgroundJob, operation)).status == "completed"
        assert (await session.get(VectorStore, plan.store_id)).state == "active"


@pytest.mark.parametrize("change", ["epoch", "content", "policy", "store", "owner"])
async def test_stale_plan_refused(recovery_case, change):
    app, requests, _ = recovery_case
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    async with app.database.session() as session:
        policy = await session.scalar(
            select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == 20)
        )
        if change == "epoch":
            policy.authorization_epoch += 1
        elif change == "policy":
            policy.filtering_level = "strict"
        elif change == "content":
            (await session.get(TelegramMessage, 2)).text = "Edited synthetic project content"
        elif change == "store":
            (await session.get(VectorStore, plan.store_id)).dimension = 4
        elif change == "owner":
            app.user.owner_id = 8
    with pytest.raises((ValueError, PermissionError)):
        await recovery.enqueue(plan.plan_id, 7)
    assert requests == []
    assert sorted(app.rag.vectors.reference_ids()) == [1, 3]


async def test_enqueue_atomic_once_and_ignores_forged_plan(recovery_case):
    app, _, _ = recovery_case
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    with pytest.raises(PermissionError):
        await recovery.enqueue(plan.plan_id, 8)
    operation = await recovery.enqueue(plan.plan_id, 7)
    assert await recovery.enqueue(plan.plan_id, 7) == operation
    with pytest.raises(ValueError):
        await recovery.enqueue("forged-browser-evidence", 7)
    async with app.database.session() as session:
        assert len(list((await session.scalars(select(BackgroundJob))).all())) == 1


async def test_failed_store_not_activated(recovery_case):
    app, requests, set_handler = recovery_case
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    before = await snapshot(app)
    set_handler(lambda request: httpx.Response(500, json={"error": {"message": "synthetic"}}))
    _, result = await build(app, recovery, plan)
    assert result.state.value == "uncertain"
    assert await snapshot(app) == before
    assert len(requests) == 1


async def test_new_preview_cannot_authorize_uncertain_recovery_retry(recovery_case):
    app, requests, set_handler = recovery_case
    recovery = service(app)
    first = await recovery.preview(20, app.settings.embedding_profile.store_id)
    set_handler(lambda request: httpx.Response(500, json={"error": {"message": "synthetic"}}))
    _, result = await build(app, recovery, first)
    assert result.state.value == "uncertain"
    second = await recovery.preview(20, app.settings.embedding_profile.store_id)
    set_handler(None)
    _, second_result = await build(app, service(app), second)
    assert second_result.state.value == "uncertain"
    assert len(requests) == 1
    assert sorted(app.rag.vectors.reference_ids()) == [1, 3]


async def test_retrieval_verified_before_activation(recovery_case, monkeypatch):
    app, _, _ = recovery_case
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    before = await snapshot(app)
    monkeypatch.setattr(LocalVectorStore, "search", lambda *args, **kwargs: [])
    _, result = await build(app, recovery, plan)
    assert result.state.value == "failed"
    assert await snapshot(app) == before


@pytest.mark.parametrize("state", ["submitted", "uncertain", "reserved", "missing"])
async def test_prior_unresolved_intent_never_retried(recovery_case, state):
    app, requests, _ = recovery_case
    recovery = service(app)
    async with app.database.session() as session:
        row = await session.get(TelegramMessage, 2)
        row.metadata_json = {"embedding_request_id": "existing-unknown-intent"}
    if state != "missing":
        async with app.database.session() as session:
            await app.budget.reserve(
                session,
                request_id="existing-unknown-intent",
                provider="openai",
                model="text-embedding-3-small",
                input_tokens=100,
                output_tokens=0,
                operation="embedding",
                feature="embedding",
                chat_id=20,
            )
        async with app.database.session() as session:
            (await session.get(AiBudgetReservation, "existing-unknown-intent")).state = state
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    _, result = await build(app, recovery, plan)
    assert result.state.value == "uncertain"
    assert requests == []
    assert sorted(app.rag.vectors.reference_ids()) == [1, 3]


async def test_pause_restart_before_submission_is_idempotent(recovery_case):
    app, requests, _ = recovery_case
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    operation = await recovery.enqueue(plan.plan_id, 7)
    await recovery.control(operation, "pause", 7)
    assert await claim_runtime_job(app.database, ("vector_recovery",)) is None
    await service(app).control(operation, "resume", 7)
    claimed = await claim_runtime_job(app.database, ("vector_recovery",))
    with worker_lease(claimed[0]):
        result = await service(app).run(claimed[0])
    assert result.state.value == "completed"
    assert len(requests) == 1


async def test_expired_submitted_worker_requires_reconcile(recovery_case):
    app, requests, _ = recovery_case
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    operation = await recovery.enqueue(plan.plan_id, 7)
    claimed = await claim_runtime_job(app.database, ("vector_recovery",))
    async with app.database.session() as session:
        job = await session.get(BackgroundJob, operation)
        job.payload = {**job.payload, "external_effect_started": True}
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await claim_runtime_job(app.database, ("vector_recovery",)) is None
    with pytest.raises(ValueError):
        await recovery.control(operation, "resume", 7)
    with worker_lease(claimed[0]):
        with pytest.raises(Exception, match="lease|revoked"):
            await recovery.run(claimed[0])
    assert requests == []
    async with app.database.session() as session:
        assert (await session.get(BackgroundJob, operation)).status == "uncertain"


async def test_corrupt_committed_pointer_never_opens_empty_corpus(recovery_case):
    app, _, _ = recovery_case
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    await build(app, recovery, plan)
    from tg_assistant.services.vector_paths import active_vector_path, pointer_key

    async with app.database.session() as session:
        pointer = await session.get(AppSetting, pointer_key(plan.store_id))
        pointer.value = {**pointer.value, "generation_id": "../../outside"}
    async with app.database.session() as session:
        with pytest.raises(ValueError):
            await active_vector_path(session, app.settings)
    assert sorted(app.rag.vectors.reference_ids()) == [1, 2, 3]


class SimulatedProcessCrash(BaseException):
    pass


async def expire_worker(app, operation):
    async with app.database.session() as session:
        job = await session.get(BackgroundJob, operation)
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)


async def test_other_source_edit_during_build_preserves_active_generation(
    recovery_case, monkeypatch
):
    app, _, _ = recovery_case
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    old_path = (await snapshot(app))[1][0]
    original = app.embedding_ai.embed_many

    async def concurrent_edit(*args, **kwargs):
        response = await original(*args, **kwargs)
        async with app.database.session() as session:
            row = await session.get(TelegramMessage, 3)
            row.text = "Other source concurrently edited useful content"
            row.vector_dirty = True
        return response

    monkeypatch.setattr(app.embedding_ai, "embed_many", concurrent_edit)
    _, result = await build(app, recovery, plan)
    assert result.state.value in {"failed", "uncertain"}
    assert (await snapshot(app))[1][0] == old_path
    assert sorted(app.rag.vectors.reference_ids()) == [1, 3]


@pytest.mark.parametrize("crash_phase", ["before_submit", "after_response", "before_activate"])
async def test_process_crash_preserves_pointer_and_restarts_safely(
    recovery_case, monkeypatch, crash_phase
):
    app, requests, _ = recovery_case
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    operation = await recovery.enqueue(plan.plan_id, 7)
    lease, _ = await claim_runtime_job(app.database, ("vector_recovery",))
    original_embed = app.embedding_ai.embed_many
    original_activate = recovery._activate

    async def crash_embed(*args, **kwargs):
        if crash_phase == "before_submit":
            raise SimulatedProcessCrash()
        await original_embed(*args, **kwargs)
        raise SimulatedProcessCrash()

    async def crash_activate(*args, **kwargs):
        raise SimulatedProcessCrash()

    if crash_phase == "before_activate":
        monkeypatch.setattr(recovery, "_activate", crash_activate)
    else:
        monkeypatch.setattr(app.embedding_ai, "embed_many", crash_embed)
    with worker_lease(lease), pytest.raises(SimulatedProcessCrash):
        await recovery.run(lease)
    assert sorted(app.rag.vectors.reference_ids()) == [1, 3]
    monkeypatch.setattr(app.embedding_ai, "embed_many", original_embed)
    monkeypatch.setattr(recovery, "_activate", original_activate)
    await expire_worker(app, operation)
    claimed = await claim_runtime_job(app.database, ("vector_recovery",))
    if crash_phase == "after_response":
        assert claimed is None
        async with app.database.session() as session:
            assert (await session.get(BackgroundJob, operation)).status == "uncertain"
    else:
        assert claimed
        with worker_lease(claimed[0]):
            result = await service(app).run(claimed[0])
        assert result.state.value == "completed"
        assert sorted(app.rag.vectors.reference_ids()) == [1, 2, 3]
    assert len(requests) == 1


async def test_staged_point_without_settled_reservation_cannot_resume(recovery_case, monkeypatch):
    app, requests, _ = recovery_case
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    operation = await recovery.enqueue(plan.plan_id, 7)
    lease, _ = await claim_runtime_job(app.database, ("vector_recovery",))

    async def crash(*args, **kwargs):
        raise SimulatedProcessCrash()

    monkeypatch.setattr(recovery, "_activate", crash)
    with worker_lease(lease), pytest.raises(SimulatedProcessCrash):
        await recovery.run(lease)
    async with app.database.session() as session:
        job = await session.get(BackgroundJob, operation)
        request_id = job.payload["request_ids"]["2"]
        (await session.get(AiBudgetReservation, request_id)).state = "uncertain"
    await expire_worker(app, operation)
    claimed = await claim_runtime_job(app.database, ("vector_recovery",))
    with worker_lease(claimed[0]):
        result = await service(app).run(claimed[0])
    assert result.state.value == "uncertain"
    assert sorted(app.rag.vectors.reference_ids()) == [1, 3]
    assert len(requests) == 1


@pytest.mark.parametrize(
    "invalid", ["foreign_sid", "foreign_owner", "missing_generation", "corrupt_manifest"]
)
async def test_invalid_pointer_refused_with_prior_corpus_retained(recovery_case, invalid):
    app, _, _ = recovery_case
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    await build(app, recovery, plan)
    from tg_assistant.services.vector_paths import active_vector_path, generation_path, pointer_key

    async with app.database.session() as session:
        pointer = await session.get(AppSetting, pointer_key(plan.store_id))
        record = dict(pointer.value)
        if invalid == "foreign_sid":
            record["sid"] = "S-1-5-21-foreign-synthetic"
        elif invalid == "foreign_owner":
            record["owner_id"] = 8
        elif invalid == "missing_generation":
            record["generation_id"] = "f" * 32
        else:
            path = generation_path(app.settings, record["generation_id"], existing=True)
            (path / "recovery-ready.json").write_text("{}", encoding="utf-8")
        pointer.value = record
    async with app.database.session() as session:
        with pytest.raises(ValueError):
            await active_vector_path(session, app.settings, owner_id=7)
    assert sorted(app.rag.vectors.reference_ids()) == [1, 2, 3]


async def test_generation_hardlink_refused_without_touching_target(recovery_case):
    app, _, _ = recovery_case
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    await build(app, recovery, plan)
    from tg_assistant.services.vector_paths import active_vector_path, generation_path, pointer_key

    async with app.database.session() as session:
        pointer = await session.get(AppSetting, pointer_key(plan.store_id))
        path = generation_path(app.settings, pointer.value["generation_id"], existing=True)
    target = app.settings.data_dir / "kept-original.txt"
    target.write_text("preserve synthetic target", encoding="utf-8")
    os.link(target, path / "unexpected-link")
    async with app.database.session() as session:
        with pytest.raises(ValueError):
            await active_vector_path(session, app.settings)
    assert target.read_text(encoding="utf-8") == "preserve synthetic target"


async def test_running_pause_has_truthful_operation_state(recovery_case):
    app, _, _ = recovery_case
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    operation = await recovery.enqueue(plan.plan_id, 7)
    await claim_runtime_job(app.database, ("vector_recovery",))
    result = await recovery.control(operation, "pause", 7)
    assert result.state.value == "running"
    async with app.database.session() as session:
        assert (await session.get(BackgroundJob, operation)).status == "pause_requested"


async def test_committed_activation_survives_process_publish_crash(recovery_case):
    app, requests, _ = recovery_case
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    operation = await recovery.enqueue(plan.plan_id, 7)
    lease, _ = await claim_runtime_job(app.database, ("vector_recovery",))

    def crash(vectors):
        raise SimulatedProcessCrash()

    recovery.publish = crash
    with worker_lease(lease), pytest.raises(SimulatedProcessCrash):
        await recovery.run(lease)
    assert sorted(app.rag.vectors.reference_ids()) == [1, 3]
    with pytest.raises(ValueError):
        await service(app).preview(20, app.settings.embedding_profile.store_id)
    from tg_assistant.services.vector_paths import resolve_vector_path

    selected = resolve_vector_path(app.database, app.settings, owner_id=7)
    recovered = LocalVectorStore(selected, vector_size=3, profile=app.settings.embedding_profile)
    app.rag.vectors.close()
    app.rag.vectors = recovered
    assert sorted(recovered.reference_ids()) == [1, 2, 3]
    assert await claim_runtime_job(app.database, ("vector_recovery",)) is None
    async with app.database.session() as session:
        assert (await session.get(BackgroundJob, operation)).status == "completed"
    assert len(requests) == 1


async def test_stale_enqueued_job_reports_failure_without_provider(recovery_case):
    app, requests, _ = recovery_case
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    operation = await recovery.enqueue(plan.plan_id, 7)
    async with app.database.session() as session:
        (await session.get(TelegramMessage, 2)).text = "Source changed after durable enqueue"
    lease, _ = await claim_runtime_job(app.database, ("vector_recovery",))
    with worker_lease(lease):
        result = await recovery.run(lease)
    assert result.state.value == "failed"
    assert requests == []
    assert sorted(app.rag.vectors.reference_ids()) == [1, 3]
    async with app.database.session() as session:
        assert (await session.get(BackgroundJob, operation)).status == "failed"


async def test_exhausted_budget_refuses_submission_and_preserves_corpus(recovery_case):
    app, requests, _ = recovery_case
    app.budget.daily_limit = 0
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    _, result = await build(app, recovery, plan)
    assert result.state.value == "failed"
    assert requests == []
    assert sorted(app.rag.vectors.reference_ids()) == [1, 3]


async def test_verified_recovery_has_durable_completion_audit(recovery_case):
    app, _, _ = recovery_case
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    operation, result = await build(app, recovery, plan)
    assert result.state.value == "completed"
    async with app.database.session() as session:
        audit = await session.scalar(
            select(AuditLog).where(
                AuditLog.correlation_id == operation, AuditLog.action == "vector_recovery_verified"
            )
        )
        assert audit is not None and audit.outcome == "success"
        assert audit.actor_id == 7 and audit.target_id == "20"
        source = await session.get(KnowledgeSource, 20)
        assert source is not None and source.vector_count == 2
        assert source.status == "completed" and source.last_job_id == operation


async def test_preview_handler_creates_server_plan_action_and_audit(recovery_case):
    app, requests, _ = recovery_case
    from tg_assistant.admin_api.recovery_routes import create_recovery_preview

    context = SimpleNamespace(
        database=app.database,
        owner_id=7,
        settings_getter=lambda: app.settings,
        vectors_getter=lambda: app.rag.vectors,
    )
    response = await create_recovery_preview(
        context,
        PendingActionService(),
        20,
        action_json=lambda action: {"action_id": action.action_id, "status": action.status},
    )
    assert response["pending_action"]["status"] == "pending"
    assert response["recovery_plan"]["expected_count"] == 2
    assert response["coverage"]["missing_count"] == 1
    assert response["coverage"]["active_vectors"] == 1
    async with app.database.session() as session:
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "vector_recovery_preview_created")
        )
        assert audit is not None and audit.outcome == "pending"
    assert requests == []


@pytest.mark.parametrize(
    "loss", ["epoch", "lease", "consent", "embedding_disabled", "profile_changed"]
)
async def test_guard_loss_after_provider_never_switches_corpus(recovery_case, monkeypatch, loss):
    app, requests, _ = recovery_case
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    operation = await recovery.enqueue(plan.plan_id, 7)
    lease, _ = await claim_runtime_job(app.database, ("vector_recovery",))
    original = app.embedding_ai.embed_many

    async def lose_guard(*args, **kwargs):
        response = await original(*args, **kwargs)
        with worker_lease(None):
            async with app.database.session() as session:
                if loss == "epoch":
                    policy = await session.scalar(
                        select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == 20)
                    )
                    policy.allowed = False
                    policy.authorization_epoch += 1
                elif loss == "lease":
                    (await session.get(BackgroundJob, operation)).claim_token = "new-worker-token"
                elif loss == "consent":
                    app.settings.cloud_consent = False
                    save_settings(app.settings)
                elif loss == "embedding_disabled":
                    save_settings(app.settings.model_copy(update={"enable_embeddings": False}))
                else:
                    save_settings(app.settings.model_copy(update={"cloud_embedding_dimension": 4}))
        return response

    monkeypatch.setattr(app.embedding_ai, "embed_many", lose_guard)
    with worker_lease(lease):
        try:
            result = await recovery.run(lease)
        except Exception as exc:
            from tg_assistant.services.jobs import LeaseLost

            assert isinstance(exc, LeaseLost)
        else:
            assert result.state.value in {"failed", "uncertain"}
    assert sorted(app.rag.vectors.reference_ids()) == [1, 3]
    assert len(requests) == 1


async def test_recovery_routes_require_session_and_hide_private_job_tokens(recovery_case):
    app, _, _ = recovery_case
    from fastapi import FastAPI, HTTPException

    from tg_assistant.admin_api.recovery_routes import install_recovery_routes

    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    operation = await recovery.enqueue(plan.plan_id, 7)
    lease, _ = await claim_runtime_job(app.database, ("vector_recovery",))
    context = SimpleNamespace(
        database=app.database,
        owner_id=7,
        settings_getter=lambda: app.settings,
        vectors_getter=lambda: app.rag.vectors,
    )
    denied_app = FastAPI()

    def denied():
        raise HTTPException(status_code=401, detail="session_required")

    install_recovery_routes(denied_app, context, denied, denied)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=denied_app), base_url="http://test"
    ) as client:
        assert (await client.get(f"/api/v1/recovery-jobs/{operation}")).status_code == 401
        assert (await client.post(f"/api/v1/recovery-jobs/{operation}/pause")).status_code == 401
    permitted_app = FastAPI()
    install_recovery_routes(permitted_app, context, lambda: object(), lambda: object())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=permitted_app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/v1/recovery-jobs/{operation}")
        assert response.status_code == 200
        assert response.json()["state"] == "running"
        assert lease.claim_token not in response.text
        assert "request_ids" not in response.text and "generation_id" not in response.text
        assert (await client.post(f"/api/v1/recovery-jobs/{operation}/retry")).status_code == 409


async def bind_external_store(app):
    """Create independent actual corpus at an explicitly approved legacy location."""
    from qdrant_client.models import PointStruct

    from tg_assistant.services.vector_recovery import points

    settings = app.settings.model_copy(
        update={"ollama_qdrant_path": app.settings.data_dir.parent / "external"}
    )
    external = LocalVectorStore(
        settings.resolved_semantic_vector_path, vector_size=3, profile=settings.embedding_profile
    )
    external.client.upsert(
        external.COLLECTION,
        [PointStruct(id=p.id, vector=p.vector, payload=p.payload) for p in points(app.rag.vectors)],
        wait=True,
    )
    app.rag.vectors.close()
    app.rag.vectors = external
    app.settings = settings
    save_settings(settings)
    async with app.database.session() as session:
        registry = await session.get(VectorStore, settings.embedding_profile.store_id)
        registry.path = str(settings.resolved_semantic_vector_path.resolve())
    return settings.resolved_semantic_vector_path


def tree_bytes(path):
    # Windows Qdrant holds an exclusive lock; its bytes cannot be opened while live.
    return {
        str(p.relative_to(path)): (p.stat().st_size, p.stat().st_mtime_ns)
        if p.name == ".lock"
        else p.read_bytes()
        for p in path.rglob("*")
        if p.is_file()
    }


def reopen_store_class():
    """Integrated checks always exercise the live producer constructor."""
    return LocalVectorStore


async def test_owned_explicit_external_corpus_startup_and_recovery(recovery_case):
    app, requests, _ = recovery_case
    from tg_assistant.services.vector_paths import active_vector_path, resolve_vector_path

    old_path = await bind_external_store(app)
    assert not old_path.is_relative_to(app.settings.data_dir)
    marker = (app.settings.data_dir / ".tg-assistant-data").read_bytes()
    before = tree_bytes(old_path)
    assert resolve_vector_path(app.database, app.settings, owner_id=7) == old_path
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    assert tree_bytes(old_path) == before and requests == []
    _, result = await build(app, recovery, plan)
    assert result.state.value == "completed" and len(requests) == 1
    async with app.database.session() as session:
        selected = await active_vector_path(session, app.settings, owner_id=7)
    assert selected.parent == old_path / "generations"
    assert sorted(app.rag.vectors.reference_ids()) == [1, 2, 3]
    assert resolve_vector_path(app.database, app.settings, owner_id=7) == selected
    previous = LocalVectorStore(old_path, vector_size=3, profile=app.settings.embedding_profile)
    try:
        assert sorted(previous.reference_ids()) == [1, 3]
        assert previous.search([0.1, 0.2, 0.3], allowed_chat_ids=[30])[0][0] == 3
    finally:
        previous.close()
    assert (app.settings.data_dir / ".tg-assistant-data").read_bytes() == marker


@pytest.mark.parametrize(
    "damage", ["missing", "corrupt", "foreign", "registry", "hardlink", "junction", "owner"]
)
async def test_external_refusal_preserves_all_bytes(recovery_case, monkeypatch, damage):
    app, requests, _ = recovery_case
    from tg_assistant.services.vector_paths import active_vector_path

    path = await bind_external_store(app)
    alias = None
    if damage == "missing":
        (path / "embedding-profile.json").unlink()
    elif damage == "corrupt":
        (path / "embedding-profile.json").write_text("{", encoding="utf-8")
    elif damage == "foreign":
        (path / "embedding-profile.json").write_text('{"store_id":"foreign"}', encoding="utf-8")
    elif damage == "registry":
        async with app.database.session() as session:
            (await session.get(VectorStore, app.settings.embedding_profile.store_id)).path = str(
                path.parent
            )
    elif damage == "hardlink":
        original = path.parent / "retained-sentinel"
        original.write_bytes(b"outside bytes unchanged")
        alias = path / "hardlink"
        os.link(original, alias)
    elif damage == "junction":
        original = path.parent / "retained-directory"
        original.mkdir()
        (original / "sentinel").write_bytes(b"outside bytes unchanged")
        alias = path / "junction"
        if os.name == "nt":
            shell = (
                Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            )
            quoted_alias = str(alias).replace("'", "''")
            quoted_original = str(original).replace("'", "''")
            process = await asyncio.create_subprocess_exec(
                str(shell),
                "-NoProfile",
                "-Command",
                f"$ErrorActionPreference='Stop'; $null = New-Item -ItemType Junction -Path '{quoted_alias}' -Target '{quoted_original}'",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()
            assert process.returncode == 0
        else:
            alias.symlink_to(original, target_is_directory=True)
    else:
        from tg_assistant.services import vector_paths

        actual = vector_paths._assert_owned_path

        def foreign_owner(entry):
            if entry == path:
                raise OSError("storage_access_denied")
            actual(entry)

        monkeypatch.setattr(vector_paths, "_assert_owned_path", foreign_owner)
    before = tree_bytes(path)
    marker = (app.settings.data_dir / ".tg-assistant-data").read_bytes()
    try:
        async with app.database.session() as session:
            with pytest.raises((ValueError, OSError)):
                await active_vector_path(session, app.settings, owner_id=7)
        with pytest.raises((ValueError, OSError)):
            await service(app).preview(20, app.settings.embedding_profile.store_id)
        assert tree_bytes(path) == before
        assert requests == [] and sorted(app.rag.vectors.reference_ids()) == [1, 3]
        assert (app.settings.data_dir / ".tg-assistant-data").read_bytes() == marker
    finally:
        if alias is not None:
            if damage == "junction" and os.name == "nt":
                alias.rmdir()
            else:
                alias.unlink()


@pytest.mark.parametrize("existing_empty", [False, True])
async def test_missing_explicit_external_root_readonly_refuses_creation(
    recovery_case, existing_empty
):
    app, _, _ = recovery_case
    from tg_assistant.services.vector_paths import active_vector_path, validate_profile_tree

    settings = app.settings.model_copy(
        update={"ollama_qdrant_path": app.settings.data_dir.parent / "missing-external"}
    )
    if existing_empty:
        settings.resolved_semantic_vector_path.mkdir(parents=True)
    async with app.database.session() as session:
        with pytest.raises((ValueError, OSError)):
            await active_vector_path(session, settings, owner_id=7)
    if existing_empty:
        assert list(settings.resolved_semantic_vector_path.iterdir()) == []
    else:
        assert not settings.ollama_qdrant_path.exists()
    with pytest.raises(ValueError):
        validate_profile_tree(settings, app.settings.data_dir.parent / "arbitrary")


@pytest.mark.parametrize(
    "damage",
    [
        "meta",
        "collection",
        "sqlite",
        "schema",
        "corrupt",
        "dimension",
        "distance",
        "metadata_limit",
    ],
)
async def test_selected_generation_storage_loss_refuses_empty_recreation(recovery_case, damage):
    """Manifest-only acceptance would let LocalVectorStore recreate an empty corpus."""
    app, requests, _ = recovery_case
    from tg_assistant.services.vector_paths import active_vector_path, resolve_vector_path

    old_path = app.settings.resolved_semantic_vector_path
    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    _, result = await build(app, recovery, plan)
    assert result.state.value == "completed"
    async with app.database.session() as session:
        selected = await active_vector_path(session, app.settings, owner_id=7)
    app.rag.vectors.close()
    app.rag.vectors = LocalVectorStore(
        old_path, vector_size=3, profile=app.settings.embedding_profile
    )
    physical = {
        "meta": selected / "meta.json",
        "collection": selected / "collection" / "telegram_messages",
        "sqlite": selected / "collection" / "telegram_messages" / "storage.sqlite",
        "schema": selected / "collection" / "telegram_messages" / "storage.sqlite",
        "corrupt": selected / "collection" / "telegram_messages" / "storage.sqlite",
        "dimension": selected / "meta.json",
        "distance": selected / "meta.json",
        "metadata_limit": selected / "meta.json",
    }[damage]
    retained = selected.parent / ("retained-" + uuid4().hex)
    physical.rename(retained)
    if damage == "schema":
        with sqlite3.connect(physical) as connection:
            connection.execute("CREATE TABLE unrelated (id INTEGER)")
    elif damage == "corrupt":
        physical.write_bytes(b"not a sqlite database")
    elif damage in {"dimension", "distance"}:
        meta = json.loads(retained.read_bytes())
        meta["collections"]["telegram_messages"]["vectors"].update(
            {"size": 4} if damage == "dimension" else {"distance": "Dot"}
        )
        physical.write_text(json.dumps(meta), encoding="utf-8")
    elif damage == "metadata_limit":
        physical.write_bytes(retained.read_bytes() + b" " * 65536)
    before = tree_bytes(selected)
    retained_before = tree_bytes(retained) if retained.is_dir() else retained.read_bytes()
    previous_before = tree_bytes(old_path)
    sql_before = await snapshot(app)
    async with app.database.session() as session:
        from tg_assistant.services.vector_paths import pointer_key

        pointer_before = (await session.get(AppSetting, pointer_key(plan.store_id))).value
    calls = len(requests)
    for resolve in ("async", "startup"):
        with pytest.raises(ValueError):
            if resolve == "async":
                async with app.database.session() as session:
                    await active_vector_path(session, app.settings, owner_id=7)
            else:
                resolve_vector_path(app.database, app.settings, owner_id=7)
    # The path was valid before the loss. The reopen constructor independently
    # checks it again before Qdrant can create absent metadata or SQLite storage.
    with pytest.raises(ValueError):
        reopen_store_class()(
            selected, vector_size=3, profile=app.settings.embedding_profile, require_existing=True
        )
    assert tree_bytes(selected) == before
    assert (tree_bytes(retained) if retained.is_dir() else retained.read_bytes()) == retained_before
    assert tree_bytes(old_path) == previous_before
    assert await snapshot(app) == sql_before
    async with app.database.session() as session:
        assert (await session.get(AppSetting, pointer_key(plan.store_id))).value == pointer_before
    assert app.rag.vectors.reference_ids() == [1, 3]
    assert len(requests) == calls


async def test_selected_generation_accepts_legitimate_active_index_updates(recovery_case):
    """Reopen must validate collection identity rather than freeze its original contents."""
    app, _, _ = recovery_case
    from tg_assistant.services.vector_paths import active_vector_path, resolve_vector_path

    recovery = service(app)
    plan = await recovery.preview(20, app.settings.embedding_profile.store_id)
    _, result = await build(app, recovery, plan)
    assert result.state.value == "completed"
    app.rag.vectors.upsert(99, [0.3, 0.2, 0.1], chat_id=30, message_id=99)
    async with app.database.session() as session:
        selected = await active_vector_path(session, app.settings, owner_id=7)
    app.rag.vectors.close()
    assert resolve_vector_path(app.database, app.settings, owner_id=7) == selected
    app.rag.vectors = reopen_store_class()(
        selected, vector_size=3, profile=app.settings.embedding_profile, require_existing=True
    )
    assert sorted(app.rag.vectors.reference_ids()) == [1, 2, 3, 99]


async def test_reopen_alias_preflight_precedes_every_metadata_read(recovery_case, monkeypatch):
    app, _, _ = recovery_case
    from tg_assistant.services.vector_paths import validate_persisted_collection

    path = app.settings.resolved_semantic_vector_path
    sentinel = path.parent / "retained-original"
    sentinel.write_bytes(b"preserve outside bytes")
    alias = path / "collection" / "telegram_messages" / "nested-hardlink"
    os.link(sentinel, alias)
    before = tree_bytes(path)

    def forbidden_read(*args, **kwargs):
        raise AssertionError("metadata read before complete alias preflight")

    with monkeypatch.context() as reads:
        reads.setattr(Path, "open", forbidden_read)
        with pytest.raises(ValueError):
            validate_persisted_collection(path, 3)
        with pytest.raises(ValueError):
            reopen_store_class()(path, vector_size=3, require_existing=True)
    assert tree_bytes(path) == before and sentinel.read_bytes() == b"preserve outside bytes"
    alias.unlink()


async def test_private_reopen_option_preserves_fresh_candidate_creation(recovery_case):
    app, _, _ = recovery_case
    from tg_assistant.services.vector_paths import generation_path

    path = generation_path(app.settings, uuid4().hex)
    candidate = reopen_store_class()(
        path, vector_size=3, profile=app.settings.embedding_profile, require_existing=False
    )
    try:
        candidate.upsert(88, [0.1, 0.2, 0.3], chat_id=20, message_id=88)
        assert candidate.reference_ids() == [88]
    finally:
        candidate.close()


async def test_existing_external_storage_loss_refuses_recreation(recovery_case):
    app, requests, _ = recovery_case
    from tg_assistant.services.vector_paths import active_vector_path, requires_existing_vector_path

    original = app.settings.resolved_semantic_vector_path
    path = await bind_external_store(app)
    app.rag.vectors.close()
    app.rag.vectors = LocalVectorStore(
        original, vector_size=3, profile=app.settings.embedding_profile
    )
    physical = path / "collection" / "telegram_messages" / "storage.sqlite"
    retained = path.parent / ("retained-" + uuid4().hex)
    physical.rename(retained)
    before = tree_bytes(path)
    saved = retained.read_bytes()
    async with app.database.session() as session:
        with pytest.raises(ValueError):
            await active_vector_path(session, app.settings, owner_id=7)
    with pytest.raises(ValueError):
        reopen_store_class()(
            path,
            vector_size=3,
            profile=app.settings.embedding_profile,
            require_existing=requires_existing_vector_path(app.settings, path),
        )
    assert tree_bytes(path) == before and retained.read_bytes() == saved
    assert requests == [] and sorted(app.rag.vectors.reference_ids()) == [1, 3]


async def test_legacy_init_from_corpus_reopens_without_metadata_rewrite(recovery_case):
    """Rejecting the loader-supported deprecated field strands a readable corpus."""
    app, requests, _ = recovery_case
    from tg_assistant.services.vector_paths import active_vector_path, validate_persisted_collection

    path = await bind_external_store(app)
    app.rag.vectors.close()
    metadata = path / "meta.json"
    saved = json.loads(metadata.read_bytes())
    saved["collections"]["telegram_messages"]["init_from"] = None
    metadata.write_text(json.dumps(saved), encoding="utf-8")
    before = tree_bytes(path)
    # Positive control: this is actually readable by the installed original
    # Qdrant loader, which removes this exact known deprecated field in memory.
    control = LocalVectorStore(path, vector_size=3, profile=app.settings.embedding_profile)
    try:
        assert sorted(control.reference_ids()) == [1, 3]
        assert control.search([0.1, 0.2, 0.3], allowed_chat_ids=[30])[0][0] == 3
    finally:
        control.close()
    assert tree_bytes(path) == before
    validate_persisted_collection(path, 3)
    async with app.database.session() as session:
        assert await active_vector_path(session, app.settings, owner_id=7) == path
    app.rag.vectors = reopen_store_class()(
        path, vector_size=3, profile=app.settings.embedding_profile, require_existing=True
    )
    assert sorted(app.rag.vectors.reference_ids()) == [1, 3]
    assert app.rag.vectors.search([0.1, 0.2, 0.3], allowed_chat_ids=[30])[0][0] == 3
    assert tree_bytes(path) == before and requests == []


async def test_legacy_init_from_does_not_allow_unknown_metadata_fields(recovery_case):
    app, _, _ = recovery_case
    from tg_assistant.services.vector_paths import validate_persisted_collection

    path = await bind_external_store(app)
    app.rag.vectors.close()
    metadata = path / "meta.json"
    saved = json.loads(metadata.read_bytes())
    saved["collections"]["telegram_messages"].update(
        init_from=None, unknown_collection_configuration=True
    )
    metadata.write_text(json.dumps(saved), encoding="utf-8")
    before = tree_bytes(path)
    with pytest.raises(ValueError):
        validate_persisted_collection(path, 3)
    with pytest.raises(ValueError):
        reopen_store_class()(
            path, vector_size=3, profile=app.settings.embedding_profile, require_existing=True
        )
    assert tree_bytes(path) == before


async def test_integrated_admin_preview_confirm_worker_and_session_boundary(
    recovery_case, monkeypatch
):
    app, requests, _ = recovery_case
    from tg_assistant.admin_api import AdminContext, create_admin_app, dashboard_login_code
    from tg_assistant.admin_api.auth import SESSION_COOKIE

    async def unused(*args):
        raise AssertionError("unrelated provider/admin operation invoked")

    secret = "synthetic-v03-admin-session-secret"
    api = create_admin_app(
        AdminContext(
            database=app.database,
            policy=app.policy,
            owner_id=7,
            settings_getter=lambda: app.settings,
            vectors_getter=lambda: app.rag.vectors,
            ollama=SimpleNamespace(),
            ai_switch_handler=unused,
            ollama_activate_handler=unused,
            pause_all_handler=unused,
            resume_all_handler=unused,
            paths={"data": app.settings.data_dir},
            admin_secret=secret,
            management_admission=lambda: True,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://127.0.0.1:8765"
    ) as client:
        preview_url = "/api/v1/groups/20/recovery-preview"
        assert (await client.post(preview_url)).status_code == 401
        assert (await client.get("/api/v1/recovery-jobs/absent")).status_code == 401
        login = await client.post("/api/v1/auth/login", json={"code": dashboard_login_code(secret)})
        assert login.status_code == 200
        headers = {"X-CSRF-Token": login.json()["csrf_token"], "Origin": "http://127.0.0.1:8765"}
        assert (await client.post(preview_url)).status_code == 403
        assert (
            await client.post(preview_url, headers={**headers, "Origin": "https://foreign.example"})
        ).status_code == 403
        # A live session for another owner cannot read or write these handlers.
        original_cookie = client.cookies.get(SESSION_COOKIE)
        foreign = api.state.admin_auth.create_session(8)
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE, foreign.token)
        assert (await client.post(preview_url, headers=headers)).status_code == 403
        assert (await client.get("/api/v1/recovery-jobs/absent")).status_code == 403
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE, original_cookie)
        forged = await client.post(
            "/api/v1/groups/20/actions",
            headers=headers,
            json={
                "action_type": "recover_source_index",
                "payload": {"plan_id": "forged"},
                "preview": "forged plan",
            },
        )
        assert forged.status_code == 422
        before = await snapshot(app)
        preview = await client.post(preview_url, headers=headers)
        assert preview.status_code == 201 and await snapshot(app) == before and requests == []
        action_id = preview.json()["pending_action"]["action_id"]
        confirm_url = f"/api/v1/pending-actions/{action_id}/confirm"
        assert (await client.post(confirm_url)).status_code == 403
        assert (await client.post(confirm_url, headers=headers)).status_code == 200
        assert (await client.post(confirm_url, headers=headers)).status_code == 409

        async def stop_after_cycle(seconds):
            app.stopping.set()

        monkeypatch.setattr("tg_assistant.runtime.asyncio.sleep", stop_after_cycle)
        await app._execute_actions()
        async with app.database.session() as session:
            operation = (await session.get(PendingAction, action_id)).payload["operation_id"]
        control_url = f"/api/v1/recovery-jobs/{operation}/pause"
        assert (await client.post(control_url)).status_code == 403
        assert (
            await client.post(control_url, headers={**headers, "Origin": "https://foreign.example"})
        ).status_code == 403
        await app._process_admin_jobs()
        status = await client.get(f"/api/v1/recovery-jobs/{operation}")
        assert status.status_code == 200 and status.json()["state"] == "completed"
        assert "claim_token" not in status.text and "request_ids" not in status.text
        assert sorted(app.rag.vectors.reference_ids()) == [1, 2, 3] and len(requests) == 1


class NoNetworkDependency:
    model = "synthetic"

    async def close(self):
        pass


def synthetic_runtime_dependencies(app, monkeypatch):
    """Keep runtime vector/DB/Rag code real; stub unrelated native/network clients."""
    from tg_assistant import runtime

    monkeypatch.setattr(runtime, "SecretStore", SyntheticSecrets)
    monkeypatch.setattr(runtime, "configure_logging", lambda *args: None)
    monkeypatch.setattr(runtime, "make_database", lambda *args: app.database)
    monkeypatch.setattr(runtime, "make_user_client", lambda *args: app.user)
    monkeypatch.setattr(runtime, "make_budget", lambda *args: app.budget)
    monkeypatch.setattr(runtime, "make_ai_engine", lambda *args: NoNetworkDependency())
    monkeypatch.setattr(runtime, "make_ai_router", lambda *args: NoNetworkDependency())
    monkeypatch.setattr(runtime, "make_local_embedding_engine", lambda *args: app.embedding_ai)
    monkeypatch.setattr(runtime, "CoinGeckoClient", lambda *args: NoNetworkDependency())
    monkeypatch.setattr(runtime, "OllamaService", lambda *args: NoNetworkDependency())
    monkeypatch.setattr(
        runtime,
        "AsyncIOScheduler",
        lambda **kwargs: SimpleNamespace(add_listener=lambda *args: None),
    )


@pytest.mark.parametrize("storage_missing", [False, True])
async def test_integrated_initialize_uses_selected_persistence(
    recovery_case, monkeypatch, storage_missing
):
    app, _, _ = recovery_case
    from tg_assistant.services.vector_paths import resolve_vector_path

    recovery = service(app)
    await build(app, recovery, await recovery.preview(20, app.settings.embedding_profile.store_id))
    selected = resolve_vector_path(app.database, app.settings, owner_id=7)
    app.rag.vectors.close()
    app.rag.vectors = LocalVectorStore(
        app.settings.resolved_semantic_vector_path,
        vector_size=3,
        profile=app.settings.embedding_profile,
    )
    if storage_missing:
        physical = selected / "collection" / "telegram_messages" / "storage.sqlite"
        physical.rename(selected.parent / ("retained-" + uuid4().hex))
    before = tree_bytes(selected)
    synthetic_runtime_dependencies(app, monkeypatch)
    restarted = Application.__new__(Application)
    restarted.settings = app.settings
    restarted._initialize()
    if storage_missing:
        assert restarted.rag.vectors is None and tree_bytes(selected) == before
        assert sorted(app.rag.vectors.reference_ids()) == [1, 3]
    else:
        assert sorted(restarted.rag.vectors.reference_ids()) == [1, 2, 3]
        assert restarted.rag.vectors.search([0.1, 0.2, 0.3], allowed_chat_ids=[30])[0][0] == 3
        restarted.rag.vectors.close()


async def test_integrated_hot_switch_reopens_selected_generation(recovery_case, monkeypatch):
    app, _, _ = recovery_case
    recovery = service(app)
    await build(app, recovery, await recovery.preview(20, app.settings.embedding_profile.store_id))
    synthetic_runtime_dependencies(app, monkeypatch)
    app.rag.vectors.close()
    app.rag.vectors = None
    app.store, app.ai_router = SyntheticSecrets(), NoNetworkDependency()
    selected_settings = app.settings.model_copy(update={"ai_provider": "off"})
    monkeypatch.setattr("tg_assistant.runtime.save_settings_env", lambda *args: None)
    monkeypatch.setattr("tg_assistant.runtime.get_settings", lambda: selected_settings)
    try:
        await app._switch_ai_provider("off")
        assert sorted(app.rag.vectors.reference_ids()) == [1, 2, 3]
    finally:
        if app.rag.vectors is None:
            app.rag.vectors = LocalVectorStore(
                app.settings.resolved_semantic_vector_path,
                vector_size=3,
                profile=app.settings.embedding_profile,
            )


async def test_integrated_authenticated_startup_refuses_pointer_for_other_owner(recovery_case):
    app, _, _ = recovery_case
    from tg_assistant.services.vector_paths import pointer_key, resolve_vector_path

    recovery = service(app)
    await build(app, recovery, await recovery.preview(20, app.settings.embedding_profile.store_id))
    selected = resolve_vector_path(app.database, app.settings, owner_id=7)
    async with app.database.session() as session:
        pointer = await session.get(
            AppSetting, pointer_key(app.settings.embedding_profile.store_id)
        )
        altered = {**pointer.value, "owner_id": 8}
        pointer.value = altered
        (selected / "recovery-ready.json").write_text(json.dumps(altered), encoding="utf-8")
    before = tree_bytes(selected)

    async def synthetic_resume():
        return SimpleNamespace(id=7, bot=False, deleted=False)

    app.user.resume_existing = synthetic_resume
    app.admin_app_ready = True
    handle = app.rag.vectors
    try:
        # No Telegram account is enrolled in this fixture, so the unchanged
        # pairing boundary stops _run after its real generation-owner guard.
        with pytest.raises(RuntimeError, match="owner_pairing_required"):
            await app._run()
        assert app.rag.vectors is None and handle.client._client.closed
        assert tree_bytes(selected) == before
    finally:
        app.rag.vectors = LocalVectorStore(
            app.settings.resolved_semantic_vector_path,
            vector_size=3,
            profile=app.settings.embedding_profile,
        )


async def paired_runtime_startup(app, monkeypatch):
    """Exercise real vector startup with an explicitly isolated bot composition.

    This double supplies no Telegram health evidence. Native O04 suites own that
    proof; here real SQL/vector callbacks must run against a still-live runtime.
    """
    from tg_assistant import runtime

    async with app.database.session() as session:
        session.add(TelegramAccount(telegram_user_id=7, is_owner_paired=True, is_active=True))

    async def resumed():
        app.user.owner_id = 7
        return SimpleNamespace(id=7, bot=False, deleted=False)

    async def discover(session):
        pass

    callbacks = {}
    registered = asyncio.Event()

    class PrivateBotContext:
        def __init__(self, runtime):
            self.runtime, self.user = runtime, runtime.user
            self.account_client = runtime.user.client
            self.bot = object()
            self.prepared = False

        async def prepare(self):
            assert self.runtime.bot is None and self.user.owner_id == 7
            self.prepared = True

        def management_admitted(self):
            return (
                self.prepared is True
                and self.runtime.user is self.user
                and self.user.client is self.account_client
                and type(self.user.owner_id) is int
                and self.user.owner_id == 7
            )

        async def run_updates(self, dispatcher, borrowed_bot):
            assert borrowed_bot is self.bot
            await app.stopping.wait()

        async def close(self):
            self.prepared = False

    def bot(*args, **kwargs):
        assert app.bot_runtime.prepared and kwargs["bot_instance"] is app.bot_runtime.bot
        callbacks.update(kwargs)
        return SimpleNamespace(**kwargs, run=app.stopping.wait)

    app.user.resume_existing = resumed
    app.user.discover_dialogs = discover
    app.user.register_handlers = lambda *args, **kwargs: None
    app.user.client = SimpleNamespace(run_until_disconnected=app.stopping.wait)
    app.store = SyntheticSecrets()
    app.ai = app.ai_router = app.coingecko = app.ollama = NoNetworkDependency()
    app.paths = {"data": app.settings.data_dir}
    app.scheduler = SimpleNamespace(start=lambda: None, add_job=lambda *args, **kwargs: None)
    app._closed, app._resources_released = False, False
    app._tasks, app.bot_runtime = [], None
    app.bot_runtime_factory = PrivateBotContext
    app.admin_app_ready = lambda api: registered.set()
    app.runtime_ready = None
    monkeypatch.setattr(runtime, "ControlBot", bot)
    monkeypatch.setattr(runtime, "make_ai_engine", lambda *args: NoNetworkDependency())
    monkeypatch.setattr(runtime, "make_ai_router", lambda *args: NoNetworkDependency())
    monkeypatch.setattr(runtime, "make_local_embedding_engine", lambda *args: NoNetworkDependency())
    monkeypatch.setattr(runtime, "save_settings_env", lambda *args: None)
    startup = asyncio.create_task(app._run())
    try:
        await asyncio.wait_for(registered.wait(), 5)
        assert app.management_admitted() is True
    finally:
        # Drain only this fixture's background work. Marking the owning runtime
        # stopped would correctly deny the subsequent hot-switch callbacks.
        startup.cancel()
        tasks = [startup, *app._tasks]
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert not any(
            isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError)
            for result in results
        )
        app._tasks = []
    assert not app.stopping.is_set() and app.management_admitted() is True
    assert callbacks["owner_id"] == 7
    return callbacks


async def alter_generation_owner(app, owner_id):
    from tg_assistant.services.vector_paths import pointer_key, resolve_vector_path

    selected = resolve_vector_path(app.database, app.settings, owner_id=7)
    async with app.database.session() as session:
        pointer = await session.get(
            AppSetting, pointer_key(app.settings.embedding_profile.store_id)
        )
        altered = {**pointer.value, "owner_id": owner_id}
        pointer.value = altered
        (selected / "recovery-ready.json").write_text(json.dumps(altered), encoding="utf-8")
    return selected, altered


@pytest.mark.parametrize("callback", ["switch", "activate"])
@pytest.mark.parametrize("owner_matches", [False, True])
async def test_paired_startup_hot_switch_rechecks_generation_owner(
    recovery_case, monkeypatch, callback, owner_matches
):
    app, requests, _ = recovery_case
    from tg_assistant.services.vector_paths import pointer_key

    recovery = service(app)
    await build(app, recovery, await recovery.preview(20, app.settings.embedding_profile.store_id))
    selected, pointer_before = await alter_generation_owner(app, 7 if owner_matches else 8)
    before = tree_bytes(selected)
    callbacks = await paired_runtime_startup(app, monkeypatch)
    assert app.user.owner_id == 7
    if not owner_matches:
        assert app.rag.vectors is None
    else:
        app.rag.vectors.close()
        app.rag.vectors = None
    settings_before = app.settings
    candidate = settings_before.model_copy(
        update={"ai_provider": "off"}
        if callback == "switch"
        else {
            "ai_provider": "ollama",
            "embedding_provider": "ollama",
            "ollama_primary_model": "synthetic-chat",
            "ollama_embedding_model": "synthetic-embed",
            "ollama_vector_size": 3,
        }
    )
    monkeypatch.setattr("tg_assistant.runtime.get_settings", lambda: candidate)
    calls_before = len(requests)

    async def invoke():
        if callback == "switch":
            return await callbacks["ai_provider_switch_handler"]("off")
        return await callbacks["ollama_activate_handler"]("synthetic-chat", "synthetic-embed", 3)

    try:
        if not owner_matches:
            with pytest.raises(ValueError, match="invalid_vector_pointer"):
                await invoke()
            assert app.rag.vectors is None and app.settings == settings_before
            assert not candidate.resolved_semantic_vector_path.exists() or callback == "switch"
        else:
            await invoke()
            if callback == "switch":
                assert sorted(app.rag.vectors.reference_ids()) == [1, 2, 3]
            else:
                assert app.rag.vectors.profile.store_id == candidate.embedding_profile.store_id
                assert app.rag.vectors.reference_ids() == []
        async with app.database.session() as session:
            assert (
                await session.get(
                    AppSetting, pointer_key(settings_before.embedding_profile.store_id)
                )
            ).value == pointer_before
        assert tree_bytes(selected) == before and len(requests) == calls_before
    finally:
        if app.rag.vectors is None:
            app.rag.vectors = LocalVectorStore(
                settings_before.resolved_semantic_vector_path,
                vector_size=3,
                profile=settings_before.embedding_profile,
            )


@pytest.mark.parametrize("callback", ["switch", "activate"])
@pytest.mark.parametrize("unverified_owner", [None, True, 0])
async def test_paired_hot_switch_refuses_unavailable_authenticated_owner(
    recovery_case, monkeypatch, callback, unverified_owner
):
    app, _, _ = recovery_case
    recovery = service(app)
    await build(app, recovery, await recovery.preview(20, app.settings.embedding_profile.store_id))
    callbacks = await paired_runtime_startup(app, monkeypatch)
    original = app.settings
    app.user.owner_id = unverified_owner
    monkeypatch.setattr("tg_assistant.runtime.get_settings", lambda: original)
    try:
        with pytest.raises(PermissionError, match="vector_owner_unverified"):
            if callback == "switch":
                await callbacks["ai_provider_switch_handler"]("off")
            else:
                await callbacks["ollama_activate_handler"]("synthetic-chat", "synthetic-embed", 3)
        assert app.settings == original
    finally:
        app.user.owner_id = 7


async def test_paired_activation_target_pointer_requires_authenticated_owner(
    recovery_case, monkeypatch
):
    app, _, _ = recovery_case
    from tg_assistant.paths import current_user_sid
    from tg_assistant.services.vector_paths import generation_path, pointer_key, write_ready
    from tg_assistant.services.vector_recovery import corpus_hash

    recovery = service(app)
    await build(app, recovery, await recovery.preview(20, app.settings.embedding_profile.store_id))
    callbacks = await paired_runtime_startup(app, monkeypatch)
    current = app.rag.vectors
    candidate = app.settings.model_copy(
        update={
            "ai_provider": "ollama",
            "embedding_provider": "ollama",
            "ollama_primary_model": "synthetic-chat",
            "ollama_embedding_model": "synthetic-embed",
            "ollama_vector_size": 3,
        }
    )
    profile = candidate.embedding_profile
    generation_id = uuid4().hex
    path = generation_path(candidate, generation_id)
    target = LocalVectorStore(path, vector_size=3, profile=profile)
    target.upsert(50, [0.1, 0.2, 0.3], chat_id=20, message_id=50)
    pointer = {
        "profile_id": candidate.profile_id,
        "sid": current_user_sid(),
        "owner_id": 8,
        "store_id": profile.store_id,
        "identity": profile.model_dump(mode="json", exclude={"cloud_consent"}),
        "generation_id": generation_id,
        "generation": 1,
        "state": "ready",
        "verified_corpus": corpus_hash(target),
    }
    write_ready(path, pointer)
    target.close()
    async with app.database.session() as session:
        session.add(AppSetting(key=pointer_key(profile.store_id), value=pointer))
        session.add(
            VectorStore(
                store_id=profile.store_id,
                path=str(path.resolve()),
                collection="telegram_messages",
                provider=profile.provider,
                endpoint_id=profile.endpoint_id,
                model=profile.model,
                embedding_version=profile.embedding_version,
                dimension=profile.dimension,
                role="semantic_candidate",
                state="ready",
            )
        )
    before = tree_bytes(path)
    monkeypatch.setattr("tg_assistant.runtime.get_settings", lambda: candidate)
    with pytest.raises(ValueError, match="invalid_vector_pointer"):
        await callbacks["ollama_activate_handler"]("synthetic-chat", "synthetic-embed", 3)
    assert app.rag.vectors is current and sorted(current.reference_ids()) == [1, 2, 3]
    assert tree_bytes(path) == before
    async with app.database.session() as session:
        assert (await session.get(AppSetting, pointer_key(profile.store_id))).value == pointer
