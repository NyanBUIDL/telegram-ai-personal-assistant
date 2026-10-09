"""Persistent admission fence backed by an owned cross-process OS lock.

Expiry/process death does not resume writers. Recovery must reacquire the
exclusive lock before explicitly clearing the persistent draining state.
"""

from __future__ import annotations

import inspect
import json
import math
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path
from uuid import uuid4

from ..contracts import MaintenanceLease


class MaintenanceBusy(RuntimeError):
    """Profile admission is closed or active operations have not drained."""


_active: ContextVar[tuple] = ContextVar("profile_writer_guards", default=())


def admitted(key):
    # Child tasks inherit context, but cannot outlive the actual owned OS handle.
    return any(root == key and not lock.closed for root, lock in _active.get())


def profile_writer(settings_getter):
    """Fence the entire managed operation, including network/vector writes."""

    def decorate(function):
        def fence():
            from ..paths import ensure_runtime_dirs

            settings = settings_getter()
            paths = ensure_runtime_dirs(settings.data_dir, profile_id=settings.profile_id)
            return MaintenanceService(paths["config"], profile_id=settings.profile_id)

        if inspect.iscoroutinefunction(function):

            @wraps(function)
            async def asynchronous(*args, **kwargs):
                with fence().operation():
                    return await function(*args, **kwargs)

            return asynchronous

        @wraps(function)
        def synchronous(*args, **kwargs):
            with fence().operation():
                return function(*args, **kwargs)

        return synchronous

    return decorate


def profile_maintenance(settings_getter):
    """Legacy schema/restore entry points must own the exclusive fence."""

    def decorate(function):
        @wraps(function)
        def exclusive(*args, **kwargs):
            from ..paths import ensure_runtime_dirs

            settings = settings_getter()
            paths = ensure_runtime_dirs(settings.data_dir, profile_id=settings.profile_id)
            service = MaintenanceService(paths["config"], profile_id=settings.profile_id)
            lease = service.acquire(function.__name__, lease_seconds=86400, timeout=0)
            try:
                result = function(*args, **kwargs)
                service.release(lease)
                return result
            finally:
                # Failure retains admission state until explicit validated recovery.
                service.close()

        return exclusive

    return decorate


