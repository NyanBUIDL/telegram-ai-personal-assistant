# B00 — checkout, baseline, art và contracts

Status: InReview. Design approved by user 2026-10-02. Contract implementation committed at 6ac2476fab2a77921911c76102ee1f9bf06d47b9; independent review pending; baseline completed.

## Checkout and isolation

Fresh clone at E:/ChatGPT Project/Telegram/repository, feature branch codex/windows-public-beta. GitHub master and initial checkout match audit baseline 7429fcffd61860e9502f066e0b6a921df72fe176. No previous local Git checkout existed. Source/art assets fetched via Git; sparse checkout skips generated dependencies/caches/evidence. Not using audit-snapshot as product checkout.

Repo originally tracked dependencies/build/cache/local .env despite ignore rules. Bootstrap removes 28,087 ignored tracked files from feature branch index only; disk files retained. Root .gitignore additionally excludes execution scratch and temporary test helper. Git history untouched; no remote writes.

## Baseline observed

Commands executed at actual checkout, using bundled runtime and existing audit-deps for bootstrap:

```text
Python scripts/check.py pytest tests --basetemp=.test-temp/baseline -ra
150 passed in 18.44s; exit 0

Python scripts/check.py ruff check src tests alembic
exit 1: existing I001 in alembic/versions/0005_vector_store_reliability.py
This known baseline lint issue belongs to F02; no product failure hidden.

pnpm dlx npm@11.6.2 ci --ignore-scripts --no-audit --no-fund
138 packages added; package-lock used; exit 0

Node node_modules/vite/bin/vite.js build
4581 modules; exit 0; built dist/client/index.html + assets

Node node_modules/eslint/bin/eslint.js src --max-warnings=0
exit 0

Node scripts/prepare-sites-build.mjs
exit 0; dist/server/index.js and dist/.openai/hosting.json prepared

Node --test tests/sites-worker.test.mjs
4 passed, 0 failed; exit 0
```

Environment corrections: first pytest run had 135 pass/15 fixture errors because root .test-temp parent was missing, fixed by creating parent, then full baseline rerun. First Vite sandbox run hit Windows realpath EPERM; outside-sandbox build passed. Sites checks attempted while build session still running lacked dist input; repeated after build finished passed. These are bootstrap sequencing/environment errors, not product defect evidence or accepted test-red.

## Art inspection

Read actual dashboard-prototype/AGENTS.md from baseline. Reference and fonts exist in full checkout; root hashes recorded in art-baseline.json beside this report. PeterObscure only logo/main title; DarleySans other UI; paper/ink/teal/magenta/yellow, square borders/hard shadows preserved as constraints. License redistribution not yet verified (R01/P01). Source asset presence does not prove UI redesign visually matches.

## Contracts and review

Agent B00 implemented contracts.py/generated.js/contract tests and durable AGENTS instructions; focused50 passed, generator drift/targeted Ruff/generated JS ESLint clean. Full integrated suite at commit 6ac2476f: 200 passed in 17.42s, exit0. Independent reviewer must verify public schemas exclude secrets/internal tickets/leases, decimal string Telegram IDs and native command allowlist. B00 remains InReview until that review passes.

## Limits

Baseline tested synthetic fixtures only. No Telegram login, real bot/API call, MySQL integration service, clean Windows VM or usability/soak run. New task evidence must be tied to its own commit/artifact; baseline cannot sign off later behavior.
