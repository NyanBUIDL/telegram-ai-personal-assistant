"""Owned observations over migrated SQLite and installed local Qdrant; no live calls."""
import asyncio
import hashlib
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import insert, select
from test_first_value_index import CHAT, OWNER, Env
from test_first_value_index_integration import adopt_application

from tg_assistant.ai.router import AiRouter
from tg_assistant.config import save_settings
from tg_assistant.contracts import OnboardingStage
from tg_assistant.db.base import Base
from tg_assistant.db.migrations import upgrade_database
from tg_assistant.db.models import (
    AiBudgetReservation,
    BackgroundJob,
    PendingAction,
    TelegramChat,
    TelegramChatPermission,
    TelegramChatPolicy,
    TelegramMessage,
)
from tg_assistant.services.first_value import _cas_header, _LearningAssociation


@pytest.fixture
async def observed(tmp_path, monkeypatch):
    def migrate(engine):
        with engine.connect() as connection:
            upgrade_database(connection, script_location=Path(__file__).resolve().parents[2] / "alembic")
    with monkeypatch.context() as patch:
        patch.setattr(Base.metadata, "create_all", migrate)
        env = await Env.create(tmp_path)
    env.application = None
    await adopt_application(env)
    app = env.application
    app.embedding_ai = app.ai
    app.embedding_ai.embedding_dimension = env.profile.dimension
    app.ai_router = AiRouter({"ollama": app.ai}, default_provider="ollama")
    save_settings(app.settings)
    app.bot_runtime.pairing_verification = lambda: SimpleNamespace(owner_id=OWNER, fingerprint="b" * 64)
    app.bot_runtime.service.verified_identity = SimpleNamespace(bot_id=456, username="Verified_bot")
    await env.reader.close()
    try:
        yield env
    finally:
        await env.aclose()
        await app.ai.close()


def initialize(env):
    assert callable(getattr(env.first_value, "initialize", None)), "Explicit post-assignment reader ownership is missing"
    env.first_value.initialize()


def learned(env, **changes):
    action_id, job_id = "fv1-" + "c" * 32, "cccccccc-cccc-cccc-cccc-cccccccccccc"
    payload = dict(action_id=action_id, chat_id=CHAT, owner_id=OWNER, authorization_epoch=1,
                   limit=1000, history_completed=True, interval_upper=10, interval_after=0,
                   history_stop_reason=None, progress=100)
    payload.update(changes)
    with env.first_value._transaction() as connection:
        connection.execute(insert(PendingAction).values(action_id=action_id, action_type="enable_group_learning",
            chat_id=CHAT, requested_by=OWNER, status="executed", payload={}, preview="bounded", expires_at=env.clock))
        connection.execute(insert(BackgroundJob).values(id=job_id, job_type="learn_group", status="completed", payload=payload))
        header = env.first_value._read_header(connection)
        association = _LearningAssociation(selection_generation=header.selection_generation,
            restore_epoch=header.restore_epoch, source_id=CHAT, source_epoch=1, owner_id=OWNER,
            action_id=action_id, job_id=job_id)
        _cas_header(connection, before=header, after=header.model_copy(update={
            "revision": header.revision + 1, "learning": association}))
        connection.execute(insert(TelegramChatPermission).values(chat_id=CHAT, permission="sync_history", enabled=True))
    return job_id


async def test_attach_is_after_assignment_and_owns_one_reader(observed):
    env = observed
    initialize(env)
    reader = env.first_value.reader
    assert reader._base[12] is env.first_value
    env.first_value.initialize()
    assert env.first_value.reader is reader
    assert env.coordinator._verifiers[OnboardingStage.SOURCE_SELECTED] == env.first_value.source_verification
    assert env.coordinator._verifiers[OnboardingStage.FIRST_ANSWER] == env.first_value.first_answer_verification


