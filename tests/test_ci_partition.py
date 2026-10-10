"""Prove shard coverage using real disposable pytest collections and executions."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COUNTS = {"passed", "failed", "skipped", "errors", "exit_code"}


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "def pytest_sessionfinish(session):\n"
        "    Path('inventory.json').write_text(json.dumps([item.nodeid for item in session.items]))\n",
        encoding="utf-8",
    )
    for name in ("alpha", "beta", "gamma", "delta"):
        (tmp_path / f"test_{name}.py").write_text(
            "import pytest\n@pytest.mark.parametrize('value', [1, 2], "
            "ids=['synthetic-private-one', 'synthetic-private-two'])\n"
            "def test_value(value): assert value > 0\n",
            encoding="utf-8",
        )
    return tmp_path


def run(project, *args, direct=False):
    command = [sys.executable]
    command += ["-m", "pytest"] if direct else [
        str(ROOT / "scripts/ci_checks.py"), "--report", "summary.json"
    ]
    result = subprocess.run(
        [*command, "-q", *args], cwd=project, env=dict(os.environ),
        capture_output=True, text=True, timeout=60,
    )
    inventory = project / "inventory.json"
    nodes = set(json.loads(inventory.read_text())) if inventory.exists() else set()
    records = [line.removeprefix("ci_partition=") for line in result.stdout.splitlines()
               if line.startswith("ci_partition=")]
    partition = json.loads(records[0]) if records else None
    if partition is not None:
        assert len(records) == 1
        assert set(partition) == {"collected", "selected", "deselected", "shard_index", "shard_count"}
        assert all(type(value) is int and value >= 0 for value in partition.values())
    return result, nodes, partition


def test_real_partition_union_disjoint_whole_files_and_new_discovery(project):
    direct, complete, _ = run(project, direct=True)
    default, unsharded, _ = run(project)
    assert direct.returncode == default.returncode == 0
    assert len(complete) == 8 and complete == unsharded
    summary = json.loads((project / "summary.json").read_text())
    assert set(summary) == COUNTS and summary["passed"] == len(complete)
    for newly_added in (False, True):
        if newly_added:
            (project / "test_newly_discovered.py").write_text("def test_new(): assert True\n")
            result, complete, _ = run(project, direct=True)
            assert result.returncode == 0 and len(complete) == 9
        selections = []
        for index in range(2):
            result, selected, counts = run(project, "--shard-index", str(index), "--shard-count", "2")
            assert result.returncode == 0, result.stdout + result.stderr
            assert selected
            assert f"{len(complete - selected)} deselected" in result.stdout
            assert counts == {"collected": len(complete), "selected": len(selected),
                              "deselected": len(complete - selected), "shard_index": index,
                              "shard_count": 2}
            summary = json.loads((project / "summary.json").read_text())
            assert set(summary) == COUNTS and summary["passed"] == len(selected)
            assert "synthetic-private" not in json.dumps(counts) + json.dumps(summary)
            selections.append(selected)
        assert selections[0] | selections[1] == complete
        assert not selections[0] & selections[1]
        files = [{node.split("::", 1)[0] for node in selected} for selected in selections]
        assert not files[0] & files[1]
        repeated, same_selection, _ = run(project, "--shard-index", "0", "--shard-count", "2")
        assert repeated.returncode == 0 and same_selection == selections[0]


def test_selected_failure_and_empty_shard_fail(project):
    results = [run(project, "test_alpha.py", "--shard-index", str(index), "--shard-count", "2")
               for index in range(2)]
    assert sorted(result.returncode for result, _, _ in results) == [0, 5]
    owner = next(index for index, (_, nodes, _) in enumerate(results) if nodes)
    (project / "test_alpha.py").write_text("def test_failure(): assert False\n")
    result, selected, counts = run(project, "test_alpha.py", "--shard-index", str(owner), "--shard-count", "2")
    assert result.returncode == 1 and len(selected) == 1 and counts["selected"] == 1
    assert json.loads((project / "summary.json").read_text())["failed"] == 1


def test_collection_failure_is_not_hidden_in_either_shard(project):
    (project / "test_broken.py").write_text("def broken(\n")
    for index in range(2):
        result, _, _ = run(project, "--shard-index", str(index), "--shard-count", "2")
        assert result.returncode == 2
        assert json.loads((project / "summary.json").read_text())["errors"] == 1


@pytest.mark.parametrize("args", [
    ["--shard-index", "0"], ["--shard-count", "2"],
    ["--shard-index", "-1", "--shard-count", "2"],
    ["--shard-index", "2", "--shard-count", "2"],
    ["--shard-index", "0", "--shard-count", "0"],
    ["--shard-index", "0", "--shard-count", "-2"],
    ["--shard-index", "invalid", "--shard-count", "2"],
])
def test_invalid_partition_arguments_fail_before_collection(project, args):
    result, nodes, _ = run(project, *args)
    assert result.returncode == 2
    assert "usage: ci_checks.py" in result.stderr
    assert not nodes and not (project / "summary.json").exists()
