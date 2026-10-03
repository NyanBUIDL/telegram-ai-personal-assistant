"""Real, independent connections: a lease must prevent duplicate work."""

import asyncio
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from tg_assistant.contracts import OperationResult
from tg_assistant.db.migrations import upgrade_database
from tg_assistant.db.models import (
    BackgroundJob,
    PendingAction,
    PermissionName,
    Reminder,
    Task,
    TelegramChat,
    TelegramChatPermission,
    TelegramChatPolicy,
    TelegramMessage,
)


@pytest.fixture(params=["sqlite", "mysql"])
def storage(tmp_path, request):
    admin = None
    if request.param == "mysql":
        value = os.environ.get("TG_TEST_MYSQL_URL")
        if not value:
            pytest.skip("Disposable MySQL not configured")
        url = sa.engine.make_url(value)
        assert url.host in {"127.0.0.1", "localhost"} and (url.database or "").startswith("codex_")
        name = "codex_s02_" + uuid4().hex
        admin = sa.create_engine(url.set(database=None))
        with admin.begin() as connection:
            connection.exec_driver_sql(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
        engine = sa.create_engine(url.set(database=name))
    else:
        engine = sa.create_engine(sa.URL.create("sqlite", database=str(tmp_path / "jobs.sqlite3")))
    try:
        with engine.connect() as connection:
            upgrade_database(
                connection, script_location=Path(__file__).resolve().parents[2] / "alembic"
            )
        yield engine
    finally:
        engine.dispose()
        if admin:
            with admin.begin() as connection:
                connection.exec_driver_sql(f"DROP DATABASE `{name}`")
            admin.dispose()


def queue(engine, *, job_type="history_backfill", payload=None):
    with Session(engine) as session, session.begin():
        job = BackgroundJob(job_type=job_type, payload=payload or {}, status="queued")
        session.add(job)
        session.flush()
        return job.id


def repository(engine, **kwargs):
    if find_spec("tg_assistant.services.jobs") is None:
        pytest.fail("Atomic job repository is not implemented")
    from tg_assistant.services.jobs import JobRepository

    return JobRepository(engine.url, profile_id="default", **kwargs)


def result(job_id):
    return OperationResult(
        operation_id=job_id,
        state="completed",
        progress=100,
        code="completed",
        message="Completed",
        next_action=None,
    )


def test_two_workers_single_claim(storage):
    job_id = queue(storage)
    barrier = Barrier(2)
    now = datetime.now(UTC)

    def claim(worker):
        repo = repository(storage)
        try:
            barrier.wait(timeout=10)
            return repo.claim("history_backfill", worker, now, 30)
        finally:
            repo.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, ("worker-a", "worker-b")))
    winners = [value for value in claims if value is not None]
    assert len(winners) == 1 and winners[0].id == job_id
    with Session(storage) as observed:
        assert observed.get(BackgroundJob, job_id).attempts == 1


def test_lease_expiry_and_restart(storage):
    queue(storage)
    now = datetime.now(UTC)
    original = repository(storage)
    first = original.claim("history_backfill", "interrupted-worker", now, 1)
    original.close()
    restarted = repository(storage)
    try:
        second = restarted.claim("history_backfill", "restart", now + timedelta(seconds=2), 30)
        assert second is not None and second.id == first.id
        assert second.claim_token != first.claim_token
        assert restarted.complete(first, result(first.id)) is False
        assert restarted.complete(second, result(second.id)) is True
        assert restarted.complete(second, result(second.id)) is False
    finally:
        restarted.close()


def test_started_external_effect_is_uncertain_after_restart(storage):
    job_id = queue(storage, job_type="history_link_delete")
    now = datetime.now(UTC)
    first = repository(storage)
    lease = first.claim("history_link_delete", "interrupted-worker", now, 1)
    assert first.mark_external_started(lease) is True
    first.close()
    restarted = repository(storage)
    try:
        assert (
            restarted.claim("history_link_delete", "restart", now + timedelta(seconds=2), 30)
            is None
        )
        with Session(storage) as observed:
            job = observed.get(BackgroundJob, job_id)
            assert job.status == "uncertain" and job.payload["requires_reconciliation"] is True
            assert job.attempts == 1
        assert restarted.complete(lease, result(job_id)) is False
    finally:
        restarted.close()


