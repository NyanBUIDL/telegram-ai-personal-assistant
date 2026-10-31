# Ease-of-use Implementation Plan

> Agentic execution: explicit owner authorization permits role delegation. Main agent owns startup/DevOps/docs and final integration; scoped implementers own Telegram, RAG and dashboard changes. Each behavior fix requires a reproducing regression followed by passing checks.

**Goal:** Reliable first start and recoverable everyday interactions.
**Architecture:** Preserve existing service boundaries and authorization checks; fix root causes at the shared caller/helper.
**Stack:** Python 3.12+, pytest, SQLite mocks, PowerShell, React/Vite, Node tests, Playwright.
**Spec:** docs/superpowers/specs/2026-10-31-ease-of-use-design.md

## Constraints / review focus

- No live Telegram/AI, credential access, publishing or data deletion.
- Preserve local config/environment; no automatic recursive cleanup.
- Check state navigation commands, local midnight, invalid search dates, expired cache refresh, citation modes and list pagination.

## Task 1 — Telegram recovery

Files: src/tg_assistant/telegram/control_bot.py; focused tests in tests/.
- [x] Reproduce command interception, local-day task boundary and direct-search error/chunking problems.
- [x] Reuse shared handlers/date parsing; keep authorization behavior.
- [x] Run focused tests and lint. Return exact coverage and evidence.

## Task 2 — RAG cache reliability

Files: src/tg_assistant/ai/rag.py, local_first.py if needed; tests/.
- [x] Reproduce expired-row collision and opposite citation-mode reuse.
- [x] Implement portable race-safe refresh under current transactions and fingerprint rendering options.
- [x] Test failure recovery/concurrent insert conflict where practical; run relevant existing RAG tests.

## Task 3 — Dashboard fidelity

Files: dashboard-prototype/src/views/GroupsView.jsx and focused supporting files/tests only.
- [x] Confirm a visible control failure in inactive recommendations or navigation.
- [x] Correct filtering/pagination/navigation using existing APIs and UI.
- [x] Test controls and boundary states, run lint/build/Node suite; browser if available.

## Task 4 — Startup and delivery

Files: start.ps1, tests/startup regression, README.md, TROUBLESHOOTING.md, docs/review, .github/workflows.
- [x] Test broken environment and child-command failure using offline subprocess stubs.
- [x] Validate Python without silently continuing on native exit failure; preserve .venv and show recovery instructions.
- [x] Clarify first successful Telegram flow and developer setup; automate offline regression checks in CI.
- [x] Review tracked generated artifacts without printing their contents; record a safe cleanup recommendation.

## Integration

- [x] Independent review of final code diff.
- [x] Full pytest, Ruff, frontend lint/build/Node tests, targeted mocked browser checks where tooling permits.
- [x] Record passed/failed/not-run evidence, coverage, backlog and risks; commit local branch and prepare draft PR text/patch without publishing.

## Discovered first-run blockers added during review

- [x] Task 5: migration 0001 creates current metadata, so 0002/0003 collide on modern tables/columns. Make later upgrades idempotent following 0004/0005, test fresh and partial SQLite schemas without altering live MySQL. Fix reindex's missing BudgetService argument and test CLI with fake local engines.
- [x] Task 6: a queued learning job must not undo a newer owner BLOCK. Check authorization revision under policy row lock before grants and embedding; cancel obsolete jobs with a visible reason. Preserve explicitly confirmed later learning. Test queue/block/worker and same-second conservative handling.

Ruling: include these verified blockers because a new installation cannot complete and an owner cannot trust source revocation otherwise. No schema migration or Telegram operation is applied to the owner's live data. Historical migration metadata design remains a documented limitation; no schema rewrite is attempted.
