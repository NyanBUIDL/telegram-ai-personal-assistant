"""Actual guided action and first-value owner association on disposable SQLite."""
# ruff: noqa: F811
from datetime import UTC, datetime, timedelta
from time import monotonic
from types import SimpleNamespace

import pytest
from aiogram.types import Update
from services.test_first_value import incoming, prepared  # noqa: F401
from services.test_first_value_observation import observed  # noqa: F401
from sqlalchemy import delete, func, select
from test_first_source_preview import actions, create, effects, execute, guided  # noqa: F401
from test_first_value_index import CHAT, OWNER
from test_first_value_selection import selection  # noqa: F401
from test_incremental_catchup import HistoryClient

from tg_assistant.admin_api.app import _update_job_state
from tg_assistant.contracts import OnboardingStage
from tg_assistant.db.models import (
    BackgroundJob,
    KnowledgeSource,
    PendingAction,
    PermissionName,
    SyncState,
    TelegramChatPermission,
    TelegramChatPolicy,
    TelegramMessage,
)
from tg_assistant.desktop.setup_context import SetupContext
from tg_assistant.services.actions import PendingActionService
from tg_assistant.services.jobs import claim_runtime_job, worker_lease
from tg_assistant.services.onboarding import StageVerification
from tg_assistant.telegram.control_bot import ControlBot
from tg_assistant.telegram.user_client import UserClientAdapter


async def confirm_history(app):
    async with app.database.session() as session:
        action = await app.first_value.preview.create(session, CHAT, 1000)
    async with app.database.session() as session:
        await PendingActionService(first_source_validator=app._validate_first_source_action).confirm(
            session, action.action_id, OWNER)
    try:
        await execute(SimpleNamespace(runtime=app))
    except PermissionError as error:
        assert app.stopping.is_set() and str(error) == "owner_pairing_required"
    app.stopping.clear()
    return action, app.first_value.history().header.learning


@pytest.fixture
async def history_journey(prepared):
    env, app = prepared, prepared.application

    class History(HistoryClient):
        fetched = 0
        retained = True
        fail_after = None

        async def iter_messages(self, *args, **kwargs):
            async for message in super().iter_messages(*args, **kwargs):
                self.fetched += 1
                message.message = "Kế hoạch ra mắt ứng dụng Telegram vào thứ Sáu; nhóm cần hoàn thành kiểm thử trước thứ Năm."
                if not self.retained:
                    message.date -= timedelta(days=1000)
                yield message
                if self.fail_after and self.fetched >= self.fail_after:
                    raise RuntimeError("synthetic history transport interrupted")

    adapter = object.__new__(UserClientAdapter)
    adapter.owner_id, adapter.policy, adapter.database = OWNER, app.policy, app.database
    adapter.management_admission = app.management_admitted
    adapter.client = History(range(2, 2503))
    app.user = adapter
    app.first_value.initialize()
    async with app.database.session() as session:
        await session.execute(delete(TelegramChatPermission))
        policy = await session.scalar(select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == CHAT))
        policy.max_messages, policy.max_storage_mb, policy.retention_days = 10000, 100, 30
    await app.first_value.select_source(CHAT)
    return env


