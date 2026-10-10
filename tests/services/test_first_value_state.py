"""Durable first-value history on disposable, migrated SQLite; no live effects."""
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text

from tg_assistant.ai.observations import ChatModelCandidate, SourceIndexBinding
from tg_assistant.db.base import configure_sqlite
from tg_assistant.db.migrations import upgrade_database
from tg_assistant.services.connections import ConnectionHealth
from tg_assistant.services.first_value import FirstValueService
from tg_assistant.services.maintenance import MaintenanceService
from tg_assistant.services.onboarding import OnboardingCoordinator

CHAT = -100123


@pytest.fixture
def state(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "state.db"))
    event.listen(engine, "connect", configure_sqlite)
    with engine.connect() as connection:
        upgrade_database(connection, script_location=Path(__file__).resolve().parents[2] / "alembic")
    fence = MaintenanceService(tmp_path / "config", profile_id="owner-profile")
    coordinator = OnboardingCoordinator(engine=engine, profile_id="owner-profile",
        storage_backend="sqlite", fence=fence, connections=ConnectionHealth(probes={}), verifiers={})
    admitted = [True]
    runtime = SimpleNamespace(settings=SimpleNamespace(profile_id="owner-profile"),
        management_admitted=lambda: admitted[0], first_value=None)
    runtime.bot_runtime = SimpleNamespace(engine=engine, fence=fence, _runtime=runtime,
        service=SimpleNamespace(verified_identity=SimpleNamespace(username="Verified_bot")))
    owner = FirstValueService(runtime=runtime, coordinator=coordinator, windows_sid="S-1-5-21-test")
    runtime.first_value = owner
    owner._select_source(CHAT)
    yield SimpleNamespace(owner=owner, engine=engine, admitted=admitted)
    engine.dispose()
    fence.close()


def raw_header(state):
    with state.engine.connect() as connection:
        return connection.execute(text("SELECT value FROM app_settings WHERE key=:key"),
                                  {"key": state.owner._key}).scalar_one()


@pytest.mark.parametrize("corrupt", ["duplicate", "overflow", "descendant"])
def test_namespace_rejects_corruption_before_selection_write(state, corrupt):
    original = raw_header(state)
    with state.engine.begin() as connection:
        if corrupt == "descendant":
            connection.execute(text("INSERT INTO app_settings(key,value) VALUES (:key,'{}')"),
                               {"key": state.owner._key + ".unknown"})
        else:
            value = original[:-1] + ', "revision": 1}' if corrupt == "duplicate" else original.replace('"revision": 1', '"revision": 9223372036854775808')
            connection.execute(text("UPDATE app_settings SET value=:value WHERE key=:key"),
                               {"key": state.owner._key, "value": value})
    with pytest.raises(ValueError, match="^first_value_state_invalid$"):
        state.owner._select_source(-100456)


def test_oversize_value_is_never_projected_into_python(state):
    with state.engine.begin() as connection:
        connection.execute(text("UPDATE app_settings SET value=:value WHERE key=:key"),
                           {"key": state.owner._key, "value": '"' + "x" * 9000 + '"'})
    statements = []
    def trace(_connection, _cursor, statement, *_):
        statements.append(statement.lower())
    event.listen(state.engine, "before_cursor_execute", trace)
    try:
        with pytest.raises(ValueError, match="^first_value_state_invalid$"):
            state.owner.selection_status()
    finally:
        event.remove(state.engine, "before_cursor_execute", trace)
    assert not any("select cast(app_settings.value as varchar)" in sql for sql in statements), "Oversize value was selected before its SQL byte bound"


def request_and_binding(state, message_id=1, generation="a" * 32):
    from tg_assistant.services.first_value import _RequestIdentity
    owner = state.owner
    with state.engine.connect() as connection:
        header = owner._read_header(connection)
    pair = SimpleNamespace(owner_id=123, fingerprint="b" * 64)
    owner.runtime.user = SimpleNamespace(owner_id=123)
    owner._bot.service.verified_identity.bot_id = 456
    owner._bot.pairing_verification = lambda: pair
    enrollment = {"generation": generation, "bot_id": "456"}
    owner._bot.repository = SimpleNamespace(engine=state.engine,
        _load=lambda connection: ({"enrollment": enrollment, "pairing": {
            "owner_id": "123", "bot_id": "456", "enrollment_generation": generation}}, True))
    owner._bot.service.repository = owner._bot.repository
    request = _RequestIdentity(bot_id=456, enrollment_generation=generation, owner_id=123,
                               incoming_message_id=message_id)
    binding = SourceIndexBinding("owner-profile", 123, "b" * 64, header.selection_generation,
        header.restore_epoch, CHAT, 1, "policy-v1", "ollama", "local", "embed", "1", 3,
        "store", 1, "vector-v1", "retrieval-v1", (ChatModelCandidate("ollama", "local", "model", "candidate-v1"),))
    return request, binding


