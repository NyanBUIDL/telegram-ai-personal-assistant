"""Prepare a Windows-native persistent execution ledger and task briefs."""
from __future__ import annotations

import itertools
import json
import re
import shutil
from pathlib import Path


def file_keys(text: str) -> set[str]:
    # Ownership collisions are file-level; shortened plan paths use common suffixes.
    return set(re.findall(r"[\w.-]+\.(?:py|jsx|js|css|toml|ya?ml|iss|ps1|md)", text))


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    checkout = root / "repository"
    shutil.copytree(root / "docs", checkout / "docs", dirs_exist_ok=True)
    shutil.copy2(root / "RELEASE_READINESS_REVIEW_VI.md", checkout / "RELEASE_READINESS_REVIEW_VI.md")
    handoff = checkout / "docs" / "handoff"
    state = json.loads((handoff / "status.json").read_text(encoding="utf-8"))
    plan_path = state["canonical_plan"]
    plan = (checkout / plan_path).read_text(encoding="utf-8")
    workspace = checkout / ".superpowers" / "sdd" / "windows-public-beta-2026-10-02"
    workspace.mkdir(parents=True, exist_ok=True)
    constraints = plan.split("## Global Constraints", 1)[1].split("## Review Focus", 1)[0]
    sections = {}
    for task in state["tasks"]:
        section = re.search(rf"^## {task['id']} —.*?(?=^## |\Z)", plan, re.M | re.S).group()
        sections[task["id"]] = section
        (workspace / f"task-{task['id']}-brief.md").write_text(
            section + "\n## Binding global constraints\n" + constraints,
            encoding="utf-8"
        )
    ledger = workspace / "progress.md"
    if not ledger.exists():
        lines = [
            f"# SDD ledger — plan: {plan_path}",
            "", "User approved design and implementation on 2026-10-02.",
            "Baseline GitHub master/checkout: 7429fcffd61860e9502f066e0b6a921df72fe176.",
            "Branch: codex/windows-public-beta; fresh dedicated clone, no existing user checkout modified.",
            "", "Ruling: Use fresh dedicated clone on a feature branch as isolation — workspace originally had no Git checkout; cost if wrong: move commits to a managed worktree before integration.",
            "Ruling: Use Windows-native Python brief/ledger setup instead of bundled bash scripts — bash executable unavailable; preserve task/report/diff artifacts and review semantics; cost if wrong: adjust helper paths.",
            "Ruling: User-authorized parallel workers may implement disjoint files after contracts lock; serialize shared files — explicit approved plan overrides skill's generic sequential implementation rule; cost if wrong: integration rework.",
            "Ruling: Preserve evidence ledger and user-requested plan/trackers at completion instead of deleting them — user requested durable comparison/handoff; cost if wrong: extra ignored scratch storage.",
            "", "## Preflight: task internal consistency", "", "| Task | Tests vs implementation / file ownership review |", "|---|---|"
        ]
        corrections = {
            "B00": "New contract tests meaningful; baseline/checkout/art use inspection, no fake-red. CI owned Q01.",
            "F02": "Repair must precede failing 0002; freeze historical schema, refuse unknown DB; dialect fixture acceptance required.",
            "S01": "aiosqlite moves to runtime; no automatic MySQL conversion; resolve per-user paths before full setup.",
            "S02": "SQLite CAS/leases required despite singleton; cannot promise exactly-once Telegram delivery.",
            "V01": "Canonical EmbeddingProfile includes endpoint_id; LOCAL ONLY also filters shared chat context; budget reservation atomic.",
            "D02": "Setup-only session supports nullable owner; management requires verified pairing; one-use ticket/code.",
            "O01": "Readiness evidence backend-issued; incomplete setup allowed but must not claim first AI answer.",
            "Q03": "Real users/72h soak remain pending until actually performed; no fake completion by mocks.",
            "R02": "Beta-ready is gated by human/artifact proof; public publish is separate authorized action."
        }
        for task in state["tasks"]:
            lines.append(f"| {task['id']} | {corrections.get(task['id'], 'Acceptance aligns with spec; implementation and test file names are proposed additions; verify against checkout before editing.')} |")
        lines += ["", "## Preflight: shared file/interface pairs", "", "| Pair | Shared surface | Resolution |", "|---|---|---|"]
        for left, right in itertools.combinations(state["tasks"], 2):
            left_files = re.search(r"\*\*Files:\*\* (.+)", sections[left["id"]]).group(1)
            right_files = re.search(r"\*\*Files:\*\* (.+)", sections[right["id"]]).group(1)
            common = file_keys(left_files) & file_keys(right_files)
            if common:
                lines.append(f"| {left['id']} / {right['id']} | {', '.join(sorted(common))} | Serialize edits; canonical spec contract, coordinator integration and task-scoped review. |")
            elif left["id"] in right["dependencies"]:
                lines.append(f"| {left['id']} / {right['id']} | prerequisite service/schema/evidence | Consumer starts after producer Verified; preserve canonical service names. |")
        lines += ["", "All tasks consume contracts.py/generated.js from B00. Coordinator owns changes to shared schema; no parallel DTO edits.", "", "## Execution", "", "Task B00: InProgress — checkout prepared; baseline and contract implementation next."]
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(workspace)


if __name__ == "__main__":
    main()
