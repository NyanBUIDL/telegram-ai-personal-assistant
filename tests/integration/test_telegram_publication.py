"""Synthetic SDK sessions, fake credentials, real independently migrated stores."""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, delete, event, insert, select, update
from telethon.crypto import AuthKey
from telethon.sessions import MemorySession

from alembic import command
from tg_assistant.db.base import configure_sqlite
from tg_assistant.db.models import AppSetting, TelegramAccount
from tg_assistant.desktop.instance import InstanceGuard
from tg_assistant.paths import APP_NAME, current_user_sid
from tg_assistant.services.maintenance import MaintenanceBusy, MaintenanceService

ROOT = Path(__file__).resolve().parents[2]
OWNER = 9007199254740993


class Store:
    def __init__(self):
        self.values = {}
        self.fail = None

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        if self.fail == name:
            self.fail = None
            raise RuntimeError("synthetic-private-canary")
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def candidate(byte=7):
    session = MemorySession()
    session.set_dc(2, "149.154.167.51", 443)
    session.auth_key = AuthKey(bytes([byte]) * 256)
    return session


@pytest.fixture(scope="module", params=["sqlite"])
def owned_engine(tmp_path_factory, request):
    tmp_path = tmp_path_factory.mktemp("o03-" + request.param)
    engine = create_engine("sqlite:///" + str(tmp_path / "selected.db"))
    event.listen(engine, "connect", configure_sqlite)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def system(tmp_path, owned_engine):
    engine = owned_engine
    # These two tables are exclusively owned by this module's disposable DB;
    # full migrations remain real, with no ORM create_all or shared-schema DDL.
    with engine.begin() as connection:
        connection.execute(delete(AppSetting))
        connection.execute(delete(TelegramAccount))
    root = tmp_path / "profile"
    root.mkdir()
    profile = "publication-profile"
    marker = root / ".tg-assistant-data"
    marker.write_text(
        json.dumps(dict(version=1, app=APP_NAME, profile_id=profile, sid=current_user_sid()))
    )
    fence = MaintenanceService(root / "config", profile_id=profile)
    store = Store()
    adapters = []

    def make(**kwargs):
        try:
            module = importlib.import_module("tg_assistant.services.telegram_publication")
        except ModuleNotFoundError:
            # Existing login default denies actual publication; RED asserts the
            # missing durable behavior, rather than an import-only failure.
            class Unavailable:
                def publish(self, *args, **kwargs):
                    return False

                def close(self):
                    pass

            return Unavailable()
        adapter = module.EncryptedSessionPublication(
            engine=engine,
            profile_root=root,
            profile_id=profile,
            maintenance=fence,
            store=store,
            control_directory=tmp_path / "control",
            **kwargs,
        )
        adapters.append(adapter)
        return adapter

    value = SimpleNamespace(
        engine=engine,
        root=root,
        marker=marker,
        fence=fence,
        store=store,
        make=make,
        control=tmp_path / "control",
    )
    try:
        yield value
    finally:
        for adapter in adapters:
            adapter.close()
        fence.close()


def publish(adapter, session=None, **kwargs):
    return adapter.publish(
        session or candidate(),
        api_id=12345,
        api_hash="a" * 32,
        owner_id=kwargs.pop("owner_id", OWNER),
        check_binding=kwargs.pop("check_binding", lambda: None),
        **kwargs,
    )


