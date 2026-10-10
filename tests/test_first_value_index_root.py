"""Root regressions for the current-index contract, using actual installed storage."""

from datetime import UTC, datetime, timedelta

import pytest
from test_first_value_index import CHAT, forbid_corpus_and_io, run

from tg_assistant.db.models import AiBudgetReservation, TelegramChatPolicy, TelegramMessage


@pytest.mark.parametrize("stage", ["bind", "references"])
def test_vector_mutation_requires_a_new_observation(tmp_path, stage):
    async def body(env):
        row = env.add_indexed(10)
        observation = await env.reader.read_source_binding(CHAT)
        binding = env.binding(observation)
        if stage == "references":
            assert env.reader.bind_current(observation, binding)
        env.add_indexed(11)
        if stage == "bind":
            assert env.reader.bind_current(observation, binding) is False
        else:
            assert await env.checked(binding, row) is None
        _, fresh = await env.bound()
        assert await env.checked(fresh, row) is not None

    run(tmp_path, body)


@pytest.mark.parametrize("mutation", ["none", "deleted", "policy", "ledger", "vector"])
def test_pre_provider_binding_check_uses_pinned_rows_without_io(tmp_path, monkeypatch, mutation):
    async def body(env):
        row = env.add_indexed(10)
        _, binding = await env.bound()
        if mutation == "deleted":
            env.update(TelegramMessage, TelegramMessage.id == row.row_id, is_deleted=True)
        elif mutation == "policy":
            env.update(TelegramChatPolicy, TelegramChatPolicy.chat_id == CHAT,
                       authorization_epoch=2)
        elif mutation == "ledger":
            env.update(AiBudgetReservation, AiBudgetReservation.request_id == row.request_id,
                       state="uncertain")
        elif mutation == "vector":
            env.add_indexed(11)
        forbid_corpus_and_io(env, monkeypatch)
        assert env.current(binding, None) is (mutation == "none")

    run(tmp_path, body)


@pytest.mark.parametrize("version", [None, "obsolete-policy"])
def test_missing_or_stale_index_policy_version_cannot_qualify(tmp_path, version):
    async def body(env):
        env.add_indexed(10, metadata={"embedding_policy_version": version})
        assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body)


def test_strict_filtering_excludes_a_previously_indexed_short_row(tmp_path):
    async def body(env):
        env.add_indexed(10, text="abc")
        env.update(TelegramChatPolicy, TelegramChatPolicy.chat_id == CHAT, filtering_level="strict")
        assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body)


@pytest.mark.parametrize("content_type,text", [
    ("task", "TODO deadline cho bản phát hành thứ Sáu"),
    ("decision", "Đã thống nhất quyết định phát hành vào thứ Sáu"),
])
def test_supported_search_type_keeps_actual_matching_reference(tmp_path, content_type, text):
    async def body(env):
        row = env.add_indexed(10, text=text)
        _, binding = await env.bound()
        assert await env.checked(binding, row, scope=env.scope(content_type=content_type)) is not None

    run(tmp_path, body)


def test_read_deadline_is_two_seconds_and_late_worker_cannot_issue_proof(tmp_path, monkeypatch):
    import asyncio
    import threading
    import time

    async def body(env):
        env.add_indexed(10)
        original = env.reader._read_work
        started, release = threading.Event(), threading.Event()

        def held(*args):
            started.set()
            release.wait(4)
            return original(*args)

        monkeypatch.setattr(env.reader, "_read_work", held)
        before = time.monotonic()
        pending = asyncio.create_task(env.reader.read_source_binding(CHAT))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            result = await pending
            elapsed = time.monotonic() - before
            assert result is None
            assert elapsed < 2.8
            assert env.runtime._knowledge_lock.locked()
        finally:
            release.set()
            await env.reader.close()
        assert not env.runtime._knowledge_lock.locked()
        assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body)


@pytest.mark.parametrize("values", [
    {"is_local": False},
    {"actual_cost_usd": 1},
    {"settled_at": datetime.now(UTC) - timedelta(days=1)},
    {"pricing_rates": {}},
])
def test_settled_local_embedding_requires_coherent_actual_ledger(tmp_path, values):
    async def body(env):
        row = env.add_indexed(10)
        env.update(AiBudgetReservation, AiBudgetReservation.request_id == row.request_id, **values)
        assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body)