def accept(state, message_id=1, generation="a" * 32):
    assert hasattr(state.owner, "_transaction"), "Owned first-value writer transaction is missing"
    request, binding = request_and_binding(state, message_id, generation)
    with state.owner._transaction() as connection:
        return state.owner._accept_in_transaction(connection, request=request, binding=binding,
                                                  accepted_at=datetime.now(UTC))


def change(state, attempt, phase, **kwargs):
    with state.owner._transaction() as connection:
        return state.owner._transition_in_transaction(connection, attempt=attempt, phase=phase, **kwargs)


def test_concurrent_duplicate_then_cross_enrollment_is_same_tombstone(state):
    assert hasattr(state.owner, "_transaction"), "Owned first-value writer transaction is missing"
    request, binding = request_and_binding(state)
    def run(_):
        with state.owner._transaction() as connection:
            return state.owner._accept_in_transaction(connection, request=request, binding=binding,
                                                       accepted_at=datetime.now(UTC))
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(run, range(2)))
    assert sorted(created for _, created in outcomes) == [False, True]
    attempt = outcomes[0][0]
    with pytest.raises(ValueError, match="first_value_busy"):
        accept(state, 2)
    cancelled = change(state, attempt, "cancelled", terminal_code="cancelled_before_effect")
    historical, created = accept(state, generation="c" * 32)
    assert not created and historical == cancelled
    from tg_assistant.services.first_value import _row_key
    assert len(_row_key(attempt.namespace, "attempt", attempt.request_digest)) == 120


def test_cas_send_gap_and_restart_preserve_uncertainty(state):
    attempt, _ = accept(state)
    generating = change(state, attempt, "generating")
    with pytest.raises(ValueError, match="first_value_write_conflict"):
        change(state, attempt, "generating")
    delivering = change(state, generating, "delivering", chunk_count=2)
    with state.owner._transaction() as connection:
        with pytest.raises(ValueError):
            state.owner._submit_in_transaction(connection, attempt=delivering, ordinal=2)
        submitted = state.owner._submit_in_transaction(connection, attempt=delivering, ordinal=1)
    with state.owner._transaction() as connection:
        returned = state.owner._returned_in_transaction(connection, attempt=submitted, ordinal=1, message_id=900)
    with state.owner._transaction() as connection:
        submitted = state.owner._submit_in_transaction(connection, attempt=returned, ordinal=2)
    state.admitted[0] = False
    with pytest.raises(PermissionError):
        change(state, submitted, "uncertain", terminal_code="delivery_uncertain")
    state.admitted[0] = True
    assert state.owner.history().attempts[0].phase == "delivering", "GET must not perform startup recovery"
    with state.owner._transaction() as connection:
        state.owner._recover_in_transaction(connection)
    recovered = state.owner.history().attempts[0]
    assert (recovered.phase, recovered.submitted_ordinal, recovered.returned_message_ids) == ("uncertain", 2, (900,))
    with pytest.raises(ValueError):
        change(state, recovered, "generating")
    assert accept(state)[1] is False


def test_restore_demotes_accepted_and_preserves_tombstone(state):
    attempt, _ = accept(state)
    from tg_assistant.services.first_value import invalidate_first_value_after_restore
    with state.engine.begin() as connection:
        invalidate_first_value_after_restore(connection, profile_id="owner-profile", windows_sid="S-1-5-21-test")
    current = state.owner.history()
    assert current.header.restore_epoch != attempt.restore_epoch
    assert current.header.selection_generation == attempt.selection_generation + 1
    assert current.attempts[0].phase == "cancelled"
    assert current.attempts[0].request_digest == attempt.request_digest
    assert accept(state)[1] is False