@pytest.mark.parametrize("retained", [True, False])
async def test_guided_confirmation_caps_aggregate_history_reads(history_journey, retained):
    env, app = history_journey, history_journey.application
    adapter = app.user
    adapter.client.retained = retained
    async with app.database.session() as session:
        session.add(SyncState(chat_id=CHAT, last_message_id=1))
    action, association = await confirm_history(app)
    assert association is not None and association.action_id == action.action_id
    counts = []
    tokens = []
    for _ in range(3):
        claimed = await claim_runtime_job(app.database, ("learn_group",))
        if claimed is None:
            break
        tokens.append(claimed[0].claim_token)
        with worker_lease(claimed[0]):
            await app._run_learning_lease(claimed[0], monotonic())
        async with app.database.session() as session:
            job = await session.get(BackgroundJob, association.job_id)
            counts.append((job.status, job.payload.get("history_read"), job.payload.get("synced_count")))
            job.run_after = None
    async with app.database.session() as session:
        job = await session.get(BackgroundJob, association.job_id)
        state = await session.scalar(select(SyncState).where(SyncState.chat_id == CHAT))
        stored = await session.scalar(select(func.count(TelegramMessage.id)))
        print(ascii({"preview": action.preview, "claims": counts, "fetched": adapter.client.fetched,
               "stored": stored, "cursor": (state.last_message_id, state.catchup_upper_id, state.catchup_after_id),
               "provider_calls": len(env.provider_calls)}))
        assert adapter.client.fetched <= 1000
        assert job.payload["history_read"] == 1000
        assert job.status == "failed" and job.last_error == "history_limit_reached"
        assert job.payload["history_completed"] is False
        assert job.payload["history_stop_reason"] == "confirmation_limit"
        assert (state.last_message_id, state.catchup_upper_id, state.catchup_after_id) == (1, 2502, 1000)
        assert stored == (999 if retained else 0)
        assert job.payload["authorization_epoch"] == association.source_epoch
        assert job.claim_token == tokens[-1]
    await app._refresh_group_knowledge()
    assert not env.provider_calls
    await app.first_value.refresh_observation()
    status = app.first_value.selection_status()
    assert not status.test_available
    assert status.learning_operation.code == "history_limit_reached"
    assert status.learning_operation.next_action
    async with app.database.session() as session:
        _update_job_state(await session.get(BackgroundJob, association.job_id), "retry")
    await app._process_learning_jobs()
    assert adapter.client.fetched == 1000 and not env.provider_calls
    async with app.database.session() as session:
        job = await session.get(BackgroundJob, association.job_id)
        assert job.status == "failed" and job.last_error == "history_limit_reached"
        source = await session.get(KnowledgeSource, CHAT)
        assert source.last_job_id == job.id and source.status == "failed"
        assert source.last_error == "history_limit_reached" and not source.requested_for_learning
    if retained:
        for after, expected_status in ((2000, "failed"), (2502, "completed")):
            new_action, new_association = await confirm_history(app)
            assert new_association.job_id != association.job_id
            assert new_association.action_id == new_action.action_id != action.action_id
            before = adapter.client.fetched
            await app._process_learning_jobs()
            assert 0 < adapter.client.fetched - before <= 1000
            async with app.database.session() as session:
                new_job = await session.get(BackgroundJob, new_association.job_id)
                assert new_job.status == expected_status
                assert new_job.payload["authorization_epoch"] == new_association.source_epoch
                state = await session.scalar(select(SyncState).where(SyncState.chat_id == CHAT))
                if expected_status == "failed":
                    assert (state.last_message_id, state.catchup_upper_id, state.catchup_after_id) == (1, 2502, after)
                    assert not env.provider_calls
                else:
                    assert state.last_message_id == after and state.catchup_upper_id is None
                    assert await session.scalar(select(func.count(TelegramMessage.id))) == 2501
            association = new_association
        await app.first_value.refresh_observation()
        assert app.first_value.selection_status().test_available


async def test_guided_initial_latest_thousand_completes_without_extra_fetch(history_journey):
    app = history_journey.application
    _, association = await confirm_history(app)
    await app._process_learning_jobs()
    async with app.database.session() as session:
        job = await session.get(BackgroundJob, association.job_id)
        assert job.status == "completed" and job.payload["history_completed"] is True
        assert job.payload["history_read"] == app.user.client.fetched == 1000
        ids = set(await session.scalars(select(TelegramMessage.message_id)))
        assert ids == set(range(1503, 2503))
        state = await session.scalar(select(SyncState).where(SyncState.chat_id == CHAT))
        assert state.last_message_id == 2502 and state.catchup_upper_id is None
    await app.first_value.refresh_observation()
    assert app.first_value.selection_status().test_available


