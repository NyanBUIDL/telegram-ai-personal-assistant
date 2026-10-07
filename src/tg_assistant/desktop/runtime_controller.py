"""Nonblocking process lifecycle; credentials never enter subprocess arguments."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPConnection, HTTPException
from threading import RLock
from uuid import UUID, uuid4

import httpx
import psutil

from ..contracts import ConnectionStatus, OnboardingStatus
from ..paths import ensure_runtime_dirs, resource_path
from .instance import process_incarnation_exists, process_matches_owner


def _readiness_response(port, *, timeout=0.5):
    """Probe the fixed HTTP loopback endpoint without TLS/proxy initialization.

    Timeout bounds socket operations, not total wall time. Readiness callers
    keep their own overall deadlines and validate identity and public status.
    """
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("runtime_readiness_port_invalid")
    connection = HTTPConnection("127.0.0.1", port, timeout=timeout)
    response = None
    try:
        connection.request("GET", "/api/v1/runtime/readiness")
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("runtime_readiness_status_rejected")
        body = response.read(8193)
        if len(body) > 8192:
            raise ValueError("runtime_readiness_body_oversized")
        return response.getheader("X-TG-Runtime-ID"), json.loads(body)
    finally:
        try:
            if response is not None:
                response.close()
        finally:
            connection.close()


@dataclass(frozen=True)
class RuntimeSnapshot:
    phase: str
    code: str
    mode: str = "setup"
    pid: int | None = None
    port: int | None = None
    run_id: str | None = None
    owner_id: str | None = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}" if self.phase == "ready" else None


class RuntimeController:
    def __init__(self, settings, *, worker_command=None):
        self.settings = settings
        self.process = None
        self.worker_command = worker_command
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="desktop-readiness")
        self.snapshot = RuntimeSnapshot("stopped", "runtime_stopped")
        self.spawn_options = {}
        self.stop_requested = False
        self._stop_lock = RLock()
        self._stop_target = None
        self._stop_sent = False
        self.launch_id = None
        self.start_failed = False
        self.attached_process = None
        self.handoff_active = False
        self._handoff_failed = False
        self._handoff_process = None
        self._native_handoff_context = None

    @property
    def state_file(self):
        return self.settings.data_dir / "config/desktop-runtime.json"

    def _read_state(self):
        if self.state_file.is_symlink():
            raise ValueError("runtime_state_invalid")
        if os.name != "nt":
            with self.state_file.open(encoding="utf-8") as stream:
                content = stream.read(8193)
        else:
            import ctypes
            import msvcrt
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateFileW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ]
            kernel.CreateFileW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            # Polling must permit the worker's atomic state replacement/removal.
            handle = kernel.CreateFileW(str(self.state_file), 0x80000000, 7, None, 3, 0, None)
            if handle == ctypes.c_void_p(-1).value:
                raise OSError("runtime_state_unavailable")
            try:
                descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except OSError:
                kernel.CloseHandle(handle)
                raise
            with os.fdopen(descriptor, encoding="utf-8") as stream:
                content = stream.read(8193)
        if len(content.encode("utf-8")) > 8192:
            raise ValueError("runtime_state_invalid")
        try:
            state = json.loads(content)
        except (ValueError, RecursionError):
            raise ValueError("runtime_state_invalid") from None
        fields = {"profile_id", "pid", "process_started_at", "port", "run_id", "mode", "owner_id"}
        if not isinstance(state, dict) or set(state) != fields:
            raise ValueError("runtime_state_invalid")
        if (
            type(state["profile_id"]) is not str
            or type(state["pid"]) is not int
            or not 0 < state["pid"] <= 0xFFFFFFFF
            or type(state["port"]) is not int
            or not 1024 <= state["port"] <= 65535
            or type(state["process_started_at"]) not in {int, float}
            or not 0 < state["process_started_at"] < 1e12
            or not math.isfinite(state["process_started_at"])
            or type(state["run_id"]) is not str
            or UUID(state["run_id"]).hex != state["run_id"]
            or type(state["mode"]) is not str
            or state["mode"] not in {"setup", "assistant"}
        ):
            raise ValueError("runtime_state_invalid")
        if (state["mode"] == "setup" and state["owner_id"] is not None) or (
            state["mode"] == "assistant"
            and (
                type(state["owner_id"]) is not str
                or not re.fullmatch(r"[1-9][0-9]*", state["owner_id"])
            )
        ):
            raise ValueError("runtime_state_invalid")
        return state

    def start(self):
        with self._stop_lock:
            if self.handoff_active:
                return self.executor.submit(lambda: False)
            if self._stop_target is not None:
                try:
                    if process_incarnation_exists(*self._stop_target[1:]):
                        return self.executor.submit(lambda: False)
                except psutil.Error:
                    return self.executor.submit(lambda: False)
            if self.process is None or self.process.poll() is not None:
                # A fresh start intent clears an earlier unbound/completed stop.
                # Stop calls after this intent still win while startup is queued.
                self.stop_requested = False
                self._stop_target = None
                self._stop_sent = False
            self.start_failed = False
            return self.executor.submit(self._start_safely)

    def _start_safely(self):
        try:
            self._start()
        except (OSError, ValueError):
            self.start_failed = True
            self.snapshot = RuntimeSnapshot("error", "runtime_start_failed")

    def _start(self):
        if self.handoff_active:
            return
        with self._stop_lock:
            if self.process and self.process.poll() is None:
                return
            if self._stop_target is not None:
                try:
                    if process_incarnation_exists(*self._stop_target[1:]):
                        return
                except psutil.Error:
                    return
        if self.process is None and self._refresh().phase == "ready":
            return
        paths = ensure_runtime_dirs(self.settings.data_dir, profile_id=self.settings.profile_id)
        command = self.worker_command or (
            [sys.executable, "--desktop-worker"]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "tg_assistant.desktop.app", "--desktop-worker"]
        )
        self.spawn_options = dict(
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            cwd=str(resource_path()),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        # Per-user settings are nonsecret. No key, token, OTP, session or phone
        # is transferred through environment overrides or the command line.
        environment = dict(os.environ)
        environment["TG_ASSISTANT_DATA_DIR"] = str(paths["data"])
        self.launch_id = uuid4().hex
        environment["TG_ASSISTANT_DESKTOP_RUN_ID"] = self.launch_id
        self.process = subprocess.Popen(command, env=environment, **self.spawn_options)
        self.snapshot = RuntimeSnapshot(
            "starting", "runtime_starting", pid=self.process.pid, run_id=self.launch_id
        )

    def refresh(self):
        return self.executor.submit(self._refresh)

    def quiesce(self, *, timeout=20.0):
        """Gracefully release the verified worker before native session ownership.

        This is a lifecycle prerequisite, not proof of a writer fence. The
        native publisher must still acquire the actual SID InstanceGuard.
        """
        if type(timeout) not in {int, float} or not 0 < timeout <= 60:
            raise ValueError("runtime_handoff_timeout_invalid")
        self.handoff_active = True
        return self.executor.submit(self._quiesce, timeout)

    def _quiesce(self, timeout):
        self._handoff_failed = False
        state = self._refresh()
        if state.phase != "ready":
            self.handoff_active = False
            return False
        try:
            actual = self._read_state()
            if actual["run_id"] != state.run_id or not process_matches_owner(
                actual["pid"], actual["process_started_at"]
            ):
                self.handoff_active = False
                return False
            self.stop()
            self._handoff_process = (actual["pid"], actual["process_started_at"])
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if not process_incarnation_exists(actual["pid"], actual["process_started_at"]):
                    if self.process is not None and self.process.poll() is None:
                        time.sleep(0.05)
                        continue
                    self.snapshot = RuntimeSnapshot("stopped", "runtime_stopped")
                    return True
                time.sleep(0.05)
        except (OSError, ValueError, KeyError, psutil.Error):
            self._handoff_failed = True
            return False
        self._handoff_failed = True
        return False

    def resume_after_handoff(self):
        """Only call after the native context has drained and released its guard."""
        return self.executor.submit(self._resume_after_handoff)

    def _resume_after_handoff(self):
        self.handoff_active = False
        self._handoff_failed = False
        self._handoff_process = None
        with self._stop_lock:
            self.stop_requested = False
            self._stop_target = None
            self._stop_sent = False
        self._start_safely()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = self._refresh()
            if state.phase == "ready":
                return state
            if state.phase == "error":
                raise RuntimeError("runtime_start_failed")
            time.sleep(0.05)
        raise RuntimeError("runtime_start_pending")

    def open_dashboard(self):
        return self.executor.submit(self._dashboard_url)

    def setup_status(self):
        return self.executor.submit(self._setup_status)

    def _setup_status(self):
        state = self._refresh()
        if state.phase != "ready" or self.handoff_active:
            return None
        try:
            response = httpx.get(
                state.url + "/api/v1/setup/status", timeout=5, trust_env=False,
                follow_redirects=False,
            )
            response.raise_for_status()
            return OnboardingStatus.model_validate(response.json())
        except (httpx.HTTPError, ValueError):
            return None

    def claim_native_dialogs(self, available):
        return self.executor.submit(self._claim_native_dialogs, tuple(available))

    def _claim_native_dialogs(self, available):
        from ..contracts import NativeCommand
        from .ipc import native_request

        names = {"open_connection_dialog", "open_telegram_login", "open_bot_dialog"}
        if any(type(name) is not str for name in available) or not set(available) <= names:
            raise ValueError("native_command_denied")
        state = self._refresh()
        if state.phase != "ready" or self.stop_requested or self.handoff_active:
            return None
        result = native_request(self.settings.profile_id, state.run_id, {
            "name": "open_connection_dialog", "request_id": uuid4().hex,
            "profile_id": self.settings.profile_id,
            "payload_nonsecret": {"delivery": "claim", "available": list(available)},
        }, server_pid=state.pid)
        if not isinstance(result, dict) or set(result) != {"command"}:
            raise ValueError("native_command_denied")
        if result["command"] is None:
            return None
        command = NativeCommand.model_validate(result["command"])
        command.model_dump_json()
        if (
            command.profile_id != self.settings.profile_id
            or command.name.value not in available
            or command.payload_nonsecret
        ):
            raise ValueError("native_command_denied")
        return command

    def _dashboard_url(self):
        from .ipc import native_request
        state = self._refresh()
        if state.phase != "ready":
            raise OSError("runtime_not_ready")
        # _refresh verifies SID, process creation time, profile and run incarnation.
        result = native_request(self.settings.profile_id, state.run_id, {
            "name": "issue_dashboard_ticket", "request_id": uuid4().hex,
            "profile_id": self.settings.profile_id, "payload_nonsecret": {},
        }, server_pid=state.pid)
        ticket = result.get("raw_ticket")
        if not isinstance(ticket, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", ticket):
            raise OSError("dashboard_launch_failed")
        return state.url + "#launch_ticket=" + ticket

    def _refresh(self):
        if self.handoff_active and self._handoff_failed and self._handoff_process:
            try:
                if not process_incarnation_exists(*self._handoff_process) and (
                    self.process is None or self.process.poll() is not None
                ):
                    # No publisher was opened for a failed quiesce. Only the
                    # actual completed process stop permits an explicit retry.
                    self.handoff_active = False
                    self._handoff_failed = False
                    self._handoff_process = None
            except psutil.Error:
                pass
        if self.start_failed:
            return self.snapshot
        if self.process and self.process.poll() is not None:
            phase = "stopped" if self.process.returncode == 0 else "error"
            code = (
                "runtime_stopped"
                if phase == "stopped"
                else (
                    "runtime_already_running"
                    if self.process.returncode == 17
                    else "runtime_start_failed"
                )
            )
            self.snapshot = RuntimeSnapshot(phase, code)
            return self.snapshot
        if self.stop_requested and self.attached_process:
            try:
                if not process_incarnation_exists(*self.attached_process):
                    self.snapshot = RuntimeSnapshot("stopped", "runtime_stopped")
                    return self.snapshot
            except psutil.Error:
                pass
        with self._stop_lock:
            if self._stop_sent:
                self.snapshot = RuntimeSnapshot(
                    "stopping", "runtime_stopping", pid=self._stop_target[1],
                    run_id=self._stop_target[0],
                )
                return self.snapshot
        self.snapshot = RuntimeSnapshot(
            "starting" if self.process else "unknown",
            "runtime_readiness_unavailable",
            pid=self.snapshot.pid,
            run_id=self.snapshot.run_id,
        )
        try:
            if not self.state_file.exists() or self.state_file.stat().st_size > 8192:
                return self.snapshot
            state = self._read_state()
            if state["profile_id"] != self.settings.profile_id or (
                self.process and state["run_id"] != self.launch_id
            ):
                return self.snapshot
            if UUID(state["run_id"]).hex != state["run_id"] or not process_matches_owner(
                state["pid"], state["process_started_at"]
            ):
                return self.snapshot
            port = state["port"]
            if type(port) is not int or not 1024 <= port <= 65535:
                return self.snapshot
            runtime_header, body = _readiness_response(port)
            if runtime_header != state["run_id"]:
                return self.snapshot
            measured = ConnectionStatus.model_validate(body)
            age = (
                (datetime.now(UTC) - measured.checked_at).total_seconds()
                if measured.checked_at
                else 60
            )
            if measured.service.value != "runtime" or not 0 <= age < 5:
                self.snapshot = RuntimeSnapshot("starting", "runtime_readiness_stale")
                return self.snapshot
            if measured.state.value != "ready":
                self.snapshot = RuntimeSnapshot("starting", "runtime_not_ready")
                return self.snapshot
            mode = state["mode"]
            if (
                (mode == "setup" and measured.capabilities != ["setup"])
                or (mode == "assistant" and measured.capabilities != ["management"])
                or mode not in {"setup", "assistant"}
            ):
                return self.snapshot
            with self._stop_lock:
                identity = (state["run_id"], state["pid"], state["process_started_at"])
                if (self._stop_target is not None and identity != self._stop_target) or (
                    self.process and state["run_id"] != self.launch_id
                ):
                    return self.snapshot
                self.attached_process = (state["pid"], state["process_started_at"])
                self.snapshot = RuntimeSnapshot(
                    "ready",
                    "runtime_ready",
                    state["mode"],
                    state["pid"],
                    port,
                    state["run_id"],
                    state.get("owner_id"),
                )
                if self.stop_requested:
                    self.stop()
        except (OSError, ValueError, KeyError, TypeError, HTTPException, psutil.Error):
            pass
        return self.snapshot

    def stop(self):
        with self._stop_lock:
            self.stop_requested = True
            if self._stop_sent:
                return
            if self.snapshot.phase not in {"starting", "ready", "stopping"}:
                return
            # Bind even a failed publication to this verified incarnation. A
            # later readiness poll must never redirect its stop to a new run.
            temporary = None
            try:
                state = self._read_state()
                identity = (state["run_id"], state["pid"], state["process_started_at"])
                if state["profile_id"] != self.settings.profile_id or not process_matches_owner(
                    state["pid"], state["process_started_at"]
                ):
                    return
                if self._stop_target is not None and identity != self._stop_target:
                    return
                if self.snapshot.run_id is None:
                    if not self.process or state["run_id"] != self.launch_id:
                        return
                elif state["run_id"] != self.snapshot.run_id:
                    return
                self._stop_target = identity
                marker = self.settings.data_dir / "stop.request"
                candidate = marker.with_name(f".stop-{uuid4().hex}.tmp")
                # Never open the final marker for writing: Windows denies its
                # deletion while a normal Python write handle is still open.
                # Exclusive creation also refuses an existing alias at the temp path.
                with candidate.open("x", encoding="ascii") as stream:
                    temporary = candidate
                    stream.write(state["run_id"])
                current = self._read_state()
                if (
                    (current["run_id"], current["pid"], current["process_started_at"]) != identity
                    or current["profile_id"] != self.settings.profile_id
                    or not process_matches_owner(current["pid"], current["process_started_at"])
                ):
                    return
                os.replace(temporary, marker)
                self._stop_sent = True
                self.snapshot = RuntimeSnapshot(
                    "stopping", "runtime_stopping", pid=state["pid"], run_id=state["run_id"]
                )
            except (OSError, ValueError, KeyError, psutil.Error):
                return
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def close(self):
        # Closing a launcher/dashboard does not stop the managed worker.
        self.executor.shutdown(wait=False, cancel_futures=True)