@pytest.mark.parametrize("initial_off", [False, True])
async def test_actual_provider_activation_reattaches_without_recovery(observed, monkeypatch, initial_off):
    from tg_assistant.desktop.provider_activation import reconcile_saved_provider_settings
    env, app = observed, observed.application
    app.store = SimpleNamespace(get=lambda name: None)
    if initial_off:
        app.settings.enable_embeddings = False
        save_settings(app.settings)
    initialize(env)
    env.add_indexed(10)
    learned(env)
    await env.first_value.refresh_observation()
    assert env.first_value.selection_status().test_available is (not initial_off)
    def forbidden(*args):
        pytest.fail("activation replayed startup recovery")
    monkeypatch.setattr(env.first_value, "_recover_in_transaction", forbidden)
    if initial_off:
        save_settings(app.settings.model_copy(update={"enable_embeddings": True}))
    try:
        for update in ({}, {"ollama_primary_model": "compatible-chat"}):
            if update:
                save_settings(app.settings.model_copy(update=update))
            old_reader, old_settings = env.first_value.reader, app.settings
            assert await reconcile_saved_provider_settings(app, current_sid=lambda: env.sid, fence=env.fence)
            assert app.settings is not old_settings
            assert env.first_value.reader is not old_reader
            assert old_reader is None or old_reader._closed
            assert env.first_value.current_binding is None
            await env.first_value.refresh_observation()
            assert env.first_value.source_verification() is not None
            assert env.first_value.selection_status().test_available
            current = env.first_value.reader
            assert not await reconcile_saved_provider_settings(app, current_sid=lambda: env.sid, fence=env.fence)
            assert env.first_value.reader is current
    finally:
        await app.ai_router.close()
        await app.embedding_ai.close()


@pytest.mark.parametrize("held_kind", ["refresh", "reader", "answer"])
@pytest.mark.parametrize("shutdown", [False, True])
async def test_activation_drains_before_clients_close_even_when_cancelled(observed, monkeypatch, held_kind, shutdown):
    import threading

    from tg_assistant.desktop.provider_activation import reconcile_saved_provider_settings
    env, app, owner = observed, observed.application, observed.first_value
    app.store = SimpleNamespace(get=lambda name: None)
    initialize(env)
    env.add_indexed(10)
    learned(env)
    await owner.refresh_observation()
    old_reader, old_settings, old_binding = owner.reader, app.settings, owner.current_binding
    release, started = threading.Event(), threading.Event()
    async_release, async_started = asyncio.Event(), asyncio.Event()
    def blocking():
        started.set()
        assert release.wait(10), "test must release actual borrowed worker"
    async def held(*args):
        async_started.set()
        # Prove activation is not holding the same knowledge lock while draining.
        await async_release.wait()
        async with app._knowledge_lock:
            return None
    if held_kind == "reader":
        pending = asyncio.create_task(old_reader._offload(blocking, 0.05))
        assert await asyncio.to_thread(started.wait, 2)
    elif held_kind == "refresh":
        monkeypatch.setattr(old_reader, "read_source_binding", held)
        pending = asyncio.create_task(owner.refresh_observation())
        await async_started.wait()
    else:
        pending = asyncio.create_task(held())
        owner._answer_tasks.add(pending)
        await async_started.wait()
    closed = []
    old_close = app.ai.close
    async def tracked_close():
        assert old_reader._closed and not old_reader._tasks
        assert pending.done() and not owner._answer_tasks and not owner._refresh_tasks
        assert owner.reader is None
        closed.append(True)
        await old_close()
    monkeypatch.setattr(app.ai, "close", tracked_close)
    activation = asyncio.create_task(reconcile_saved_provider_settings(app, current_sid=lambda: env.sid, fence=env.fence))
    second = shutdown_task = None
    try:
        async def transitioning():
            while not owner._provider_transition:
                await asyncio.sleep(0)
        await asyncio.wait_for(transitioning(), 2)
        assert owner.current_binding is None and not closed
        with env.engine.connect() as connection:
            assert not owner.authorize_current_in_transaction(connection, old_binding)
        with pytest.raises(ValueError, match="first_value_busy"):
            await owner.refresh_observation()
        activation.cancel()
        await asyncio.sleep(0)
        activation.cancel()
        assert not activation.done() and app.settings is old_settings
        if shutdown:
            shutdown_task = asyncio.create_task(owner.close())
            await asyncio.sleep(0)
            shutdown_task.cancel()
            await asyncio.sleep(0)
            assert not shutdown_task.done()
        else:
            second = asyncio.create_task(reconcile_saved_provider_settings(app, current_sid=lambda: env.sid, fence=env.fence))
            await asyncio.sleep(0)
            assert not second.done()
    finally:
        release.set()
        async_release.set()
        results = await asyncio.wait_for(asyncio.gather(
            pending, activation, *(task for task in (second, shutdown_task) if task is not None),
            return_exceptions=True), 5)
        assert isinstance(results[1], asyncio.CancelledError)
        if shutdown:
            assert not closed and app.settings is old_settings and owner.reader is None
            # Runtime owns client release after FirstValueService.close returns.
            await old_close()
        else:
            assert closed and results[2] is False
            assert owner.reader is not old_reader and old_reader._closed
            await owner.refresh_observation()
            assert owner.selection_status().test_available
        assert not owner._activation_tasks and not owner._provider_transition
        await app.ai_router.close()
        await app.embedding_ai.close()