def receipt_for(attempt, binding):
    from tg_assistant.services.first_value import _Receipt
    now = datetime.now(UTC)
    return _Receipt(schema_version=1, namespace=attempt.namespace, revision=0,
        request_digest=attempt.request_digest, attempt_revision=attempt.revision + 1,
        completed_at=now, configuration_fingerprint="d" * 64, binding=asdict(binding),
        execution=dict(request_id="request-1", profile_id="owner-profile", provider="ollama",
            endpoint_id="local", requested_model="model", reported_model=None, capability_fingerprint="capability-v1",
            route="local_rag", fallback_used=False, pricing_version="local", settled_at=now,
            input_tokens=1, output_tokens=1, cached_tokens=0, cache_write_tokens=0, cost_usd="0"),
        query_embedding_request_id="embedding-1", cited_refs=[dict(chat_id=CHAT, message_id=20,
            reference_id=30, content_hash="e" * 64, context_hash="f" * 64, semantic_used=True, keyword_used=False)],
        retrieval_mode="semantic", scope=dict(selected_chat_id=CHAT, after=None, before=now, sender_id=None,
            has=None, content_type=None, query_route="local_rag", embedding_route="local_embedding"),
        message_ids=attempt.returned_message_ids)


def delivered(state):
    attempt, _ = accept(state)
    _, binding = request_and_binding(state)
    attempt = change(state, attempt, "generating")
    attempt = change(state, attempt, "delivering", chunk_count=1)
    with state.owner._transaction() as connection:
        attempt = state.owner._submit_in_transaction(connection, attempt=attempt, ordinal=1)
    with state.owner._transaction() as connection:
        attempt = state.owner._returned_in_transaction(connection, attempt=attempt, ordinal=1, message_id=901)
    return attempt, receipt_for(attempt, binding)


def test_receipt_requires_final_callback_and_rolls_back_with_caller(state):
    attempt, receipt = delivered(state)
    with pytest.raises(ValueError):
        with state.owner._transaction() as connection:
            state.owner._complete_in_transaction(connection, attempt=attempt, receipt=receipt, authorize=lambda c: False)
    with pytest.raises(RuntimeError, match="caller rollback"):
        with state.owner._transaction() as connection:
            def authorize(actual):
                assert actual is connection
                return True
            state.owner._complete_in_transaction(connection, attempt=attempt, receipt=receipt, authorize=authorize)
            raise RuntimeError("caller rollback")
    assert state.owner.history().receipts == ()
    assert state.owner.history().attempts[0] == attempt
    with state.owner._transaction() as connection:
        state.owner._complete_in_transaction(connection, attempt=attempt, receipt=receipt, authorize=lambda c: True)
    history = state.owner.history()
    assert history.attempts[0].phase == "completed" and history.receipts == (receipt,)
    with pytest.raises(ValueError):
        with state.owner._transaction() as connection:
            state.owner._complete_in_transaction(connection, attempt=attempt, receipt=receipt, authorize=lambda c: True)


def test_nested_sqlite_budget_restores_outer_and_clears(state):
    from sqlalchemy.exc import OperationalError

    from tg_assistant.services.first_value_index import _bounded
    query = text("WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM cnt WHERE x<100000) SELECT sum(x) FROM cnt")
    with state.engine.connect() as connection:
        with _bounded(connection, 0):
            outer = connection.info["first_value_sqlite_budget"]
            with _bounded(connection, 20):
                assert connection.info["first_value_sqlite_budget"] != outer
                with pytest.raises(OperationalError, match="interrupted"):
                    connection.execute(query)
            assert connection.info["first_value_sqlite_budget"] == outer
            with pytest.raises(OperationalError, match="interrupted"):
                connection.execute(query)
        assert "first_value_sqlite_budget" not in connection.info
        assert connection.execute(query).scalar_one() == 5000050000


@pytest.mark.parametrize("value", [True, 1.0, "1", -1, 2**63])
def test_strict_storage_counter_rejects_without_rewriting(state, value):
    document = json.loads(raw_header(state))
    document["revision"] = value
    encoded = json.dumps(document)
    with state.engine.begin() as connection:
        connection.execute(text("UPDATE app_settings SET value=:value WHERE key=:key"),
                           {"key": state.owner._key, "value": encoded})
    with pytest.raises(ValueError, match="^first_value_state_invalid$"):
        state.owner.history()
    assert raw_header(state) == encoded


