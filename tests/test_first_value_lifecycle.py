"""Application keeps borrowed resources until the actual SQL worker drains."""
# ruff: noqa: F811 - shared disposable fixture registration
import asyncio
import threading
from types import SimpleNamespace

import pytest
from desktop.test_runtime_bot_context import owned_engine, runtime_system, system  # noqa: F401
from test_first_value_selection import A, rows, selection, service  # noqa: F401

from tg_assistant.config import Settings
from tg_assistant.runtime import Application


async def test_application_close_drains_selection_before_borrowed_resources(selection, tmp_path):
    application = Application(settings=Settings(_env_file=None, data_dir=tmp_path, profile_id="owner-profile"))
    application.bot_runtime = selection.runtime.bot_runtime
    application.bot_runtime._runtime = application
    application.bot_runtime.management_admitted = lambda: selection.current[0]
    selection.runtime = application
    owner = service(selection)
    released = []
    async def close_bot():
        released.append("bot")
    async def close_database():
        released.append("database")
    application.bot_runtime.close = close_bot
    application.database = SimpleNamespace(close=close_database)
    application.rag = SimpleNamespace(vectors=SimpleNamespace(close=lambda: released.append("vector")))
    entered, release = threading.Event(), threading.Event()
    original = selection.coordinator.save_source_selection
    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    selection.coordinator.save_source_selection = paused
    request = asyncio.create_task(owner.select_source(A))
    while not entered.is_set():
        await asyncio.sleep(.001)
    closing = asyncio.create_task(application.close())
    try:
        await asyncio.sleep(.01)
        assert released == [], "Application released resources before retained selection drained"
        assert not closing.done() and not application._resources_released
        closing.cancel()
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(.01)
        assert released == [] and not closing.done()
    finally:
        release.set()
        await asyncio.gather(request, closing, return_exceptions=True)
    assert released == ["vector", "bot", "database"]
    assert application._resources_released
    assert not owner._selection_tasks
    assert rows(selection) == {}


async def test_current_actual_bot_status_reads_without_refreshing_proof(runtime_system):
    from tg_assistant.paths import current_user_sid
    from tg_assistant.services.connections import ConnectionHealth
    from tg_assistant.services.first_value import FirstValueService
    from tg_assistant.services.onboarding import OnboardingCoordinator

    value = runtime_system
    context = value.compose()
    await context.prepare()
    value.runtime.bot_runtime = context
    value.runtime.management_admitted = context.management_admitted
    coordinator = OnboardingCoordinator(engine=value.engine, fence=value.fence,
        profile_id=value.settings.profile_id, storage_backend="sqlite",
        connections=ConnectionHealth(probes={}), verifiers={})
    owner = FirstValueService(runtime=value.runtime, coordinator=coordinator, windows_sid=current_user_sid())
    value.runtime.first_value = owner
    before = (value.client.get_me_calls, tuple(value.transports[0].calls),
              value.transports[0].identity_checked_at, value.transports[0].poll_checked_at)
    for _ in range(2):
        projection = owner.selection_status()
        assert projection.bot_username == "fixture_bot"
        assert projection.source_id is None and projection.test_available is False
    assert before == (value.client.get_me_calls, tuple(value.transports[0].calls),
                      value.transports[0].identity_checked_at, value.transports[0].poll_checked_at)
    await owner.select_source(A)
    assert owner.selection_status().source_id == A
    value.runtime.user.client = object()
    with pytest.raises(PermissionError):
        owner.selection_status()
    with pytest.raises(PermissionError):
        await owner.select_source(A)
    value.runtime.user.client = value.client
    await owner.close()


async def test_worker_attaches_first_value_to_actual_setup_coordinator(runtime_system, monkeypatch):
    from tg_assistant import runtime
    from tg_assistant.db.base import Database
    from tg_assistant.desktop import worker
    from tg_assistant.desktop.setup_context import SetupContext
    from tg_assistant.paths import ensure_runtime_dirs
    from tg_assistant.services.connections import ConnectionHealth
    from tg_assistant.services.onboarding import OnboardingCoordinator

    value = runtime_system
    context = value.compose()
    await context.prepare()
    value.runtime.bot_runtime = context
    value.runtime.management_admitted = context.management_admitted
    value.runtime.first_value = None
    value.runtime._resources_released = False
    coordinator = OnboardingCoordinator(engine=value.engine, fence=value.fence,
        profile_id=value.settings.profile_id, storage_backend="sqlite",
        connections=ConnectionHealth(probes={}), verifiers={})
    provider = SimpleNamespace(drain=lambda: None, refresh_saved_health=lambda **_kwargs: None)
    setup = SetupContext(coordinator, value.engine, value.fence, provider)
    paths = ensure_runtime_dirs(value.settings.data_dir, profile_id=value.settings.profile_id)
    database = Database("sqlite+aiosqlite:///" + str(value.root / "db/assistant.sqlite3"))
    attached = asyncio.Event()
    async def close():
        value.runtime._closed = True
        value.runtime.stopping.set()
        if value.runtime.first_value is not None:
            await value.runtime.first_value.close()
        await context.close()
        value.runtime._resources_released = True
    value.runtime.close = close
    async def run_application(*, runtime_ready, runtime_owned, **_kwargs):
        runtime_owned(value.runtime)
        runtime_ready(value.runtime)
        attached.set()
        try:
            await asyncio.Event().wait()
        finally:
            await close()
    monkeypatch.setattr(runtime, "run_application", run_application)
    with worker.reserve_loopback(0) as listener:
        serving = asyncio.create_task(worker._serve(value.settings, paths, database, listener,
            setup_context=setup, instance_guard=value.guard))
        try:
            await asyncio.wait_for(attached.wait(), 5)
            owner = value.runtime.first_value
            assert owner is not None, "Worker did not attach actual FirstValueService"
            assert owner.coordinator is setup.coordinator
            assert owner._engine is value.engine and owner._fence is value.fence
            assert setup.telegram is context.account_observation
            assert setup.bot is context.setup_observation
            await owner.select_source(A)
            assert owner.selection_status().source_id == A
            assert len(value.transports) == 1
        finally:
            serving.cancel()
            await asyncio.gather(serving, return_exceptions=True)
            await database.close()
    assert value.runtime._resources_released
    assert not owner._selection_tasks