async def test_activation_skip_then_enabled_and_later_off_on(observed):
    from tg_assistant.desktop.provider_activation import reconcile_saved_provider_settings
    env, app, owner = observed, observed.application, observed.first_value
    app.store = SimpleNamespace(get=lambda name: None)
    env.coordinator.save_options({"ai_mode": "skip"})
    initialize(env)
    env.add_indexed(10)
    learned(env)
    try:
        assert await reconcile_saved_provider_settings(app, current_sid=lambda: env.sid, fence=env.fence)
        assert owner.reader is None
        # Same providers, but the explicit owner transition can acquire the first reader.
        env.coordinator.save_options({"ai_mode": "local"})
        assert not await reconcile_saved_provider_settings(app, current_sid=lambda: env.sid, fence=env.fence)
        assert owner.reader is not None
        await owner.refresh_observation()
        assert owner.selection_status().test_available
        reader = owner.reader
        for enabled in (False, True):
            save_settings(app.settings.model_copy(update={"enable_embeddings": enabled}))
            assert await reconcile_saved_provider_settings(app, current_sid=lambda: env.sid, fence=env.fence)
            await owner.refresh_observation()
            assert owner.selection_status().test_available is enabled
            assert (owner.reader is not None) is enabled
        assert reader._closed and owner.reader is not reader
    finally:
        await app.ai_router.close()
        await app.embedding_ai.close()


async def test_activation_preserves_original_active_attempt_without_recovery(observed, monkeypatch):
    from tg_assistant.desktop.provider_activation import reconcile_saved_provider_settings
    from tg_assistant.services.first_value import _RequestIdentity
    env, owner, app = observed, observed.first_value, observed.application
    await completed_receipt(env)
    app.store = SimpleNamespace(get=lambda name: None)
    request = _RequestIdentity(bot_id=456, enrollment_generation="a" * 32, owner_id=OWNER, incoming_message_id=124)
    with owner._transaction() as connection:
        attempt, _ = owner._accept_in_transaction(connection, request=request, binding=owner.current_binding, accepted_at=datetime.now(UTC))
        attempt = owner._transition_in_transaction(connection, attempt=attempt, phase="generating")
        attempt = owner._transition_in_transaction(connection, attempt=attempt, phase="delivering", chunk_count=1)
        attempt = owner._submit_in_transaction(connection, attempt=attempt, ordinal=1)
    original = owner.history()
    def forbidden(*args):
        pytest.fail("activation must not replay startup recovery")
    monkeypatch.setattr(owner, "_recover_in_transaction", forbidden)
    try:
        assert await reconcile_saved_provider_settings(app, current_sid=lambda: env.sid, fence=env.fence)
        await owner.refresh_observation()
        assert owner.history() == original
        assert owner.current_binding is None and owner.first_answer_verification() is None
        status = owner.selection_status()
        assert not status.test_available
        assert "khởi động lại" in status.answer_operation.next_action.lower()
    finally:
        await app.ai_router.close()
        await app.embedding_ai.close()


async def test_source_authorization_does_not_require_learning_or_index(observed):
    initialize(observed)
    await observed.first_value.refresh_observation()
    assert observed.first_value.source_verification() is not None
    assert observed.first_value.current_binding is None
    assert not observed.first_value.selection_status().test_available
    observed.update(TelegramChatPermission, TelegramChatPermission.chat_id == CHAT, enabled=False)
    assert observed.first_value.source_verification() is None


@pytest.mark.parametrize("completed", [True, False])
async def test_only_exact_completed_guided_job_unlocks_test(observed, completed):
    initialize(observed)
    observed.add_indexed(10)
    await observed.first_value.refresh_observation()
    assert not observed.first_value.selection_status().test_available
    learned(observed, history_completed=completed)
    await observed.first_value.refresh_observation()
    assert observed.first_value.selection_status().test_available is completed
    assert observed.first_value.first_answer_verification() is None