def seed_completed_history(state, count, *, row_size=None):
    from tg_assistant.services.first_value import _request_digest, _row_key
    attempt, receipt = delivered(state)
    with state.owner._transaction() as connection:
        completed = state.owner._complete_in_transaction(connection, attempt=attempt, receipt=receipt, authorize=lambda c: True)
    with state.engine.begin() as connection:
        connection.execute(text("DELETE FROM app_settings WHERE key!=:header AND key LIKE :prefix"),
                           {"header": state.owner._key, "prefix": state.owner._key + "%"})
        for i in range(1, count + 1):
            digest = _request_digest(attempt.namespace, bot_id=456, owner_id=123,
                                     enrollment_generation="a" * 32, incoming_message_id=i)
            a = completed.model_copy(update={"incoming_message_id": i, "request_digest": digest})
            r = receipt.model_copy(update={"request_digest": digest})
            for kind, record in (("attempt", a), ("receipt", r)):
                value = json.dumps(record.model_dump(mode="json"))
                if row_size:
                    value += " " * (row_size - len(value.encode()))
                connection.execute(text("INSERT INTO app_settings(key,value) VALUES(:key,:value)"),
                    {"key": _row_key(attempt.namespace, kind, digest), "value": value})


def test_full_history_still_returns_duplicate_but_refuses_new_request(state):
    seed_completed_history(state, 128)
    assert accept(state)[1] is False
    with pytest.raises(ValueError, match="^history_full$"):
        accept(state, 129)
    assert len(state.owner.history().attempts) == 128


def test_capacity_reserves_maximal_attempt_and_receipt_including_header(state):
    seed_completed_history(state, 127, row_size=32768)
    with pytest.raises(ValueError, match="^history_full$"):
        accept(state, 128)
    header_size = len(raw_header(state).encode())
    with state.engine.begin() as connection:
        key, raw = connection.execute(text("SELECT key,value FROM app_settings WHERE key LIKE :prefix LIMIT 1"),
            {"prefix": state.owner._key + ".receipt.%"}).one()
        connection.execute(text("UPDATE app_settings SET value=:value WHERE key=:key"),
                           {"key": key, "value": raw[:-header_size]})
    attempt, created = accept(state, 128)
    assert created and attempt.phase == "accepted"
    # A metadata revision gaining one byte must preserve the pending 64 KiB reservation.
    with state.engine.begin() as connection:
        raw = raw_header(state)
        padded = raw + " "
        connection.execute(text("UPDATE app_settings SET value=:value WHERE key=:key"),
                           {"key": state.owner._key, "value": padded})
    with state.owner._transaction() as connection:
        from tg_assistant.services.first_value import _capacity, _read_namespace
        history = _read_namespace(connection, namespace=state.owner._namespace)
        with pytest.raises(ValueError, match="^history_full$"):
            _capacity(connection, history, {})


@pytest.mark.parametrize("kind", ["oversize", "unknown", "excess", "numeric_id", "orphan"])
def test_hostile_history_is_rejected(state, kind):
    from tg_assistant.services.first_value import _row_key
    attempt, _ = accept(state)
    with state.engine.begin() as connection:
        key = _row_key(attempt.namespace, "attempt", attempt.request_digest)
        if kind == "oversize":
            connection.execute(text("UPDATE app_settings SET value=:value WHERE key=:key"),
                               {"key": key, "value": " " * 32769})
        elif kind == "unknown":
            connection.execute(text("UPDATE app_settings SET key=:new WHERE key=:key"),
                               {"key": key, "new": key[:-1] + "!"})
        elif kind == "excess":
            for i in range(258):
                connection.execute(text("INSERT INTO app_settings(key,value) VALUES(:key,'{}')"),
                                   {"key": state.owner._key + ".hostile" + str(i)})
        elif kind == "numeric_id":
            raw = attempt.model_dump(mode="json")
            raw["owner_id"] = 123
            connection.execute(text("UPDATE app_settings SET value=:value WHERE key=:key"),
                               {"key": key, "value": json.dumps(raw)})
        else:
            connection.execute(text("DELETE FROM app_settings WHERE key=:key"), {"key": state.owner._key})
    with pytest.raises(ValueError, match="^first_value_state_invalid$"):
        state.owner.history()


