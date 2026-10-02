"""Update the shared handoff tracker without inventing completion evidence."""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--task")
    parser.add_argument("--status", choices=["Planned", "Ready", "InProgress", "InReview", "Verified", "Blocked"])
    parser.add_argument("--evidence", action="append", default=[])
    args = parser.parse_args()
    handoff = Path(__file__).resolve().parent
    target = handoff / "status.json"
    state = json.loads(target.read_text(encoding="utf-8"))
    if args.approve:
        state["design_review"] = "approved_by_user_2026-10-02"
        state["implementation_started"] = True
    if args.task:
        if not args.status:
            parser.error("--task requires --status")
        task = next(t for t in state["tasks"] if t["id"] == args.task)
        evidence = list(dict.fromkeys(task["evidence"] + args.evidence))
        if args.status == "Verified" and not evidence:
            parser.error("Verified requires evidence")
        task["status"] = args.status
        task["evidence"] = evidence
    target.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checklist_path = handoff / "MASTER_CHECKLIST.md"
    checklist = checklist_path.read_text(encoding="utf-8")
    if args.approve:
        checklist = checklist.replace(
            "- [ ] Người dùng review thiết kế/kế hoạch để bắt đầu implementation.",
            "- [x] Người dùng duyệt thiết kế/kế hoạch ngày 02/10/2026; implementation bắt đầu."
        ).replace(
            "**Tài liệu đã chuẩn bị; code chưa triển khai; beta chưa sẵn sàng.**",
            "**Thiết kế đã duyệt; implementation đang thực hiện; beta chưa sẵn sàng.**"
        )
    for task in state["tasks"]:
        pattern = rf"^- \[[ x]\] \*\*{task['id']} — .*?$"
        replacement = (
            f"- [{'x' if task['status'] == 'Verified' else ' '}] **{task['id']} — {task['title']}**"
            f" · {task['owner']} · phụ thuộc: {', '.join(task['dependencies']) or 'không'} · {task['status']}."
        )
        checklist = re.sub(pattern, lambda _: replacement, checklist, flags=re.M)
    checklist_path.write_text(checklist, encoding="utf-8")
    fields = ["id", "milestone", "owner", "dependencies", "status", "acceptance", "evidence"]
    with (handoff / "roadmap.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for task in state["tasks"]:
            writer.writerow({
                "id": task["id"], "milestone": task["milestone"], "owner": task["owner"],
                "dependencies": "|".join(task["dependencies"]), "status": task["status"],
                "acceptance": task["acceptance"], "evidence": "|".join(task["evidence"])
            })
    print(f"Updated {args.task or 'design review'}; beta_ready={state['beta_ready']}")


if __name__ == "__main__":
    main()