def test_revocation_wins_completion(storage):
    with Session(storage) as session, session.begin():
        session.add(TelegramChatPolicy(chat_id=-1001, allowed=True, authorization_epoch=2))
    job_id = queue(storage, payload={"chat_id": -1001, "authorization_epochs": {"-1001": 2}})
    repo = repository(storage)
    try:
        lease = repo.claim("history_backfill", "worker", datetime.now(UTC), 30)
        with Session(storage) as session, session.begin():
            session.execute(
                sa.update(TelegramChatPolicy)
                .where(TelegramChatPolicy.chat_id == -1001)
                .values(allowed=False, authorization_epoch=3)
            )
        assert repo.complete(lease, result(job_id)) is False
        with Session(storage) as session:
            assert session.get(BackgroundJob, job_id).status == "cancelled"
    finally:
        repo.close()


def test_cancelled_lease_never_completes(storage):
    job_id = queue(storage, job_type="ollama_pull")
    repo = repository(storage)
    try:
        lease = repo.claim("ollama_pull", "worker", datetime.now(UTC), 30)
        with Session(storage) as session, session.begin():
            session.execute(
                sa.update(BackgroundJob)
                .where(BackgroundJob.id == job_id)
                .values(status="cancel_requested")
            )
        assert repo.complete(lease, result(job_id)) is False
    finally:
        repo.close()


@pytest.mark.asyncio
async def test_runtime_claim_preserves_oldest_job(storage):
    from tg_assistant.db.base import Database
    from tg_assistant.services.jobs import claim_runtime_job

    older = queue(storage, job_type="history_backfill")
    newer = queue(storage, job_type="ollama_pull")
    with Session(storage) as session, session.begin():
        session.execute(
            sa.update(BackgroundJob)
            .where(BackgroundJob.id == older)
            .values(created_at=datetime.now(UTC) - timedelta(minutes=1))
        )
    driver = "sqlite+aiosqlite" if storage.dialect.name == "sqlite" else "mysql+asyncmy"
    db = Database(storage.url.set(drivername=driver).render_as_string(hide_password=False))
    try:
        lease, kind = await claim_runtime_job(db, ("ollama_pull", "history_backfill"))
        assert lease.id == older and kind == "history_backfill"
        with Session(storage) as session:
            assert session.get(BackgroundJob, newer).status == "queued"
    finally:
        await db.close()


def test_sqlite_busy_is_bounded(storage):
    if storage.dialect.name != "sqlite":
        pytest.skip("SQLite busy behavior is dialect specific")
    queue(storage)
    repo = repository(storage)
    held = storage.connect()
    held.exec_driver_sql("BEGIN IMMEDIATE")
    started = time.monotonic()
    try:
        from tg_assistant.services.jobs import StorageBusy

        with pytest.raises(StorageBusy):
            repo.claim("history_backfill", "blocked-worker", datetime.now(UTC), 30)
        assert time.monotonic() - started < 2
    finally:
        held.rollback()
        held.close()
        repo.close()


def maintenance(root):
    if find_spec("tg_assistant.services.maintenance") is None:
        pytest.fail("Maintenance must own a real writer fence")
    from tg_assistant.services.maintenance import MaintenanceService

    return MaintenanceService(root, profile_id="default")


def test_maintenance_blocks_claim(storage, tmp_path):
    queue(storage)
    service = maintenance(tmp_path / "profile")
    other_process = maintenance(tmp_path / "profile")
    repo = repository(storage, fence=other_process)
    from tg_assistant.services.maintenance import MaintenanceBusy

    try:
        lease = service.acquire("restore-test", lease_seconds=30)
        assert service.validate(lease) is True
        assert other_process.validate(lease) is False
        with pytest.raises(MaintenanceBusy):
            repo.claim("history_backfill", "blocked", datetime.now(UTC), 30)
        service.release(lease)
        assert repo.claim("history_backfill", "resumed", datetime.now(UTC), 30) is not None
    finally:
        repo.close()
        service.close()
        other_process.close()