def test_receipt_bytes_immutable_through_restore_and_reconstruction(state):
    from tg_assistant.services.first_value import invalidate_first_value_after_restore
    attempt, receipt = delivered(state)
    with state.owner._transaction() as connection:
        state.owner._complete_in_transaction(connection, attempt=attempt, receipt=receipt, authorize=lambda c: True)
    with state.engine.connect() as connection:
        raw = connection.execute(text("SELECT value FROM app_settings WHERE key LIKE :prefix"),
                                 {"prefix": state.owner._key + ".receipt.%"}).scalar_one()
    with state.engine.begin() as connection:
        invalidate_first_value_after_restore(connection, profile_id="owner-profile", windows_sid="S-1-5-21-test")
    with state.engine.connect() as connection:
        assert connection.execute(text("SELECT value FROM app_settings WHERE key LIKE :prefix"),
                                  {"prefix": state.owner._key + ".receipt.%"}).scalar_one() == raw
    restored = FirstValueService(runtime=state.owner.runtime, coordinator=state.owner.coordinator,
                                windows_sid="S-1-5-21-test")
    state.owner.runtime.first_value = restored
    assert restored.history().receipts == (receipt,)
    assert restored.history().header.restore_epoch != receipt.binding.restore_epoch


async def test_async_budget_restores_outer_and_clears(state):
    from sqlalchemy.exc import OperationalError

    from tg_assistant.db.base import Database
    from tg_assistant.services.first_value_index import _bounded
    database = Database("sqlite+aiosqlite:///" + str(state.engine.url.database))
    query = text("WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM cnt WHERE x<100000) SELECT sum(x) FROM cnt")
    try:
        async with database.session() as session:
            def check(sync):
                connection = sync.connection()
                with _bounded(connection, 0):
                    prior = connection.info["first_value_sqlite_budget"]
                    with _bounded(connection, 20):
                        with pytest.raises(OperationalError, match="interrupted"):
                            connection.execute(query)
                    assert connection.info["first_value_sqlite_budget"] == prior
                    with pytest.raises(OperationalError, match="interrupted"):
                        connection.execute(query)
                assert "first_value_sqlite_budget" not in connection.info
                assert connection.execute(query).scalar_one() == 5000050000
            await session.run_sync(check)
    finally:
        await database.close()


@pytest.fixture
async def learning(state):
    from tg_assistant.ai.engine import AiEngine
    from tg_assistant.ai.router import AiRouter
    from tg_assistant.config import Settings
    from tg_assistant.db.base import Database
    from tg_assistant.db.models import TelegramChat
    from tg_assistant.policy import PolicyEngine
    runtime = state.owner.runtime
    runtime.settings = Settings(_env_file=None, data_dir=Path(state.engine.url.database).parent,
        profile_id="owner-profile", ai_provider="ollama", embedding_provider="ollama")
    runtime.database = Database("sqlite+aiosqlite:///" + str(state.engine.url.database))
    runtime.database.fence = state.owner._fence
    runtime.database.management_admission = runtime.management_admitted
    runtime.policy = PolicyEngine()
    runtime.user = SimpleNamespace(owner_id=123)
    runtime.ai = AiEngine(api_key=None, model="qwen3:8b", embedding_model="nomic-embed-text:latest",
        provider="ollama", base_url="http://127.0.0.1:11434/v1", budget=None, max_output_tokens=1000)
    runtime.embedding_ai = runtime.ai
    runtime.ai_router = AiRouter({"ollama": runtime.ai}, default_provider="ollama")
    runtime.bot_runtime.pairing_verification = lambda: SimpleNamespace(owner_id=123, fingerprint="b" * 64)
    async with runtime.database.session() as session:
        session.add(TelegramChat(chat_id=CHAT, chat_type="supergroup", title="Synthetic"))
        await runtime.policy.set_allowed(session, CHAT, True)
    async with runtime.database.session() as session:
        action = await state.owner.preview.create(session, CHAT, 1000)
    yield state, action.action_id
    await runtime.ai.close()
    await runtime.database.close()