async def test_skip_has_source_authority_without_reader(observed):
    observed.coordinator.save_options({"ai_mode": "skip"})
    initialize(observed)
    assert observed.first_value.reader is None
    await observed.first_value.refresh_observation()
    assert observed.first_value.source_verification() is not None
    assert not observed.first_value.selection_status().test_available


async def test_get_and_verifiers_do_not_probe_or_recover(observed, monkeypatch):
    initialize(observed)
    observed.add_indexed(10)
    learned(observed)
    await observed.first_value.refresh_observation()
    def forbidden(*args, **kwargs):
        pytest.fail("GET/verifier attempted observation or recovery")
    monkeypatch.setattr(observed.first_value.reader, "read_source_binding", forbidden)
    monkeypatch.setattr(observed.first_value, "_recover_in_transaction", forbidden)
    assert observed.first_value.selection_status().test_available
    assert observed.first_value.source_verification() is not None
    assert observed.first_value.first_answer_verification() is None


async def test_close_withdraws_before_retained_refresh_drains(observed, monkeypatch):
    initialize(observed)
    entered, release = asyncio.Event(), asyncio.Event()
    async def held(*args):
        entered.set()
        await release.wait()
        return None
    monkeypatch.setattr(observed.first_value.reader, "read_source_binding", held)
    refresh = asyncio.create_task(observed.first_value.refresh_observation())
    await entered.wait()
    close = asyncio.create_task(observed.first_value.close())
    await asyncio.sleep(0)
    close.cancel()
    await asyncio.sleep(0)
    assert not close.done() and observed.first_value._refresh_tasks
    assert OnboardingStage.SOURCE_SELECTED not in observed.coordinator._verifiers
    release.set()
    await asyncio.gather(refresh, close, return_exceptions=True)
    assert not observed.first_value._refresh_tasks


