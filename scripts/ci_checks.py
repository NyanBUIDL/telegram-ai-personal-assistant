"""Run installed SQLite/product pytest with sanitized count-only evidence."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest


def prepare_windows_fixture_owner() -> None:
    """Create test objects for this user even in an elevated Windows runner.

    An elevated runner's default object owner can be Administrators. Normalize
    this process token's default owner to its existing user SID, without changing
    privileges, identities or any existing object's owner/security descriptor.
    Production's foreign-owner refusal remains unchanged.
    """
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    security.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    security.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    security.SetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    security.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    token = wintypes.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x88, ctypes.byref(token)):
        raise RuntimeError("windows_fixture_owner_unavailable")
    try:

        def information(kind):
            size = wintypes.DWORD()
            security.GetTokenInformation(token, kind, None, 0, ctypes.byref(size))
            if not 0 < size.value <= 65536:
                raise RuntimeError("windows_fixture_owner_unavailable")
            buffer = ctypes.create_string_buffer(size.value)
            if not security.GetTokenInformation(token, kind, buffer, size, ctypes.byref(size)):
                raise RuntimeError("windows_fixture_owner_unavailable")
            return buffer

        user = information(1)  # TOKEN_USER starts with SID_AND_ATTRIBUTES.Sid.
        owner = information(4)  # TOKEN_OWNER starts with its Owner pointer.
        user_sid = ctypes.cast(user, ctypes.POINTER(ctypes.c_void_p))[0]
        owner_sid = ctypes.cast(owner, ctypes.POINTER(ctypes.c_void_p))[0]
        if not security.EqualSid(user_sid, owner_sid):
            desired = ctypes.c_void_p(user_sid)
            if not security.SetTokenInformation(
                token, 4, ctypes.byref(desired), ctypes.sizeof(desired)
            ):
                raise RuntimeError("windows_fixture_owner_unavailable")
            owner = information(4)
            owner_sid = ctypes.cast(owner, ctypes.POINTER(ctypes.c_void_p))[0]
            if not security.EqualSid(user_sid, owner_sid):
                raise RuntimeError("windows_fixture_owner_unavailable")
    finally:
        kernel.CloseHandle(token)


class Evidence:
    def __init__(self, report: Path):
        self.report = report
        self.counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}

    def pytest_runtest_logreport(self, report):
        if report.when == "call":
            self.counts[report.outcome] += 1
        elif report.failed:
            self.counts["errors"] += 1
        elif report.skipped:
            self.counts["skipped"] += 1

    def pytest_collectreport(self, report):
        if report.failed:
            self.counts["errors"] += 1

    def pytest_sessionfinish(self, session, exitstatus):
        self.report.parent.mkdir(parents=True, exist_ok=True)
        self.report.write_text(
            json.dumps({**self.counts, "exit_code": int(session.exitstatus)}) + "\n",
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=Path(".ci-work/summary.json"))
    args, pytest_args = parser.parse_known_args()
    try:
        prepare_windows_fixture_owner()
    except (OSError, RuntimeError):
        print("windows_fixture_owner_unavailable")
        return 2
    return int(pytest.main(pytest_args, plugins=[Evidence(args.report)]))


if __name__ == "__main__":
    raise SystemExit(main())
