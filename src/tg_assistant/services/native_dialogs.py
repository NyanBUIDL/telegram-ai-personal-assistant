"""Volatile requests from authenticated HTTP to trusted same-SID native IPC.

The owning boundaries supply session checks and actual installed dialog handlers.
This core never opens a dialog, issues tickets or verifies a connection itself.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from threading import RLock
from uuid import uuid4

from ..contracts import NativeCommand, OperationResult
from ..paths import current_user_sid
from .maintenance import MaintenanceBusy

_NAMES = frozenset({"open_connection_dialog", "open_telegram_login", "open_bot_dialog"})
_MESSAGES = {
    "native_dialog_unavailable": "Mở ứng dụng Windows và kiểm tra lại kết nối.",
    "native_command_denied": "Không thể mở hộp kết nối từ yêu cầu này.",
    "native_request_replayed": "Yêu cầu này đã được nhận; hãy kiểm tra ứng dụng Windows.",
    "native_dialog_busy": "Ứng dụng đang chờ mở hộp kết nối. Hãy thử lại sau.",
    "native_dialog_queued": "Đã gửi yêu cầu tới ứng dụng Windows; kết nối chưa được xác minh.",
}


@dataclass(frozen=True)
class _Pending:
    command: NativeCommand
    authorized: object
    expires: float


class NativeDialogRelay:
    """Only trusted HTTP/native owners may invoke submit/claim respectively.

    authorized must be a bounded, non-I/O check of the actual issuing session,
    not a browser-supplied bool. Claim consumes a command, not a success proof.
    """

    def __init__(self, *, profile_id, fence, sid_getter=current_user_sid, clock=time.monotonic):
        if fence.profile_id != profile_id or not callable(sid_getter) or not callable(clock):
            raise ValueError("native_relay_invalid")
        self.profile_id, self.fence = profile_id, fence
        self._sid_getter, self._clock = sid_getter, clock
        self._sid = sid_getter()
        self._lock = RLock()
        self._queue, self._spent = deque(), OrderedDict()
        self._available, self._heartbeat = frozenset(), None
        self._closed, self._last_time = False, None

    def _time(self):
        measured = self._clock()
        if type(measured) not in {int, float} or not math.isfinite(measured):
            raise ValueError("native_relay_clock_unavailable")
        if self._last_time is not None and measured < self._last_time:
            raise ValueError("native_relay_clock_unavailable")
        self._last_time = measured
        return measured

    def _current(self):
        return not self._closed and self._sid_getter() == self._sid

    @staticmethod
    def _authorized(callback):
        try:
            return callable(callback) and callback() is True
        except Exception:
            return False

    @staticmethod
    def _result(code):
        return OperationResult(
            operation_id="native-" + uuid4().hex,
            state="queued" if code == "native_dialog_queued" else "failed",
            progress=None,
            code=code,
            message=_MESSAGES[code],
            next_action=None,
        )

    def _withdraw(self):
        self._queue.clear()
        self._available, self._heartbeat = frozenset(), None

    def _prune(self, now):
        self._queue = deque(item for item in self._queue if item.expires > now)
        while self._spent and next(iter(self._spent.values())) <= now:
            self._spent.popitem(last=False)

    def submit(self, command, *, authorized):
        with self._lock:
            try:
                # Frozen DTOs can contain mutated nested dicts; validate wire
                # boundary before cloning or retaining any browser-controlled data.
                selected = NativeCommand.model_validate_json(command.model_dump_json())
            except (OSError, ValueError, TypeError, AttributeError):
                return self._result("native_command_denied")
            try:
                if (
                    selected.profile_id != self.profile_id
                    or selected.name.value not in _NAMES
                    or selected.payload_nonsecret
                    or not self._authorized(authorized)
                ):
                    return self._result("native_command_denied")
                with self.fence.operation():
                    if not self._current():
                        self._withdraw()
                        return self._result("native_dialog_unavailable")
                    now = self._time()
                    self._prune(now)
                    if (
                        self._heartbeat is None
                        or now - self._heartbeat >= 5
                        or selected.name.value not in self._available
                    ):
                        return self._result("native_dialog_unavailable")
                    digest = hashlib.sha256(selected.request_id.encode()).hexdigest()
                    if digest in self._spent:
                        return self._result("native_request_replayed")
                    if len(self._queue) >= 8 or len(self._spent) >= 256:
                        return self._result("native_dialog_busy")
                    self._spent[digest] = now + 300
                    self._queue.append(_Pending(selected, authorized, now + 30))
                    return self._result("native_dialog_queued")
            except (MaintenanceBusy, OSError, ValueError, TypeError, AttributeError):
                self._withdraw()
                return self._result("native_dialog_unavailable")

    def claim(self, available):
        with self._lock:
            try:
                names = frozenset(available)
                if not names <= _NAMES:
                    self._withdraw()
                    return None
                with self.fence.operation():
                    if not self._current():
                        self._withdraw()
                        return None
                    now = self._time()
                    self._prune(now)
                    self._available, self._heartbeat = names, now
                    while self._queue:
                        pending = self._queue.popleft()
                        if (
                            pending.command.name.value in names
                            and self._authorized(pending.authorized)
                            and self._current()
                            and self._time() < pending.expires
                        ):
                            return pending.command.model_copy(deep=True)
            except (MaintenanceBusy, OSError, ValueError, TypeError):
                self._withdraw()
            return None

    def available_commands(self):
        """Read-only sanitized projection; does not renew a heartbeat or request."""
        with self._lock:
            try:
                with self.fence.operation():
                    now = self._time()
                    if (
                        self._current()
                        and self._heartbeat is not None
                        and now - self._heartbeat < 5
                    ):
                        return tuple(sorted(self._available))
            except (MaintenanceBusy, OSError, ValueError, TypeError):
                return ()
            return ()

    def close(self):
        with self._lock:
            self._closed = True
            self._withdraw()
            self._spent.clear()