def test_maintenance_drains_real_writer(tmp_path):
    service = maintenance(tmp_path / "profile")
    writer = maintenance(tmp_path / "profile")
    from tg_assistant.services.maintenance import MaintenanceBusy

    entered, finish = Event(), Event()

    def admitted_writer():
        with writer.operation():
            entered.set()
            assert finish.wait(timeout=10)
            # The admitted operation can finish nested transactions while draining.
            with writer.operation():
                pass

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(admitted_writer)
        assert entered.wait(timeout=10)
        try:
            with pytest.raises(MaintenanceBusy):
                service.acquire("restore-test", lease_seconds=30, timeout=0.1)
            with pytest.raises(MaintenanceBusy):
                with writer.operation():
                    pytest.fail("New operation admitted while draining")
        finally:
            finish.set()
        future.result(timeout=10)
    # A failed drain persists; recovery requires an exclusive lock, not time.
    service.recover()
    lease = service.acquire("restore-test", lease_seconds=30)
    assert service.validate(lease)
    service.release(lease)


def test_expired_maintenance_requires_explicit_recovery(tmp_path):
    service = maintenance(tmp_path / "profile")
    from tg_assistant.services.maintenance import MaintenanceBusy

    lease = service.acquire("restore-test", lease_seconds=1)
    service.close()  # Simulates process death: the OS lock closes, persistent fence remains.
    restarted = maintenance(tmp_path / "profile")
    with pytest.raises(MaintenanceBusy):
        with restarted.operation():
            pytest.fail("Expired holder must not silently resume writers")
    assert restarted.validate(lease) is False
    restarted.recover()
    with restarted.operation():
        pass


@pytest.mark.asyncio
async def test_database_sessions_obey_owned_fence(storage, tmp_path):
    from tg_assistant.db.base import Database

    service = maintenance(tmp_path / "profile")
    driver = "sqlite+aiosqlite" if storage.dialect.name == "sqlite" else "mysql+asyncmy"
    database = Database(storage.url.set(drivername=driver).render_as_string(hide_password=False))
    database.fence = maintenance(tmp_path / "profile")
    from tg_assistant.services.maintenance import MaintenanceBusy

    lease = service.acquire("migration-test", lease_seconds=30)
    try:
        with pytest.raises(MaintenanceBusy):
            async with database.session() as session:
                session.add(BackgroundJob(job_type="forbidden", payload={}))
    finally:
        service.release(lease)
        await database.close()
    with Session(storage) as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(BackgroundJob)) == 0


@pytest.mark.asyncio
async def test_runtime_workers_dispatch_only_one_lease(storage):
    from tg_assistant.db.base import Database
    from tg_assistant.runtime import Application

    job_id = queue(storage)
    driver = "sqlite+aiosqlite" if storage.dialect.name == "sqlite" else "mysql+asyncmy"
    databases = [
        Database(storage.url.set(drivername=driver).render_as_string(hide_password=False))
        for _ in range(2)
    ]
    calls = []

    async def external_dispatch(value):
        calls.append(value)
        await asyncio.sleep(0.05)

    apps = []
    selected = 0
    both_selected = asyncio.Event()

    class ScheduledDatabase:
        """Widen only the real SELECT-to-UPDATE gap; keep all DB work real."""

        def __init__(self, database):
            self.database = database

        @asynccontextmanager
        async def session(self):
            nonlocal selected
            async with self.database.session() as session:
                real_scalar = session.scalar

                async def scheduled_scalar(query, *args, **kwargs):
                    nonlocal selected
                    value = await real_scalar(query, *args, **kwargs)
                    if isinstance(value, BackgroundJob) and value.status == "queued":
                        selected += 1
                        if selected == 2:
                            both_selected.set()
                        await asyncio.wait_for(both_selected.wait(), timeout=5)
                    return value

                session.scalar = scheduled_scalar
                yield session

    for database in databases:
        app = object.__new__(Application)
        app.database = ScheduledDatabase(database)
        app.user = SimpleNamespace(owner_id=42)
        app._run_history_backfill_job = external_dispatch
        apps.append(app)
    try:
        await asyncio.gather(*(app._process_admin_jobs() for app in apps))
        assert calls == [job_id]
    finally:
        for database in databases:
            await database.close()


