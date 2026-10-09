"""Private durable SQLite bot enrollment and one-use owner pairing."""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, insert, select, text, update
from sqlalchemy.pool import NullPool

from tg_assistant.db.models import AppSetting, TelegramAccount
from tg_assistant.desktop.instance import _assert_owned_path, _refuse_reparse
from tg_assistant.paths import APP_NAME, current_user_sid

HEX32 = re.compile(r"[a-f0-9]{32}\Z")
HEX64 = re.compile(r"[a-f0-9]{64}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
DECIMAL = re.compile(r"[1-9][0-9]{0,18}\Z")
MANUAL = re.compile(r"[A-Z2-7]{4}(?:-[A-Z2-7]{4}){3}\Z")
NONCE = re.compile(r"pair_[A-Za-z0-9_-]{43}\Z")
REFERENCE = re.compile(r"bot:v1:([a-f0-9]{64}):([a-f0-9]{32})\Z")


class PairingUnavailable(RuntimeError):
    pass


def require(condition):
    if not condition:
        raise PairingUnavailable("pairing_unavailable")


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def positive_id(value):
    return type(value) is int and 0 < value <= 2**63 - 1


def decimal_id(value):
    return type(value) is str and DECIMAL.fullmatch(value) and int(value) <= 2**63 - 1


def fields(value, names):
    require(type(value) is dict and set(value) == set(names.split()))


def timestamp(value):
    require(type(value) is str and len(value) <= 40)
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None and result.utcoffset() == timedelta(0))
    return result.astimezone(UTC)


@dataclass(frozen=True)
class AccountProof:
    """Private owning callback result; freshness is the callback's obligation."""

    owner_id: int
    fingerprint: str


@dataclass(frozen=True)
class NativePairingInvitation:
    """Transient native memory only. Never serialize this into public status."""

    payload: str = field(repr=False)
    manual_code: str = field(repr=False)
    generation: str
    expires_at: datetime