@pytest.mark.parametrize("retry_old", [False, True])
async def test_exhausted_old_retry_preserves_completed_source_and_new_indexing(history_journey, retry_old):
    env, app = history_journey, history_journey.application
    await test_guided_confirmation_caps_aggregate_history_reads(env, True)
    # Finish older duplicate batches so a single refresh reaches the new monitored row.
    for _ in range(20):
        await app._refresh_group_knowledge()
    async with app.database.session() as session:
        assert await session.scalar(select(func.count(TelegramMessage.id)).where(TelegramMessage.vector_status == "pending")) == 0
        jobs = list(await session.scalars(select(BackgroundJob).order_by(BackgroundJob.created_at, BackgroundJob.id)))
        assert [job.status for job in jobs] == ["failed", "failed", "completed"]
        first_id, latest_id = jobs[0].id, jobs[-1].id
        epoch = (await session.scalar(select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == CHAT))).authorization_epoch
        assert [job.payload["authorization_epoch"] for job in jobs] == [epoch, epoch, epoch]
        session.add(TelegramMessage(chat_id=CHAT, message_id=2503,
            text="The new monitored update announces a revised launch timetable with an additional independent review on Monday.",
            sent_at=datetime.now(UTC)))
    before_calls, before_fetches = len(env.provider_calls), app.user.client.fetched
    if retry_old:
        async with app.database.session() as session:
            _update_job_state(await session.get(BackgroundJob, first_id), "retry")
        await app._process_learning_jobs()
        assert len(env.provider_calls) == before_calls and app.user.client.fetched == before_fetches
    await app._refresh_group_knowledge()
    async with app.database.session() as session:
        first, latest = await session.get(BackgroundJob, first_id), await session.get(BackgroundJob, latest_id)
        source = await session.get(KnowledgeSource, CHAT)
        state = await session.scalar(select(SyncState).where(SyncState.chat_id == CHAT))
        row = await session.scalar(select(TelegramMessage).where(TelegramMessage.chat_id == CHAT, TelegramMessage.message_id == 2503))
        assert first.status == "failed" and first.last_error == "history_limit_reached"
        assert first.payload["history_completed"] is False
        assert latest.status == "completed" and latest.payload["history_completed"] is True
        assert source.last_job_id == latest_id and source.status == "learned" and source.last_error is None
        assert (state.last_message_id, state.catchup_upper_id, state.catchup_after_id) == (2502, None, None)
        assert row.vector_status == "indexed"
    assert len(env.provider_calls) == before_calls + 1 and app.user.client.fetched == before_fetches
    await app.first_value.refresh_observation()
    assert app.first_value.selection_status().test_available


async def test_guided_interrupted_fetch_cannot_reuse_reserved_allowance(history_journey):
    env, app = history_journey, history_journey.application
    async with app.database.session() as session:
        session.add(SyncState(chat_id=CHAT, last_message_id=1))
    app.user.client.fail_after = 100
    _, association = await confirm_history(app)
    await app._process_learning_jobs()
    async with app.database.session() as session:
        job = await session.get(BackgroundJob, association.job_id)
        assert job.status == "queued" and job.payload["history_read_reserved"] == 1000
        assert job.payload["history_read"] == 0
        job.run_after = None
    app.user.client.fail_after = None
    await app._process_learning_jobs()
    assert app.user.client.fetched == 100
    assert not env.provider_calls
    async with app.database.session() as session:
        job = await session.get(BackgroundJob, association.job_id)
        assert job.status == "failed" and job.last_error == "history_limit_reached"
        state = await session.scalar(select(SyncState).where(SyncState.chat_id == CHAT))
        assert state.last_message_id == 1 and state.catchup_upper_id is None
        assert await session.scalar(select(func.count(TelegramMessage.id))) == 0


