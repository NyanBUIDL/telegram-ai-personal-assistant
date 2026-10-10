"""Bot borrower lifecycle over an actual account guard and disposable SQLite.

The drain double models resource ownership, never Telegram/API authentication.
"""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, event

from alembic import command
from tg_assistant.config import Settings
from tg_assistant.db.base import configure_sqlite
from tg_assistant.desktop.instance import AlreadyRunning, InstanceGuard
from tg_assistant.paths import ensure_runtime_dirs
from tg_assistant.services.maintenance import MaintenanceService

_spec = importlib.util.spec_from_file_location(
    "o04_bot_context_account_fixtures", Path(__file__).with_name("test_bot_owner_binding.py")
)
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
account = _fixtures.account


@pytest.fixture
def system(tmp_path):
    """Use the actual native selected path, unlike O03's generic store harness."""
    paths = ensure_runtime_dirs(tmp_path / "profile", profile_id="publication-profile")
    engine = create_engine("sqlite:///" + str(paths["db"] / "assistant.sqlite3"))
    event.listen(engine, "connect", configure_sqlite)
    repository = Path(__file__).resolve().parents[2]
    config = Config(str(repository / "alembic.ini"))
    config.set_main_option("script_location", str(repository / "alembic"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    fence = MaintenanceService(paths["config"], profile_id="publication-profile")
    store = _fixtures._fixtures.Store()
    adapters = []

    def make():
        from tg_assistant.services.telegram_publication import EncryptedSessionPublication

        adapter = EncryptedSessionPublication(
            engine=engine, profile_root=paths["data"], profile_id="publication-profile",
            maintenance=fence, store=store, control_directory=tmp_path / "control",
        )
        adapters.append(adapter)
        return adapter

    try:
        yield SimpleNamespace(
            engine=engine, root=paths["data"], fence=fence, store=store, make=make,
        )
    finally:
        for adapter in adapters:
            adapter.close()
        fence.close()
        engine.dispose()


class MemoryCredentials:
    def __init__(self):
        self.values = {}

    def set_password(self, service, name, value):
        self.values[service, name] = value

    def get_password(self, service, name):
        return self.values.get((service, name))

    def delete_password(self, service, name):
        del self.values[service, name]


class DrainService:
    def __init__(self, repository, credentials, **options):
        self.repository, self.credentials = repository, credentials
        self.closed = 0
        self.fail_close = False

    async def close(self):
        if self.fail_close:
            raise RuntimeError("synthetic_drain_pending")
        self.closed += 1


def compose(account):
    spec = importlib.util.find_spec("tg_assistant.desktop.bot_context")
    assert spec is not None, "Native bot composition is missing"
    from tg_assistant.desktop.bot_context import open_bot_context

    context = account.context
    context.refresh()
    settings = Settings(
        _env_file=None, data_dir=account.system.root, profile_id="publication-profile"
    )
    return open_bot_context(
        settings, engine=context.engine, fence=context.fence, account_context=context,
        _credential_backend=MemoryCredentials(), _service_factory=DrainService,
    )


def test_borrower_shares_actual_loop_and_does_not_take_second_guard(account):
    bot = compose(account)
    try:
        assert bot.runner is account.context.runner
        assert bot.service.repository.engine is account.context.engine
        assert bot.service.repository.fence is account.context.fence
        with pytest.raises(AlreadyRunning):
            InstanceGuard(account.context.publication._guard.directory).acquire()
        assert bot.service.repository.read()["enrollment"] is None
    finally:
        bot.close()
    assert bot.service.closed == 1
    assert account.context.runner.thread.is_alive()
    assert account.context.verification() is not None


def test_failed_bot_drain_retains_account_guard_and_allows_own_retry(account):
    bot = compose(account)
    bot.service.fail_close = True
    with pytest.raises(ValueError, match="bot_shutdown_pending"):
        bot.close()
    assert account.context.runner.thread.is_alive()
    with pytest.raises(AlreadyRunning):
        InstanceGuard(account.context.publication._guard.directory).acquire()
    bot.service.fail_close = False
    bot.close()
    bot.close()
    assert bot.service.closed == 1


def test_bot_cleanup_remains_possible_after_account_proof_expires(account):
    from datetime import timedelta

    bot = compose(account)
    account.now[0] += timedelta(seconds=31)
    assert account.context.verification() is None
    bot.close()
    assert bot.service.closed == 1
    assert account.context.runner.thread.is_alive()


def test_compose_refuses_unverified_account_before_credential_access(account):
    spec = importlib.util.find_spec("tg_assistant.desktop.bot_context")
    assert spec is not None, "Native bot composition is missing"
    from tg_assistant.desktop.bot_context import open_bot_context

    settings = Settings(
        _env_file=None, data_dir=account.system.root, profile_id="publication-profile"
    )
    with pytest.raises(ValueError, match="bot_context_unavailable"):
        open_bot_context(
            settings, engine=account.context.engine, fence=account.context.fence,
            account_context=account.context,
            _credential_backend=MemoryCredentials(), _service_factory=DrainService,
        )


def test_async_refresh_runs_actual_account_check_on_same_loop(account):
    bot = compose(account)
    observed = []

    async def resume():
        observed.append(asyncio.get_running_loop())

    bot.service.resume = resume
    try:
        before = account.context.service._checked_until
        bot.runner.submit(bot.refresh_async()).result(5)
        assert observed == [account.context.runner.loop]
        assert account.context.service._checked_until >= before
    finally:
        bot.close()


def test_setup_detach_keeps_both_owners_when_bot_drain_fails(account):
    from tg_assistant.desktop.setup_context import SetupContext

    bot = compose(account)
    connections = SimpleNamespace(telegram_reader=None, bot_reader=None)
    coordinator = SimpleNamespace(_verifiers={}, connections=connections)
    setup = SetupContext(coordinator, account.context.engine, account.context.fence, None)
    setup.install_telegram(account.context)
    assert hasattr(setup, "install_bot"), "Setup bot integration is missing"
    setup.install_bot(bot)
    bot.service.fail_close = True
    try:
        with pytest.raises(ValueError, match="bot_shutdown_pending"):
            setup.detach_telegram()
        assert setup.bot is bot and setup.telegram is account.context
        assert account.context.runner.thread.is_alive()
        assert set(coordinator._verifiers) == {"telegram_verified", "bot_verified", "owner_paired"}
    finally:
        bot.service.fail_close = False
        setup.detach_telegram()
    assert setup.bot is None and setup.telegram is None
    assert not coordinator._verifiers
    assert connections.telegram_reader is None and connections.bot_reader is None
