"""Intent writes use real disposable SQLite, including cancellation and fencing."""
import asyncio
import importlib
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, insert, select

from tg_assistant.db.base import Base, configure_sqlite
from tg_assistant.db.models import AppSetting, BackgroundJob, PendingAction, TelegramChat
from tg_assistant.services.connections import ConnectionHealth
from tg_assistant.services.maintenance import MaintenanceBusy, MaintenanceService
from tg_assistant.services.onboarding import OnboardingCoordinator

A = -1009007199254740993
B = -100456


@pytest.fixture
def selection(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "selected.db"))
    event.listen(engine, "connect", configure_sqlite)
    Base.metadata.create_all(engine)
    fence = MaintenanceService(tmp_path / "config", profile_id="owner-profile")
    coordinator = OnboardingCoordinator(engine=engine, profile_id="owner-profile",
        storage_backend="sqlite", fence=fence, connections=ConnectionHealth(probes={}), verifiers={})
    current = [True]
    runtime = SimpleNamespace(settings=SimpleNamespace(profile_id="owner-profile"),
                              management_admitted=lambda: current[0], first_value=None)
    bot = SimpleNamespace(engine=engine, fence=fence, _runtime=runtime,
                          service=SimpleNamespace(verified_identity=SimpleNamespace(username="Verified_bot")))
    runtime.bot_runtime = bot
    yield SimpleNamespace(engine=engine, fence=fence, coordinator=coordinator, runtime=runtime, current=current)
    engine.dispose()
    fence.close()


def service(selection):
    try:
        module = importlib.import_module("tg_assistant.services.first_value")
    except ModuleNotFoundError:
        pytest.fail("FirstValueService atomic intent implementation missing")
    selection.runtime.first_value = module.FirstValueService(
        runtime=selection.runtime, coordinator=selection.coordinator, windows_sid="S-1-5-21-test")
    return selection.runtime.first_value


def rows(selection):
    with selection.engine.connect() as connection:
        return dict(connection.execute(select(AppSetting.key, AppSetting.value)).all())


def header(selection):
    return next(value for key, value in rows(selection).items() if key.startswith("fv1."))


@pytest.mark.parametrize("failure", [ValueError, KeyboardInterrupt])
def test_coordinator_callback_rolls_back_both_rows(selection, failure):
    assert hasattr(selection.coordinator, "save_source_selection"), "Atomic source-selection adjunct missing"
    def update(connection):
        assert connection.in_transaction()
        connection.execute(insert(AppSetting).values(key="metadata", value={"generation": 1}))
        raise failure("synthetic")
    with pytest.raises(failure):
        selection.coordinator.save_source_selection(A, transaction_update=update)
    assert rows(selection) == {}


def test_coordinator_callback_runs_even_unchanged(selection):
    assert hasattr(selection.coordinator, "save_source_selection"), "Atomic source-selection adjunct missing"
    def update(connection):
        assert connection.in_transaction()
        connection.execute(insert(AppSetting).values(key="metadata", value={"generation": 1}))
    selection.coordinator.save_source_selection(A, transaction_update=update)
    def denied(_connection):
        raise PermissionError("withdrawn")
    before = rows(selection)
    with pytest.raises(PermissionError):
        selection.coordinator.save_source_selection(A, transaction_update=denied)
    assert rows(selection) == before


@pytest.mark.parametrize("value", [True, 0, "-100123", 1.5])
def test_coordinator_rejects_non_int_source(selection, value):
    assert hasattr(selection.coordinator, "save_source_selection"), "Atomic source-selection adjunct missing"
    with pytest.raises(ValueError):
        selection.coordinator.save_source_selection(value, transaction_update=lambda _connection: None)
    assert rows(selection) == {}


async def test_select_is_only_intent_and_get_is_read_only(selection):
    owner = service(selection)
    before = rows(selection)
    status = owner.selection_status()
    assert status.source_id is None and status.test_available is False
    assert status.bot_username == "Verified_bot"
    assert rows(selection) == before
    await owner.select_source(A)
    first = header(selection)
    assert first["selection_generation"] == 1
    await owner.select_source(A)
    assert header(selection)["selection_generation"] == 1
    assert header(selection)["restore_epoch"] == first["restore_epoch"]
    await owner.select_source(B)
    await owner.select_source(A)
    assert header(selection)["selection_generation"] == 3
    status = owner.selection_status()
    assert status.source_id == A
    assert status.learning_operation is None and status.answer_operation is None
    assert status.test_available is False
    assert selection.coordinator.status().stage_evidence_ids == {}
    with selection.engine.connect() as connection:
        for model in (BackgroundJob, PendingAction, TelegramChat):
            assert connection.execute(select(model)).all() == []
    await owner.close()


