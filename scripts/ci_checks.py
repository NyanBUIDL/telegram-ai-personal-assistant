"""Run installed pytest with count-only evidence and mandatory dialect coverage."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

REQUIRED_MYSQL_MODULES = {
    "test_migrations.py",
    "test_revocation_jobs.py",
    "test_job_concurrency.py",
    "test_embedding_profiles.py",
    "test_budget_migrations.py",
    "test_runtime_embeddings.py",
}
# F02 parametrizes the SQLite rowid semantic check over both fixture backends;
# its MySQL variant is intentionally inapplicable, not required dialect coverage.
SQLITE_ONLY_CASES = {
    ("test_migrations.py", "test_sqlite_integer_pk_alias_refused"),
    ("test_job_concurrency.py", "test_sqlite_busy_is_bounded"),
    ("test_job_concurrency.py", "test_cancel_does_not_overwrite_confirmed_action"),
}


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
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    security.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    security.SetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
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


def disposable_mysql_configured() -> bool:
    """Never print connection values, and never accept a production DB target."""
    databases = []
    for key, driver in (
        ("TG_TEST_MYSQL_URL", "mysql+pymysql"),
        ("TG_TEST_F01_MYSQL_URL", "mysql+asyncmy"),
    ):
        try:
            url = make_url(os.environ.get(key, ""))
            if (
                url.drivername != driver
                or url.host not in {"127.0.0.1", "localhost"}
                or not (url.database or "").startswith("codex_")
            ):
                return False
            databases.append(url.database)
        except (ValueError, TypeError, ArgumentError):
            return False
    return len(set(databases)) == 2


class Evidence:
    def __init__(self, report: Path, mysql_required: bool):
        self.report = report
        self.mysql_required = mysql_required
        self.counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
        self.required = set()
        self.passed = set()

    def pytest_collection_modifyitems(self, config, items):
        if not self.mysql_required:
            return
        selected, deselected = [], []
        for item in items:
            params = getattr(getattr(item, "callspec", None), "params", {})
            backend = params.get(
                "connection",
                params.get(
                    "backend",
                    params.get(
                        "storage", params.get("budget_connection", params.get("runtime_case"))
                    ),
                ),
            )
            is_mysql = (
                backend == "mysql"
                if backend is not None
                else item.originalname.startswith("test_mysql_")
            )
            module = Path(item.path).name
            if (
                module in REQUIRED_MYSQL_MODULES
                and is_mysql
                and (module, item.originalname) not in SQLITE_ONLY_CASES
            ):
                selected.append(item)
                self.required.add(item.nodeid)
            else:
                deselected.append(item)
        items[:] = selected
        config.hook.pytest_deselected(items=deselected)

    def pytest_runtest_logreport(self, report):
        if report.when == "call":
            self.counts[report.outcome] += 1
            if report.passed:
                self.passed.add(report.nodeid)
        elif report.failed:
            self.counts["errors"] += 1
        elif report.skipped:
            self.counts["skipped"] += 1

    def pytest_collectreport(self, report):
        if report.failed:
            self.counts["errors"] += 1

    def pytest_sessionfinish(self, session, exitstatus):
        modules = {Path(node.split("::")[0]).name for node in self.required}
        if self.mysql_required and (
            modules != REQUIRED_MYSQL_MODULES
            or self.required != self.passed
            or self.counts["skipped"]
            or self.counts["errors"]
        ):
            session.config.get_terminal_writer().line("required MySQL cases did not pass")
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        self.report.parent.mkdir(parents=True, exist_ok=True)
        self.report.write_text(
            json.dumps({**self.counts, "exit_code": int(session.exitstatus)}) + "\n",
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mysql-required", action="store_true")
    parser.add_argument("--report", type=Path, default=Path(".ci-work/summary.json"))
    args, pytest_args = parser.parse_known_args()
    if args.mysql_required and not disposable_mysql_configured():
        print("disposable MySQL URLs required (separate loopback codex_ databases)")
        return 2
    try:
        prepare_windows_fixture_owner()
    except (OSError, RuntimeError):
        print("windows_fixture_owner_unavailable")
        return 2
    return int(pytest.main(pytest_args, plugins=[Evidence(args.report, args.mysql_required)]))


if __name__ == "__main__":
    raise SystemExit(main())