@pytest.mark.asyncio
async def test_stale_runtime_write_cannot_overwrite_reclaimed_job(storage):
    from tg_assistant.db.base import Database
    from tg_assistant.services import jobs

    if not hasattr(jobs, "worker_lease"):
        pytest.fail("Runtime writes do not validate durable lease ownership")
    now = datetime.now(UTC)
    job_id = queue(storage)
    repo = repository(storage)
    first = repo.claim("history_backfill", "first", now, 1)
    driver = "sqlite+aiosqlite" if storage.dialect.name == "sqlite" else "mysql+asyncmy"
    database = Database(storage.url.set(drivername=driver).render_as_string(hide_password=False))
    try:
        with pytest.raises(jobs.LeaseLost):
            with jobs.worker_lease(first):
                async with database.session() as session:
                    stale = await session.get(BackgroundJob, job_id)
                    second = await asyncio.to_thread(
                        repo.claim, "history_backfill", "second", now + timedelta(seconds=2), 30
                    )
                    stale.status = "completed"
                    stale.payload = {"stale": True}
        with Session(storage) as session:
            current = session.get(BackgroundJob, job_id)
            assert current.status == "running" and current.claim_token == second.claim_token
            assert "stale" not in current.payload
    finally:
        repo.close()
        await database.close()


