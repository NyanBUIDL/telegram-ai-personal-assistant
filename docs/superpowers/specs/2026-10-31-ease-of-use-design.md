# Ease-of-use release design — 2026-10-31

Owner request: autonomously review the project with PM/PO, BA, UX, architecture, development and DevOps roles, then improve ease of use. The owner delegated routine design decisions; no additional approval is needed for local edits/tests. Publishing remains outside this release.

## Goal and acceptance

A Windows owner can identify setup problems, start reliably, escape Telegram input flows, see tasks for their local calendar day, and repeat AI questions without cache failures. Dashboard controls must accurately affect the displayed list.

Keep current Python/Telegram/MySQL/FastAPI/React architecture, Vietnamese interaction copy, default-deny permissions, local credentials and confirmation boundaries. No real Telegram accounts, credential reads, data deletion, cloud AI calls, infrastructure, dependency upgrades, deployment or push.

## Approaches

1. Rewrite onboarding/dashboard architecture: excessive scope and regression risk.
2. Add new features: increases complexity before existing flows are reliable.
3. Repair observable setup/navigation/cache failures with regression coverage and concise guidance: selected by the implementing agent under the user's autonomous authorization.

## Release

- Validate the actual Python interpreter at startup; fail clearly on a relocated/broken environment and failed installation. Preserve existing environments and data.
- Telegram commands bypass text-wait handlers; cancellation and menu navigation release waiting state. Local calendar dates and search error handling use existing services.
- Expired RAG cache rows can refresh without duplicate primary-key errors. Concurrent identical misses do not poison the transaction. Citation rendering options are part of the cache identity.
- Fix the highest-impact verified dashboard navigation/list issue after review, with automated browser or JavaScript regression coverage.
- Document first useful workflow, recovery, coverage, test evidence and prioritized backlog. Add offline CI appropriate to existing tests.

## Limits

No claim that binary/generated dependencies or personal browser captures were read. Inventory all tracked paths, review authored code/docs/tests by responsibility, exclude .env content, dependencies, logs, sessions and personal captures. Repository artifact hygiene is recorded separately from behavior changes. Live Telegram/MySQL and installation on a clean Windows account remain manual acceptance checks.
