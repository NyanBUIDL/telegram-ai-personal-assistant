"""Process lifetime proof with real disposable children and retained handles."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from types import SimpleNamespace

import psutil
import pytest

from tg_assistant.desktop.instance import process_incarnation_exists, process_matches_owner


@pytest.mark.skipif(os.name != "nt", reason="Windows process-object lifetime")
@pytest.mark.parametrize("exit_code", [1, 259])
def test_waited_child_is_absent_even_while_its_process_handle_is_retained(exit_code):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    process = subprocess.Popen([sys._base_executable, "-c", "import time; time.sleep(30)"])
    try:
        started_at = psutil.Process(process.pid).create_time()
        assert process_incarnation_exists(process.pid, started_at)
        assert kernel.TerminateProcess(int(process._handle), exit_code)
        assert process.wait(timeout=5) == exit_code
        # A retained Windows process object is still queryable after it exits.
        # The signaled original handle proves completion, including exit 259
        # which is also the GetExitCodeProcess sentinel for a running process.
        assert kernel.WaitForSingleObject(int(process._handle), 0) == 0
        assert not process_incarnation_exists(process.pid, started_at)
        assert not process_matches_owner(process.pid, started_at)
    finally:
        if process.poll() is None:
            process.kill()  # Only the original disposable child's handle.
        process.wait(timeout=5)
        process._handle.Close()


@pytest.mark.skipif(os.name != "nt", reason="Windows process creation-time and SID proof")
def test_live_child_requires_exact_creation_time_and_current_sid():
    process = subprocess.Popen([sys._base_executable, "-c", "import time; time.sleep(30)"])
    try:
        started_at = psutil.Process(process.pid).create_time()
        assert process_incarnation_exists(process.pid, started_at)
        assert process_matches_owner(process.pid, started_at)
        assert not process_incarnation_exists(process.pid, started_at + 1)
        assert not process_matches_owner(process.pid, started_at + 1)
        assert not process_incarnation_exists(process.pid, float("nan"))
        assert not process_incarnation_exists(process.pid, float("inf"))
    finally:
        process.kill()
        process.wait(timeout=5)
        process._handle.Close()


@pytest.mark.parametrize("pid, started_at", [(0, 1), (-1, 1), (True, 1), (1.0, 1), (1, True)])
def test_invalid_incarnation_identity_is_absent(pid, started_at):
    assert not process_incarnation_exists(pid, started_at)


@pytest.mark.skipif(os.name != "nt", reason="Windows inspection uncertainty")
@pytest.mark.parametrize("failed_operation", ["open", "times", "wait"])
def test_inspection_failure_cannot_prove_exit_and_closes_acquired_handle(
    failed_operation, monkeypatch
):
    started_at = psutil.Process().create_time()
    closed = []

    def open_process(*_):
        if failed_operation == "open":
            ctypes.set_last_error(5)
            return 0
        return 17

    def get_times(_handle, creation, *_):
        if failed_operation == "times":
            ctypes.set_last_error(5)
            return False
        ticks = int((started_at + 11_644_473_600) * 10_000_000)
        created = ctypes.cast(creation, ctypes.POINTER(wintypes.FILETIME)).contents
        created.dwLowDateTime = ticks & 0xFFFFFFFF
        created.dwHighDateTime = ticks >> 32
        return True

    def wait(*_):
        ctypes.set_last_error(6)
        return 0xFFFFFFFF

    # OS permission and invalid-handle failures cannot be induced safely on
    # the current owner's real process, so inject only the native API boundary.
    kernel = SimpleNamespace(
        OpenProcess=open_process,
        GetProcessTimes=get_times,
        WaitForSingleObject=wait,
        CloseHandle=lambda handle: closed.append(handle),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel)
    expected = psutil.Error if failed_operation == "wait" else psutil.AccessDenied
    with pytest.raises(expected):
        process_incarnation_exists(os.getpid(), started_at)
    assert closed == ([] if failed_operation == "open" else [17])