async def test_guided_block_cancels_original_job_without_fetch(history_journey):
    env, app = history_journey, history_journey.application
    _, association = await confirm_history(app)
    async with app.database.session() as session:
        await app.policy.set_allowed(session, CHAT, False)
    await app._process_learning_jobs()
    async with app.database.session() as session:
        job = await session.get(BackgroundJob, association.job_id)
        assert job.status == "cancelled" and job.payload["authorization_epoch"] == association.source_epoch
    assert app.user.client.fetched == 0 and not env.provider_calls
    await app.first_value.refresh_observation()
    assert not app.first_value.selection_status().test_available


async def test_actual_guided_action_associates_original_job(guided):
    action = await create(guided)
    async with guided.runtime.database.session() as session:
        await actions(guided).confirm(session, action.action_id, 123)
    await execute(guided)
    association = guided.owner.history().header.learning
    assert association is not None, "runtime omitted the original guided job association"
    assert association.action_id == action.action_id
    assert association.source_epoch > action.payload["authorization_epochs"][str(action.chat_id)]


async def test_confirmed_preset_restores_only_its_action_and_new_job_epoch(guided):
    app, owner = guided.runtime, guided.owner
    async with app.database.session() as session:
        await app.policy.set_permission(session, CHAT, PermissionName.PIN_MESSAGES, True)
    action = await create(guided)
    capture_payload = dict(action.payload)
    async with app.database.session() as session:
        other = await PendingActionService().create(session, action_type="pin_message", requested_by=123,
            chat_id=action.chat_id, message_id=10, payload={}, preview="old work")
        executing = await PendingActionService().create(session, action_type="pin_message", requested_by=123,
            chat_id=action.chat_id, message_id=10, payload={}, preview="old executing work")
        executing.status = "executing"
        executing.execution_started_at = datetime.now(UTC)
        old_job = BackgroundJob(job_type="history_backfill", status="running", payload={"chat_id": action.chat_id})
        session.add(old_job)
        await session.flush()
        ids = other.action_id, executing.action_id, old_job.id
        await actions(guided).confirm(session, action.action_id, 123)
    await execute(guided)
    association = owner.history().header.learning
    assert association is not None
    async with app.database.session() as session:
        finished = await session.get(PendingAction, action.action_id)
        assert finished.status == "executed" and finished.error is None
        assert finished.payload["authorization_epochs"] == capture_payload["authorization_epochs"]
        assert (await session.get(PendingAction, ids[0])).status == "cancelled"
        assert (await session.get(PendingAction, ids[1])).status == "cancelled"
        assert (await session.get(PendingAction, ids[1])).error == "source_authorization_revoked"
        assert (await session.get(BackgroundJob, ids[2])).status == "cancelled"
        policy = await session.scalar(select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == action.chat_id))
        job = await session.get(BackgroundJob, association.job_id)
        assert association.source_epoch == job.payload["authorization_epoch"] == policy.authorization_epoch
        assert association.source_epoch == capture_payload["authorization_epochs"][str(action.chat_id)] + 2
        await app.policy.set_allowed(session, action.chat_id, False)
        await app.policy.apply_template(session, action.chat_id, "knowledge")
    async with app.database.session() as session:
        assert (await session.get(BackgroundJob, association.job_id)).status == "cancelled"
        policy = await session.scalar(select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == action.chat_id))
        assert policy.authorization_epoch > association.source_epoch


@pytest.mark.parametrize("change", ["reason", "status", "external", "capture"])
async def test_preset_cannot_restore_any_other_cancellation(guided, change, monkeypatch):
    action = await create(guided)
    async with guided.runtime.database.session() as session:
        await actions(guided).confirm(session, action.action_id, 123)
    before = await effects(guided)
    apply = guided.runtime.policy.apply_template
    async def alter(session, *args, **kwargs):
        await apply(session, *args, **kwargs)
        current = await session.get(PendingAction, action.action_id)
        assert current.status == "cancelled"
        if change == "reason":
            current.error = "different-cancellation"
        elif change == "status":
            current.status = "uncertain"
        elif change == "external":
            current.payload = {**current.payload, "external_effect_started": True}
        else:
            session.sync_session.info["first_source_validated_actions"].pop(action.action_id)
    monkeypatch.setattr(guided.runtime.policy, "apply_template", alter)
    await execute(guided)
    assert await effects(guided) == before
    assert guided.owner.history().header.learning is None


