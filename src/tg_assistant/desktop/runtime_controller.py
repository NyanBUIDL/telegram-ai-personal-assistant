"""Nonblocking process lifecycle; credentials never enter subprocess arguments."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import psutil

from ..contracts import ConnectionStatus
from ..paths import ensure_runtime_dirs, resource_path
from .instance import process_incarnation_exists, process_matches_owner


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
        self.launch_id = None
        self.start_failed = False
        self.attached_process = None

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
        self.stop_requested = False
        self.start_failed = False
        return self.executor.submit(self._start_safely)

    def _start_safely(self):
        try:
            self._start()
        except (OSError, ValueError):
            self.start_failed = True
            self.snapshot = RuntimeSnapshot("error", "runtime_start_failed")

    def _start(self):
        if self.process and self.process.poll() is None:
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

    def _refresh(self):
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
            response = httpx.get(
                f"http://127.0.0.1:{port}/api/v1/runtime/readiness",
                timeout=0.5,
                follow_redirects=False,
                trust_env=False,
            )
            response.raise_for_status()
            if response.headers.get("X-TG-Runtime-ID") != state["run_id"]:
                return self.snapshot
            measured = ConnectionStatus.model_validate(response.json())
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
        except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError, psutil.Error):
            pass
        return self.snapshot

    def stop(self):
        self.stop_requested = True
        if self.snapshot.phase not in {"starting", "ready"}:
            return
        # A stale controller cannot send a shutdown for a newer incarnation.
        try:
            state = self._read_state()
            if self.snapshot.run_id is None:
                if not self.process or state["run_id"] != self.launch_id:
                    return
            elif state["run_id"] != self.snapshot.run_id:
                return
            (self.settings.data_dir / "stop.request").write_text(state["run_id"], encoding="ascii")
            self.snapshot = RuntimeSnapshot("stopping", "runtime_stopping", pid=state["pid"])
        except (OSError, ValueError, KeyError):
            return

    def close(self):
        # Closing a launcher/dashboard does not stop the managed worker.
        self.executor.shutdown(wait=False, cancel_futures=True)
