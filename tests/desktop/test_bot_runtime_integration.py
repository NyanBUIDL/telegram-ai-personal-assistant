"""Application composition cannot trust legacy paired flags/global bot tokens."""

# ruff: noqa: F811 - imported real disposable native fixture

import pytest
from desktop.test_runtime_gateway import native_case  # noqa: F401

from tg_assistant import runtime


async def test_legacy_sql_pair_and_global_token_do_not_start_management(native_case):
    settings, paths, _store, transports = native_case
    application = runtime.Application(
        settings=settings,
        admin_app_ready=lambda _admin: pytest.fail("Legacy pair flag started management"),
    )
    with pytest.raises(RuntimeError, match="owner_pairing_required"):
        await application.run()
    assert transports[0].closed == 1
    assert application.bot is None
    assert not (paths["sessions"] / "account.session").exists()


async def test_private_bot_preparation_precedes_handlers_scheduler_and_api(native_case, monkeypatch):
    settings, _paths, _store, transports = native_case
    events = []

    class PrivateBotContext:
        bot = object()

        async def prepare(self):
            assert application.bot is None
            assert not application.scheduler.running
            events.append("prepare")

        def management_admitted(self):
            return True

        async def run_updates(self, _dispatcher, _bot):
            pytest.fail("Mock ControlBot transport should not invoke real polling")

        async def close(self):
            events.append("drain_bot")

    context = PrivateBotContext()

    def attach_runtime(actual):
        assert actual.bot_runtime is context
        events.append("runtime")

    def attach_admin(admin):
        assert events == ["prepare", "runtime"]
        assert admin.state.admin_context.management_admission() is True
        events.append("admin")
        application.stopping.set()

    application = runtime.Application(
        settings=settings, admin_app_ready=attach_admin, runtime_ready=attach_runtime,
        bot_runtime_factory=lambda _application: context,
    )
    await application.run()
    assert events == ["prepare", "runtime", "admin", "drain_bot"]
    assert transports[0].closed == 1
    assert application.management_admitted() is False


async def test_uncertain_bot_drain_retains_account_until_successful_retry(native_case):
    settings, _paths, _store, transports = native_case

    class PendingContext:
        bot = object()
        pending = True

        async def prepare(self):
            pass

        def management_admitted(self):
            return True

        async def run_updates(self, *_args):
            pass

        async def close(self):
            if self.pending:
                raise RuntimeError("synthetic_bot_drain_pending")

    context = PendingContext()
    application = runtime.Application(
        settings=settings, admin_app_ready=lambda _admin: application.stopping.set(),
        bot_runtime_factory=lambda _application: context,
    )
    with pytest.raises(RuntimeError, match="runtime_cleanup_pending"):
        await application.run()
    assert transports[0].closed == 0, "Uncertain bot drain must retain its account owner"
    assert application.management_admitted() is False
    context.pending = False
    await application.close()
    assert transports[0].closed == 1


async def test_worker_drain_retains_actual_application_until_retry_succeeds(native_case):
    import asyncio

    from tg_assistant.desktop import worker

    settings, _paths, _store, transports = native_case
    pending_seen = asyncio.Event()

    class PendingContext:
        bot = object()
        pending = True

        async def prepare(self):
            pass

        def management_admitted(self):
            return True

        async def run_updates(self, *_args):
            pass

        async def close(self):
            if self.pending:
                raise RuntimeError("synthetic_bot_drain_pending")

    context = PendingContext()
    application = runtime.Application(
        settings=settings, admin_app_ready=lambda _admin: application.stopping.set(),
        bot_runtime_factory=lambda _application: context,
    )
    with pytest.raises(RuntimeError, match="runtime_cleanup_pending"):
        await application.run()
    assert hasattr(worker, "drain_runtime_owner"), "Worker must retain a failed drain owner"
    draining = asyncio.create_task(worker.drain_runtime_owner(application, on_pending=pending_seen.set))
    try:
        await asyncio.wait_for(pending_seen.wait(), 3)
        assert transports[0].closed == 0 and not draining.done()
        draining.cancel()
        await asyncio.sleep(0)
        assert not draining.done(), "Worker cancellation cannot abandon pending SDK cleanup"
        context.pending = False
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(draining, 3)
        assert transports[0].closed == 1 and application._resources_released is True
    finally:
        context.pending = False
        await asyncio.gather(draining, return_exceptions=True)


async def test_legacy_bootstrap_guides_native_setup_without_accessing_credentials(monkeypatch, capsys):
    def forbidden():
        pytest.fail("Legacy bootstrap must not prompt/adopt global credentials")

    monkeypatch.setattr(runtime, "get_settings", forbidden)
    monkeypatch.setattr(runtime, "SecretStore", forbidden)
    await runtime.bootstrap()
    assert "Windows" in capsys.readouterr().out


async def test_worker_serve_cancellation_keeps_actual_guard_until_bot_drains(native_case, monkeypatch, tmp_path):
    import asyncio

    from desktop.test_runtime_gateway import ACTUAL_UVICORN_SERVER

    from tg_assistant.desktop import worker
    from tg_assistant.desktop.instance import AlreadyRunning, InstanceGuard

    settings, paths, store, transports = native_case
    attached, pending_seen = asyncio.Event(), asyncio.Event()
    applications = []
    instance_directory = tmp_path / "native-control"

    class PendingContext:
        bot = object()
        pending = True

        async def prepare(self):
            pass

        def management_admitted(self):
            return True

        async def run_updates(self, *_args):
            pass

        async def close(self):
            if self.pending:
                pending_seen.set()
                raise RuntimeError("synthetic_bot_drain_pending")

    context = PendingContext()
    original_application = runtime.Application

    class PendingApplication(original_application):
        def __init__(self, *, settings, admin_app_ready, runtime_ready=None, bot_runtime_factory=None):
            def attach(admin):
                admin_app_ready(admin)
                attached.set()
                self.stopping.set()

            super().__init__(
                settings=settings, admin_app_ready=attach, runtime_ready=runtime_ready,
                bot_runtime_factory=lambda _application: context,
            )
            applications.append(self)

    monkeypatch.setattr(runtime, "Application", PendingApplication)
    monkeypatch.setattr(runtime.uvicorn, "Server", ACTUAL_UVICORN_SERVER)
    database = runtime.make_database(settings, store)

    async def parent():
        # This is the actual OS owner surrounding the worker, as in serve_worker.
        with InstanceGuard(instance_directory) as guard, worker.reserve_loopback(0) as listener:
            await worker._serve(settings, paths, database, listener, instance_guard=guard)

    serving = asyncio.create_task(parent())
    try:
        await asyncio.wait_for(attached.wait(), 5)
        await asyncio.wait_for(pending_seen.wait(), 5)
        serving.cancel()
        await asyncio.sleep(0.4)
        assert not serving.done(), "Worker finalizer must keep its pending SDK owner"
        assert transports[0].closed == 0
        with pytest.raises(AlreadyRunning):
            with InstanceGuard(instance_directory):
                pytest.fail("A second owner entered while actual bot cleanup remained pending")
        context.pending = False
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(serving, 5)
        assert applications[0]._resources_released is True
        assert transports[0].closed == 1
        with InstanceGuard(instance_directory):
            pass
    finally:
        context.pending = False
        serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)
        for application in applications:
            await application.close()
        await database.close()
