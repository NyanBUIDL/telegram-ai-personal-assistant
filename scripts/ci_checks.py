"""Run installed SQLite/product pytest with sanitized count-only evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from windows_fixture_owner import prepare_windows_fixture_owner


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
