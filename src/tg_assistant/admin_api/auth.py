from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Request

from ..contracts import LaunchTicket
from ..security import SecretStore

LOGIN_CODE_PERIOD_SECONDS = 300
SESSION_COOKIE = "tg_admin_session"


def ensure_dashboard_secret(store: SecretStore) -> str:
    value = store.get("admin_dashboard_secret")
    if value:
        return value
    value = secrets.token_urlsafe(48)
    store.set("admin_dashboard_secret", value)
    return value


def dashboard_login_code(secret: str, *, at: float | None = None) -> str:
    moment = time.time() if at is None else at
    period = int(moment // LOGIN_CODE_PERIOD_SECONDS)
    digest = hmac.new(
        secret.encode("utf-8"),
        f"telegram-ai-dashboard:{period}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{int.from_bytes(digest[:8], 'big') % 100_000_000:08d}"


def login_code_expires_at(*, at: float | None = None) -> datetime:
    moment = time.time() if at is None else at
    next_period = (int(moment // LOGIN_CODE_PERIOD_SECONDS) + 1) * LOGIN_CODE_PERIOD_SECONDS
    return datetime.fromtimestamp(next_period, tz=UTC)


@dataclass(frozen=True, slots=True)
class AdminSession:
    token: str = field(repr=False)
    owner_id: int | None
    csrf_token: str = field(repr=False)
    created_at: datetime
    expires_at: datetime
    profile_id: str = "default"
    authority: str = "management"

    @property
    def session_id(self):
        return self.token


class LegacyCodeReplayStore:
    """Bounded profile/SID replay state for the advanced standalone fallback.

    A single high-water period forbids clock rollback. An unknown secret
    fingerprint or malformed state fails closed rather than resetting authority.
    Native desktop HTTP fallback never uses this store to gain access.
    """

    def __init__(self, config_directory: Path, *, profile_id: str):
        self.directory = config_directory
        self.profile_id = profile_id

    def consume(self, secret: str, period: int) -> bool:
        from ..desktop.instance import (
            _assert_owned_path,
            _refuse_reparse,
            secure_directory,
            secure_tree,
        )
        from ..paths import current_user_sid
        from ..services.maintenance import FileLock, MaintenanceBusy

        state_path = self.directory / "admin-login-code-consumed.json"
        lock_path = self.directory / "admin-login-code-consumed.lock"
        temporary = None

        def private_file(path):
            _refuse_reparse(path)
            if path.exists():
                if not path.is_file() or path.stat().st_nlink != 1:
                    raise OSError("replay_state_denied")
                _assert_owned_path(path)

        try:
            if type(period) is not int or not 0 <= period < 2**63:
                return False
            _refuse_reparse(self.directory)
            _assert_owned_path(self.directory.parent)
            # An inheritable DACL affects every existing descendant, including
            # outside hardlink aliases. Preflight the entire affected tree first.
            if self.directory.exists():
                secure_tree(self.directory)
            else:
                secure_directory(self.directory)
            private_file(lock_path)
            with FileLock(lock_path, exclusive=True, timeout=0.5):
                private_file(lock_path)
                private_file(state_path)
                binding = {
                    "version": 1, "profile_id": self.profile_id,
                    "windows_sid": current_user_sid(),
                    "secret_fingerprint": hashlib.sha256(secret.encode("utf-8")).hexdigest(),
                }
                if state_path.exists():
                    with state_path.open(encoding="utf-8") as stream:
                        raw = stream.read(4097)
                    if len(raw.encode("utf-8")) > 4096:
                        return False
                    state = json.loads(raw)
                    if not isinstance(state, dict) or set(state) != {*binding, "consumed_period"}:
                        return False
                    if any(state[key] != value or type(state[key]) is not type(value) for key, value in binding.items()):
                        return False
                    consumed = state["consumed_period"]
                    if type(consumed) is not int or not 0 <= consumed < 2**63 or period <= consumed:
                        return False
                temporary = self.directory / (".admin-code-" + uuid4().hex)
                with temporary.open("x", encoding="utf-8") as stream:
                    json.dump({**binding, "consumed_period": period}, stream, separators=(",", ":"))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, state_path)
                return True
        except (OSError, ValueError, RecursionError, MaintenanceBusy):
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


class AdminAuth:
    def __init__(self, secret: str, *, session_minutes: int = 480, replay_store=None) -> None:
        self.secret = secret
        self.session_ttl = timedelta(minutes=session_minutes)
        self.sessions: dict[str, AdminSession] = {}
        self.login_attempts: defaultdict[str, deque[float]] = defaultdict(deque)
        self._codes_used: set[int] = set()
        self._lock = threading.RLock()
        self._replay_store = replay_store

    def validate_login_code(self, value: str, *, at: float | None = None) -> bool:
        code = "".join(character for character in value if character.isdigit())
        if len(code) != 8:
            return False
        period = int((time.time() if at is None else at) // LOGIN_CODE_PERIOD_SECONDS)
        with self._lock:
            if period in self._codes_used or not hmac.compare_digest(code, dashboard_login_code(self.secret, at=at)):
                return False
            if self._replay_store is not None and not self._replay_store.consume(self.secret, period):
                return False
            self._codes_used.add(period)
            self._codes_used.intersection_update({period})
            return True

    def allow_login_attempt(self, client: str, *, at: float | None = None) -> bool:
        moment = time.time() if at is None else at
        attempts = self.login_attempts[client]
        while attempts and attempts[0] <= moment - 60:
            attempts.popleft()
        if len(attempts) >= 5:
            return False
        attempts.append(moment)
        return True

    def create_session(self, owner_id: int) -> AdminSession:
        now = datetime.now(UTC)
        session = AdminSession(
            token=secrets.token_urlsafe(48),
            owner_id=owner_id,
            csrf_token=secrets.token_urlsafe(32),
            created_at=now,
            expires_at=now + self.session_ttl,
        )
        self.sessions[session.token] = session
        return session

    def get_session(self, token: str | None) -> AdminSession | None:
        if not token:
            return None
        session = self.sessions.get(token)
        if not session:
            return None
        if session.expires_at <= datetime.now(UTC):
            self.sessions.pop(token, None)
            return None
        return session

    def revoke_session(self, token: str | None) -> None:
        if token:
            self.sessions.pop(token, None)

    def cleanup(self) -> None:
        now = datetime.now(UTC)
        expired = [token for token, session in self.sessions.items() if session.expires_at <= now]
        for token in expired:
            self.sessions.pop(token, None)


def is_loopback_origin(value: str | None, *, port: int) -> bool:
    if not value:
        return True
    parsed = urlsplit(value)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    allowed = {
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        f"http://[::1]:{port}",
    }
    return origin in allowed


class DashboardTicketService(AdminAuth):
    """Process-local, hash-only, bounded tickets. Only the native pipe issues them.

    Authority is supplied by the gateway's verified assistant lifecycle, never
    by a browser field, persisted owner UUID or the existence of a cookie.
    """

    def __init__(self, *, profile_id: str, windows_sid: str, origin: str, session_minutes=480):
        super().__init__("", session_minutes=session_minutes)
        self.profile_id, self.windows_sid, self.origin = profile_id, windows_sid, origin
        self._tickets: dict[str, tuple[datetime, str, str, int]] = {}
        self._verified_owner = None
        self._generation = 0
        self._available = True
        self.before_authority = None

    def _reconcile_authority(self):
        if self.before_authority is None:
            return True
        reconciled = False
        try:
            reconciled = self.before_authority() is None
        except Exception:
            reconciled = False
        if not reconciled:
            self.invalidate()
            self._verified_owner = None
        return reconciled

    def shutdown(self):
        with self._lock:
            self._available = False
            self.invalidate()

    def set_verified_owner(self, owner_id: int | None):
        if owner_id is not None and (type(owner_id) is not int or owner_id <= 0):
            raise ValueError("owner_invalid")
        with self._lock:
            if owner_id != self._verified_owner:
                self._verified_owner = owner_id
                self.invalidate()

    def invalidate(self):
        with self._lock:
            self._generation += 1
            self._tickets.clear()
            self.sessions.clear()

    def issue(self, profile_id: str, windows_sid: str, now: datetime) -> LaunchTicket:
        if profile_id != self.profile_id or windows_sid != self.windows_sid or now.tzinfo is None:
            raise PermissionError("native_authority_denied")
        with self._lock:
            if not self._reconcile_authority():
                raise PermissionError("native_authority_denied")
            if not self._available:
                raise PermissionError("native_authority_denied")
            self._tickets = {digest: record for digest, record in self._tickets.items() if record[0] > now}
            if len(self._tickets) >= 128:
                raise PermissionError("ticket_limit")
            raw = secrets.token_urlsafe(32)
            expires = now + timedelta(seconds=30)
            self._tickets[hashlib.sha256(raw.encode("ascii")).hexdigest()] = (
                expires, profile_id, "dashboard", self._generation
            )
            return LaunchTicket(raw_ticket=raw, expires_at=expires)

    def redeem(self, ticket: str, origin: str, now: datetime) -> AdminSession:
        import re
        if origin != self.origin or not isinstance(ticket, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", ticket) or now.tzinfo is None:
            raise PermissionError("ticket_denied")
        digest = hashlib.sha256(ticket.encode("ascii")).hexdigest()
        with self._lock:
            if not self._reconcile_authority():
                raise PermissionError("ticket_denied")
            record = self._tickets.pop(digest, None)
            if not record or record[0] <= now or record[1:] != (self.profile_id, "dashboard", self._generation):
                raise PermissionError("ticket_denied")
            session = AdminSession(
                token=secrets.token_urlsafe(48), owner_id=self._verified_owner,
                csrf_token=secrets.token_urlsafe(32), created_at=now, expires_at=now + self.session_ttl,
                profile_id=self.profile_id, authority="management" if self._verified_owner else "setup_only",
            )
            self.sessions[session.token] = session
            return session

    def revoke_session(self, token):
        with self._lock:
            super().revoke_session(token)
            # A previously issued but unused ticket cannot undo logout.
            self._tickets.clear()

    def get_session(self, token):
        with self._lock:
            if not self._reconcile_authority():
                return None
            return super().get_session(token) if self._available else None


def install_native_auth_routes(app, tickets, *, command_handler=None,
                               session_command_handler=None, dialog_availability=None):
    """Routes shared by setup and assistant gateway; no HTTP mint endpoint."""
    from fastapi.responses import JSONResponse

    from ..contracts import NativeCommand

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response

    def output(session):
        return dict(authenticated=True, owner_id=str(session.owner_id) if session.owner_id else None,
                    profile_id=session.profile_id, authority=session.authority,
                    csrf_token=session.csrf_token, expires_at=session.expires_at.isoformat())

    async def auth_route(request: Request):
        origin = request.headers.get("origin")
        path = request.url.path
        if request.method == "POST" and origin != tickets.origin:
            return JSONResponse({"code": "origin_denied"}, status_code=403)
        if path == "/api/v1/auth/login":
            return JSONResponse({"code": "native_reopen_required"}, status_code=403)
        if path == "/api/v1/auth/launch/redeem":
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 1024:
                    return JSONResponse({"code": "ticket_denied"}, status_code=401)
            try:
                import json
                payload = json.loads(body)
                if not isinstance(payload, dict) or set(payload) != {"ticket"}:
                    raise ValueError()
                session = tickets.redeem(payload["ticket"], origin, datetime.now(UTC))
            except (ValueError, TypeError, PermissionError, RecursionError):
                return JSONResponse({"code": "ticket_denied"}, status_code=401)
            response = JSONResponse(output(session))
            response.set_cookie(SESSION_COOKIE, session.token, httponly=True, samesite="strict", path="/", max_age=int(tickets.session_ttl.total_seconds()))
            return response
        session = tickets.get_session(request.cookies.get(SESSION_COOKIE))
        if not session:
            return JSONResponse({"code": "native_reopen_required"}, status_code=401)
        if request.method == "GET":
            if path == "/api/v1/native/dialogs":
                return JSONResponse({
                    "profile_id": tickets.profile_id,
                    "commands": list(dialog_availability()) if dialog_availability else [],
                })
            return JSONResponse(output(session))
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf_token):
            return JSONResponse({"code": "csrf_denied"}, status_code=403)
        if path == "/api/v1/auth/logout":
            tickets.revoke_session(session.token)
            response = JSONResponse({"authenticated": False})
            response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
            return response
        try:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 8192:
                    raise ValueError()
            command = NativeCommand.model_validate_json(body)
            if command.profile_id != tickets.profile_id or command.name.value == "issue_dashboard_ticket":
                raise ValueError()
            command.model_dump_json()  # Revalidate nested mutable values.
        except (ValueError, TypeError):
            return JSONResponse({"code": "native_command_denied"}, status_code=403)
        if session_command_handler is not None:
            return JSONResponse(session_command_handler(command, session))
        if command_handler is None:
            return JSONResponse({"code": "native_dialog_unavailable"}, status_code=409)
        return JSONResponse(command_handler(command))

    for path, methods in (
        ("/api/v1/auth/launch/redeem", ["POST"]), ("/api/v1/auth/login", ["POST"]),
        ("/api/v1/auth/session", ["GET"]), ("/api/v1/auth/logout", ["POST"]),
        ("/api/v1/native/commands", ["POST"]),
        ("/api/v1/native/dialogs", ["GET"]),
    ):
        app.add_api_route(path, auth_route, methods=methods)