def pending(storage):
    with Session(storage) as session, session.begin():
        action = PendingAction(
            action_type="create_task",
            requested_by=42,
            payload={"title": "Fixture task"},
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        session.add(action)
        session.flush()
        return action.action_id


@pytest.mark.asyncio
async def test_double_confirm_single_execution(storage):
    from tg_assistant.db.base import Database
    from tg_assistant.services.actions import PendingActionService

    service = PendingActionService()
    if not hasattr(service, "claim_execution"):
        pytest.fail("Action execution needs an atomic claim independent of confirmation")
    action_id = pending(storage)
    driver = "sqlite+aiosqlite" if storage.dialect.name == "sqlite" else "mysql+asyncmy"
    databases = [
        Database(storage.url.set(drivername=driver).render_as_string(hide_password=False))
        for _ in range(2)
    ]

    async def confirm(database):
        try:
            async with database.session() as session:
                await service.confirm(session, action_id, 42)
            return True
        except ValueError:
            return False

    effects = []

    async def execute(database):
        async with database.session() as session:
            claimed = await service.claim_execution(session, action_id, 42)
        if claimed:
            effects.append("one external effect")

    try:
        assert sum(await asyncio.gather(*(confirm(db) for db in databases))) == 1
        await asyncio.gather(*(execute(db) for db in databases))
        assert effects == ["one external effect"]
    finally:
        for database in databases:
            await database.close()


@pytest.mark.asyncio
async def test_cancel_does_not_overwrite_confirmed_action(storage):
    if storage.dialect.name != "sqlite":
        pytest.skip("MySQL locks the pending read; SQLite requires conditional cancel")
    from tg_assistant.db.base import Database
    from tg_assistant.services.actions import PendingActionService

    service = PendingActionService()
    action_id = pending(storage)
    database = Database(
        storage.url.set(drivername="sqlite+aiosqlite").render_as_string(hide_password=False)
    )
    selected, continue_cancel = asyncio.Event(), asyncio.Event()

    async def stale_cancel():
        async with database.session() as session:
            original = session.scalar

            async def delayed(query, *args, **kwargs):
                value = await original(query, *args, **kwargs)
                if isinstance(value, PendingAction):
                    selected.set()
                    await asyncio.wait_for(continue_cancel.wait(), timeout=5)
                return value

            session.scalar = delayed
            await service.cancel(session, action_id, 42)

    cancellation = asyncio.create_task(stale_cancel())
    try:
        await asyncio.wait_for(selected.wait(), timeout=5)
        async with database.session() as session:
            await service.confirm(session, action_id, 42)
        continue_cancel.set()
        with pytest.raises(ValueError):
            await cancellation
        with Session(storage) as session:
            assert session.get(PendingAction, action_id).status == "confirmed"
    finally:
        continue_cancel.set()
        await asyncio.gather(cancellation, return_exceptions=True)
        await database.close()


@pytest.mark.asyncio
async def test_reminder_claim_commits_before_external_effect(storage):
    from tg_assistant.db.base import Database
    from tg_assistant.runtime import Application

    with Session(storage) as session, session.begin():
        reminder = Reminder(
            message="Synthetic reminder", remind_at=datetime.now(UTC) - timedelta(seconds=1)
        )
        session.add(reminder)
        session.flush()
        reminder_id = reminder.id
    driver = "sqlite+aiosqlite" if storage.dialect.name == "sqlite" else "mysql+asyncmy"
    databases = [
        Database(storage.url.set(drivername=driver).render_as_string(hide_password=False))
        for _ in range(2)
    ]
    effects = []

    async def uncertain_send(*args, **kwargs):
        with Session(storage) as observer:
            assert observer.get(Reminder, reminder_id).status == "dispatching"
        effects.append("submitted")
        await asyncio.sleep(0.05)
        raise TimeoutError("Synthetic response lost after submission")

    apps = []
    for db in databases:
        app = object.__new__(Application)
        app.database = db
        app.user = SimpleNamespace(owner_id=42)
        app.bot = SimpleNamespace(bot=SimpleNamespace(send_message=uncertain_send))
        apps.append(app)
    try:
        await asyncio.gather(*(app._dispatch_reminders() for app in apps))
        await apps[0]._dispatch_reminders()
        assert effects == ["submitted"]
        with Session(storage) as session:
            assert session.get(Reminder, reminder_id).status == "uncertain"
    finally:
        for db in databases:
            await db.close()


@pytest.mark.asyncio
async def test_startup_does_not_steal_unexpired_job(storage):
    from tg_assistant.db.base import Database
    from tg_assistant.runtime import recover_interrupted_learning_jobs

    job_id = queue(storage, job_type="learn_group")
    repo = repository(storage)
    lease = repo.claim("learn_group", "live-worker", datetime.now(UTC), 900)
    driver = "sqlite+aiosqlite" if storage.dialect.name == "sqlite" else "mysql+asyncmy"
    db = Database(storage.url.set(drivername=driver).render_as_string(hide_password=False))
    try:
        async with db.session() as session:
            assert await recover_interrupted_learning_jobs(session) == 0
        with Session(storage) as session:
            assert session.get(BackgroundJob, job_id).claim_token == lease.claim_token
            assert session.get(BackgroundJob, job_id).status == "running"
    finally:
        repo.close()
        await db.close()


@pytest.mark.asyncio
async def test_task_update_rejects_stale_editor(storage):
    from sqlalchemy.orm.exc import StaleDataError

    from tg_assistant.db.base import Database
    from tg_assistant.services.tasks import TaskService

    with Session(storage) as session, session.begin():
        task = Task(title="Original")
        session.add(task)
        session.flush()
        task_id = task.id
    driver = "sqlite+aiosqlite" if storage.dialect.name == "sqlite" else "mysql+asyncmy"
    db = Database(storage.url.set(drivername=driver).render_as_string(hide_password=False))
    try:
        with pytest.raises(StaleDataError):
            async with db.session() as stale_editor:
                stale = await stale_editor.get(Task, task_id)
                async with db.session() as current_editor:
                    await TaskService().update(current_editor, task_id, title="Current owner edit")
                await TaskService().update(stale_editor, task_id, title="Obsolete edit")
                assert stale.title == "Obsolete edit"
        with Session(storage) as session:
            assert session.get(Task, task_id).title == "Current owner edit"
    finally:
        await db.close()


def test_maintenance_drains_foreign_process(tmp_path):
    service = maintenance(tmp_path / "profile")
    from tg_assistant.services.maintenance import MaintenanceBusy

    code = "from pathlib import Path; import sys; from tg_assistant.services.maintenance import MaintenanceService; service=MaintenanceService(Path(sys.argv[1]),profile_id='default'); guard=service.operation(); guard.__enter__(); print('ready',flush=True); sys.stdin.readline(); guard.__exit__(None,None,None)"
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path / "profile")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(MaintenanceBusy):
            service.acquire("restore-test", lease_seconds=30, timeout=0.1)
    finally:
        _, errors = child.communicate("finish\n", timeout=10)
        assert child.returncode == 0, errors
    service.recover()
    lease = service.acquire("restore-test", lease_seconds=30)
    assert service.validate(lease)
    service.release(lease)


