from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import pytest_asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from tg_assistant import cli, runtime
from tg_assistant.db.models import TelegramAccount


@pytest_asyncio.fixture
async def lifecycle(session, monkeypatch):
    """Keep application orchestration real; replace account and transport boundaries."""
    app = object.__new__(runtime.Application)
    events, outcomes, component_tasks = [], {}, set()
    failure = RuntimeError("offline component failed")
    cleanup_failure = ValueError("offline component cleanup failed")

    async def component(name):
        component_tasks.add(asyncio.current_task())
        events.append(f"{name}:started")
        try:
            outcome = outcomes.get(name)
            if outcome == "stop_and_error":
                app.stopping.set()
                raise failure
            if outcome == "error":
                raise failure
            if outcome == "cancel":
                raise asyncio.CancelledError
            if outcome == "return":
                return
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if outcome == "cancel_error":
                raise cleanup_failure from None
            raise
        finally:
            events.append(f"{name}:finished")

    def closer(name):
        async def close():
            events.append(f"{name}:closed")
            if outcomes.get(f"{name}:close"):
                raise ValueError(f"{name} close failed")
        return close

    @asynccontextmanager
    async def database_session():
        yield session

    async def authenticate(_phone):
        if outcomes.get("authenticate"):
            raise failure
        return SimpleNamespace(id=42)

    async def discover(_session):
        return 0

    def vectors_close():
        events.append("vectors:closed")

    app.settings = SimpleNamespace(
        learning_job_interval_seconds=3600, admin_api_enabled=False,
        admin_api_host="127.0.0.1", admin_api_port=8765,
    )
    app.store = SimpleNamespace(get=lambda _key: "")
    app.paths = {}
    app.policy = object()
    app.ai = object()
    app.budget = object()
    app.database = SimpleNamespace(session=database_session, close=closer("database"))
    app.user = SimpleNamespace(
        authenticate=authenticate, discover_dialogs=discover,
        register_handlers=lambda *_args, **_kwargs: None,
        client=SimpleNamespace(run_until_disconnected=lambda: component("telegram")),
        close=closer("user"),
    )
    app.rag = SimpleNamespace(vectors=SimpleNamespace(close=vectors_close))
    app.ai_router = SimpleNamespace(close=closer("ai"))
    app.embedding_ai = SimpleNamespace(close=closer("embedding"))
    app.ollama = SimpleNamespace(close=closer("ollama"))
    app.coingecko = SimpleNamespace(close=closer("coingecko"))
    app.bot = None
    app.admin_server = None
    app.scheduler = AsyncIOScheduler()
    app.stopping = asyncio.Event()
    app._scheduled_tasks = set()
    app._closed = False
    app._execute_actions = lambda: component("actions")
    bot = SimpleNamespace(run=lambda: component("bot"), close=closer("bot"))

    async def registry(*_args):
        pass

    def admin_app(_context):
        if outcomes.get("dashboard_setup"):
            raise failure
        return object()

    monkeypatch.setattr(runtime, "ControlBot", lambda *_args, **_kwargs: bot)
    monkeypatch.setattr(runtime, "ensure_vector_store_registry", registry)
    monkeypatch.setattr(runtime, "ensure_dashboard_secret", lambda _store: "offline")
    monkeypatch.setattr(runtime, "create_admin_app", admin_app)
    monkeypatch.setattr(runtime.uvicorn, "Server", lambda _config: SimpleNamespace(
        serve=lambda: component("dashboard"), should_exit=False,
    ))
    session.add(TelegramAccount(telegram_user_id=42, is_owner_paired=True))
    await session.flush()
    yield SimpleNamespace(
        app=app, events=events, outcomes=outcomes,
        failure=failure, cleanup_failure=cleanup_failure,
    )
    if app.scheduler.running:
        app.scheduler.shutdown(wait=False)
    await asyncio.sleep(0)
    for task in component_tasks:
        task.cancel()
    await asyncio.gather(*component_tasks, return_exceptions=True)


@pytest.mark.parametrize("name", ["bot", "telegram", "actions", "dashboard"])
@pytest.mark.parametrize("outcome", ["error", "return", "cancel"])
async def test_unexpected_core_exit_terminates_and_closes(lifecycle, name, outcome):
    flow = lifecycle
    flow.app.settings.admin_api_enabled = name == "dashboard"
    flow.outcomes[name] = outcome
    with pytest.raises(RuntimeError) as caught:
        await asyncio.wait_for(flow.app.run(), timeout=0.5)
    if outcome == "error":
        assert caught.value is flow.failure
    else:
        assert name in str(caught.value)
    assert "database:closed" in flow.events
    assert all(
        f"{name}:finished" in flow.events
        for name in ("bot", "telegram", "actions")
    )


