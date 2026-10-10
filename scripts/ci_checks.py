"""Run installed SQLite/product pytest with sanitized count-only evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pytest
from windows_fixture_owner import prepare_windows_fixture_owner


class Evidence:
    def __init__(self, report: Path, shard_index: int | None = None, shard_count: int | None = None):
        self.report = report
        self.shard_index = shard_index
        self.shard_count = shard_count
        self.counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config, items):
        if self.shard_count is None:
            return
        selected, deselected = [], []
        for item in items:
            filename = item.path.relative_to(config.rootpath).as_posix()
            shard = int.from_bytes(hashlib.sha256(filename.encode("utf-8")).digest()) % self.shard_count
            (selected if shard == self.shard_index else deselected).append(item)
        print("\nci_partition=" + json.dumps({
            "collected": len(items), "selected": len(selected), "deselected": len(deselected),
            "shard_index": self.shard_index, "shard_count": self.shard_count,
        }))
        items[:] = selected
        config.hook.pytest_deselected(items=deselected)

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
    parser.add_argument("--shard-index", type=int)
    parser.add_argument("--shard-count", type=int)
    args, pytest_args = parser.parse_known_args()
    if (args.shard_index is None) != (args.shard_count is None):
        parser.error("--shard-index and --shard-count must be supplied together")
    if args.shard_count is not None and (
        args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count
    ):
        parser.error("shards require count > 0 and 0 <= index < count")
    try:
        prepare_windows_fixture_owner()
    except (OSError, RuntimeError):
        print("windows_fixture_owner_unavailable")
        return 2
    return int(pytest.main(pytest_args, plugins=[Evidence(args.report, args.shard_index, args.shard_count)]))


if __name__ == "__main__":
    raise SystemExit(main())
