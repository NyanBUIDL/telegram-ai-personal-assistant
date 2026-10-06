"""Candidate-only Telegram login. Never opens/writes runtime account.session.

Trusted native runtime must supply an atomic encrypted publisher. Its publish()
method owns session/credential/owner persistence, calls check_binding immediately
before each commit, preserves previous ciphertext on failure and never retains the
candidate object. No adapter is supplied by default: verified auth is unavailable
for activation. This module is not a browser-facing credential API.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Protocol

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import MemorySession

from ..paths import APP_NAME, current_user_sid
from .maintenance import MaintenanceBusy, MaintenanceService

API_HELP_URL = "https://my.telegram.org/apps"
API_DOCUMENTATION_URL = "https://core.telegram.org/api/obtaining_api_id"


@dataclass(frozen=True, slots=True)
class LoginStatus:
    """Sanitized progress only; never establishes onboarding/pairing authority."""
    state: str = "idle"
    code: str = "login_idle"
    retry_after: int = 0
    attempts_remaining: int = 3
    owner_id: int | None = None


@dataclass(frozen=True, slots=True)
class NativeQR:
    """Native memory only. Never serialize, log, persist or send to browser."""
    uri: str = field(repr=False)
    expires: datetime


class SessionPublication(Protocol):
    """Trusted runtime integration, not an arbitrary external callback.

    Both methods execute under maintenance.operation() and the service lock.
    publish must return literal True only after encrypted atomic commit; it must
    enforce runtime exclusive session ownership, current verified owner and call
    check_binding at commit. is_current must verify actual runtime session identity.
    Neither method may log inputs or retain raw sessions/API hash.
    """
    def publish(self, session: MemorySession, *, api_id: int, api_hash: str,
                owner_id: int, check_binding: Callable[[], None]) -> bool: ...

    def is_current(self, session: MemorySession, owner_id: int) -> bool: ...


class _Denied(Exception):
    def __init__(self, code):
        self.code = code


class _SilentLogger(logging.Logger):
    def getChild(self, suffix):
        # Telethon derives child loggers; a standalone disabled parent alone
        # does not suppress globally registered children of the same name.
        return self


def _real_client(session, api_id, api_hash):
    # Private disabled logger prevents Telethon exception/request details reaching
    # the app logs. No interactive start(), automatic flood sleep or blind retry.
    private_log = _SilentLogger("native-telegram-login", level=logging.CRITICAL + 1)
    private_log.disabled = True
    private_log.propagate = False
    private_log.addHandler(logging.NullHandler())
    return TelegramClient(session, api_id, api_hash, timeout=10,
                          request_retries=0, connection_retries=0,
                          auto_reconnect=False, flood_sleep_threshold=0,
                          raise_last_call_error=True, base_logger=private_log)


class TelegramLoginService:
    """One native event loop owns all methods/client; request_cancel is thread safe.

    profile_root/maintenance/existing_owner/publication come from current-SID
    runtime, never browser/config claims. Auth starts in MemorySession. API hash,
    phone code hash and phone remain native memory until cancelled/finished.
    Saved inputs are never provided to callers; OTP/password are not stored here.
    """
    def __init__(self, *, profile_root: Path, profile_id: str,
                 maintenance: MaintenanceService, existing_owner=None,
                 publication: SessionPublication | None = None,
                 client_factory=_real_client, sid_provider=current_user_sid,
                 request_timeout=15.0, qr_timeout=60.0, on_observed=None):
        if not (0 < request_timeout <= 30 and 0 < qr_timeout <= 120):
            raise ValueError("login_timeout_invalid")
        self._root = Path(profile_root).resolve()
        self._profile = profile_id
        self._maintenance = maintenance
        self._sid_provider = sid_provider
        self._sid = sid_provider()
        self._existing_owner = existing_owner
        self._publication = publication
        self._factory = client_factory
        self._timeout, self._qr_timeout = request_timeout, qr_timeout
        self._lock = asyncio.Lock()
        self._cancelled = Event()
        self._operation = self._qr_task = self._loop = None
        self._client = self._qr = self._native = None
        self._api_id = self._api_hash = self._phone = self._phone_hash = None
        self._status = LoginStatus()
        self._attempts = 3
        self._on_observed = on_observed
        self._blocked_until = self._checked_until = 0.0

    def status(self):
        if self._status.state == "active" and monotonic() >= self._checked_until:
            return LoginStatus("reconnect", "session_check_required")
        wait = max(0, math.ceil(self._blocked_until - monotonic()))
        if wait:
            return LoginStatus(self._status.state, "flood_wait", wait,
                               self._attempts)
        return self._status

    def native_qr(self):
        if self._native and self._native.expires > datetime.now(UTC):
            return self._native
        return None

    def _binding(self):
        if self._cancelled.is_set():
            raise _Denied("login_cancelled")
        if (self._sid_provider() != self._sid or
                self._maintenance.profile_id != self._profile or
                self._maintenance.root != self._root / "config"):
            raise _Denied("profile_binding_invalid")
        try:
            marker = json.loads((self._root / ".tg-assistant-data").read_text("utf-8"))
        except (OSError, ValueError):
            raise _Denied("profile_binding_invalid") from None
        if (marker != {"version": 1, "app": APP_NAME,
                       "profile_id": self._profile, "sid": self._sid} or
                type(marker.get("version")) is not int):
            raise _Denied("profile_binding_invalid")

    async def _call(self, coroutine):
        return await asyncio.wait_for(coroutine, self._timeout)

    def _set(self, state, code, owner=None):
        self._status = LoginStatus(state, code, attempts_remaining=self._attempts, owner_id=owner)
        if self._on_observed is not None:
            self._on_observed(self._status)
        return self.status()

    def request_cancel(self):
        """Interrupt network waits immediately from GUI thread; async cancel cleans up."""
        self._cancelled.set()
        if self._loop and not self._loop.is_closed():
            for task in (self._operation, self._qr_task):
                if task and not task.done():
                    self._loop.call_soon_threadsafe(task.cancel)

    async def _stop_qr(self):
        task, self._qr_task = self._qr_task, None
        self._native = self._qr = None
        if task and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _dispose(self):
        await self._stop_qr()
        client, self._client = self._client, None
        self._phone = self._phone_hash = None
        self._checked_until = 0
        if client is not None:
            try:
                await self._call(client.disconnect())
            except (Exception, asyncio.CancelledError):  # noqa: S110 - native exception text is secret
                pass
            finally:
                client.session.close()

    async def _error(self, exc):
        if isinstance(exc, _Denied) and exc.code == "auth_state_invalid":
            # A queued duplicate must not tear down an already verified session.
            return LoginStatus("unavailable", exc.code, attempts_remaining=self._attempts)
        if isinstance(exc, FloodWaitError):
            self._blocked_until = monotonic() + max(1, int(exc.seconds))
            return self.status()
        if isinstance(exc, SessionPasswordNeededError):
            await self._stop_qr()
            self._attempts = 3
            return self._set("password", "password_required")
        if isinstance(exc, (PhoneCodeInvalidError, PasswordHashInvalidError)):
            self._attempts -= 1
            if self._attempts > 0:
                return self._set(self._status.state, "code_invalid" if isinstance(exc, PhoneCodeInvalidError)
                                 else "password_invalid")
            code = "retry_limit"
        elif isinstance(exc, PhoneCodeExpiredError):
            code = "code_expired"
        elif isinstance(exc, PhoneNumberInvalidError):
            code = "phone_invalid"
        elif isinstance(exc, MaintenanceBusy):
            code = "maintenance_busy"
        elif isinstance(exc, _Denied):
            code = exc.code
        elif isinstance(exc, TimeoutError):
            code = "auth_timeout"
        else:
            code = "auth_failed"
        await self._dispose()
        return self._set("cancelled" if code == "login_cancelled" else "unavailable", code)

    async def _run(self, action):
        self._loop = asyncio.get_running_loop()
        async with self._lock:
            self._operation = asyncio.current_task()
            try:
                if self.status().retry_after:
                    return self.status()
                return await action()
            except asyncio.CancelledError:
                await self._dispose()
                self._api_id = self._api_hash = None
                return self._set("cancelled", "login_cancelled")
            except Exception as exc:
                return await self._error(exc)
            finally:
                self._operation = None

    def _credentials(self, api_id, api_hash):
        if api_id is None and api_hash is None:
            api_id, api_hash = self._api_id, self._api_hash
        if (type(api_id) is not int or api_id <= 0 or
                not isinstance(api_hash, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash)):
            raise _Denied("api_credentials_required")
        self._api_id, self._api_hash = api_id, api_hash

    async def _connect_candidate(self, api_id, api_hash):
        self._binding()
        self._credentials(api_id, api_hash)
        self._set("checking", "auth_checking")
        await self._dispose()
        self._attempts = 3
        self._client = self._factory(MemorySession(), self._api_id, self._api_hash)
        await self._call(self._client.connect())
        self._binding()

    async def begin_qr(self, *, api_id=None, api_hash=None):
        async def action():
            await self._connect_candidate(api_id, api_hash)
            self._qr = await self._call(self._client.qr_login())
            expiry = min(self._qr.expires, datetime.now(UTC) + timedelta(seconds=self._qr_timeout))
            if expiry <= datetime.now(UTC):
                raise _Denied("qr_expired")
            self._qr_task = asyncio.create_task(self._watch_qr(self._qr, expiry))
            # Start wait/register Telethon's update listener before presenting QR.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self._native = NativeQR(self._qr.url, expiry)
            return self._set("qr", "qr_pending")
        return await self._run(action)

    async def _watch_qr(self, qr, expiry):
        try:
            timeout = max(0.001, (expiry - datetime.now(UTC)).total_seconds())
            await asyncio.wait_for(qr.wait(timeout=timeout), timeout)
            async with self._lock:
                if self._qr is qr:
                    self._native = None
                    try:
                        await self._finish()
                    except Exception as exc:
                        await self._error(exc)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            async with self._lock:
                if self._qr is qr:
                    self._native = self._qr = None
                    if isinstance(exc, TimeoutError):
                        self._set("qr_expired", "qr_expired")
                    else:
                        await self._error(exc)

    async def submit_phone(self, phone, *, api_id=None, api_hash=None):
        async def action():
            if not isinstance(phone, str) or not re.fullmatch(r"\+[0-9]{8,15}", phone):
                raise _Denied("phone_invalid")
            await self._connect_candidate(api_id, api_hash)
            sent = await self._call(self._client.send_code_request(phone))
            self._phone, self._phone_hash = phone, sent.phone_code_hash
            return self._set("code", "code_required")
        return await self._run(action)

    async def submit_code(self, code):
        async def action():
            if self._status.state != "code" or self._client is None:
                raise _Denied("auth_state_invalid")
            self._binding()
            if not isinstance(code, str) or not re.fullmatch(r"[0-9]{3,10}", code):
                return self._set("code", "code_input_invalid")
            await self._call(self._client.sign_in(self._phone, code, phone_code_hash=self._phone_hash))
            return await self._finish()
        return await self._run(action)

    async def submit_password(self, password):
        async def action():
            if self._status.state != "password" or self._client is None:
                raise _Denied("auth_state_invalid")
            self._binding()
            if not isinstance(password, str) or not 1 <= len(password) <= 1024:
                return self._set("password", "password_input_invalid")
            await self._call(self._client.sign_in(password=password))
            return await self._finish()
        return await self._run(action)

    async def _identity(self):
        if not await self._call(self._client.is_user_authorized()):
            raise _Denied("session_revoked")
        me = await self._call(self._client.get_me())
        identity = getattr(me, "id", None)
        if (type(identity) is not int or identity <= 0 or
                getattr(me, "bot", True) is not False or getattr(me, "deleted", False)):
            raise _Denied("owner_invalid")
        return identity

    def _owner_binding(self, owner):
        self._binding()
        if self._existing_owner is None:
            raise _Denied("publication_unavailable")
        previous = self._existing_owner()
        if previous is not None and (type(previous) is not int or previous != owner):
            raise _Denied("owner_mismatch")

    async def _finish(self):
        owner = await self._identity()
        self._phone = self._phone_hash = self._native = None
        if self._publication is None:
            raise _Denied("publication_unavailable")
        with self._maintenance.operation():
            self._owner_binding(owner)
            success = self._publication.publish(
                self._client.session, api_id=self._api_id, api_hash=self._api_hash,
                owner_id=owner, check_binding=lambda: self._owner_binding(owner))
            self._owner_binding(owner)
            if success is not True or self._publication.is_current(self._client.session, owner) is not True:
                raise _Denied("publication_unavailable")
            self._checked_until = monotonic() + 30
            return self._set("active", "session_verified", owner)

    async def check_session(self):
        async def action():
            if self._client is None or self._status.state != "active":
                return self._set("reconnect", "session_check_required")
            try:
                owner = await self._identity()
                with self._maintenance.operation():
                    self._owner_binding(owner)
                    if (owner != self._status.owner_id or self._publication is None or
                            self._publication.is_current(self._client.session, owner) is not True):
                        raise _Denied("session_revoked")
                    self._checked_until = monotonic() + 30
                    return self._set("active", "session_verified", owner)
            except Exception:
                await self._dispose()
                return self._set("reconnect", "session_check_required")
        return await self._run(action)

    async def resume_existing(self, session, *, api_id=None, api_hash=None):
        """Runtime supplies a decrypted in-memory session, never a filename.

        No OTP/QR/password is resumed; current runtime binding and actual Telegram
        authorization/get_me must all agree. This method never publishes/writes.
        """
        async def action():
            try:
                if not isinstance(session, MemorySession):
                    raise _Denied("session_memory_required")
                self._binding()
                self._credentials(api_id, api_hash)
                await self._dispose()
                self._client = self._factory(session, self._api_id, self._api_hash)
                await self._call(self._client.connect())
                owner = await self._identity()
                with self._maintenance.operation():
                    self._owner_binding(owner)
                    if (self._publication is None or self._existing_owner() != owner or
                            self._publication.is_current(session, owner) is not True):
                        raise _Denied("publication_unavailable")
                    self._binding()
                    self._checked_until = monotonic() + 30
                    return self._set("active", "session_verified", owner)
            except Exception:
                await self._dispose()
                return self._set("reconnect", "session_check_required")
        return await self._run(action)

    async def cancel(self):
        self.request_cancel()
        async with self._lock:
            await self._dispose()
            self._api_id = self._api_hash = None
            return self._set("cancelled", "login_cancelled")