def test_fresh_actual_sqlite_format_roundtrip(system):
    adapter = system.make()
    session = candidate()
    assert publish(adapter, session) is True
    assert adapter.is_current(session, OWNER) is True
    restored = adapter.read_current()
    assert isinstance(restored, MemorySession)
    assert restored is not session and restored.auth_key.key == session.auth_key.key
    encrypted = (system.root / "sessions" / "account.session.enc").read_bytes()
    raw = Fernet(system.store.get("session_encryption_key").encode()).decrypt(encrypted)
    assert raw.startswith(b"SQLite format 3\x00")
    connection = sqlite3.connect(":memory:")
    connection.deserialize(raw)
    assert connection.execute(
        "select dc_id,server_address,port,auth_key from sessions"
    ).fetchone() == (2, session.server_address, 443, session.auth_key.key)
    connection.close()
    with system.engine.connect() as connection:
        row = connection.execute(select(TelegramAccount)).mappings().one()
        assert row["telegram_user_id"] == OWNER and row["is_owner_paired"] is False
    assert sorted(p.name for p in (system.root / "sessions").iterdir()) == ["account.session.enc"]


@pytest.mark.parametrize("owner", [None, True, False, 0, -1, OWNER + 1])
def test_invalid_or_different_owner_preserves_exact_prior(system, owner):
    adapter = system.make()
    assert publish(adapter) is True
    prior = (system.root / "sessions" / "account.session.enc").read_bytes()
    credentials = dict(system.store.values)
    with pytest.raises(RuntimeError, match="^session_publication_unavailable$"):
        publish(adapter, candidate(8), owner_id=owner)
    assert (system.root / "sessions" / "account.session.enc").read_bytes() == prior
    assert system.store.values == credentials
    assert adapter.is_current(candidate(), OWNER)


def test_same_owner_pairing_preserved_and_candidate_not_retained(system):
    import weakref

    adapter = system.make()
    assert publish(adapter)
    with system.engine.begin() as connection:
        connection.execute(update(TelegramAccount).values(is_owner_paired=True))
    session = candidate(8)
    reference = weakref.ref(session)
    session.close = lambda: pytest.fail("publisher must not close candidate")
    assert publish(adapter, session)
    assert not adapter.is_current(candidate(), OWNER)
    assert adapter.is_current(session, OWNER)
    del session
    assert reference() is None
    with system.engine.connect() as connection:
        assert connection.execute(select(TelegramAccount.is_owner_paired)).scalar_one() is True


def test_ambiguous_account_rows_refused(system):
    adapter = system.make()
    with system.engine.begin() as connection:
        connection.execute(insert(TelegramAccount).values(telegram_user_id=OWNER))
        connection.execute(insert(TelegramAccount).values(telegram_user_id=None))
    with pytest.raises(RuntimeError, match="^session_publication_unavailable$"):
        publish(adapter)
    assert not system.store.values


def test_actual_instance_guard_excludes_worker_and_closed_writer(system):
    with InstanceGuard(system.control):
        with pytest.raises(RuntimeError, match="^session_publication_unavailable$"):
            system.make()
    adapter = system.make()
    with pytest.raises(RuntimeError):
        InstanceGuard(system.control).acquire()
    adapter.close()
    with InstanceGuard(system.control):
        with pytest.raises(RuntimeError, match="^session_publication_unavailable$"):
            publish(adapter)


def test_actual_maintenance_fence_no_upgrade_and_drain(system):
    adapter = system.make()
    with system.fence.operation():
        assert publish(adapter)
        with pytest.raises(MaintenanceBusy):
            system.fence.acquire("cannot-upgrade")
    lease = system.fence.acquire("real-drain")
    try:
        with pytest.raises(RuntimeError, match="^session_publication_unavailable$"):
            publish(adapter, candidate(8))
    finally:
        system.fence.release(lease)
    assert adapter.is_current(candidate(), OWNER)