@pytest.mark.parametrize("stage", ["authenticate", "dashboard_setup"])
async def test_startup_failure_closes_resources(lifecycle, stage):
    flow = lifecycle
    flow.outcomes[stage] = True
    flow.app.settings.admin_api_enabled = stage == "dashboard_setup"
    with pytest.raises(RuntimeError) as caught:
        await flow.app.run()
    assert caught.value is flow.failure
    assert "user:closed" in flow.events
    assert "database:closed" in flow.events
    assert not flow.app.scheduler.running


async def test_cleanup_failure_does_not_hide_runtime_failure_or_skip_resources(lifecycle):
    flow = lifecycle
    flow.outcomes.update({"bot": "error", "bot:close": True})
    with pytest.raises(RuntimeError) as caught:
        await asyncio.wait_for(flow.app.run(), timeout=0.5)
    assert caught.value is flow.failure
    assert "embedding:closed" in flow.events
    assert "user:closed" in flow.events
    assert "database:closed" in flow.events


async def test_requested_stop_does_not_hide_simultaneous_core_failure(lifecycle):
    flow = lifecycle
    flow.outcomes["bot"] = "stop_and_error"
    with pytest.raises(RuntimeError) as caught:
        await asyncio.wait_for(flow.app.run(), timeout=0.5)
    assert caught.value is flow.failure


async def test_core_failure_takes_priority_over_simultaneous_normal_return(lifecycle):
    flow = lifecycle
    flow.outcomes.update({"bot": "return", "actions": "error"})
    with pytest.raises(RuntimeError) as caught:
        await asyncio.wait_for(flow.app.run(), timeout=0.5)
    assert caught.value is flow.failure


async def test_shutdown_task_failure_is_visible_after_all_closers_run(lifecycle):
    flow = lifecycle
    flow.outcomes.update({"bot": "cancel_error", "bot:close": True})
    running = asyncio.create_task(flow.app.run())
    async with asyncio.timeout(0.5):
        while "bot:started" not in flow.events:
            await asyncio.sleep(0)
    flow.app.stopping.set()
    with pytest.raises(ValueError) as caught:
        await asyncio.wait_for(running, timeout=0.5)
    assert caught.value is flow.cleanup_failure
    assert "database:closed" in flow.events


async def test_runtime_failure_wins_over_sibling_shutdown_failure(lifecycle):
    flow = lifecycle
    flow.outcomes.update({"bot": "error", "actions": "cancel_error"})
    with pytest.raises(RuntimeError) as caught:
        await asyncio.wait_for(flow.app.run(), timeout=0.5)
    assert caught.value is flow.failure
    assert "database:closed" in flow.events


async def test_requested_stop_is_clean_and_close_is_idempotent(lifecycle):
    flow = lifecycle
    running = asyncio.create_task(flow.app.run())
    await asyncio.sleep(0)
    flow.app.stopping.set()
    await asyncio.wait_for(running, timeout=0.5)
    await flow.app.close()
    assert flow.events.count("database:closed") == 1


async def test_cancellation_does_not_hide_cleanup_failure(lifecycle):
    flow = lifecycle
    flow.outcomes["bot:close"] = True
    running = asyncio.create_task(flow.app.run())
    async with asyncio.timeout(0.5):
        while "bot:started" not in flow.events:
            await asyncio.sleep(0)
    running.cancel()
    with pytest.raises(ValueError, match="bot close failed"):
        await running
    assert "database:closed" in flow.events


async def test_scheduled_job_finalizer_finishes_before_database_closes(lifecycle):
    flow = lifecycle
    started = asyncio.Event()
    finished = asyncio.Event()

    async def scheduled():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            flow.events.append("job:finished")
            finished.set()

    flow.app._process_learning_jobs = scheduled
    # Exercise the registration and cancellation order through the actual scheduler.
    running = asyncio.create_task(flow.app.run())
    try:
        async with asyncio.timeout(0.5):
            while (job := flow.app.scheduler.get_job("process_learning_jobs")) is None:
                await asyncio.sleep(0)
        job.reschedule("date")
        await asyncio.wait_for(started.wait(), timeout=0.5)
        flow.app.stopping.set()
        await asyncio.wait_for(running, timeout=0.5)
        await asyncio.wait_for(finished.wait(), timeout=0.5)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    assert flow.events.index("job:finished") < flow.events.index("database:closed")


@pytest.mark.parametrize("failure", [False, True])
def test_worker_propagates_failure_and_cleans_runtime_files(tmp_path, monkeypatch, failure):
    pid, lock, stop, started = [tmp_path / name for name in ("pid", "lock", "stop", "started")]
    expected = RuntimeError("offline startup failed")

    async def application():
        if failure:
            raise expected
        stop.write_text("stop", encoding="ascii")
        await asyncio.Event().wait()

    monkeypatch.setattr(cli, "_runtime_files", lambda: (pid, lock, stop))
    monkeypatch.setattr(cli, "_started_file", lambda: started)
    monkeypatch.setattr(cli, "run_application", application)
    if failure:
        with pytest.raises(RuntimeError) as caught:
            cli.worker()
        assert caught.value is expected
    else:
        cli.worker()
    assert not pid.exists()
    assert not started.exists()
    assert not stop.exists()