async def test_actual_intent_preview_finite_history_index_delivery_ready(prepared):
    env, app = prepared, prepared.application
    class History(HistoryClient):
        async def iter_messages(self, *args, **kwargs):
            async for message in super().iter_messages(*args, **kwargs):
                message.message = "Kế hoạch ra mắt ứng dụng Telegram vào thứ Sáu; nhóm cần hoàn thành kiểm thử trước thứ Năm."
                yield message
    adapter = object.__new__(UserClientAdapter)
    adapter.owner_id, adapter.policy, adapter.database = OWNER, app.policy, app.database
    adapter.management_admission = app.management_admitted
    adapter.client = History([10])
    app.user = adapter
    owner = app.first_value
    owner.initialize()
    with env.engine.begin() as connection:
        connection.execute(delete(TelegramChatPermission))
    await owner.select_source(CHAT)
    assert owner.history().header.learning is None
    async with app.database.session() as session:
        assert not (await session.scalars(select(TelegramChatPermission))).all()
        action = await owner.preview.create(session, CHAT, 1000)
    assert not owner.selection_status().test_available
    async with app.database.session() as session:
        assert not (await session.scalars(select(BackgroundJob))).all()
        assert not (await session.scalars(select(TelegramChatPermission))).all()
        await PendingActionService(first_source_validator=app._validate_first_source_action).confirm(session, action.action_id, OWNER)
    try:
        await execute(SimpleNamespace(runtime=app))
    except PermissionError as error:
        assert app.stopping.is_set() and str(error) == "owner_pairing_required"
    app.stopping.clear()
    association = owner.history().header.learning
    assert association is not None
    async with app.database.session() as session:
        job = await session.get(BackgroundJob, association.job_id)
        assert job.status == "queued" and job.payload["limit"] == 1000
    claimed = await claim_runtime_job(app.database, ("learn_group",))
    assert claimed is not None
    with worker_lease(claimed[0]):
        await app._run_learning_lease(claimed[0], monotonic())
    async with app.database.session() as session:
        job = await session.get(BackgroundJob, association.job_id)
        assert job.status == "completed", (job.status, job.last_error, job.payload)
        assert job.payload["history_completed"] is True
        assert job.payload["interval_upper"] == job.payload["interval_after"] == 10
        row = await session.scalar(select(TelegramMessage))
        assert row.vector_status == "indexed"
    await owner.refresh_observation()
    assert owner.selection_status().test_available
    for stage in list(OnboardingStage)[:6]:
        env.coordinator._verifiers[stage] = lambda: StageVerification(owner_id=OWNER, fingerprint="f" * 64)
    for stage in (OnboardingStage.WELCOME, OnboardingStage.STORAGE_READY):
        env.coordinator.complete_stage(stage, env.coordinator.verify_stage(stage))
    context = SetupContext(env.coordinator, env.engine, env.fence, None)
    assert context.advance_verified().profile.setup_stage == OnboardingStage.SOURCE_SELECTED
    control = ControlBot("", owner_id=OWNER, database=app.database, policy=app.policy,
        rag=app.rag, bot_instance=env.sdk, admission=app.management_admitted,
        first_value_getter=lambda: app.first_value)
    await control.dp.feed_update(env.sdk, Update(update_id=123, message=incoming(env)))
    assert len(owner.history().receipts) == 1
    assert context.advance_verified().profile.setup_stage == OnboardingStage.READY
    assert len(env.wire.calls) == 1
    assert len(env.provider_calls) == 3  # corpus embedding, query embedding, actual answer
    await control.close()