@pytest.mark.parametrize(
    "stage",
    [
        "key",
        "journal",
        "api_id",
        "api_hash",
        "ciphertext",
        "sql_owner",
        "sql_marker",
        "sql_commit",
        "retire",
    ],
)
def test_failure_boundaries_recover_previous_or_committed(system, stage, monkeypatch):
    adapter = system.make()
    if stage != "key":
        assert publish(adapter)
    prior = system.root / "sessions" / "account.session.enc"
    previous = prior.read_bytes() if prior.exists() else None
    credentials = dict(system.store.values)
    fired = []

    def fail(at):
        if at == stage and not fired:
            fired.append(at)
            raise RuntimeError("synthetic-private-canary")

    monkeypatch.setattr(adapter, "_checkpoint", fail)
    with pytest.raises(RuntimeError, match="^session_publication_unavailable$"):
        publish(adapter, candidate(8))
    assert fired
    adapter.close()
    recovered = system.make()
    recovered.reconcile()
    if stage in {"sql_commit", "retire"}:
        assert recovered.is_current(candidate(8), OWNER)
    else:
        assert (prior.read_bytes() if prior.exists() else None) == previous
        assert {k: v for k, v in system.store.values.items() if k != "session_encryption_key"} == {
            k: v for k, v in credentials.items() if k != "session_encryption_key"
        }
    assert not (system.root / "sessions" / "publication.journal.enc").exists()


@pytest.mark.parametrize(
    "stage",
    ["journal", "api_id", "api_hash", "ciphertext", "sql_owner", "sql_marker", "sql_commit"],
)
def test_crash_restart_uses_actual_sql_decision(system, stage, monkeypatch):
    class Crash(BaseException):
        pass

    adapter = system.make()
    assert publish(adapter)
    previous = (system.root / "sessions" / "account.session.enc").read_bytes()

    def crash(at):
        if at == stage:
            raise Crash()

    monkeypatch.setattr(adapter, "_checkpoint", crash)
    with pytest.raises(Crash):
        publish(adapter, candidate(9))
    adapter.close()
    recovered = system.make()
    recovered.reconcile()
    if stage == "sql_commit":
        assert recovered.is_current(candidate(9), OWNER)
    else:
        assert recovered.is_current(candidate(), OWNER)
        assert (system.root / "sessions" / "account.session.enc").read_bytes() == previous


@pytest.mark.parametrize(
    "stage",
    [
        "key",
        "journal",
        "api_id",
        "api_hash",
        "ciphertext",
        "sql_owner",
        "sql_marker",
        "sql_commit",
        "retire",
    ],
)
@pytest.mark.parametrize("change", ["sid", "profile"])
def test_binding_rechecked_after_every_durable_boundary(system, stage, change, monkeypatch):
    sid = [current_user_sid()]
    adapter = system.make(sid_provider=lambda: sid[0])

    def changed(at):
        if at == stage:
            if change == "sid":
                sid[0] = "foreign-sid"
            else:
                system.marker.write_text("{}")

    monkeypatch.setattr(adapter, "_checkpoint", changed)
    with pytest.raises(RuntimeError, match="^session_publication_unavailable$"):
        publish(adapter)
    assert not adapter.is_current(candidate(), OWNER)


@pytest.mark.parametrize(
    "damage",
    [
        "key",
        "cipher",
        "oversized",
        "journal",
        "journal_size",
        "hardlink",
        "plaintext",
        "sql_marker",
    ],
)
def test_unavailable_material_fails_closed_and_preserves(system, damage):
    adapter = system.make()
    assert publish(adapter)
    path = system.root / "sessions" / "account.session.enc"
    if damage == "key":
        system.store.delete("session_encryption_key")
    elif damage == "cipher":
        path.write_bytes(b"corrupt")
    elif damage == "oversized":
        path.write_bytes(b"x" * (512 * 1024 + 1))
    elif damage.startswith("journal"):
        (path.parent / "publication.journal.enc").write_bytes(
            b"x" * (2 * 1024 * 1024 + 1) if damage == "journal_size" else b"corrupt"
        )
    elif damage == "hardlink":
        os.link(path, path.parent / "alias")
    elif damage == "plaintext":
        (path.parent / "account.session").write_bytes(b"legacy")
    elif damage == "sql_marker":
        with system.engine.begin() as connection:
            connection.execute(update(AppSetting).values(value={"version": 999}))
    before = path.read_bytes()
    credentials = dict(system.store.values)
    assert not adapter.is_current(candidate(), OWNER)
    with pytest.raises(RuntimeError, match="^session_publication_unavailable$"):
        publish(adapter, candidate(8))
    assert path.read_bytes() == before
    assert system.store.values == credentials


