"""Trusted native Telegram ownership and measured original-age O01 evidence.

Factories are synchronous; invoke on the native setup executor after graceful
worker quiescence. Guard acquisition, not a stopped flag, proves writer ownership.
No secret or measured authority is accepted from browser/IPC/public DTOs.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import weakref
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock, RLock, Thread
from time import monotonic

from alembic.script import ScriptDirectory
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.engine import make_url

from ..config import validate_settings
from ..contracts import ConnectionStatus
from ..db.migrations import actual_revision
from ..db.models import AppSetting, TelegramAccount
from ..paths import APP_NAME, current_user_sid, resource_path
from ..security import SecretStore
from ..services.onboarding import StageVerification
from ..services.telegram_login import TelegramLoginService, _real_client
from ..services.telegram_publication import (
    MAX_CIPHER,
    EncryptedSessionPublication,
    _check_tree,
    _decode_sqlite,
    _digest,
    _material,
    _require,
)
from .instance import InstanceGuard, _assert_owned_path, _refuse_reparse, native_control_directory


def _client_matches_candidate(client, material):
    """Read current private SDK material without renewing measured evidence."""
    try:
        return (
            client is not None
            and material is not None
            and client.is_connected() is True
            and _material(client.session) == material
        )
    except Exception:
        return False


class NativeTelegramRunner:
    """A single event loop owns the complete native SDK lifetime."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = Thread(target=self._run, name="native-telegram-context", daemon=True)
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            if pending:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.close()

    def submit(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        if self.thread.is_alive():
            raise RuntimeError("telegram_shutdown_pending")


class BotAccountBinding:
    """Private, candidate-bound borrow; the account owner retains every resource.

    This handle is neither a public status DTO nor transferable writer evidence.
    Each access rechecks the owning context; account replacement requires a fresh
    borrow. Consumers must drain their bot work before the account owner closes.
    """

    __slots__ = ("_context", "_publication", "_runner", "_client", "_guard", "_proof", "_material")

    def __init__(self, context):
        try:
            with context._mutex:
                self._context, self._publication = context, context.publication
                _require(type(self._publication) is EncryptedSessionPublication)
                self._runner, self._client = context.runner, context.service._client
                self._material = _material(self._client.session)
                self._guard = self._publication._guard
                self._proof = context.verification()
                self._current_proof()
        except Exception:
            raise ValueError("bot_account_binding_unavailable") from None

    def _current_proof(self):
        try:
            context, publication = self._context, self._publication
            with context._mutex:
                _require(
                    not context._closed
                    and context._readonly_resume is False
                    and context.publication is publication
                    and publication._owns_guard is True
                    and publication._guard is self._guard
                    and type(self._guard) is InstanceGuard
                    and context.engine is publication.engine
                    and context.fence is publication.maintenance
                    and context.store is publication.store
                    and context.profile_id == publication.profile_id
                    and Path(context._root).resolve() == publication.root
                    and current_user_sid() == context._sid == publication._sid
                )
                publication._check()
                _require(
                    context.runner is self._runner
                    and type(self._runner) is NativeTelegramRunner
                    and self._runner.thread.is_alive()
                    and not self._runner.loop.is_closed()
                    and self._client is not None
                    and context.service._client is self._client
                    and not context.service._cancelled.is_set()
                    and monotonic() < context.service._checked_until
                    and _client_matches_candidate(self._client, self._material)
                )
                # This is private original-age SDK evidence. The public login
                # view and a caller-supplied owner/bool cannot establish it.
                proof = context.verification()
                _require(
                    self._proof is not None
                    and proof is not None
                    and proof.owner_id == self._proof.owner_id
                    and proof.fingerprint == self._proof.fingerprint
                    and publication.is_current_readonly(self._client.session, proof.owner_id)
                    is True
                )
                publication._check()
                # The SDK loop may dispose a candidate before it can publish a
                # disconnected observation on this context's mutex.
                _require(
                    context.service._client is self._client
                    and not context.service._cancelled.is_set()
                    and monotonic() < context.service._checked_until
                    and _client_matches_candidate(self._client, self._material)
                )
                return proof
        except Exception:
            raise ValueError("bot_account_binding_unavailable") from None

    def account_verifier(self) -> StageVerification | None:
        """Return only current owning proof, without advancing measured time."""
        try:
            return self._current_proof()
        except ValueError:
            return None

    def check_ownership(self) -> None:
        self._current_proof()

    @property
    def runner(self) -> NativeTelegramRunner:
        self._current_proof()
        return self._runner

    @property
    def owner_id(self) -> int:
        return int(self._current_proof().owner_id)

    @property
    def fingerprint(self) -> str:
        return self._current_proof().fingerprint


class TelegramContext:
    """Actual SDK checks + encrypted selected-store identity, no pairing claim."""

    def __init__(
        self,
        *,
        engine,
        profile_root,
        profile_id,
        fence,
        store,
        publication,
        client_factory=_real_client,
        now=lambda: datetime.now(UTC),
        request_timeout=15.0,
        readonly_resume=False,
    ):
        self.engine, self.fence, self.store = engine, fence, store
        self.publication = publication
        self.profile_id, self._root = profile_id, profile_root
        self._now, self._readonly_resume = now, readonly_resume
        self._sid = current_user_sid()
        self._mutex, self._closed = RLock(), False
        self._shutdown_mutex, self._released = Lock(), False
        self._checked_at = self._fingerprint = self._owner = None
        self._state = "unknown"
        self._timeout = request_timeout
        service_publication = _ReadOnlyPublication(publication) if readonly_resume else publication
        self.service = TelegramLoginService(
            profile_root=profile_root,
            profile_id=profile_id,
            maintenance=fence,
            existing_owner=self._existing_owner,
            publication=service_publication,
            client_factory=client_factory,
            request_timeout=request_timeout,
            on_observed=self._observe,
        )
        self.runner = NativeTelegramRunner()

    def _existing_owner(self):
        # Read-only: publication calls this while its actual SQL write is open.
        with self.engine.connect() as connection:
            rows = connection.execute(select(TelegramAccount.telegram_user_id).limit(2)).all()
        if len(rows) > 1:
            raise ValueError("telegram_owner_ambiguous")
        owner = rows[0][0] if rows else None
        if owner is not None and (type(owner) is not int or owner <= 0):
            raise ValueError("telegram_owner_invalid")
        return owner

    def _time(self):
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("telegram_clock_invalid")
        return value.astimezone(UTC)

    def _observe(self, status):
        # Private owning-service callback fires only after its actual SDK and
        # publication checks. Caller LoginStatus/on_changed is never evidence.
        owner = status.owner_id if status.state == "active" else None
        fingerprint = self.publication.current_fingerprint(owner) if owner else None
        with self._mutex:
            self._owner, self._fingerprint = owner, fingerprint
            self._state = "ready" if fingerprint else "disconnected"
            self._checked_at = self._time()

    async def _refresh(self):
        if self.service.status().state == "active":
            return await self.service.check_session()
        session = None
        try:
            read = (
                self.publication.read_current_readonly
                if self._readonly_resume
                else self.publication.read_current
            )
            session = read()
            raw_id = self.store.get("telegram_api_id")
            api_hash = self.store.get("telegram_api_hash")
            if not isinstance(raw_id, str) or not raw_id.isascii() or not raw_id.isdecimal():
                raise ValueError("telegram_credentials_unavailable")
            return await self.service.resume_existing(
                session, api_id=int(raw_id), api_hash=api_hash
            )
        except Exception:
            if session is not None:
                session.close()
            with self._mutex:
                self._owner = self._fingerprint = None
                self._state, self._checked_at = "disconnected", self._time()
            return None

    def refresh(self):
        """Bounded network IO; run outside GUI/O01 transactions on setup executor."""
        if self._closed:
            raise ValueError("telegram_context_closed")
        return self.runner.submit(self._refresh()).result(4 * self._timeout + 5)

    def verification(self):
        """Read-only admission/current identity; never refresh a measurement's age."""
        with self._mutex:
            owner, fingerprint, checked = self._owner, self._fingerprint, self._checked_at
            if (
                self._closed
                or self._state != "ready"
                or owner is None
                or checked is None
                or current_user_sid() != self._sid
                or not timedelta(0) <= self._time() - checked < timedelta(seconds=30)
            ):
                return None
        if self.publication.current_fingerprint(owner) != fingerprint:
            return None
        return StageVerification(
            owner_id=str(owner),
            fingerprint=hashlib.sha256(
                f"{self.profile_id}:{self._sid}:{owner}:{fingerprint}".encode()
            ).hexdigest(),
        )

    def borrow_bot_binding(self) -> BotAccountBinding:
        """Borrow native ownership of this exact verified account candidate."""
        return BotAccountBinding(self)

    def connection_status(self):
        proof = self.verification()
        with self._mutex:
            checked, state = self._checked_at, self._state
        if proof is not None:
            state, code, message = (
                "ready",
                "telegram_verified",
                "Tài khoản Telegram đã được xác minh.",
            )
        elif checked is None or self._closed or self._time() - checked >= timedelta(seconds=30):
            state, code, message = (
                "unknown",
                "telegram_check_required",
                "Cần kiểm tra lại phiên Telegram.",
            )
        else:
            state, code, message = (
                "disconnected",
                "telegram_unavailable",
                "Phiên Telegram chưa được xác minh.",
            )
        return ConnectionStatus(
            service="telegram_account",
            state=state,
            checked_at=checked,
            code=code,
            message=message,
            next_action=None if proof else "Mở cửa sổ Telegram trong ứng dụng Windows.",
            capabilities=["authenticated"] if proof else [],
        )

    def close(self):
        """Call on background executor; never release fence before SDK drain."""
        with self._shutdown_mutex:
            if self._released:
                return
            with self._mutex:
                self._closed = True
                self._owner = self._fingerprint = None
            self.service.request_cancel()
            # A timeout retains the guard; a later close may finish the drain.
            if self.runner.thread.is_alive():
                self.runner.submit(self.service.cancel()).result(self._timeout + 5)
                self.runner.stop()
            self.publication.close()
            self._released = True


def _selected_storage(settings, engine, fence):
    expected = make_url(settings.database_url("", async_driver=False))
    selected = engine.url
    fields = ("drivername", "host", "port", "username", "database")
    if settings.storage_backend == "sqlite":
        matches = (
            engine.dialect.name == "sqlite"
            and selected.database
            and Path(selected.database).resolve() == Path(expected.database).resolve()
        )
    else:
        matches = all(getattr(expected, name) == getattr(selected, name) for name in fields)
    if not matches:
        raise ValueError("telegram_storage_mismatch")
    try:
        with fence.operation(), engine.connect() as connection:
            head = ScriptDirectory(str(resource_path("alembic"))).get_current_head()
            if actual_revision(connection) != head:
                raise ValueError("telegram_storage_unavailable")
    except Exception:
        raise ValueError("telegram_storage_unavailable") from None


def open_telegram_context(settings, *, engine, fence, secret_store_factory=SecretStore):
    settings = validate_settings(settings)
    _selected_storage(settings, engine, fence)
    store = secret_store_factory()
    publisher = EncryptedSessionPublication(
        engine=engine,
        profile_root=settings.data_dir,
        profile_id=settings.profile_id,
        maintenance=fence,
        store=store,
    )
    try:
        return TelegramContext(
            engine=engine,
            profile_root=settings.data_dir,
            profile_id=settings.profile_id,
            fence=fence,
            store=store,
            publication=publisher,
        )
    except Exception:
        publisher.close()
        raise ValueError("telegram_context_unavailable") from None


class _ReadOnlyPublication:
    def __init__(self, publication):
        self.publication = publication

    def publish(self, *args, **kwargs):
        raise ValueError("telegram_probe_readonly")

    def is_current(self, session, owner_id):
        return self.publication.is_current_readonly(session, owner_id)


def open_worker_telegram_probe(settings, *, engine, fence, guard, secret_store_factory=SecretStore):
    """Setup worker only: borrow its checked actual guard, never release it."""
    settings = validate_settings(settings)
    _selected_storage(settings, engine, fence)
    store = secret_store_factory()
    publisher = EncryptedSessionPublication(
        engine=engine,
        profile_root=settings.data_dir,
        profile_id=settings.profile_id,
        maintenance=fence,
        store=store,
        _retained_guard=guard,
    )
    try:
        return TelegramContext(
            engine=engine,
            profile_root=settings.data_dir,
            profile_id=settings.profile_id,
            fence=fence,
            store=store,
            publication=publisher,
            readonly_resume=True,
        )
    except Exception:
        publisher.close()
        raise ValueError("telegram_context_unavailable") from None


class RuntimeTelegramObservation(TelegramContext):
    """Measure the existing Application client on its owning loop, never adopt it.

    Unlike the login publisher, an active runtime legitimately owns a plaintext
    SQLite working session. This separate read-only bridge verifies that path's
    ownership while preserving the publisher's deliberate plaintext refusal.
    """

    def __init__(
        self,
        settings,
        *,
        engine,
        fence,
        guard,
        store,
        now=lambda: datetime.now(UTC),
        request_timeout=15.0,
    ):
        settings = validate_settings(settings)
        _selected_storage(settings, engine, fence)
        _require(0 < request_timeout <= 30)
        _refuse_reparse(settings.data_dir)
        self._root = settings.data_dir.resolve()
        self.profile_id = settings.profile_id
        self.engine, self.fence, self.store, self._guard = engine, fence, store, guard
        self._sid, self._now, self._timeout = current_user_sid(), now, request_timeout
        self._mutex, self._closed = RLock(), False
        self._checked_at = self._fingerprint = self._owner = self._client_ref = None
        self._client_material = None
        self._state = "unknown"
        self._selection = _digest(
            [
                engine.url.drivername,
                engine.url.host,
                engine.url.port,
                engine.url.database,
                engine.url.username,
            ]
        )
        self._key = "native.telegram." + hashlib.sha256(self.profile_id.encode()).hexdigest()
        self.publication = self  # inherited evidence reader uses the safe reader below
        self._check()

    def _check(self):
        _require(not self._closed and current_user_sid() == self._sid)
        guard = self._guard
        _require(type(guard) is InstanceGuard and guard.lock and not guard.lock.closed)
        _require(not guard.lock.file.closed)
        _require(guard.directory.resolve() == native_control_directory().resolve())
        _check_tree(guard.directory)
        path_stat = (guard.directory / "runtime.lock").stat()
        handle_stat = os.fstat(guard.lock.file.fileno())
        _require(
            path_stat.st_nlink == 1
            and (path_stat.st_dev, path_stat.st_ino) == (handle_stat.st_dev, handle_stat.st_ino)
        )
        _require(
            self.fence.root == self._root / "config" and self.fence.profile_id == self.profile_id
        )
        url = self.engine.url
        _require(
            self._selection
            == _digest([url.drivername, url.host, url.port, url.database, url.username])
        )
        _refuse_reparse(self._root)
        _assert_owned_path(self._root)
        marker = self._root / ".tg-assistant-data"
        _refuse_reparse(marker)
        _assert_owned_path(marker)
        _require(marker.stat().st_nlink == 1 and marker.stat().st_size <= 4096)
        raw = json.loads(marker.read_text("utf-8"))
        _require(
            raw == dict(version=1, app=APP_NAME, profile_id=self.profile_id, sid=self._sid)
            and type(raw.get("version")) is int
        )
        _check_tree(self._root / "sessions")
        _require(not (self._root / "sessions/publication.journal.enc").exists())

    def _durable(self, owner_id):
        _require(type(owner_id) is int and owner_id > 0)
        self._check()
        with self.engine.connect() as connection:
            rows = connection.execute(select(TelegramAccount).limit(2)).mappings().all()
            _require(
                len(rows) == 1 and rows[0]["is_active"] and rows[0]["telegram_user_id"] == owner_id
            )
            marker = connection.scalar(select(AppSetting.value).where(AppSetting.key == self._key))
        EncryptedSessionPublication._validate_marker(self, marker)
        _require(marker["owner_id"] == owner_id)
        key = self.store.get("session_encryption_key")
        _require(isinstance(key, str) and len(key) == 44)
        credentials = [self.store.get("telegram_api_id"), self.store.get("telegram_api_hash")]
        raw = EncryptedSessionPublication._read(
            self, self._root / "sessions/account.session.enc", MAX_CIPHER
        )
        _require(raw is not None)
        session = _decode_sqlite(Fernet(key.encode("ascii")).decrypt(raw))
        try:
            material = _material(session)
            dc, address, port, auth_key = material
            identity = _digest(
                [
                    marker["generation"],
                    credentials,
                    dc,
                    address,
                    port,
                    base64.b64encode(auth_key).decode("ascii"),
                ]
            )
            _require(marker["identity"] == identity)
            _require(self.store.get("session_encryption_key") == key)
            self._check()
            return material, _digest(marker)
        finally:
            session.close()

    def current_fingerprint(self, owner_id):
        try:
            with self._mutex, self.fence.operation():
                return self._durable(owner_id)[1]
        except Exception:
            return None

    async def refresh(self, runtime):
        """Called only with the private actual Application on its SDK loop."""
        with self._mutex:
            self._owner = self._fingerprint = self._client_ref = None
            self._client_material = None
            self._state = "checking"
        try:
            self._check()
            user = runtime.user
            client = user.client
            _require(
                getattr(client, "loop", asyncio.get_running_loop()) is asyncio.get_running_loop()
            )
            _require(
                runtime.settings.profile_id == self.profile_id
                and runtime.settings.data_dir.resolve() == self._root
            )
            _require(client.is_connected() is True)
            _require(await asyncio.wait_for(client.is_user_authorized(), self._timeout) is True)
            me = await asyncio.wait_for(client.get_me(), self._timeout)
            owner = getattr(me, "id", None)
            _require(
                type(owner) is int
                and owner > 0
                and getattr(me, "bot", True) is False
                and not getattr(me, "deleted", False)
            )
            _require(type(user.owner_id) is int and user.owner_id == owner)
            _require(runtime.store is self.store)
            with self.fence.operation():
                material, fingerprint = self._durable(owner)
                _require(_client_matches_candidate(client, material))
                self._check()
            with self._mutex:
                self._owner, self._fingerprint, self._client_ref = (
                    owner,
                    fingerprint,
                    weakref.ref(client),
                )
                self._client_material = material
                self._state, self._checked_at = "ready", self._time()
        except Exception:
            with self._mutex:
                self._owner = self._fingerprint = self._client_ref = None
                self._client_material = None
                self._state, self._checked_at = "disconnected", self._time()
        return self.connection_status()

    def verification(self):
        try:
            with self._mutex:
                client = self._client_ref() if self._client_ref else None
                material = self._client_material
                measured = (self._owner, self._fingerprint, self._checked_at)
                if not _client_matches_candidate(client, material):
                    return None
                proof = super().verification()
                if proof is None:
                    return None
                # Durable reads cannot keep a disposed/replaced SDK candidate
                # admitted or extend the original owning measurement's age.
                self._check()
                if (
                    self._client_ref is None
                    or self._client_ref() is not client
                    or self._client_material != material
                    or measured != (self._owner, self._fingerprint, self._checked_at)
                    or self._state != "ready"
                    or not _client_matches_candidate(client, material)
                    or not timedelta(0) <= self._time() - self._checked_at < timedelta(seconds=30)
                ):
                    return None
                return proof
        except Exception:
            return None

    def close(self):
        with self._mutex:
            self._closed = True
            self._owner = self._fingerprint = self._client_ref = None
            self._client_material = None