def test_legacy_cli_writer_is_fenced_before_configuration(tmp_path, monkeypatch):
    from tg_assistant import cli
    from tg_assistant.config import Settings
    from tg_assistant.contracts import PublicProfile
    from tg_assistant.services.maintenance import MaintenanceBusy
    from tg_assistant.services.storage import StorageService

    settings = Settings(_env_file=None, data_dir=tmp_path / "owned")
    service = StorageService(settings)
    database = service.open(
        PublicProfile(
            profile_id="default",
            owner_id=None,
            storage_backend="sqlite",
            setup_stage="welcome",
            version=1,
        )
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    def forbidden_credentials():
        pytest.fail("Legacy writer reached credentials before checking maintenance")

    monkeypatch.setattr(cli, "SecretStore", forbidden_credentials)
    lease = service.fence.acquire("restore-test", lease_seconds=30)
    try:
        with pytest.raises(MaintenanceBusy):
            cli.sync(limit=10)
    finally:
        service.fence.release(lease)
        asyncio.run(database.close())


def test_start_running_instance_skips_migration_and_setup(tmp_path, monkeypatch):
    from tg_assistant import cli

    pid = tmp_path / "assistant.pid"
    pid.write_text("12345", encoding="ascii")
    monkeypatch.setattr(
        cli, "_runtime_files", lambda: (pid, tmp_path / "assistant.lock", tmp_path / "stop.request")
    )
    monkeypatch.setattr(cli, "_process_exists", lambda value: True)
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(admin_api_enabled=False))

    def forbidden_setup():
        pytest.fail("Running instance must not trigger migrations/setup")

    monkeypatch.setattr(cli, "_ensure_ready", forbidden_setup)
    cli.start()


def test_legacy_mysql_setup_cannot_bypass_maintenance(tmp_path, monkeypatch):
    from tg_assistant.config import Settings
    from tg_assistant.contracts import PublicProfile
    from tg_assistant.services.maintenance import MaintenanceBusy
    from tg_assistant.services.storage import StorageService
    from tg_assistant.setup import wizard

    settings = Settings(_env_file=None, data_dir=tmp_path / "owned")
    service = StorageService(settings)
    database = service.open(
        PublicProfile(
            profile_id="default",
            owner_id=None,
            storage_backend="sqlite",
            setup_stage="welcome",
            version=1,
        )
    )
    monkeypatch.setattr(wizard, "get_settings", lambda: settings)

    def forbidden_credentials():
        pytest.fail("Provisioner bypassed maintenance admission")

    monkeypatch.setattr(wizard, "SecretStore", forbidden_credentials)
    lease = service.fence.acquire("restore-test", lease_seconds=30)
    try:
        with pytest.raises(MaintenanceBusy):
            wizard.run_setup()
    finally:
        service.fence.release(lease)
        asyncio.run(database.close())


def test_storage_open_does_not_overwrite_config_during_maintenance(tmp_path):
    from tg_assistant.config import Settings
    from tg_assistant.contracts import PublicProfile
    from tg_assistant.services.maintenance import MaintenanceBusy
    from tg_assistant.services.storage import StorageService

    settings = Settings(_env_file=None, data_dir=tmp_path / "owned")
    profile = PublicProfile(
        profile_id="default",
        owner_id=None,
        storage_backend="sqlite",
        setup_stage="welcome",
        version=1,
    )
    service = StorageService(settings)
    database = service.open(profile)
    original = (settings.data_dir / "config/settings.json").read_bytes()
    contender = StorageService(settings.model_copy(update={"log_level": "DEBUG"}))
    lease = service.fence.acquire("restore-test", lease_seconds=30)
    try:
        with pytest.raises(MaintenanceBusy):
            contender.open(profile)
        assert (settings.data_dir / "config/settings.json").read_bytes() == original
    finally:
        service.fence.release(lease)
        asyncio.run(database.close())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "started,external,expected",
    [(False, True, "uncertain"), (False, False, "cancelled"), (True, True, "executing")],
)
async def test_interrupted_action_requires_reconciliation(storage, started, external, expected):
    from tg_assistant.db.base import Database
    from tg_assistant.services.actions import PendingActionService

    service = PendingActionService()
    if not hasattr(service, "recover_interrupted"):
        pytest.fail("Interrupted external actions have no durable reconciliation state")
    action_id = pending(storage)
    now = datetime.now(UTC)
    with Session(storage) as session, session.begin():
        session.execute(
            sa.update(PendingAction)
            .where(PendingAction.action_id == action_id)
            .values(
                status="executing",
                payload={"external_effect_started": external},
                execution_started_at=now if started else now - timedelta(minutes=16),
            )
        )
    driver = "sqlite+aiosqlite" if storage.dialect.name == "sqlite" else "mysql+asyncmy"
    db = Database(storage.url.set(drivername=driver).render_as_string(hide_password=False))
    try:
        async with db.session() as session:
            await service.recover_interrupted(session)
        with Session(storage) as session:
            assert session.get(PendingAction, action_id).status == expected
        async with db.session() as session:
            assert await service.claim_execution(session, action_id, 42) is False
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_action_current_read_does_not_relock_own_claim(storage):
    from tg_assistant.db.base import Database
    from tg_assistant.runtime import Application

    action_id = pending(storage)
    with Session(storage) as session, session.begin():
        session.execute(
            sa.update(PendingAction)
            .where(PendingAction.action_id == action_id)
            .values(status="confirmed")
        )
    driver = "sqlite+aiosqlite" if storage.dialect.name == "sqlite" else "mysql+asyncmy"
    db = Database(storage.url.set(drivername=driver).render_as_string(hide_password=False))
    app = object.__new__(Application)
    app.database = db
    try:
        async with db.session() as session:
            action = await session.scalar(
                sa.select(PendingAction)
                .where(PendingAction.action_id == action_id)
                .with_for_update()
            )
            await asyncio.wait_for(app._action_fence(action), timeout=1)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_inherited_child_holds_own_writer_handle(tmp_path):
    from tg_assistant.services.maintenance import MaintenanceBusy

    writer = maintenance(tmp_path / "profile")
    restorer = maintenance(tmp_path / "profile")
    entered, finish = asyncio.Event(), asyncio.Event()

    async def child_writer():
        with writer.operation():
            entered.set()
            await finish.wait()

    with writer.operation():
        child = asyncio.create_task(child_writer())
        await entered.wait()
    try:
        with pytest.raises(MaintenanceBusy):
            restorer.acquire("restore-test", timeout=0.1)
    finally:
        finish.set()
        await child
        restorer.close()
    restorer.recover()


