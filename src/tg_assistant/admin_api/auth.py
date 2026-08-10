from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

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
    token: str
    owner_id: int
    csrf_token: str
    created_at: datetime
    expires_at: datetime


class AdminAuth:
    def __init__(self, secret: str, *, session_minutes: int = 480) -> None:
        self.secret = secret
        self.session_ttl = timedelta(minutes=session_minutes)
        self.sessions: dict[str, AdminSession] = {}
        self.login_attempts: defaultdict[str, deque[float]] = defaultdict(deque)

    def validate_login_code(self, value: str, *, at: float | None = None) -> bool:
        code = "".join(character for character in value if character.isdigit())
        if len(code) != 8:
            return False
        return hmac.compare_digest(code, dashboard_login_code(self.secret, at=at))

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