@pytest.mark.parametrize("corruption", ["version", "missing_version", "null", "namespace", "revision", "generation", "extra", "oversize"])
async def test_malformed_header_denies_without_overwriting(selection, corruption):
    owner = service(selection)
    await owner.select_source(A)
    raw = header(selection)
    if corruption == "version":
        raw["schema_version"] = True
    elif corruption == "missing_version":
        raw.pop("schema_version")
    elif corruption == "null":
        raw = None
    elif corruption == "namespace":
        raw["namespace"] = "foreign"
    elif corruption == "revision":
        raw["revision"] = True
    elif corruption == "generation":
        raw["selection_generation"] = -1
    elif corruption == "extra":
        raw["unexpected"] = "synthetic-sensitive-marker"
    else:
        raw = {"oversize": "x" * 8193}
    with selection.engine.begin() as connection:
        key = next(key for key in rows(selection) if key.startswith("fv1."))
        from sqlalchemy import update
        connection.execute(update(AppSetting).where(AppSetting.key == key).values(value=raw))
    before = rows(selection)
    with pytest.raises(ValueError, match="first_value_state_invalid"):
        await owner.select_source(B)
    with pytest.raises(ValueError, match="first_value_state_invalid"):
        owner.selection_status()
    assert rows(selection) == before
    await owner.close()


async def test_loss_of_admission_after_metadata_sql_rolls_back(selection):
    owner = service(selection)
    def withdraw(_connection, _cursor, statement, parameters, _context, _many):
        if statement.startswith("INSERT INTO app_settings") and parameters[0].startswith("fv1."):
            selection.current[0] = False
    event.listen(selection.engine, "after_cursor_execute", withdraw)
    try:
        with pytest.raises(PermissionError):
            await owner.select_source(A)
        assert rows(selection) == {}
    finally:
        event.remove(selection.engine, "after_cursor_execute", withdraw)
        await owner.close()


@pytest.mark.parametrize("username", [None, "bot\n", "@foreign", "https://t.me/bot", "a" * 33])
async def test_malformed_current_identity_never_projects_link(selection, username):
    owner = service(selection)
    selection.runtime.bot_runtime.service.verified_identity.username = username
    status = owner.selection_status()
    assert status.bot_username is None and not status.test_available
    assert rows(selection) == {}
    await owner.close()


async def test_admission_checked_inside_transaction_even_unchanged(selection):
    owner = service(selection)
    await owner.select_source(A)
    before = rows(selection)
    original = selection.coordinator.save_source_selection
    def withdraw(*args, **kwargs):
        selection.current[0] = False
        return original(*args, **kwargs)
    selection.coordinator.save_source_selection = withdraw
    with pytest.raises(PermissionError):
        await owner.select_source(A)
    assert rows(selection) == before
    await owner.close()


async def test_wrong_actual_owner_and_maintenance_deny(selection):
    owner = service(selection)
    selection.runtime.bot_runtime.engine = object()
    with pytest.raises(PermissionError):
        await owner.select_source(A)
    selection.runtime.bot_runtime.engine = selection.engine
    lease = selection.fence.acquire("test", timeout=0)
    try:
        with pytest.raises(MaintenanceBusy):
            await owner.select_source(A)
        assert rows(selection) == {}
    finally:
        selection.fence.release(lease)
    await owner.close()


def test_concurrent_selections_use_locked_prior_source(selection):
    owner = service(selection)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(owner._select_source, [A, B]))
    assert header(selection)["selection_generation"] == 2
    assert selection.coordinator.options()["source_id"] in {str(A), str(B)}


async def test_repeated_cancellation_retains_real_thread_and_close(selection):
    owner = service(selection)
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
    request.cancel()
    await asyncio.sleep(0)
    request.cancel()
    closing = asyncio.create_task(owner.close())
    await asyncio.sleep(0)
    closing.cancel()
    await asyncio.sleep(0)
    closing.cancel()
    await asyncio.sleep(.01)
    assert not request.done() and not closing.done()
    assert len(owner._selection_tasks) == 1
    release.set()
    results = await asyncio.gather(request, closing, return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert rows(selection) == {}, "Close withdraws admission before a delayed mutation"
    assert not owner._selection_tasks
    with pytest.raises(PermissionError):
        await owner.select_source(B)