@pytest.mark.asyncio
async def test_runtime_cancellation_wins_stale_completion(storage):
    from tg_assistant.db.base import Database
    from tg_assistant.services.jobs import LeaseLost, worker_lease

    now = datetime.now(UTC)
    job_id = queue(storage, job_type="ollama_pull")
    repo = repository(storage)
    lease = repo.claim("ollama_pull", "worker", now, 900)
    driver = "sqlite+aiosqlite" if storage.dialect.name == "sqlite" else "mysql+asyncmy"
    db = Database(storage.url.set(drivername=driver).render_as_string(hide_password=False))
    try:
        with pytest.raises(LeaseLost):
            with worker_lease(lease):
                async with db.session() as session:
                    stale = await session.get(BackgroundJob, job_id)

                    def cancel():
                        with Session(storage) as other, other.begin():
                            other.execute(
                                sa.update(BackgroundJob)
                                .where(BackgroundJob.id == job_id)
                                .values(status="cancel_requested")
                            )

                    await asyncio.to_thread(cancel)
                    stale.status = "completed"
        with Session(storage) as session:
            assert session.get(BackgroundJob, job_id).status == "cancel_requested"
    finally:
        repo.close()
        await db.close()


@pytest.mark.asyncio
async def test_flushed_worker_can_recheck_fresh_authorization(storage):
    from tg_assistant.db.base import Database
    from tg_assistant.runtime import Application
    from tg_assistant.services.jobs import worker_lease

    with Session(storage) as session, session.begin():
        session.add(TelegramChatPolicy(chat_id=-1001, allowed=True, authorization_epoch=2))
        session.add(
            TelegramChatPermission(
                chat_id=-1001, permission=PermissionName.SYNC_HISTORY.value, enabled=True
            )
        )
    job_id = queue(storage, payload={"chat_id": -1001, "authorization_epoch": 2})
    repo = repository(storage)
    lease = repo.claim("history_backfill", "worker", datetime.now(UTC), 900)
    driver = "sqlite+aiosqlite" if storage.dialect.name == "sqlite" else "mysql+asyncmy"
    db = Database(storage.url.set(drivername=driver).render_as_string(hide_password=False))
    if storage.dialect.name == "mysql":

        @sa.event.listens_for(db.engine.sync_engine, "connect")
        def short_wait(connection, record):
            cursor = connection.cursor()
            cursor.execute("SET SESSION innodb_lock_wait_timeout=1")
            cursor.close()

    app = object.__new__(Application)
    app.database = db
    try:
        with worker_lease(lease):
            async with db.session() as session:
                job = await session.get(BackgroundJob, job_id)
                job.payload = {**job.payload, "phase": "syncing"}
                await session.flush()
                assert (
                    await asyncio.wait_for(
                        app._source_fence(-1001, PermissionName.SYNC_HISTORY, 2), timeout=3
                    )
                    == 2
                )
    finally:
        repo.close()
        await db.close()