@pytest.mark.parametrize("rollback", [False, True])
async def test_learning_association_uses_exact_action_transaction(learning, rollback):
    from tg_assistant.db.models import BackgroundJob, PendingAction
    from tg_assistant.services.revocation import source_epoch
    state, action_id = learning
    owner = state.owner
    before = owner.history().header
    assert hasattr(owner, "associate_learning"), "Exact action transaction association is missing"
    class Rollback(Exception):
        pass
    try:
        async with owner.runtime.database.session() as session:
            action = await session.get(PendingAction, action_id)
            action.status = "executing"
            await session.flush()
            await owner.preview.validate(session, action)
            await owner.runtime.policy.apply_template(session, CHAT, "knowledge")
            epoch = await source_epoch(session, CHAT)
            job = BackgroundJob(job_type="learn_group", status="queued", payload=dict(action_id=action_id,
                chat_id=CHAT, owner_id=123, authorization_epoch=epoch, limit=1000))
            session.add(job)
            await session.flush()
            await owner.associate_learning(session, action=action, job=job, source_epoch=epoch)
            assert session.sync_session.info["first_source_validated_actions"][action_id] is not None
            if rollback:
                raise Rollback
        assert "first_source_validated_actions" not in session.sync_session.info
    except Rollback:
        assert "first_source_validated_actions" not in session.sync_session.info
    after = owner.history().header
    if rollback:
        assert after == before
        async with owner.runtime.database.session() as session:
            assert await session.get(BackgroundJob, job.id) is None
    else:
        assert after.learning.job_id == job.id and after.learning.action_id == action_id
        assert after.learning.source_epoch == epoch
        owner._select_source(CHAT)
        assert owner.history().header.learning == after.learning
        owner._select_source(-100456)
        assert owner.history().header.learning is None


async def test_issued_preview_without_current_transaction_validation_cannot_associate(learning):
    from tg_assistant.db.models import BackgroundJob, PendingAction
    from tg_assistant.services.revocation import source_epoch
    state, action_id = learning
    owner = state.owner
    assert hasattr(owner, "associate_learning"), "Exact action transaction association is missing"
    async with owner.runtime.database.session() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        action = await session.get(PendingAction, action_id)
        action.status = "executing"
        epoch = await source_epoch(session, CHAT)
        job = BackgroundJob(job_type="learn_group", status="queued", payload=dict(action_id=action_id,
            chat_id=CHAT, owner_id=123, authorization_epoch=epoch, limit=1000))
        session.add(job)
        await session.flush()
        with pytest.raises(ValueError, match="^first_source_preview_stale$"):
            await owner.associate_learning(session, action=action, job=job, source_epoch=epoch)
        await session.rollback()


def test_raw_timestamp_must_be_canonical_utc_text(state):
    attempt, _ = accept(state)
    from tg_assistant.services.first_value import _row_key
    raw = attempt.model_dump(mode="json")
    raw["accepted_at"] = attempt.accepted_at.timestamp()
    raw["expires_at"] = attempt.expires_at.timestamp()
    with state.engine.begin() as connection:
        connection.execute(text("UPDATE app_settings SET value=:value WHERE key=:key"),
            {"key": _row_key(attempt.namespace, "attempt", attempt.request_digest), "value": json.dumps(raw)})
    with pytest.raises(ValueError, match="^first_value_state_invalid$"):
        state.owner.history()


def test_low_level_cas_cannot_skip_effect_phases_or_edit_deadline(state):
    from tg_assistant.services.first_value import _cas_attempt
    attempt, _ = accept(state)
    with state.owner._transaction() as connection:
        with pytest.raises(ValueError):
            _cas_attempt(connection, before=attempt, after=attempt.model_copy(update={
                "revision": 1, "phase": "delivering", "chunk_count": 1}))
        with pytest.raises(ValueError):
            _cas_attempt(connection, before=attempt, after=attempt.model_copy(update={
                "revision": 1, "phase": "generating", "accepted_at": datetime.now(UTC)}))
    assert state.owner.history().attempts[0] == attempt


def test_accept_rejects_enrollment_that_is_not_actual_verified_bot(state):
    request, binding = request_and_binding(state)
    wrong = request.model_copy(update={"enrollment_generation": "f" * 32})
    with state.owner._transaction() as connection:
        with pytest.raises(ValueError, match="^first_value_binding_invalid$"):
            state.owner._accept_in_transaction(connection, request=wrong, binding=binding, accepted_at=datetime.now(UTC))
    assert state.owner.history().attempts == ()


def test_accept_rejects_replaced_wrapper_repository(state):
    request, binding = request_and_binding(state)
    state.owner._bot.repository = SimpleNamespace(engine=state.engine,
        _load=state.owner._bot.service.repository._load)
    with state.owner._transaction() as connection:
        with pytest.raises(ValueError, match="^first_value_binding_invalid$"):
            state.owner._accept_in_transaction(connection, request=request, binding=binding, accepted_at=datetime.now(UTC))
    assert state.owner.history().attempts == ()