async def completed_receipt(env):
    """A durable synthetic settlement fixture, never live provider/SDK evidence."""
    from tg_assistant.ai.budget import PricingSnapshot
    from tg_assistant.ai.observations import CitedReference, RetrievalScope
    from tg_assistant.ai.rag import SourceEvidence, source_context
    from tg_assistant.services.first_value import _Receipt, _RequestIdentity
    initialize(env)
    row = env.add_indexed(10)
    learned(env)
    await env.first_value.refresh_observation()
    owner, binding = env.first_value, env.first_value.current_binding
    assert binding is not None
    bot = owner._bot
    bot.repository = SimpleNamespace(engine=env.engine, _load=lambda connection: ({
        "enrollment": {"generation": "a" * 32, "bot_id": "456"},
        "pairing": {"owner_id": str(OWNER), "bot_id": "456", "enrollment_generation": "a" * 32}}, True))
    bot.service.repository = bot.repository
    request = _RequestIdentity(bot_id=456, enrollment_generation="a" * 32, owner_id=OWNER, incoming_message_id=123)
    stamp = datetime.now(UTC)
    with owner._transaction() as connection:
        attempt, _ = owner._accept_in_transaction(connection, request=request, binding=binding, accepted_at=stamp)
        attempt = owner._transition_in_transaction(connection, attempt=attempt, phase="generating")
        attempt = owner._transition_in_transaction(connection, attempt=attempt, phase="delivering", chunk_count=1)
        attempt = owner._submit_in_transaction(connection, attempt=attempt, ordinal=1)
        attempt = owner._returned_in_transaction(connection, attempt=attempt, ordinal=1, message_id=999)
        message = connection.execute(select(TelegramMessage.__table__).where(TelegramMessage.id == row.row_id)).one()
    context = source_context(3, SourceEvidence(CHAT, 10, 1.0, message.text, message.sent_at, "A", f"https://t.me/c/{str(CHAT)[4:]}/10"))
    reference = CitedReference(CHAT, 10, row.row_id, row.raw_hash, hashlib.sha256(context.encode()).hexdigest(), True, True)
    scope = RetrievalScope(CHAT, stamp - timedelta(days=7), stamp, None, None, None, "local_rag", "cloud_embedding")
    checked = await owner.reader.check_used_references(binding, scope, (reference,))
    assert checked is not None
    candidate = binding.chat_candidates[0]
    rates = PricingSnapshot("ollama", candidate.requested_model, "local-zero-v1", "local", *(Decimal(0) for _ in range(4)), datetime(9999, 1, 1, tzinfo=UTC))
    settled = datetime.now(UTC)
    with owner._transaction() as connection:
        for request_id, model, operation, feature, route in (
            ("answer-ledger", candidate.requested_model, "answer", "normal_ask", "local_rag"),
            ("query-ledger", binding.embedding_model, "embedding", "embedding", "cloud_embedding")):
            pricing = {**rates.as_dict(), "model": model}
            connection.execute(insert(AiBudgetReservation).values(request_id=request_id,
                profile_id=binding.profile_id, provider="ollama", model=model, operation=operation,
                feature=feature, route=route, chat_id=CHAT, occurred_at=stamp, submitted_at=stamp,
                settled_at=settled if operation == "answer" else stamp,
                pricing_version="local-zero-v1", pricing_rates=pricing, reserved_input_tokens=10, reserved_output_tokens=10,
                reserved_cost_usd=0, actual_input_tokens=10, actual_output_tokens=1 if operation == "answer" else 0,
                cached_tokens=0, cache_write_tokens=0, actual_cost_usd=0, is_local=True, fallback_used=False, state="settled"))
        receipt = _Receipt(schema_version=1, namespace=owner._namespace, revision=0,
            request_digest=attempt.request_digest, attempt_revision=attempt.revision + 1,
            completed_at=datetime.now(UTC), configuration_fingerprint=owner.configuration_fingerprint,
            binding=asdict(binding), execution=dict(request_id="answer-ledger", profile_id=binding.profile_id,
                provider="ollama", endpoint_id=candidate.endpoint_id, requested_model=candidate.requested_model,
                reported_model=None, capability_fingerprint="execution-v1:fixture", route="local_rag", fallback_used=False,
                pricing_version="local-zero-v1", settled_at=settled, input_tokens=10, output_tokens=1, cached_tokens=0,
                cache_write_tokens=0, cost_usd="0"), query_embedding_request_id="query-ledger",
            cited_refs=(asdict(reference),), retrieval_mode="hybrid", scope=asdict(scope), message_ids=(999,))
        owner._complete_in_transaction(connection, attempt=attempt, receipt=receipt,
            authorize=lambda conn: owner.authorize_current_in_transaction(conn, binding, checked))
    return receipt, row


async def test_completed_receipt_requires_fresh_point_and_reference_proof(observed):
    receipt, row = await completed_receipt(observed)
    assert observed.first_value.first_answer_verification() is None
    await observed.first_value.refresh_observation()
    assert observed.first_value.first_answer_verification() is not None
    observed.update(TelegramMessage, TelegramMessage.id == row.row_id, text="changed")
    assert observed.first_value.first_answer_verification() is None
    assert observed.first_value.history().receipts == (receipt,)


@pytest.mark.parametrize("mutation", ["ledger", "title", "route", "retention", "vector", "consent", "epoch"])
async def test_restart_history_does_not_override_current_facts(observed, mutation):
    receipt, row = await completed_receipt(observed)
    await observed.first_value.refresh_observation()
    assert observed.first_value.first_answer_verification() is not None
    if mutation == "ledger":
        observed.update(AiBudgetReservation, AiBudgetReservation.request_id == "query-ledger", state="uncertain")
    elif mutation == "title":
        observed.update(TelegramChat, TelegramChat.chat_id == CHAT, title="changed")
    elif mutation == "route":
        observed.update(TelegramChatPolicy, TelegramChatPolicy.chat_id == CHAT, ai_mode="off")
    elif mutation == "retention":
        observed.update(TelegramMessage, TelegramMessage.id == row.row_id, sent_at=datetime.now(UTC) - timedelta(days=999))
    elif mutation == "vector":
        observed.add_indexed(11)
    elif mutation == "consent":
        observed.runtime.settings.cloud_consent = not observed.runtime.settings.cloud_consent
    else:
        observed.update(TelegramChatPolicy, TelegramChatPolicy.chat_id == CHAT, authorization_epoch=2)
    assert observed.first_value.first_answer_verification() is None
    assert observed.first_value.history().receipts == (receipt,)