def test_keyring_failure_rolls_back_exact_prior(system):
    adapter = system.make()
    assert publish(adapter)
    previous = (system.root / "sessions" / "account.session.enc").read_bytes()
    credentials = dict(system.store.values)
    system.store.fail = "telegram_api_hash"
    with pytest.raises(RuntimeError, match="^session_publication_unavailable$"):
        publish(adapter, candidate(9))
    assert system.store.values == credentials
    assert (system.root / "sessions" / "account.session.enc").read_bytes() == previous


def test_callback_refusal_no_write_no_secret_output(system, caplog):
    adapter = system.make()

    def refuse():
        raise ValueError("synthetic-private-canary")

    with pytest.raises(RuntimeError) as error:
        publish(adapter, check_binding=refuse)
    assert str(error.value) == "session_publication_unavailable"
    assert error.value.__cause__ is None
    assert not system.store.values
    assert "synthetic-private-canary" not in caplog.text


@pytest.mark.parametrize("committed", [False, True])
def test_uncertain_actual_driver_commit_reads_marker(system, monkeypatch, committed):
    adapter = system.make()
    assert publish(adapter)
    prior = (system.root / "sessions" / "account.session.enc").read_bytes()
    original = system.engine.dialect.do_commit
    fired = []

    def uncertain(connection):
        if not fired:
            fired.append(True)
            if committed:
                original(connection)
            raise RuntimeError("synthetic-private-canary")
        return original(connection)

    monkeypatch.setattr(system.engine.dialect, "do_commit", uncertain)
    with pytest.raises(RuntimeError, match="^session_publication_unavailable$"):
        publish(adapter, candidate(9))
    assert fired
    assert adapter.is_current(candidate(9) if committed else candidate(), OWNER)
    if not committed:
        assert (system.root / "sessions" / "account.session.enc").read_bytes() == prior


@pytest.mark.parametrize(
    "field,value",
    [
        ("api_id", True),
        ("api_id", 0),
        ("api_hash", "synthetic-private-canary"),
        ("dc", 0),
        ("port", True),
        ("address", "secret-url"),
        ("key", b"short"),
    ],
)
def test_candidate_and_credentials_validation_no_echo(system, field, value):
    adapter = system.make()
    session = candidate()
    api_id, api_hash = 12345, "a" * 32
    if field == "api_id":
        api_id = value
    elif field == "api_hash":
        api_hash = value
    elif field == "key":
        session.auth_key = AuthKey(value)
    else:
        session.set_dc(
            value if field == "dc" else 2,
            value if field == "address" else session.server_address,
            value if field == "port" else 443,
        )
    with pytest.raises(RuntimeError, match="^session_publication_unavailable$"):
        adapter.publish(
            session, api_id=api_id, api_hash=api_hash, owner_id=OWNER, check_binding=lambda: None
        )
    assert not system.store.values


def test_null_existing_owner_does_not_preserve_pairing(system):
    adapter = system.make()
    with system.engine.begin() as connection:
        connection.execute(
            insert(TelegramAccount).values(telegram_user_id=None, is_owner_paired=True)
        )
    assert publish(adapter)
    with system.engine.connect() as connection:
        row = connection.execute(select(TelegramAccount)).mappings().one()
        assert row["telegram_user_id"] == OWNER and row["is_owner_paired"] is False