class FileLock:
    """Separate handles, shared/exclusive byte-range locks; never inherited."""

    def __init__(self, path: Path, *, exclusive: bool, timeout: float = 0):
        self.file = path.open("a+b")
        self.closed = False
        try:
            if os.name == "nt":
                import ctypes
                import msvcrt
                from ctypes import wintypes

                class Overlapped(ctypes.Structure):
                    _fields_ = [
                        ("Internal", ctypes.c_size_t),
                        ("InternalHigh", ctypes.c_size_t),
                        ("Offset", wintypes.DWORD),
                        ("OffsetHigh", wintypes.DWORD),
                        ("hEvent", wintypes.HANDLE),
                    ]

                self.api = ctypes.WinDLL("kernel32", use_last_error=True)
                self.api.LockFileEx.argtypes = [
                    wintypes.HANDLE,
                    wintypes.DWORD,
                    wintypes.DWORD,
                    wintypes.DWORD,
                    wintypes.DWORD,
                    ctypes.POINTER(Overlapped),
                ]
                self.api.LockFileEx.restype = wintypes.BOOL
                self.api.UnlockFileEx.argtypes = [
                    wintypes.HANDLE,
                    wintypes.DWORD,
                    wintypes.DWORD,
                    wintypes.DWORD,
                    ctypes.POINTER(Overlapped),
                ]
                self.api.UnlockFileEx.restype = wintypes.BOOL
                self.handle = msvcrt.get_osfhandle(self.file.fileno())
                self.overlapped = Overlapped()

                def attempt():
                    if self.api.LockFileEx(
                        self.handle,
                        1 | (2 if exclusive else 0),
                        0,
                        1,
                        0,
                        ctypes.byref(self.overlapped),
                    ):
                        return True
                    if ctypes.get_last_error() != 33:
                        raise OSError("profile_lock_failed")
                    return False
            else:
                import fcntl

                def attempt():
                    try:
                        fcntl.flock(
                            self.file,
                            (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB,
                        )
                        return True
                    except BlockingIOError:
                        return False

            deadline = time.monotonic() + timeout
            while not attempt():
                if time.monotonic() >= deadline:
                    raise MaintenanceBusy("profile_operations_active")
                time.sleep(min(0.02, max(0, deadline - time.monotonic())))
        except BaseException:
            self.file.close()
            self.closed = True
            raise

    def close(self):
        if self.closed:
            return
        try:
            if os.name == "nt":
                import ctypes

                if not self.api.UnlockFileEx(self.handle, 0, 1, 0, ctypes.byref(self.overlapped)):
                    raise OSError("profile_unlock_failed")
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_UN)
        finally:
            self.file.close()
            self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class MaintenanceService:
    def __init__(self, root: Path, *, profile_id: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.profile_id = profile_id
        self.key = str(self.root).casefold() if os.name == "nt" else str(self.root)
        self.control = self.root / "maintenance-control.lock"
        self.writers = self.root / "maintenance-writers.lock"
        self.state = self.root / "maintenance-state.json"
        self._held: FileLock | None = None
        self._lease: MaintenanceLease | None = None

    def _read(self):
        if not self.state.exists():
            return {"profile_id": self.profile_id, "generation": 0, "state": "open"}
        try:
            state = json.loads(self.state.read_text(encoding="utf-8"))
            if state["profile_id"] != self.profile_id or state["state"] not in {
                "open",
                "draining",
                "writers_fenced",
            }:
                raise ValueError
            if type(state["generation"]) is not int or state["generation"] < 0:
                raise ValueError
            return state
        except (ValueError, KeyError, TypeError):
            raise MaintenanceBusy("maintenance_state_invalid") from None

    def _write(self, state):
        path = self.root / (".maintenance-" + uuid4().hex + ".tmp")
        try:
            with path.open("x", encoding="utf-8") as output:
                json.dump(state, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(path, self.state)
        finally:
            path.unlink(missing_ok=True)

    @contextmanager
    def operation(self, *, admission_timeout=0.2):
        if (
            type(admission_timeout) not in {int, float}
            or not 0 <= admission_timeout <= 0.2
            or not math.isfinite(admission_timeout)
        ):
            raise MaintenanceBusy("maintenance_admission_invalid")
        with FileLock(self.control, exclusive=True, timeout=admission_timeout):
            state = self._read()["state"]
            if state != "open" and not admitted(self.key):
                raise MaintenanceBusy("maintenance_in_progress")
            lock = FileLock(self.writers, exclusive=False)
            if state != "open" and not admitted(self.key):
                lock.close()
                raise MaintenanceBusy("maintenance_in_progress")
        # Every nested/child operation owns a handle until ITS exit. It cannot
        # borrow a parent's handle which may close while the child is awaiting.
        token = _active.set((*_active.get(), (self.key, lock)))
        try:
            yield
        finally:
            _active.reset(token)
            lock.close()

    def acquire(self, holder: str, *, lease_seconds=300, timeout=5.0) -> MaintenanceLease:
        if self._held or admitted(self.key):
            raise MaintenanceBusy("maintenance_cannot_upgrade_active_writer")
        expiry = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        with FileLock(self.control, exclusive=True, timeout=0.2):
            state = self._read()
            if state["state"] != "open":
                raise MaintenanceBusy("maintenance_requires_recovery")
            lease = MaintenanceLease(
                profile_id=self.profile_id,
                generation=state["generation"] + 1,
                holder=holder,
                expires_at=expiry,
            )
            self._write({**lease.model_dump(mode="json"), "state": "draining"})
        # Failed drains deliberately retain the persistent admission fence.
        lock = FileLock(self.writers, exclusive=True, timeout=timeout)
        try:
            with FileLock(self.control, exclusive=True, timeout=0.2):
                state = self._read()
                if state.get("generation") != lease.generation or state.get("holder") != holder:
                    raise MaintenanceBusy("maintenance_generation_changed")
                self._write(lease.model_dump(mode="json"))
            self._held, self._lease = lock, lease
            return lease
        except BaseException:
            lock.close()
            raise

    def validate(self, lease: MaintenanceLease) -> bool:
        if (
            self._held is None
            or self._held.closed
            or self._lease != lease
            or lease.expires_at <= datetime.now(UTC)
        ):
            return False
        with FileLock(self.control, exclusive=True, timeout=0.2):
            return self._read() == lease.model_dump(mode="json")

    def release(self, lease: MaintenanceLease):
        if not self.validate(lease):
            raise MaintenanceBusy("invalid_maintenance_lease")
        with FileLock(self.control, exclusive=True, timeout=0.2):
            self._write(
                {"profile_id": self.profile_id, "generation": lease.generation, "state": "open"}
            )
            self.close()

    def recover(self):
        if self._held or admitted(self.key):
            raise MaintenanceBusy("maintenance_holder_active")
        with (
            FileLock(self.writers, exclusive=True),
            FileLock(self.control, exclusive=True, timeout=0.2),
        ):
            state = self._read()
            self._write(
                {
                    "profile_id": self.profile_id,
                    "generation": state["generation"] + 1,
                    "state": "open",
                }
            )

    def close(self):
        if self._held:
            self._held.close()
        self._held = self._lease = None