async def test_new_owner_reissues_proof_and_cannot_reuse_old_capture(observed):
    from test_first_value_index import new_vectors

    from tg_assistant.services.first_value import FirstValueService
    env = observed
    receipt, _ = await completed_receipt(env)
    await env.first_value.refresh_observation()
    previous = env.first_value.current_binding
    await env.first_value.close()
    old_vectors = env.vectors
    old_vectors.close()
    env.closed_vectors.append(old_vectors)
    env.vectors = new_vectors(env.settings, env.path)
    env.runtime.rag.vectors = env.vectors
    env.first_value = FirstValueService(runtime=env.runtime, coordinator=env.coordinator, windows_sid=env.sid)
    env.runtime.first_value = env.first_value
    initialize(env)
    assert env.first_value.first_answer_verification() is None
    await env.first_value.refresh_observation()
    current = env.first_value.current_binding
    assert current is not None and current is not previous
    assert current.vector_owner_fingerprint != previous.vector_owner_fingerprint
    assert env.first_value.first_answer_verification() is not None
    assert env.first_value.history().receipts == (receipt,)


async def test_proof_expires_until_explicit_refresh_and_requires_exact_engine(observed):
    from sqlalchemy import create_engine
    await completed_receipt(observed)
    owner = observed.first_value
    await owner.refresh_observation()
    assert owner.first_answer_verification() is not None
    for entry in owner.reader._checked.values():
        entry.mono -= 6
        entry.utc -= timedelta(seconds=6)
    assert owner.first_answer_verification() is None
    for entry in owner.reader._bound.values():
        entry.capture.mono -= 31
        entry.capture.utc -= timedelta(seconds=31)
    assert not owner.selection_status().test_available
    await owner.refresh_observation()
    assert owner.first_answer_verification() is not None
    other = create_engine(observed.engine.url)
    try:
        with other.connect() as connection:
            assert not owner.authorize_current_in_transaction(connection, owner.current_binding)
    finally:
        other.dispose()


@pytest.mark.parametrize("change", ["unrelated", "paused", "quota", "epoch", "action", "owner", "progress_none", "progress_zero"])
async def test_guided_learning_projection_keeps_unknown_progress_and_original_correlation(observed, change):
    initialize(observed)
    observed.add_indexed(10)
    job_id = learned(observed)
    with observed.engine.begin() as connection:
        payload = connection.execute(select(BackgroundJob.payload).where(BackgroundJob.id == job_id)).scalar_one()
        if change == "unrelated":
            connection.execute(insert(BackgroundJob).values(id="dddddddd-dddd-dddd-dddd-dddddddddddd", job_type="learn_group", status="completed", payload=payload))
            connection.execute(BackgroundJob.__table__.delete().where(BackgroundJob.id == job_id))
        elif change == "paused":
            connection.execute(BackgroundJob.__table__.update().where(BackgroundJob.id == job_id).values(status="paused"))
        else:
            payload.update({
                "quota": {"history_stop_reason": "quota"}, "epoch": {"authorization_epoch": 2},
                "action": {"action_id": "fv1-" + "d" * 32}, "owner": {"owner_id": OWNER + 1},
                "progress_none": {"progress": None}, "progress_zero": {"progress": 0},
            }[change])
            connection.execute(BackgroundJob.__table__.update().where(BackgroundJob.id == job_id).values(payload=payload))
    await observed.first_value.refresh_observation()
    status = observed.first_value.selection_status()
    assert status.test_available is change.startswith("progress")
    if change.startswith("progress"):
        assert status.learning_operation.progress == (0 if change == "progress_zero" else None)


async def test_setup_context_advances_through_source_to_limited_skip_ready(observed):
    from tg_assistant.desktop.setup_context import SetupContext
    from tg_assistant.services.onboarding import StageVerification
    env = observed
    env.coordinator.save_options({"ai_mode": "skip"})
    for stage in list(OnboardingStage)[:6]:
        env.coordinator._verifiers[stage] = lambda: StageVerification(owner_id=OWNER, fingerprint="f" * 64)
    for stage in (OnboardingStage.WELCOME, OnboardingStage.STORAGE_READY):
        env.coordinator.complete_stage(stage, env.coordinator.verify_stage(stage))
    initialize(env)
    context = SetupContext(env.coordinator, env.engine, env.fence, None)
    status = context.advance_verified()
    assert status.profile.setup_stage is OnboardingStage.READY
    assert OnboardingStage.SOURCE_SELECTED in status.stage_evidence_ids
    assert OnboardingStage.FIRST_ANSWER not in status.stage_evidence_ids
    assert "first_answer" in status.disabled_capabilities