def test_journal_foreign_identity_and_unknown_generation_preserved(system, monkeypatch):
    class Crash(BaseException):
        pass

    adapter = system.make()
    assert publish(adapter)

    def crash(stage):
        if stage == "journal":
            raise Crash()

    monkeypatch.setattr(adapter, "_checkpoint", crash)
    with pytest.raises(Crash):
        publish(adapter, candidate(9))
    path = system.root / "sessions" / "publication.journal.enc"
    crypto = Fernet(system.store.get("session_encryption_key").encode())
    journal = json.loads(crypto.decrypt(path.read_bytes()))
    original = path.read_bytes()
    journal["sid_hash"] = "0" * 64
    path.write_bytes(crypto.encrypt(json.dumps(journal).encode()))
    damaged = path.read_bytes()
    with pytest.raises(RuntimeError, match="^session_publication_unavailable$"):
        adapter.reconcile()
    assert path.read_bytes() == damaged
    path.write_bytes(original)
    with system.engine.begin() as connection:
        marker = connection.execute(select(AppSetting.value)).scalar_one()
        marker["generation"] = "0" * 32
        connection.execute(update(AppSetting).values(value=marker))
    with pytest.raises(RuntimeError, match="^session_publication_unavailable$"):
        adapter.reconcile()
    assert path.read_bytes() == original


def test_actual_ciphertext_replace_failure_exact_rollback(system, monkeypatch):
    adapter = system.make()
    assert publish(adapter)
    prior = (system.root / "sessions" / "account.session.enc").read_bytes()
    credentials = dict(system.store.values)
    original = adapter._replace
    fired = []

    def fail(source, target):
        if target == adapter.ciphertext and not fired:
            fired.append(True)
            raise OSError("synthetic-private-canary")
        return original(source, target)

    monkeypatch.setattr(adapter, "_replace", fail)
    with pytest.raises(RuntimeError, match="^session_publication_unavailable$"):
        publish(adapter, candidate(8))
    assert fired and system.store.values == credentials
    assert adapter.ciphertext.read_bytes() == prior
    assert sorted(p.name for p in adapter.directory.iterdir()) == ["account.session.enc"]


def test_control_hardlink_refused_before_guard_write(system):
    system.control.mkdir()
    target = system.control / "runtime.lock"
    target.write_bytes(b"owned-lock")
    alias = system.root / "foreign-alias"
    os.link(target, alias)
    with pytest.raises(RuntimeError, match="^session_publication_unavailable$"):
        system.make()
    assert alias.read_bytes() == b"owned-lock"


def test_portable_backup_actual_excludes_sessions_journal_and_credentials(system):
    from zipfile import ZipFile

    from tg_assistant.services.backup import BackupService

    adapter = system.make()
    assert publish(adapter)
    backups = system.root / "backups"
    backups.mkdir()
    settings = SimpleNamespace(
        data_dir=system.root,
        profile_id="publication-profile",
        storage_backend=system.engine.dialect.name,
    )
    storage = SimpleNamespace(settings=settings, _url=lambda **kwargs: system.engine.url)
    service = BackupService(storage)
    target = backups / "portable.zip"
    service.backup(target)
    with ZipFile(target) as archive:
        assert sorted(archive.namelist()) == [
            "database.sqlite3" if settings.storage_backend == "sqlite" else "database.json",
            "manifest.json",
        ]
        data = archive.read(
            "database.sqlite3" if settings.storage_backend == "sqlite" else "database.json"
        )
        assert b"native.telegram." not in data and b"account.session" not in data
        assert system.store.get("telegram_api_hash").encode() not in data
        assert system.store.get("session_encryption_key").encode() not in data