class PairingRepository:
    def __init__(
        self, *, engine, profile_id, profile_root, fence, check_ownership,
        account_verifier, now,
    ):
        try:
            require(type(profile_id) is str and IDENTIFIER.fullmatch(profile_id))
            self.engine, self.profile_id, self.fence = engine, profile_id, fence
            self.profile_root = Path(profile_root).absolute()
            self.check_ownership, self.account_verifier, self.now = check_ownership, account_verifier, now
            self.key = "bot." + digest(profile_id)
            self._sid = current_user_sid()
            self._reference_binding = digest(json.dumps(
                [self._sid, profile_id], ensure_ascii=False, separators=(",", ":"),
            ))
            self._publication_lock = threading.RLock()
            self._selection = self._storage_selection()
            self._binding = dict(
                profile_id=profile_id, sid_hash=digest(self._sid), storage_hash=digest(self._selection),
            )
            self._check()
            # Separate read-only connections never queue behind the writer pool.
            database_uri = (self.profile_root / "db" / "assistant.sqlite3").as_uri() + "?mode=ro"
            self._admission_engine = create_engine(
                "sqlite://", poolclass=NullPool,
                creator=lambda: sqlite3.connect(database_uri, uri=True, timeout=0),
            )
        except Exception:
            raise PairingUnavailable("pairing_unavailable") from None

    def _storage_selection(self):
        url = self.engine.url
        require(
            self.engine.dialect.name == "sqlite"
            and url.drivername in {"sqlite", "sqlite+pysqlite"}
            and url.database not in {None, "", ":memory:"}
            and not any((url.host, url.username, url.password, url.port, url.query))
        )
        database = Path(url.database).absolute()
        _refuse_reparse(database)
        require(database.resolve() == (self.profile_root / "db" / "assistant.sqlite3").resolve())
        return json.dumps([url.drivername, str(database)], separators=(",", ":"))

    def _check(self):
        self.check_ownership()
        require(current_user_sid() == self._sid)
        require(self._storage_selection() == self._selection)
        require(self.fence.root == self.profile_root / "config" and self.fence.profile_id == self.profile_id)
        for path in (self.profile_root, self.profile_root / "db", self.profile_root / "config"):
            _refuse_reparse(path)
            _assert_owned_path(path)
        marker = self.profile_root / ".tg-assistant-data"
        database = self.profile_root / "db" / "assistant.sqlite3"
        for path in (marker, database):
            _refuse_reparse(path)
            _assert_owned_path(path)
            require(path.is_file() and path.stat().st_nlink == 1)
        require(0 < marker.stat().st_size <= 4096)
        raw = json.loads(marker.read_text("utf-8"))
        require(raw == dict(version=1, app=APP_NAME, profile_id=self.profile_id, sid=self._sid))
        require(type(raw.get("version")) is int)

    def _time(self):
        result = self.now()
        require(type(result) is datetime and result.tzinfo is not None and result.utcoffset() is not None)
        return result.astimezone(UTC)

    def _empty(self):
        return dict(
            schema_version=1, publication_history_schema=1, revision=0, **self._binding,
            enrollment=None, challenge=None, pairing=None,
        )

    def _validate(self, document):
        fields(document, "schema_version publication_history_schema revision profile_id sid_hash storage_hash enrollment challenge pairing")
        require(type(document["schema_version"]) is int and document["schema_version"] == 1)
        require(type(document["publication_history_schema"]) is int and document["publication_history_schema"] == 1)
        require(type(document["revision"]) is int and 0 <= document["revision"] < 2**63 - 1)
        require(all(document[name] == value for name, value in self._binding.items()))
        require(len(json.dumps(document).encode("utf-8")) <= 16384)
        enrollment, challenge, pairing = (document[name] for name in ("enrollment", "challenge", "pairing"))
        if enrollment is not None:
            fields(enrollment, "generation credential_slot bot_id username")
            require(type(enrollment["generation"]) is str and HEX32.fullmatch(enrollment["generation"]))
            require(self._reference(enrollment["credential_slot"]) == enrollment["generation"])
            require(decimal_id(enrollment["bot_id"]))
            require(enrollment["username"] is None or (
                type(enrollment["username"]) is str
                and re.fullmatch(r"[A-Za-z0-9_]{1,32}", enrollment["username"])
            ))
        if challenge is not None:
            fields(challenge, "generation enrollment_generation owner_id account_candidate_fingerprint nonce_hash manual_hash issued_at expires_at state")
            require(enrollment is not None and challenge["enrollment_generation"] == enrollment["generation"])
            require(type(challenge["generation"]) is str and HEX32.fullmatch(challenge["generation"]))
            require(decimal_id(challenge["owner_id"]))
            for name in ("account_candidate_fingerprint", "nonce_hash", "manual_hash"):
                require(type(challenge[name]) is str and HEX64.fullmatch(challenge[name]))
            require(timestamp(challenge["expires_at"]) - timestamp(challenge["issued_at"]) == timedelta(seconds=300))
            require(challenge["state"] in {"pending", "consumed", "cancelled", "abandoned"})
            require(challenge["state"] != "consumed" or pairing is not None)
        if pairing is not None:
            fields(pairing, "generation owner_id bot_id enrollment_generation paired_at actual_update_id")
            require(enrollment is not None and pairing["enrollment_generation"] == enrollment["generation"])
            require(pairing["bot_id"] == enrollment["bot_id"] and decimal_id(pairing["owner_id"]))
            require(type(pairing["generation"]) is str and HEX32.fullmatch(pairing["generation"]))
            require(type(pairing["actual_update_id"]) is int and 0 <= pairing["actual_update_id"] <= 2**63 - 1)
            timestamp(pairing["paired_at"])
            require(challenge is None or pairing["owner_id"] == challenge["owner_id"])
        return document

    def _load(self, connection):
        row = connection.execute(select(AppSetting.value).where(AppSetting.key == self.key)).first()
        document, exists = (self._validate(copy.deepcopy(row[0])), True) if row else (self._empty(), False)
        if not exists:
            require(connection.execute(select(AppSetting.key).where(
                AppSetting.key.like("bot-pub.%"),
            ).limit(1)).first() is None)
        if document["enrollment"] is not None:
            history = self._history(connection, document["enrollment"]["credential_slot"])
            require(history is not None and history["published_revision"] <= document["revision"])
        return document, exists

    def _reference(self, reference):
        require(type(reference) is str)
        parsed = REFERENCE.fullmatch(reference)
        require(parsed is not None and parsed[1] == self._reference_binding)
        return parsed[2]

    def _history_key(self, reference):
        return "bot-pub." + digest(json.dumps(
            [self.profile_id, reference], ensure_ascii=False, separators=(",", ":"),
        ))

    def _history(self, connection, reference):
        generation = self._reference(reference)
        row = connection.execute(select(AppSetting.value).where(
            AppSetting.key == self._history_key(reference),
        )).first()
        if row is None:
            return None
        history = copy.deepcopy(row[0])
        fields(history, "schema_version profile_id sid_hash storage_hash reference_hash generation published_revision published_at")
        require(type(history["schema_version"]) is int and history["schema_version"] == 1)
        require(all(history[name] == value for name, value in self._binding.items()))
        require(history["reference_hash"] == digest(reference) and history["generation"] == generation)
        revision = history["published_revision"]
        require(type(revision) is int and 0 < revision < 2**63 - 1)
        require(timestamp(history["published_at"]).isoformat() == history["published_at"])
        return history

    @contextmanager
    def publication_guard(self):
        """Share this guard with the scoped credential adapter's deletion path.

        Its owning callback must be invalidated before restore swaps storage.
        SQL history alone cannot attest to a live RAM context after a restore.
        """
        try:
            with self._publication_lock:
                self._check()
                yield
                self._check()
        except Exception:
            raise PairingUnavailable("pairing_unavailable") from None

    def _account(self, connection):
        proof = self.account_verifier()
        require(type(proof) is AccountProof and positive_id(proof.owner_id))
        require(type(proof.fingerprint) is str and HEX64.fullmatch(proof.fingerprint))
        rows = connection.execute(select(TelegramAccount.id, TelegramAccount.telegram_user_id).where(
            TelegramAccount.is_active.is_(True),
        )).all()
        require(len(rows) == 1 and rows[0].telegram_user_id == proof.owner_id)
        return proof

    @staticmethod
    def _flag(connection, owner, paired):
        result = connection.execute(update(TelegramAccount).where(
            TelegramAccount.telegram_user_id == owner, TelegramAccount.is_active.is_(True),
        ).values(is_owner_paired=paired))
        require(result.rowcount == 1)

    def _write(self, connection, document, exists):
        revision = document["revision"]
        document["revision"] += 1
        self._validate(document)
        if exists:
            result = connection.execute(update(AppSetting).where(
                AppSetting.key == self.key, AppSetting.value["revision"].as_integer() == revision,
            ).values(value=document))
            require(result.rowcount == 1)
        else:
            connection.execute(insert(AppSetting).values(key=self.key, value=document))

    def _mutate(self, change, *, account=True):
        try:
            self._check()
            with self.publication_guard(), self.fence.operation():
                self._check()
                with self.engine.connect() as connection:
                    connection.execute(text("BEGIN IMMEDIATE"))
                    try:
                        document, exists = self._load(connection)
                        proof = self._account(connection) if account else None
                        changed, result, final_check = change(document, proof, connection)
                        if changed:
                            self._write(connection, document, exists)
                            self._check()
                            if account:
                                require(self._account(connection) == proof)
                            final_check()
                            connection.commit()
                        else:
                            connection.rollback()
                        return copy.deepcopy(result) if result is document else result
                    except BaseException:
                        connection.rollback()
                        raise
        except Exception:
            raise PairingUnavailable("pairing_unavailable") from None

    def read(self):
        return self._observe(lambda document, _connection: document)

    def read_admission(self):
        """Strict live snapshot with no pool, publication-lock or SQL busy waits.

        Filesystem, OS ownership, account callback and I/O still run synchronously;
        this method does not promise a whole-operation wall-clock deadline.
        Callers must retain/drain their owning context for executor operations.
        """
        acquired = self._publication_lock.acquire(blocking=False)
        try:
            require(acquired)
            with self.fence.operation(admission_timeout=0):
                self._check()
                with self._admission_engine.connect() as connection:
                    connection.execute(text("BEGIN"))
                    try:
                        document, _exists = self._load(connection)
                        proof = self._account(connection)
                        pairing = document["pairing"]
                        if pairing is not None:
                            require(pairing["owner_id"] == str(proof.owner_id))
                            flag = connection.scalar(select(TelegramAccount.is_owner_paired).where(
                                TelegramAccount.is_active.is_(True),
                                TelegramAccount.telegram_user_id == proof.owner_id,
                            ))
                            require(flag is True)
                        self._check()
                        require(self._account(connection) == proof)
                        return document
                    finally:
                        connection.rollback()
        except Exception:
            raise PairingUnavailable("pairing_unavailable") from None
        finally:
            if acquired:
                self._publication_lock.release()

    def _observe(self, reader):
        try:
            with self.publication_guard(), self.fence.operation():
                self._check()
                with self.engine.connect() as connection:
                    connection.execute(text("BEGIN"))
                    try:
                        document, _exists = self._load(connection)
                        result = reader(document, connection)
                        self._check()
                        return result
                    finally:
                        connection.rollback()
        except Exception:
            raise PairingUnavailable("pairing_unavailable") from None

    def current_reference(self):
        return self._observe(lambda document, _connection: (
            document["enrollment"]["credential_slot"] if document["enrollment"] else None
        ))

    def candidate_unpublished(self, reference):
        """Only meaningful with the actual owner and shared publication guard."""
        try:
            self._reference(reference)
            return self._observe(lambda document, connection: (
                self._history(connection, reference) is None
                and (document["enrollment"] is None or document["enrollment"]["credential_slot"] != reference)
            ))
        except Exception:
            raise PairingUnavailable("pairing_unavailable") from None

    def publish_enrollment(self, *, bot_id, generation, credential_slot, username=None):
        require(positive_id(bot_id))
        candidate = dict(
            bot_id=str(bot_id), generation=generation, credential_slot=credential_slot, username=username,
        )
        self._validate(dict(self._empty(), enrollment=candidate))

        def change(document, proof, connection):
            current = document["enrollment"]
            if current and current["generation"] == generation:
                require(current == candidate)
                return False, copy.deepcopy(current), lambda: None
            require(self._history(connection, credential_slot) is None)
            previous = document["pairing"]
            if previous and int(previous["owner_id"]) != proof.owner_id:
                connection.execute(update(TelegramAccount).where(
                    TelegramAccount.telegram_user_id == int(previous["owner_id"]),
                ).values(is_owner_paired=False))
            self._flag(connection, proof.owner_id, False)
            document.update(enrollment=candidate, challenge=None, pairing=None)
            connection.execute(insert(AppSetting).values(
                key=self._history_key(credential_slot),
                value=dict(
                    schema_version=1, **self._binding, reference_hash=digest(credential_slot),
                    generation=generation, published_revision=document["revision"] + 1,
                    published_at=self._time().isoformat(),
                ),
            ))
            return True, copy.deepcopy(candidate), lambda: None

        return self._mutate(change)

    def issue_challenge(self):
        def change(document, proof, connection):
            require(document["enrollment"] is not None)
            pair = document["pairing"]
            if pair and pair["owner_id"] != str(proof.owner_id):
                result = connection.execute(update(TelegramAccount).where(
                    TelegramAccount.telegram_user_id == int(pair["owner_id"]),
                ).values(is_owner_paired=False))
                require(result.rowcount in {0, 1})
                self._flag(connection, proof.owner_id, False)
                document["pairing"] = None
            issued = self._time()
            payload = "pair_" + secrets.token_urlsafe(32)
            manual = base64.b32encode(secrets.token_bytes(10)).decode("ascii")
            code = "-".join(manual[index:index + 4] for index in range(0, 16, 4))
            generation = uuid4().hex
            expires = issued + timedelta(seconds=300)
            document["challenge"] = dict(
                generation=generation, enrollment_generation=document["enrollment"]["generation"],
                owner_id=str(proof.owner_id), account_candidate_fingerprint=proof.fingerprint,
                nonce_hash=digest(payload), manual_hash=digest(code),
                issued_at=issued.isoformat(), expires_at=expires.isoformat(), state="pending",
            )
            invitation = NativePairingInvitation(payload, code, generation, expires)
            return True, invitation, lambda: require(issued <= self._time() < expires)

        return self._mutate(change)

    def consume(self, credential, *, kind, sender_id, update_id, _commit_check=None):
        require(_commit_check is None or callable(_commit_check))
        if not positive_id(sender_id) or type(update_id) is not int or not 0 <= update_id <= 2**63 - 1:
            return False
        if type(credential) is not str or type(kind) is not str:
            return False
        if kind == "nonce":
            if not NONCE.fullmatch(credential):
                return False
            decoded = base64.urlsafe_b64decode(credential[5:] + "=")
            if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).decode().rstrip("=") != credential[5:]:
                return False
        elif kind != "manual" or not MANUAL.fullmatch(credential):
            return False

        def change(document, proof, connection):
            challenge = document["challenge"]
            if challenge is None or challenge["state"] != "pending":
                return False, False, lambda: None
            issued, expires = timestamp(challenge["issued_at"]), timestamp(challenge["expires_at"])
            eligible = (
                sender_id == proof.owner_id == int(challenge["owner_id"])
                and proof.fingerprint == challenge["account_candidate_fingerprint"]
                and issued <= self._time() < expires
            )
            if not eligible or not hmac.compare_digest(digest(credential), challenge[kind + "_hash"]):
                return False, False, lambda: None
            self._flag(connection, proof.owner_id, True)
            challenge["state"] = "consumed"
            document["pairing"] = dict(
                generation=uuid4().hex, owner_id=str(proof.owner_id),
                bot_id=document["enrollment"]["bot_id"],
                enrollment_generation=document["enrollment"]["generation"],
                paired_at=self._time().isoformat(), actual_update_id=update_id,
            )
            def final_check():
                require(issued <= self._time() < expires)
                if _commit_check is not None:
                    require(_commit_check() is None)

            return True, True, final_check

        return self._mutate(change)

    def cancel_challenge(self, generation):
        require(type(generation) is str and HEX32.fullmatch(generation))

        def change(document, _proof, _connection):
            challenge = document["challenge"]
            changed = bool(challenge and challenge["generation"] == generation and challenge["state"] == "pending")
            if changed:
                challenge["state"] = "cancelled"
            return changed, document, lambda: None

        # Invalidation grants no authority and must work if account proof expires.
        return self._mutate(change, account=False)

    def abandon_pending_after_restart(self):
        def change(document, _proof, _connection):
            challenge = document["challenge"]
            changed = bool(challenge and challenge["state"] == "pending")
            if changed:
                challenge["state"] = "abandoned"
            return changed, document, lambda: None

        # Explicit lifecycle call: opening another concurrent repository is not restart.
        return self._mutate(change, account=False)

    def invalidate_enrollment(self, generation, *, reason):
        require(type(generation) is str and HEX32.fullmatch(generation))
        require(type(reason) is str and reason in {"bot_revoked", "bot_replaced", "bot_disconnected"})

        def change(document, _proof, connection):
            enrollment = document["enrollment"]
            if enrollment is None or enrollment["generation"] != generation:
                return False, document, lambda: None
            pair, challenge = document["pairing"], document["challenge"]
            owner = pair["owner_id"] if pair else challenge["owner_id"] if challenge else None
            if owner is not None:
                result = connection.execute(update(TelegramAccount).where(
                    TelegramAccount.telegram_user_id == int(owner),
                ).values(is_owner_paired=False))
                require(result.rowcount in {0, 1})
            document.update(enrollment=None, challenge=None, pairing=None)
            return True, document, lambda: None

        return self._mutate(change, account=False)