@pytest.mark.parametrize("change", ["model", "chat_model", "dimension", "endpoint", "saved_settings"])
async def test_wrong_current_embedding_or_unapplied_settings_cannot_offer_test(observed, change):
    initialize(observed)
    observed.add_indexed(10)
    learned(observed)
    await observed.first_value.refresh_observation()
    assert observed.first_value.selection_status().test_available
    if change == "model":
        observed.runtime.embedding_ai.embedding_model = "changed"
    elif change == "chat_model":
        observed.runtime.ai.model = "changed"
    elif change == "dimension":
        observed.runtime.embedding_ai.embedding_dimension += 1
    elif change == "endpoint":
        observed.runtime.embedding_ai.client.base_url = "http://127.0.0.1:11435/v1"
    else:
        save_settings(observed.runtime.settings.model_copy(update={"cloud_consent": True}))
    await observed.first_value.refresh_observation()
    assert not observed.first_value.selection_status().test_available


async def test_restart_rejects_incoherent_retrieval_participants(observed):
    from tg_assistant.db.models import AppSetting
    from tg_assistant.services.first_value import _row_key
    receipt, _ = await completed_receipt(observed)
    value = receipt.model_copy(update={"retrieval_mode": "keyword"}).model_dump(mode="json")
    observed.update(AppSetting, AppSetting.key == _row_key(receipt.namespace, "receipt", receipt.request_digest), value=value)
    await observed.first_value.refresh_observation()
    assert observed.first_value.first_answer_verification() is None


async def test_initialize_recovers_once_and_never_demotes_active_owner_work(observed):
    from tg_assistant.services.first_value import FirstValueService, _RequestIdentity
    await completed_receipt(observed)
    owner = observed.first_value
    binding = owner.current_binding
    request = _RequestIdentity(bot_id=456, enrollment_generation="a" * 32, owner_id=OWNER, incoming_message_id=124)
    with owner._transaction() as connection:
        attempt, _ = owner._accept_in_transaction(connection, request=request, binding=binding, accepted_at=datetime.now(UTC))
    owner.initialize()
    await owner.refresh_observation()
    assert owner.current_binding is binding
    assert not owner.selection_status().test_available
    assert attempt in owner.history().attempts
    await owner.close()
    observed.first_value = FirstValueService(runtime=observed.runtime, coordinator=observed.coordinator, windows_sid=observed.sid)
    observed.runtime.first_value = observed.first_value
    initialize(observed)
    recovered = next(row for row in observed.first_value.history().attempts if row.request_digest == attempt.request_digest)
    assert recovered.phase == "cancelled" and recovered.terminal_code == "interrupted"


async def test_answer_projection_does_not_apply_an_older_receipts_proof_to_newer_history(observed):
    from tg_assistant.db.models import AppSetting
    from tg_assistant.services.first_value import _request_digest, _row_key
    receipt, _ = await completed_receipt(observed)
    owner = observed.first_value
    await owner.refresh_observation()
    attempt = owner.history().attempts[0]
    digest = _request_digest(owner._namespace, bot_id=attempt.bot_id,
        enrollment_generation=attempt.enrollment_generation, owner_id=attempt.owner_id, incoming_message_id=124)
    later = attempt.model_copy(update={"request_digest": digest, "incoming_message_id": 124,
        "accepted_at": attempt.accepted_at + timedelta(microseconds=1),
        "expires_at": attempt.expires_at + timedelta(microseconds=1)})
    historical = receipt.model_copy(update={"request_digest": digest, "configuration_fingerprint": "e" * 64,
        "completed_at": receipt.completed_at + timedelta(microseconds=1)})
    with observed.engine.begin() as connection:
        for kind, item in (("attempt", later), ("receipt", historical)):
            connection.execute(insert(AppSetting).values(key=_row_key(owner._namespace, kind, digest), value=item.model_dump(mode="json")))
    assert owner.first_answer_verification() is not None
    operation = owner.selection_status().answer_operation
    assert operation.operation_id == digest
    assert operation.state == "completed_with_warning" and operation.code == "answer_requires_recheck"
