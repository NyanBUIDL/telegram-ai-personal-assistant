"""Validate handoff structure only; this does not test product readiness."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path


def main() -> None:
    handoff = Path(__file__).resolve().parent
    root = handoff.parent.parent
    state = json.loads((handoff / "status.json").read_text(encoding="utf-8"))
    tasks = state["tasks"]
    ids = [task["id"] for task in tasks]
    assert len(ids) == len(set(ids)) == 26, "Duplicate or missing task IDs"
    by_id = {task["id"]: task for task in tasks}
    allowed = {"Planned", "Ready", "InProgress", "InReview", "Verified", "Blocked"}
    visited: set[str] = set()
    active: set[str] = set()

    def visit(task_id: str) -> None:
        assert task_id in by_id, f"Unknown dependency {task_id}"
        assert task_id not in active, f"Dependency cycle at {task_id}"
        if task_id in visited:
            return
        active.add(task_id)
        for dependency in by_id[task_id]["dependencies"]:
            visit(dependency)
        active.remove(task_id)
        visited.add(task_id)

    for task in tasks:
        assert task["status"] in allowed
        visit(task["id"])
        if task["status"] == "Verified":
            assert task["evidence"], f"Missing evidence {task['id']}"
            assert all(by_id[d]["status"] == "Verified" for d in task["dependencies"])

    plan = (root / state["canonical_plan"]).read_text(encoding="utf-8")
    assert set(re.findall(r"^## ([A-Z]\d{2}) —", plan, re.M)) == set(ids)
    for task in tasks:
        section = re.search(rf"^## {task['id']} —.*?(?=^## |\Z)", plan, re.M | re.S)
        assert section is not None
        assert f"**Owner:** {task['owner']}." in section.group()
        dependencies = re.search(r"\*\*Depends on:\*\* (.+)\.", section.group())
        assert dependencies is not None
        assert re.findall(r"\b[A-Z]\d{2}\b", dependencies.group(1)) == task["dependencies"]

    checklist = (handoff / "MASTER_CHECKLIST.md").read_text(encoding="utf-8")
    rows = {task_id: mark for mark, task_id in re.findall(
        r"^- \[([ x])\] \*\*([A-Z]\d{2}) —", checklist, re.M
    )}
    assert set(rows) == set(ids)
    assert all((rows[t["id"]] == "x") == (t["status"] == "Verified") for t in tasks)

    with (handoff / "roadmap.csv").open(encoding="utf-8", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert len(csv_rows) == 26
    assert {row["id"] for row in csv_rows} == set(ids)
    for row in csv_rows:
        task = by_id[row["id"]]
        assert row["status"] == task["status"]
        assert row["milestone"] == task["milestone"]
        assert row["owner"] == task["owner"]
        assert row["dependencies"].split("|") == task["dependencies"] or (
            not row["dependencies"] and not task["dependencies"]
        )

    links_checked = 0
    documents = list(handoff.rglob("*.md")) + [root / state["canonical_spec"], root / state["canonical_plan"]]
    for document in documents:
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            if target.startswith(("https://", "http://", "#")):
                continue
            path = (document.parent / target.split("#")[0]).resolve()
            assert path.exists(), f"Broken link in {document.name}: {target}"
            links_checked += 1

    assert len(state["gates"]) == 7
    if state["beta_ready"]:
        assert all(t["status"] == "Verified" for t in tasks)
        assert all(g["status"] == "Verified" and g["evidence"] for g in state["gates"])
    print(f"PASS: {len(ids)} tasks; dependency DAG; plan/JSON/CSV/checklist agree; {links_checked} local links")
    print(f"Product: implementation_started={state['implementation_started']}, beta_ready={state['beta_ready']}")


if __name__ == "__main__":
    main()