@pytest.mark.parametrize("owner", ["engine", "factory", "factory_bind", "local_client"])
def test_in_place_borrowed_owner_replacement_withdraws_before_sql(tmp_path, owner):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tg_assistant.db.base import Database

    async def body(env):
        env.add_indexed(10)
        _, binding = await env.bound()
        assert env.current(binding, None) is True
        database = env.runtime.database
        original_engine, original_factory = database.engine, database.sessions
        original_client = env.vectors.client._client
        foreign = Database("sqlite+aiosqlite:///" + str(env.tmp / "foreign.db"))
        try:
            if owner == "engine":
                database.engine = foreign.engine
            elif owner == "factory":
                database.sessions = async_sessionmaker(original_engine)
            elif owner == "factory_bind":
                database.sessions.configure(bind=foreign.engine)
            else:
                env.vectors.client._client = object()
            before = len(env.statements)
            assert env.current(binding, None) is False
            assert len(env.statements) == before
        finally:
            database.engine, database.sessions = original_engine, original_factory
            original_factory.configure(bind=original_engine)
            env.vectors.client._client = original_client
            await foreign.engine.dispose()

    run(tmp_path, body)


@pytest.mark.parametrize("owner", ["database", "policy"])
def test_initial_rag_owner_must_be_the_actual_runtime_owner(tmp_path, owner):
    from tg_assistant.db.base import Database
    from tg_assistant.policy import PolicyEngine
    from tg_assistant.services.first_value_index import CurrentIndexReader

    async def body(env):
        original = getattr(env.runtime.rag, owner)
        foreign = Database("sqlite+aiosqlite:///" + str(env.tmp / "foreign.db"))
        try:
            await env.reader.close()
            setattr(env.runtime.rag, owner, foreign if owner == "database" else PolicyEngine())
            with pytest.raises(ValueError, match="current_index_binding_invalid"):
                CurrentIndexReader(runtime=env.runtime, first_value=env.first_value,
                                   windows_sid=env.sid)
        finally:
            setattr(env.runtime.rag, owner, original)
            await foreign.engine.dispose()

    run(tmp_path, body)


def test_sqlite_vm_budget_interrupts_by_one_hundred_thousand_steps_and_clears(tmp_path):
    from types import SimpleNamespace

    from sqlalchemy.exc import OperationalError

    from tg_assistant.services.first_value_index import _bounded

    async def body(env):
        with env.engine.connect() as connection:
            raw = connection.connection.dbapi_connection
            observed = [0]

            class ProgressProbe:
                def set_progress_handler(self, callback, steps):
                    if callback is None:
                        raw.set_progress_handler(None, 0)
                        return

                    def measured():
                        observed[0] += steps
                        return callback()

                    raw.set_progress_handler(measured, steps)

            bridge = SimpleNamespace(connection=SimpleNamespace(dbapi_connection=ProgressProbe()))
            with pytest.raises(OperationalError, match="interrupted"), _bounded(bridge, 2):
                connection.exec_driver_sql(
                    "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n "
                    "WHERE x<1000000) SELECT sum(x) FROM n"
                )
            assert observed[0] <= 100_000
            assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1

    run(tmp_path, body)


@pytest.mark.parametrize("field,value", [
    ("ollama_embedding_model", "changed-model:latest"),
    ("ollama_base_url", "http://127.0.0.1:11435"),
    ("embedding_version", "changed-version"),
    ("ollama_vector_size", 4),
    ("embedding_provider", "off"),
    ("cloud_consent", True),
    ("enable_embeddings", False),
])
def test_in_place_embedding_settings_change_withdraws_before_sql(tmp_path, field, value):
    async def body(env):
        row = env.add_indexed(10)
        _, binding = await env.bound()
        checked = await env.checked(binding, row)
        assert checked is not None and env.current(binding, checked)
        setattr(env.settings, field, value)
        env.statements.clear()
        assert env.current(binding, checked) is False
        assert not env.statements
        assert await env.checked(binding, row) is None
        assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body)


def test_in_place_cloud_consent_revocation_withdraws_current_cloud_index(tmp_path):
    from test_first_value_index_integration import cloud, cloud_ledger

    async def body(env):
        row = env.add_indexed(10)
        cloud_ledger(env, row)
        _, binding = await env.bound()
        checked = await env.checked(binding, row)
        assert checked is not None and env.current(binding, checked)
        env.settings.cloud_consent = False
        env.statements.clear()
        assert env.current(binding, checked) is False
        assert not env.statements
        assert await env.checked(binding, row) is None
        assert await env.reader.read_source_binding(CHAT) is None

    run(tmp_path, body, **cloud("openai"))