@pytest.mark.parametrize("identity", ["https://host", "C:/secret/key", "/owner/key"])
def test_receipt_rejects_endpoint_urls_and_paths(state, identity):
    from tg_assistant.services.first_value import _serialized
    _, receipt = delivered(state)
    bad = receipt.execution.model_copy(update={"endpoint_id": identity})
    with state.engine.connect() as connection:
        with pytest.raises(ValueError, match="^first_value_state_invalid$"):
            _serialized(connection, bad)


def test_restore_counter_overflow_rolls_back_without_touching_other_namespace(state):
    from tg_assistant.services.first_value import invalidate_first_value_after_restore
    raw = json.loads(raw_header(state))
    raw["revision"] = 2**63 - 1
    with state.engine.begin() as connection:
        connection.execute(text("UPDATE app_settings SET value=:value WHERE key=:key"),
                           {"key": state.owner._key, "value": json.dumps(raw)})
        connection.execute(text("INSERT INTO app_settings(key,value) VALUES ('fv1.other','unparsed foreign value')"))
    before = raw_header(state)
    with pytest.raises(ValueError, match="^first_value_state_invalid$"):
        with state.engine.begin() as connection:
            invalidate_first_value_after_restore(connection, profile_id="owner-profile", windows_sid="S-1-5-21-test")
    assert raw_header(state) == before


async def test_learning_commit_withdrawal_rolls_back_job_and_association(learning):
    from tg_assistant.db.models import BackgroundJob, PendingAction
    from tg_assistant.services.revocation import source_epoch
    state, action_id = learning
    owner = state.owner
    with pytest.raises(PermissionError):
        async with owner.runtime.database.session() as session:
            action = await session.get(PendingAction, action_id)
            action.status = "executing"
            await session.flush()
            await owner.preview.validate(session, action)
            await owner.runtime.policy.apply_template(session, CHAT, "knowledge")
            epoch = await source_epoch(session, CHAT)
            job = BackgroundJob(job_type="learn_group", status="queued", payload=dict(action_id=action_id,
                chat_id=CHAT, owner_id=123, authorization_epoch=epoch, limit=1000))
            session.add(job)
            await session.flush()
            await owner.associate_learning(session, action=action, job=job, source_epoch=epoch)
            state.admitted[0] = False
    state.admitted[0] = True
    assert owner.history().header.learning is None
    async with owner.runtime.database.session() as session:
        assert await session.get(BackgroundJob, job.id) is None


@pytest.mark.parametrize("withdrawal", ["initial", "commit", "flush"])
async def test_learning_raw_session_requires_live_maintenance_admission(learning, withdrawal):
    from tg_assistant.db.models import BackgroundJob, PendingAction
    from tg_assistant.services.revocation import source_epoch
    state, action_id = learning
    owner = state.owner
    before = owner.history().header
    lease = owner._fence.acquire("first-value-test", timeout=0) if withdrawal == "initial" else None
    try:
        with ExitStack() as guard:
            if withdrawal != "initial":
                guard.enter_context(owner._fence.operation())
            # Exact runtime engine/session binding alone must not grant writer admission.
            async with owner.runtime.database.sessions() as session:
                try:
                    action = await session.get(PendingAction, action_id)
                    action.status = "executing"
                    await session.flush()
                    await owner.preview.validate(session, action)
                    await owner.runtime.policy.apply_template(session, CHAT, "knowledge")
                    epoch = await source_epoch(session, CHAT)
                    job = BackgroundJob(job_type="learn_group", status="queued", payload=dict(action_id=action_id,
                        chat_id=CHAT, owner_id=123, authorization_epoch=epoch, limit=1000))
                    session.add(job)
                    await session.flush()
                    if withdrawal == "initial":
                        with pytest.raises(ValueError, match="^first_value_transaction_required$"):
                            await owner.associate_learning(session, action=action, job=job, source_epoch=epoch)
                    else:
                        await owner.associate_learning(session, action=action, job=job, source_epoch=epoch)
                        guard.close()
                        with pytest.raises(ValueError, match="^first_value_transaction_required$"):
                            if withdrawal == "commit":
                                await session.commit()
                            else:
                                action.preview = "Synthetic changed preview"
                                await session.flush()
                finally:
                    await session.rollback()
    finally:
        if lease is not None:
            owner._fence.release(lease)
    assert owner.history().header == before
    async with owner.runtime.database.session() as session:
        assert await session.get(BackgroundJob, job.id) is None
