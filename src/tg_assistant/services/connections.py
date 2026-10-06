"""Measured health; configuration/credential presence is never a connectivity probe.

Probe callbacks are registered by trusted owning services in process. This class
has no browser claim/publish method and never forwards exception text.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from threading import RLock

from pydantic import Field
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..contracts import (
    ConnectionService,
    ConnectionState,
    ConnectionStatus,
    ContractModel,
    Identifier,
)
from ..security import contains_secret


class HealthObservation(ContractModel):
    state: ConnectionState
    capabilities: tuple[Identifier, ...] = Field(default=(), max_length=32)


_MESSAGES = {
    ConnectionState.UNKNOWN: (
        "health_unknown",
        "Chưa có kiểm tra kết nối còn hiệu lực.",
        "Kiểm tra lại kết nối trong ứng dụng Windows.",
    ),
    ConnectionState.CHECKING: (
        "health_checking",
        "Đang kiểm tra kết nối.",
        "Chờ kết quả kiểm tra.",
    ),
    ConnectionState.READY: ("health_ready", "Kết nối đã được kiểm tra.", None),
    ConnectionState.DEGRADED: (
        "health_degraded",
        "Kết nối có giới hạn chức năng.",
        "Kiểm tra khả năng còn thiếu trong ứng dụng Windows.",
    ),
    ConnectionState.DISCONNECTED: (
        "health_disconnected",
        "Không thể sử dụng kết nối hiện tại.",
        "Mở ứng dụng Windows để kiểm tra lại kết nối.",
    ),
}


class ConnectionHealth:
    """Bounded cached observations of separate chat/embedding/service probes."""

    def __init__(
        self,
        *,
        probes: Mapping[ConnectionService | str, Callable[[], HealthObservation]],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        ttl_seconds: int = 60,
    ):
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError("health_ttl_invalid")
        self._probes = {ConnectionService(service): probe for service, probe in probes.items()}
        self._now = now
        self._ttl = timedelta(seconds=ttl_seconds)
        self._cache: dict[ConnectionService, ConnectionStatus] = {}
        self._generations: dict[ConnectionService, int] = {}
        self._lock = RLock()

    def _time(self):
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("health_clock_invalid")
        return value.astimezone(UTC)

    @staticmethod
    def _status(service, state, checked_at=None, capabilities=()):
        code, message, next_action = _MESSAGES[state]
        return ConnectionStatus(
            service=service,
            state=state,
            checked_at=checked_at,
            code=code,
            message=message,
            next_action=next_action,
            capabilities=list(capabilities),
        )

    def refresh(self, service: ConnectionService | str | None = None) -> list[ConnectionStatus]:
        """Execute only registered service probes; never infer success from options."""
        targets = [ConnectionService(service)] if service is not None else list(ConnectionService)
        for name in targets:
            probe = self._probes.get(name)
            if probe is None:
                with self._lock:
                    self._cache[name] = self._status(name, ConnectionState.UNKNOWN)
                continue
            with self._lock:
                generation = self._generations.get(name, 0) + 1
                self._generations[name] = generation
                self._cache[name] = self._status(name, ConnectionState.CHECKING, self._time())
            try:
                measured = HealthObservation.model_validate(probe())
                # Capabilities are bounded service identifiers, never provider errors/URLs.
                if any(contains_secret(value) for value in measured.capabilities):
                    raise ValueError("health_capability_invalid")
                capabilities = (
                    measured.capabilities
                    if measured.state
                    in {
                        ConnectionState.READY,
                        ConnectionState.DEGRADED,
                    }
                    else ()
                )
                result = self._status(name, measured.state, self._time(), capabilities)
            except Exception:
                result = self._status(name, ConnectionState.DISCONNECTED, self._time())
            with self._lock:
                if self._generations[name] == generation:
                    self._cache[name] = result
        return self.status()

    def status(self) -> list[ConnectionStatus]:
        now = self._time()
        result = []
        with self._lock:
            for service in ConnectionService:
                cached = self._cache.get(service)
                if (
                    cached is None
                    or cached.checked_at is None
                    or not (timedelta(0) <= now - cached.checked_at < self._ttl)
                ):
                    result.append(
                        self._status(
                            service, ConnectionState.UNKNOWN, cached.checked_at if cached else None
                        )
                    )
                else:
                    # Isolate nested capability lists from consumers of public DTOs.
                    result.append(ConnectionStatus.model_validate(cached.model_dump()))
        return result


def storage_probe(engine: Engine) -> Callable[[], HealthObservation]:
    """Probe the explicitly selected storage engine, without switching backends."""

    def measure():
        with engine.connect() as connection:
            if connection.scalar(text("SELECT 1")) != 1:
                return HealthObservation(state=ConnectionState.DISCONNECTED)
        return HealthObservation(state=ConnectionState.READY, capabilities=("read",))

    return measure