@pytest.mark.parametrize(
    "boundary", ["key", "journal_replace", "journal_fsync", "sql_owner", "sql_marker"]
)
def test_actual_storage_boundary_failures_preserve(system, monkeypatch, boundary):
    adapter = system.make()
    if boundary != "key":
        assert publish(adapter)
    prior = adapter.ciphertext.read_bytes() if adapter.ciphertext.exists() else None
    credentials = dict(system.store.values)
    fired = []
    if boundary == "key":
        system.store.fail = "session_encryption_key"
    elif boundary == "journal_replace":
        original = adapter._replace

        def replace(source, target):
            if target == adapter.journal and not fired:
                fired.append(True)
                raise OSError("synthetic-private-canary")
            return original(source, target)

        monkeypatch.setattr(adapter, "_replace", replace)
    elif boundary == "journal_fsync":
        original = os.fsync

        def fsync(descriptor):
            if not fired:
                fired.append(True)
                raise OSError("synthetic-private-canary")
            return original(descriptor)

        monkeypatch.setattr(os, "fsync", fsync)
    else:
        table = "telegram_accounts" if boundary == "sql_owner" else "app_settings"

        def refuse(connection, cursor, statement, parameters, context, executemany):
            if (
                statement.lower().startswith(("insert", "update"))
                and table in statement.lower()
                and not fired
            ):
                fired.append(True)
                raise RuntimeError("synthetic-private-canary")

        event.listen(system.engine, "before_cursor_execute", refuse)
    try:
        with pytest.raises(RuntimeError, match="^session_publication_unavailable$"):
            publish(adapter, candidate(9))
    finally:
        if boundary.startswith("sql_"):
            event.remove(system.engine, "before_cursor_execute", refuse)
    assert (adapter.ciphertext.read_bytes() if adapter.ciphertext.exists() else None) == prior
    assert system.store.values == credentials
    assert not adapter.journal.exists()


def test_actual_root_junction_or_symlink_refused(system):
    import subprocess

    from tg_assistant.services.telegram_publication import EncryptedSessionPublication

    alias = system.root.parent / "root-alias"
    if os.name == "nt":
        result = subprocess.run(
            [
                str(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"),
                "/c",
                "mklink",
                "/J",
                str(alias),
                str(system.root),
            ],
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, "Owned NTFS junction fixture unavailable"
    else:
        alias.symlink_to(system.root, target_is_directory=True)
    try:
        with pytest.raises(RuntimeError, match="^session_publication_unavailable$"):
            EncryptedSessionPublication(
                engine=system.engine,
                profile_root=alias,
                profile_id="publication-profile",
                maintenance=system.fence,
                store=system.store,
                control_directory=system.control,
            )
        assert not system.store.values
    finally:
        if os.name == "nt":
            alias.rmdir()
        else:
            alias.unlink()


def test_temporary_sdk_ram_connection_really_closed(system, monkeypatch):
    from tg_assistant.services import telegram_publication as module

    original = module.SQLiteSession
    connections = []

    def capture(*args, **kwargs):
        value = original(*args, **kwargs)
        connections.append(value._conn)
        return value

    monkeypatch.setattr(module, "SQLiteSession", capture)
    assert publish(system.make())
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("select 1")


@pytest.mark.parametrize("committed", [False, True])
def test_persistent_recovery_failure_retains_journal_until_proven(system, monkeypatch, committed):
    adapter = system.make()
    assert publish(adapter)
    original = system.store.set
    blocked = [False]

    def fail_store(name, value):
        if blocked[0] and name == "telegram_api_hash":
            raise RuntimeError("synthetic-private-canary")
        return original(name, value)

    monkeypatch.setattr(system.store, "set", fail_store)

    def fail_at_decision(stage):
        if stage == ("sql_commit" if committed else "ciphertext"):
            blocked[0] = True
            raise RuntimeError("synthetic-private-canary")

    monkeypatch.setattr(adapter, "_checkpoint", fail_at_decision)
    with pytest.raises(RuntimeError, match="^session_publication_unavailable$"):
        publish(adapter, candidate(9))
    assert adapter.journal.exists()
    journal = adapter.journal.read_bytes()
    assert not adapter.is_current(candidate(9), OWNER)
    assert adapter.journal.read_bytes() == journal
    blocked[0] = False
    adapter.reconcile()
    assert not adapter.journal.exists()
    assert adapter.is_current(candidate(9) if committed else candidate(), OWNER)
