# Proposed draft pull request

Title: Improve first-run setup and everyday Telegram/dashboard reliability
Base: master
Head: codex/ease-of-use-2026-10-31
Status: local proposal only; not published

Windows setup could stop on a missing dependency, fresh migrations collided with current metadata, commands were trapped in Telegram input flows, expired/concurrent AI cache writes failed, and background learning could restore an owner-blocked source. Dashboard refreshes also discarded edits and inactive-source controls did not affect the list.

This release repairs those paths while retaining default-deny permissions, confirmation gates, the current stack and existing visual design. It also supplies offline regression coverage, short onboarding/recovery guidance and a CI workflow. BLOCK cancels obsolete learning jobs and workers recheck authorization before sync/embedding; later explicit confirmation can create a fresh job.

Validation:198 Python tests,6 mocked Chrome regressions,4 Sites tests, Python/frontend lint, production build, packaging and pip check pass. Independent review found no remaining Important/Critical issues in the authored diff. See docs/review/VALIDATION.md and RELEASE_REVIEW.md for coverage and prioritized remaining issues.

Before using the updated dashboard, run npm ci and npm run build in dashboard-prototype. Generated dependencies/build output, .env and personal captures are excluded from this commit. Existing tracked-artifact hygiene remains a separate follow-up.

Limits: no real Telegram/model calls, MySQL concurrency run, clean-account installation or GitHub CI run. Same-second authorization ambiguity requires reconfirmation. No deployment is included.

Approval still required: push this branch and open a draft PR. Merge/deployment are separate decisions. No external GitHub mutation has occurred.