@pytest.mark.asyncio
async def test_stale_core_write_rolls_back(storage):
    from tg_assistant.db.base import Database
    from tg_assistant.services.jobs import LeaseLost, worker_lease

    queue(storage)
    repo = repository(storage)
    now = datetime.now(UTC)
    first = repo.claim("history_backfill", "first", now, 1)
    second = repo.claim("history_backfill", "second", now + timedelta(seconds=2), 900)
    assert second.claim_token != first.claim_token
    driver = "sqlite+aiosqlite" if storage.dialect.name == "sqlite" else "mysql+asyncmy"
    db = Database(storage.url.set(drivername=driver).render_as_string(hide_password=False))
    try:
        with pytest.raises(LeaseLost):
            with worker_lease(first):
                async with db.session() as session:
                    await session.execute(
                        sa.insert(BackgroundJob).values(job_type="stale_core_write", payload={})
                    )
                    assert not session.new and not session.dirty
        with Session(storage) as session:
            assert (
                session.scalar(
                    sa.select(sa.func.count())
                    .select_from(BackgroundJob)
                    .where(BackgroundJob.job_type == "stale_core_write")
                )
                == 0
            )
    finally:
        repo.close()
        await db.close()


@pytest.mark.asyncio
async def test_current_core_completion_is_allowed(storage):
    from tg_assistant.db.base import Database
    from tg_assistant.services.jobs import worker_lease

    job_id = queue(storage)
    repo = repository(storage)
    lease = repo.claim("history_backfill", "current", datetime.now(UTC), 900)
    driver = "sqlite+aiosqlite" if storage.dialect.name == "sqlite" else "mysql+asyncmy"
    db = Database(storage.url.set(drivername=driver).render_as_string(hide_password=False))
    try:
        with worker_lease(lease):
            async with db.session() as session:
                await session.execute(
                    sa.update(BackgroundJob)
                    .where(BackgroundJob.id == job_id)
                    .values(status="completed")
                )
        with Session(storage) as session:
            assert session.get(BackgroundJob, job_id).status == "completed"
    finally:
        repo.close()
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("reclaimed", [True, False])
async def test_flushed_orm_write_retains_commit_fence(storage, reclaimed):
    from tg_assistant.db.base import Database
    from tg_assistant.services.jobs import LeaseLost, worker_lease

    with Session(storage) as session, session.begin():
        session.add(TelegramChat(chat_id=8134, chat_type="group"))
    queue(storage)
    repo = repository(storage)
    now = datetime.now(UTC)
    first = repo.claim("history_backfill", "old-worker", now, 30)
    driver = "sqlite+aiosqlite" if storage.dialect.name == "sqlite" else "mysql+asyncmy"
    db = Database(storage.url.set(drivername=driver).render_as_string(hide_password=False))

    def reclaim():
        second = repo.claim("history_backfill", "new-worker", now + timedelta(seconds=31), 900)
        assert second and second.claim_token != first.claim_token

    try:
        # SQLite's flushed INSERT holds the writer lock, so reclaim precedes it.
        # MySQL permits the independent job claim after the message flush: prove
        # that exact stale-worker boundary, with no artificial ORM bookkeeping.
        if reclaimed and storage.dialect.name == "sqlite":
            reclaim()
        with pytest.raises(LeaseLost) if reclaimed else nullcontext():
            with worker_lease(first):
                async with db.session() as session:
                    session.add(
                        TelegramMessage(
                            chat_id=8134,
                            message_id=1,
                            text="Synthetic flush regression",
                            sent_at=now,
                        )
                    )
                    await session.flush()
                    assert not (session.new or session.dirty or session.deleted)
                    if reclaimed and storage.dialect.name == "mysql":
                        reclaim()
        with Session(storage) as session:
            count = session.scalar(
                sa.select(sa.func.count())
                .select_from(TelegramMessage)
                .where(TelegramMessage.chat_id == 8134)
            )
            assert count == (0 if reclaimed else 1)
    finally:
        repo.close()
        await db.close()
