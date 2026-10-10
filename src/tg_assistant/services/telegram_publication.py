"""Native encrypted publication; SQL generation is the cross-store decision.

The native owner must stop its worker gracefully before constructing this
adapter. This adapter retains the actual InstanceGuard until close; it does not
activate a worker or establish network authorization, pairing or Ready.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

import telethon
from cryptography.fernet import Fernet
from sqlalchemy import insert, select, update
from telethon.crypto import AuthKey
from telethon.sessions import MemorySession, SQLiteSession

from ..db.models import AppSetting, TelegramAccount
from ..desktop.instance import (
    InstanceGuard,
    _assert_owned_path,
    _refuse_reparse,
    _secure_owned_path,
    native_control_directory,
    secure_directory,
    secure_tree,
)
from ..paths import APP_NAME, current_user_sid

MAX_SESSION = 256 * 1024
MAX_CIPHER = 512 * 1024
MAX_JOURNAL = 2 * 1024 * 1024
ERROR = "session_publication_unavailable"
CREDS = ("telegram_api_id", "telegram_api_hash")


class PublicationUnavailable(RuntimeError):
    def __init__(self):
        super().__init__(ERROR)


def _require(condition):
    if not condition:
        raise PublicationUnavailable()


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _check_tree(root):
    """Use actual native path protections without changing ACLs during checks."""
    pending = [root]
    while pending:
        directory = pending.pop()
        _refuse_reparse(directory)
        _assert_owned_path(directory)
        _require(directory.is_dir())
        for path in directory.iterdir():
            _refuse_reparse(path)
            _assert_owned_path(path)
            if path.is_dir():
                pending.append(path)
            else:
                _require(path.is_file() and path.stat().st_nlink == 1)


def _material(session):
    _require(isinstance(session, MemorySession))
    dc, address, port = session.dc_id, session.server_address, session.port
    key = getattr(session.auth_key, "key", None)
    _require(type(dc) is int and 1 <= dc <= 100)
    _require(type(port) is int and 1 <= port <= 65535)
    _require(isinstance(address, str) and len(address) <= 64)
    ipaddress.ip_address(address)
    _require(type(key) is bytes and len(key) == 256)
    return dc, address, port, key


def _sdk_sqlite_bytes(session):
    """The sole Telethon 1.45 SQLiteSession private-connection compatibility seam.

    SQLiteSession(None) creates its real version-8 schema in RAM. Nothing is
    written to a plaintext path; fail closed for an unreviewed SDK version.
    """
    _require(telethon.__version__ == "1.45.0")
    temporary = SQLiteSession(None)
    try:
        dc, address, port, key = _material(session)
        temporary.set_dc(dc, address, port)
        temporary.auth_key = AuthKey(key)
        temporary.save()
        _require(temporary.filename == ":memory:")
        return temporary._conn.serialize()
    finally:
        # SDK close intentionally leaves :memory: connections open. Close the
        # actual connection inside this same isolated compatibility seam.
        if temporary._conn is not None:
            temporary._conn.close()
            temporary._conn = None
        temporary.close()


def _decode_sqlite(raw):
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_SESSION)
    _require(raw.startswith(b"SQLite format 3\x00"))
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(raw)
        connection.execute("PRAGMA trusted_schema=OFF")
        _require(connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)])
        _require(connection.execute("select version from version").fetchall() == [(8,)])
        _require(
            connection.execute(
                "select name from sqlite_master where type in ('view','trigger')"
            ).fetchall()
            == []
        )
        rows = connection.execute(
            "select dc_id,server_address,port,auth_key from sessions"
        ).fetchall()
        _require(len(rows) == 1)
        dc, address, port, key = rows[0]
        session = MemorySession()
        session.set_dc(dc, address, port)
        session.auth_key = AuthKey(key)
        _material(session)
        return session
    finally:
        connection.close()


class EncryptedSessionPublication:
    """Selected migrated sync Engine + current-SID store; no engine ownership.

    `control_directory` is for independently owned fixtures only; production
    omits it and uses the native per-SID directory. Public methods serialize,
    take shared maintenance admission (including when already admitted), and
    recheck SID/profile/actual owned guard before and after durable mutations.
    """

    def __init__(
        self,
        *,
        engine,
        profile_root,
        profile_id,
        maintenance,
        store,
        control_directory=None,
        sid_provider=current_user_sid,
        _retained_guard=None,
    ):
        self._guard = None
        self._owns_guard = _retained_guard is None
        self._mutex = RLock()
        try:
            _refuse_reparse(Path(profile_root))
            self.root = Path(profile_root).resolve()
            self.profile_id, self.engine = profile_id, engine
            self.maintenance, self.store = maintenance, store
            self._sid_provider, self._sid = sid_provider, sid_provider()
            _require(isinstance(profile_id, str) and 0 < len(profile_id) <= 255 and bool(self._sid))
            _require(engine.dialect.name in {"sqlite", "mysql"})
            url = engine.url
            # Exclude password/query values; fingerprint is only storage selection.
            self._selection = _digest(
                [url.drivername, url.host, url.port, url.database, url.username]
            )
            self._key = "native.telegram." + hashlib.sha256(profile_id.encode()).hexdigest()
            self.directory = self.root / "sessions"
            self.ciphertext = self.directory / "account.session.enc"
            self.journal = self.directory / "publication.journal.enc"
            if _retained_guard is not None:
                _require(control_directory is None and sid_provider is current_user_sid)
                _require(type(_retained_guard) is InstanceGuard)
                _require(_retained_guard.directory.resolve() == native_control_directory().resolve())
                self._guard = _retained_guard
            else:
                guard = InstanceGuard(control_directory)
                if guard.directory.exists():
                    secure_tree(guard.directory)
                self._guard = guard.acquire()
            self._check()
            secure_directory(self.directory)
            secure_tree(self.directory)
            self._check()
        except Exception:
            self.close()
            raise PublicationUnavailable() from None

    def close(self):
        with self._mutex:
            if self._guard and self._owns_guard:
                self._guard.close()
            self._guard = None

    def _check(self, callback=lambda: None):
        callback()
        _require(self._sid_provider() == self._sid)
        _require(self._guard and self._guard.lock and not self._guard.lock.closed)
        _require(not self._guard.lock.file.closed)
        _check_tree(self._guard.directory)
        path_stat = (self._guard.directory / "runtime.lock").stat()
        handle_stat = os.fstat(self._guard.lock.file.fileno())
        _require(
            path_stat.st_nlink == 1
            and (path_stat.st_dev, path_stat.st_ino) == (handle_stat.st_dev, handle_stat.st_ino)
        )
        _require(
            self.maintenance.root == self.root / "config"
            and self.maintenance.profile_id == self.profile_id
        )
        url = self.engine.url
        _require(
            self._selection
            == _digest([url.drivername, url.host, url.port, url.database, url.username])
        )
        _refuse_reparse(self.root)
        _assert_owned_path(self.root)
        marker = self.root / ".tg-assistant-data"
        _refuse_reparse(marker)
        _assert_owned_path(marker)
        _require(marker.stat().st_nlink == 1 and marker.stat().st_size <= 4096)
        raw = json.loads(marker.read_text("utf-8"))
        _require(
            raw == dict(version=1, app=APP_NAME, profile_id=self.profile_id, sid=self._sid)
            and type(raw.get("version")) is int
        )
        if self.directory.exists():
            _check_tree(self.directory)
            _require(
                not any(
                    (self.directory / name).exists()
                    for name in (
                        "account.session",
                        "account.session-journal",
                        "account.session-wal",
                        "account.session-shm",
                    )
                )
            )

    @contextmanager
    def _operation(self, callback=lambda: None):
        _require(self._owns_guard)
        with self._mutex:
            self._check(callback)
            with self.maintenance.operation():
                self._check(callback)
                yield
                self._check(callback)

    def _checkpoint(self, stage):
        """Named durable boundaries for isolated fault/crash injection."""

    def _mutate(self, stage, action, callback):
        self._check(callback)
        result = action()
        self._checkpoint(stage)
        self._check(callback)
        return result

    def _read(self, path, limit):
        self._check()
        if not path.exists():
            return None
        _require(0 < path.stat().st_size <= limit)
        with path.open("rb") as source:
            raw = source.read(limit + 1)
        _require(0 < len(raw) <= limit)
        self._check()
        return raw

    @staticmethod
    def _replace(source, target):
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            api = ctypes.WinDLL("kernel32", use_last_error=True)
            api.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
            api.MoveFileExW.restype = wintypes.BOOL
            _require(api.MoveFileExW(str(source), str(target), 1 | 8))
        else:
            os.replace(source, target)
            descriptor = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _write(self, target, raw, callback):
        _require(target in {self.ciphertext, self.journal})
        _require(raw is None or (type(raw) is bytes and 0 < len(raw) <= MAX_JOURNAL))
        self._check(callback)
        if raw is None:
            target.unlink(missing_ok=True)
            self._check(callback)
            return
        temporary = self.directory / (".publication-" + uuid4().hex + ".enc.tmp")
        try:
            with temporary.open("xb") as output:
                _secure_owned_path(temporary, directory=False)
                self._check(callback)
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            self._check(callback)
            self._replace(temporary, target)
            self._check(callback)
        finally:
            if temporary.exists():
                # Cleanup is also a durable mutation. On lost binding, retain
                # only encrypted staging bytes rather than write after refusal.
                self._check(callback)
                temporary.unlink()

    def _fernet(self, *, create=False, callback=lambda: None):
        key = self.store.get("session_encryption_key")
        if not key:
            _require(create and not self.ciphertext.exists() and not self.journal.exists())
            key = Fernet.generate_key().decode("ascii")
            self._mutate("key", lambda: self.store.set("session_encryption_key", key), callback)
        _require(isinstance(key, str) and len(key) == 44)
        return Fernet(key.encode("ascii")), key

    def _stable_key(self, key):
        _require(self.store.get("session_encryption_key") == key)

    def _marker(self, connection):
        query = select(AppSetting.value).where(AppSetting.key == self._key)
        if self.engine.dialect.name == "mysql":
            query = query.with_for_update()
        raw = connection.execute(query).scalar_one_or_none()
        if raw is not None:
            self._validate_marker(raw)
        return raw

    def _validate_marker(self, raw):
        _require(
            isinstance(raw, dict)
            and set(raw)
            == {
                "version",
                "profile_id",
                "sid_hash",
                "selection",
                "generation",
                "identity",
                "owner_id",
            }
        )
        _require(type(raw["version"]) is int and raw["version"] == 1)
        _require(raw["profile_id"] == self.profile_id and raw["selection"] == self._selection)
        _require(raw["sid_hash"] == hashlib.sha256(self._sid.encode()).hexdigest())
        _require(type(raw["owner_id"]) is int and raw["owner_id"] > 0)
        _require(
            isinstance(raw["generation"], str) and re.fullmatch(r"[a-f0-9]{32}", raw["generation"])
        )
        _require(
            isinstance(raw["identity"], str) and re.fullmatch(r"[a-f0-9]{64}", raw["identity"])
        )

    def _owner(self, connection, owner=None):
        query = select(TelegramAccount).order_by(TelegramAccount.id).limit(2)
        if self.engine.dialect.name == "mysql":
            query = query.with_for_update()
        rows = connection.execute(query).mappings().all()
        _require(len(rows) <= 1)
        row = rows[0] if rows else None
        if row and row["telegram_user_id"] is not None:
            _require(type(row["telegram_user_id"]) is int and row["telegram_user_id"] > 0)
            _require(owner is None or row["telegram_user_id"] == owner)
        return row

    @contextmanager
    def _transaction(self):
        with self.engine.connect() as connection:
            if self.engine.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.begin()
            try:
                yield connection
            finally:
                if connection.in_transaction():
                    connection.rollback()

    def _credentials(self):
        result = [self.store.get(name) for name in CREDS]
        _require(
            all(
                value is None or (isinstance(value, str) and 0 < len(value) <= 128)
                for value in result
            )
        )
        return result

    @staticmethod
    def _encoded(raw):
        return None if raw is None else base64.b64encode(raw).decode("ascii")

    @staticmethod
    def _unencoded(raw):
        if raw is None:
            return None
        _require(isinstance(raw, str) and 0 < len(raw) <= MAX_CIPHER * 2)
        result = base64.b64decode(raw, validate=True)
        _require(0 < len(result) <= MAX_CIPHER)
        return result

    def _identity(self, marker, credentials, session):
        dc, address, port, key = _material(session)
        return _digest([marker["generation"], credentials, dc, address, port, self._encoded(key)])

    def _validate_journal(self, journal, crypto):
        _require(
            isinstance(journal, dict)
            and set(journal)
            == {"version", "profile_id", "sid_hash", "selection", "previous", "new"}
        )
        _require(type(journal["version"]) is int and journal["version"] == 1)
        _require(
            journal["profile_id"] == self.profile_id and journal["selection"] == self._selection
        )
        _require(journal["sid_hash"] == hashlib.sha256(self._sid.encode()).hexdigest())
        for name in ("previous", "new"):
            state = journal[name]
            _require(
                isinstance(state, dict) and set(state) == {"marker", "credentials", "ciphertext"}
            )
            _require(isinstance(state["credentials"], list) and len(state["credentials"]) == 2)
            _require(
                all(
                    value is None or (isinstance(value, str) and 0 < len(value) <= 128)
                    for value in state["credentials"]
                )
            )
            marker = state["marker"]
            raw = self._unencoded(state["ciphertext"])
            if marker is not None:
                self._validate_marker(marker)
                _require(raw is not None)
            if raw is not None:
                session = _decode_sqlite(crypto.decrypt(raw))
                if marker is not None:
                    _require(
                        marker["identity"] == self._identity(marker, state["credentials"], session)
                    )
        _require(journal["new"]["marker"] is not None)
        _require(journal["new"]["marker"] != journal["previous"]["marker"])

    def _recover(self, callback):
        self._check(callback)
        raw = self._read(self.journal, MAX_JOURNAL)
        if raw is None:
            return
        crypto, key = self._fernet()
        plain = crypto.decrypt(raw)
        _require(len(plain) <= MAX_JOURNAL)
        journal = json.loads(plain)
        self._validate_journal(journal, crypto)
        # The lock and maintenance handle stay owned throughout reconciliation.
        with self._transaction() as connection:
            marker = self._marker(connection)
            previous, new = journal["previous"], journal["new"]
            _require(marker == previous["marker"] or marker == new["marker"])
            state = new if marker == new["marker"] else previous
            row = self._owner(connection)
            if marker is not None:
                _require(row and row["telegram_user_id"] == marker["owner_id"] and row["is_active"])

            def checked():
                self._check(callback)
                self._stable_key(key)
                _require(self._marker(connection) == marker)

            for name, value in zip(CREDS, state["credentials"], strict=True):
                checked()
                if value is None:
                    self.store.delete(name)
                else:
                    self.store.set(name, value)
                checked()
            checked()
            self._write(self.ciphertext, self._unencoded(state["ciphertext"]), checked)
            checked()
            _require(self._credentials() == state["credentials"])
            _require(
                self._read(self.ciphertext, MAX_CIPHER) == self._unencoded(state["ciphertext"])
            )
            self._mutate("retire", lambda: self._write(self.journal, None, checked), checked)

    def reconcile(self, *, check_binding=lambda: None):
        try:
            with self._operation(check_binding):
                self._recover(check_binding)
            return True
        except Exception:
            raise PublicationUnavailable() from None

    def publish(self, session, *, api_id, api_hash, owner_id, check_binding):
        try:
            _require(type(owner_id) is int and owner_id > 0)
            _require(type(api_id) is int and 0 < api_id <= 2147483647)
            _require(isinstance(api_hash, str) and re.fullmatch(r"[a-fA-F0-9]{32}", api_hash))
            _material(session)
            with self._operation(check_binding):
                self._recover(check_binding)
                with self._transaction() as connection:
                    row = self._owner(connection, owner_id)
                    previous_marker = self._marker(connection)
                    previous_cipher = self._read(self.ciphertext, MAX_CIPHER)
                    previous_credentials = self._credentials()
                    if previous_marker is not None:
                        _require(previous_marker["owner_id"] == owner_id)
                        _require(previous_cipher is not None)
                    crypto, key = self._fernet(
                        create=previous_cipher is None, callback=check_binding
                    )
                    if previous_cipher is not None:
                        prior_session = _decode_sqlite(crypto.decrypt(previous_cipher))
                        if previous_marker is not None:
                            _require(
                                previous_marker["identity"]
                                == self._identity(
                                    previous_marker, previous_credentials, prior_session
                                )
                            )
                    sqlite_bytes = _sdk_sqlite_bytes(session)
                    _require(_material(_decode_sqlite(sqlite_bytes)) == _material(session))
                    cipher = crypto.encrypt(sqlite_bytes)
                    credentials = [str(api_id), api_hash]
                    marker = dict(
                        version=1,
                        profile_id=self.profile_id,
                        sid_hash=hashlib.sha256(self._sid.encode()).hexdigest(),
                        selection=self._selection,
                        generation=uuid4().hex,
                        identity="",
                        owner_id=owner_id,
                    )
                    marker["identity"] = self._identity(marker, credentials, session)
                    journal = dict(
                        version=1,
                        profile_id=self.profile_id,
                        sid_hash=marker["sid_hash"],
                        selection=self._selection,
                        previous=dict(
                            marker=previous_marker,
                            credentials=previous_credentials,
                            ciphertext=self._encoded(previous_cipher),
                        ),
                        new=dict(
                            marker=marker, credentials=credentials, ciphertext=self._encoded(cipher)
                        ),
                    )
                    self._validate_journal(journal, crypto)
                    expected = [previous_marker]

                    def checked():
                        self._check(check_binding)
                        self._stable_key(key)
                        _require(self._marker(connection) == expected[0])
                        self._owner(connection, owner_id)

                    self._mutate(
                        "journal",
                        lambda: self._write(
                            self.journal,
                            crypto.encrypt(json.dumps(journal, separators=(",", ":")).encode()),
                            checked,
                        ),
                        checked,
                    )
                    for name, value, stage in zip(
                        CREDS, credentials, ("api_id", "api_hash"), strict=True
                    ):
                        self._mutate(
                            stage,
                            lambda name=name, value=value: self.store.set(name, value),
                            checked,
                        )
                    self._mutate(
                        "ciphertext", lambda: self._write(self.ciphertext, cipher, checked), checked
                    )
                    values = dict(
                        telegram_user_id=owner_id,
                        is_active=True,
                        last_authenticated_at=datetime.now(UTC),
                        is_owner_paired=bool(
                            row and row["telegram_user_id"] == owner_id and row["is_owner_paired"]
                        ),
                    )
                    checked()
                    self._mutate(
                        "sql_owner",
                        lambda: connection.execute(
                            update(TelegramAccount)
                            .where(TelegramAccount.id == row["id"])
                            .values(**values)
                            if row
                            else insert(TelegramAccount).values(**values)
                        ),
                        checked,
                    )
                    checked()
                    connection.execute(
                        update(AppSetting).where(AppSetting.key == self._key).values(value=marker)
                        if previous_marker
                        else insert(AppSetting).values(key=self._key, value=marker)
                    )
                    expected[0] = marker
                    self._checkpoint("sql_marker")
                    checked()
                    checked()
                    try:
                        connection.commit()
                    except Exception:
                        # SQLAlchemy can mark a transaction inactive even when
                        # a failed DBAPI commit leaves it physically open. Never
                        # let recovery observe that connection's uncommitted
                        # marker: discard it, then read the real decision afresh.
                        connection.invalidate()
                        raise
                    self._checkpoint("sql_commit")
                    self._check(check_binding)
                # Read the actual committed SQL marker, including uncertain
                # commits. No network retry or synthesized authorization here.
                self._recover(check_binding)
                _require(self._current(session, owner_id))
                self._check(check_binding)
                return True
        except Exception:
            # Recovery itself is fenced. If unavailable, retain encrypted journal
            # and report a fixed error; a later owned restart must reconcile.
            try:
                with self._operation(check_binding):
                    self._recover(check_binding)
            except Exception:  # noqa: S110 - never log credential-bearing exceptions
                pass
            raise PublicationUnavailable() from None

    def _current(self, session, owner_id):
        _require(type(owner_id) is int and owner_id > 0)
        with self._transaction() as connection:
            row = self._owner(connection, owner_id)
            marker = self._marker(connection)
            _require(row and row["is_active"] and row["telegram_user_id"] == owner_id)
            _require(marker and marker["owner_id"] == owner_id)
            crypto, _ = self._fernet()
            raw = self._read(self.ciphertext, MAX_CIPHER)
            _require(raw is not None)
            restored = _decode_sqlite(crypto.decrypt(raw))
            _require(_material(restored) == _material(session))
            _require(marker["identity"] == self._identity(marker, self._credentials(), restored))
            self._check()
            return True

    def is_current(self, session, owner_id):
        try:
            with self._operation():
                self._recover(lambda: None)
                return self._current(session, owner_id)
        except Exception:
            return False

    def read_current(self):
        """Bounded native MemorySession only; caller must verify via SDK network.

        Credentials are intentionally not returned through a status DTO. The
        native factory supplies them directly from its approved SecretStore.
        """
        try:
            with self._operation():
                self._recover(lambda: None)
                crypto, _ = self._fernet()
                raw = self._read(self.ciphertext, MAX_CIPHER)
                _require(raw is not None)
                session = _decode_sqlite(crypto.decrypt(raw))
                with self.engine.connect() as connection:
                    marker = self._marker(connection)
                _require(marker and self._current(session, marker["owner_id"]))
                return session
        except Exception:
            raise PublicationUnavailable() from None

    def _readonly_current(self, owner_id=None):
        """No SQL write lock/recovery: safe from an O01 transaction verifier."""
        self._check()
        _require(not self.journal.exists())
        with self.engine.connect() as connection:
            rows = connection.execute(select(TelegramAccount).limit(2)).mappings().all()
            _require(len(rows) == 1)
            row = rows[0]
            owner = row["telegram_user_id"]
            _require(type(owner) is int and owner > 0 and row["is_active"])
            _require(owner_id is None or owner_id == owner)
            marker = connection.scalar(select(AppSetting.value).where(AppSetting.key == self._key))
        self._validate_marker(marker)
        _require(marker["owner_id"] == owner)
        crypto, key = self._fernet()
        raw = self._read(self.ciphertext, MAX_CIPHER)
        _require(raw is not None)
        session = _decode_sqlite(crypto.decrypt(raw))
        _require(marker["identity"] == self._identity(marker, self._credentials(), session))
        self._stable_key(key)
        self._check()
        _require(not self.journal.exists())
        return session, _digest(marker)

    def current_fingerprint(self, owner_id):
        """Private native durable identity only; does not prove SDK authorization."""
        try:
            _require(type(owner_id) is int and owner_id > 0)
            with self._mutex, self.maintenance.operation():
                session, fingerprint = self._readonly_current(owner_id)
                session.close()
                return fingerprint
        except Exception:
            return None

    def read_current_readonly(self):
        """Worker borrows its real guard; pending reconciliation is unavailable."""
        try:
            with self._mutex, self.maintenance.operation():
                return self._readonly_current()[0]
        except Exception:
            raise PublicationUnavailable() from None

    def is_current_readonly(self, session, owner_id):
        try:
            _require(type(owner_id) is int and owner_id > 0)
            with self._mutex, self.maintenance.operation():
                restored, _ = self._readonly_current(owner_id)
                result = _material(restored) == _material(session)
                restored.close()
                return result
        except Exception:
            return False
