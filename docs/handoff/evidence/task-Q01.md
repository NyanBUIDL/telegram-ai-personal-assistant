# Q01 — CI baseline and hardening

Status: **Code-ready, awaiting independent coordinator review and a real GitHub CI run.** No remote workflow was triggered, and no remote run ID/log exists yet. Local runner results and valid YAML do not count as GitHub CI success or beta readiness.

Implemented against `codex/windows-public-beta`. The full integrated fresh-environment run used production HEAD `885f9b4d380566528093f38bff751180918eaae9` plus the uncommitted Q01 QA files. The coordinator subsequently fixed F01 at `c31a8f63`; the final required-MySQL run included that fix. F02 review fixes are a separate follow-up, not included in the historical full-suite result.

## Resulting gates

The workflow runs on push, pull request and manual dispatch. Two Windows jobs install declared `.[dev,desktop]` dependencies using Python 3.12/3.13, run Ruff including Alembic, check both generated mirrors, and run the full Python suite. Contracts, SQLite zero/upgrade migrations, policy/concurrency and native Qt are required regardless of aggregate coverage. Native logical scales 125/150/200% have separate checks; scale 100% runs in the full suite. These do not replace physical Windows DPI tests.

An Ubuntu job uses a disposable `mysql:8.4.11` service and separate migration/revocation fixture databases. The required runner selects actual MySQL cases from both F02 and F01, requires both modules to contribute passing cases, and fails on missing configuration, missing coverage, skipped eligible cases or errors. Its only inapplicable-case exemption is the exact F02 `test_sqlite_integer_pk_alias_refused[mysql]` test. A new SQLite-only test will not receive an automatic exemption.

The Node 24 job uses `npm ci`, build, lint, Sites tests and Playwright-installed Chromium. The browser tests retain production primitives, fonts and real unauthenticated screen rendering. The shared auto fixture fails on unexpected console errors or page exceptions, without echoing arbitrary error payloads. Two injected-error tests use Playwright expected-failure annotations: removing the gate causes “Expected to fail, but passed” and makes CI fail.

The CI Vite config disables optional HMR, and the isolated art HTML no longer imports an unnecessary refresh runtime. The login fixture rejects API fetches in memory, preserving the existing offline unauthenticated screen without intentionally causing browser resource errors. A separately opted-in **local-only** HMR exception is limited to documented blocked loopback diagnostics from the Vite client; it cannot apply with `CI=true`. No product styles, theme, tokens or contract schema changed.

Actions are pinned to immutable hashes read from their official v6/v4 public tags. Workflow permissions are `contents: read`; checkout does not persist credentials. There is no production secret, live Telegram/provider test, signing, publishing, deployment or PR write permission. The final “Required CI” job requires every upstream gate to succeed. Branch protection configuration remains an external coordinator action.

Artifact staging copies only a validated numeric summary and exact synthetic browser/native PNG filenames into a fresh directory. Unknown fields, unexpected files, symlinks and invalid image signatures are rejected or excluded. Neither JUnit/Playwright traces, test stdout, `.env`, sessions, credential files, local logs, database/vector data nor dumps are uploaded. Evidence retention is seven days. Packaging gates must be added when reviewed P01/P02 artifacts actually exist; Q01 does not fabricate a release gate.

## Local evidence

Python: fresh `.venv-q01`, created from bundled Python 3.12. Node: bundled Node 24.19.0, npm 11.6.2 via cached `pnpm dlx`. This validation did not require audit dependencies. PyYAML was installed **after** the integrated suite solely for local YAML inspection; it is not an undeclared project test dependency.

| Check / command | Observed result |
|---|---|
| `.venv-q01/Scripts/python.exe -m pip install -e ".[dev,desktop]"` | Fresh install successful; `pip check` clean |
| `python scripts/ci_checks.py --report .test-temp/q01-suite2/summary.json --basetemp=.test-temp/q01-suite2/pytest`, MySQL unset, Qt offscreen | 294 passed, 34 expected dialect skips, exit 0, 45.78s |
| `python scripts/ci_checks.py --report .test-temp/q01-integrated/summary.json --basetemp=.test-temp/q01-integrated/pytest`, both approved disposable MySQL URLs set, Qt offscreen | 324 passed, four intentional other-dialect skips, exit 0, 108.62s; historical HEAD `885f9b4d` |
| `python scripts/ci_checks.py --mysql-required --report .test-temp/q01-mysql-final/summary.json tests/integration/test_migrations.py tests/test_revocation_jobs.py --basetemp=.test-temp/q01-mysql-final/pytest` | 33 passed, **zero skipped**, 74 deselected, exit 0, 65.21s |
| `python -m pytest tests/test_ci_checks.py --basetemp=.test-temp/q01-final-gates` | Seven passed, exit 0, 2.31s |
| `python -m pytest tests/test_desktop_theme.py`, `QT_QPA_PLATFORM=offscreen`, scales 1/1.25/1.5/2, `ART_EVIDENCE_DIR=.test-temp/q01/native` | Each one passed, exit 0; four new synthetic PNGs written; before/after SHA256 probe confirms historical U01 native PNGs unchanged 4/4 |
| `python -m ruff check src tests scripts/ci_checks.py scripts/ci_artifacts.py scripts/generate_contracts.py scripts/generate_design_tokens.py alembic` | All checks passed, exit 0 |
| `python scripts/generate_contracts.py --check`; separately `python scripts/generate_design_tokens.py --check` | Both exit 0 |
| Frontend `npm ci` | 138 packages installed, exit 0 |
| Frontend `npm run build` | Vite build and Sites preparation passed, exit 0; 4,581 modules, 16.42s |
| Frontend `npm run lint` | Exit 0, no errors |
| Frontend `npm run test:sites` | Four passed, exit 0 |
| Frontend `node node_modules/@playwright/test/cli.js test --config playwright.ci.config.js`, local Chrome, isolated evidence directory | 11 passed, exit 0, 12.1s; nine art/accessibility cases plus two expected internal error failures |
| Same Playwright command, installed Chromium 139 / build1181, `CI=true`, workspace `PLAYWRIGHT_BROWSERS_PATH`, isolated evidence directory | 11 passed, exit 0, 11.5s; strict CI policy, no HMR exception |
| Artifact stager against actual browser/native evidence and actual MySQL summary | Exit 0; nine browser PNGs, four native PNGs and numeric-only summary staged separately; no private files |
| PyYAML BaseLoader inspection of workflow | Valid YAML; three expected events, four jobs, read-only permission; syntax validation only |
| `git diff --check` | Exit 0 |

The disposable MySQL connection names were `codex_migration_test` and `codex_f01_epoch` on the coordinator-managed loopback fixture server. Credentials are synthetic fixture-only and intentionally omitted from this report. Windows network/async process restrictions required authorized outside-sandbox checks. No real accounts or provider billing were used.

## RED and corrected environmental evidence

- Before CI helpers existed, `tests/test_ci_checks.py` produced six assertion failures for missing CLI behavior and one accidental pass for the missing-script error. The invalid-summary test was tightened to require the sanitizer's explicit generic rejection. Final seven cases passed in the fresh installed environment.
- Before the browser gate, `--grep unexpected` produced two failures: “Expected to fail, but passed.” After the gate, real page exceptions and console errors produce the expected internal failures, so the suite passes. No synthetic injected error can pass silently.
- The initial strict MySQL runner selected an intentionally inapplicable SQLite rowid case: 33 passed, one skipped, exit 1. A controlled regression reproduced this with two passes/one skip and exit 1. The exact documented exemption resolved it; missing or skipped eligible MySQL cases still fail. Final real run: 33 passed, zero skipped.
- The first full-suite invocation supplied a nonexistent nested basetemp parent: 244 passed, two skipped and 82 `tmp_path` setup errors. This was a local command setup mistake, not product RED. Creating the parent and rerunning produced the documented green suites.
- Sandbox `ensurepip`/Path.resolve and pip DNS access initially failed; workspace TEMP/TMP and authorized outside-sandbox checks resolved them. Broad local `ruff check ... scripts ...` also found two S607 errors in ignored historical `scripts/bootstrap_hygiene.py`, absent from a clean checkout. The committed production/QA scripts and Alembic linted clean; that bootstrap script was not modified.
- The first strict art run failed nine cases on deliberately aborted login requests and documented Chrome local HMR warnings; a subsequent run still caught five component cases because the fixture explicitly imported the refresh runtime. Removing those artificial effects yielded the final strict Chromium and Chrome results. None of these failures were hidden by a later shell command's exit code.
- A first JavaScript YAML-inspection attempt could not find `js-yaml`; the independent PyYAML syntax probe succeeded. This was tooling inspection, not a test or remote CI run.

## Exact owned files

- `.github/workflows/ci.yml`
- `.gitignore` (fresh QA venv and CI evidence directories only)
- `scripts/ci_checks.py`
- `scripts/ci_artifacts.py`
- `tests/test_ci_checks.py`
- `tests/test_desktop_theme.py` (approved evidence-path support only)
- `dashboard-prototype/playwright.ci.config.js`
- `dashboard-prototype/vite.ci.config.mjs`
- `dashboard-prototype/tests/visual/browser-errors.js`
- `dashboard-prototype/tests/visual/art-baseline.spec.js`
- `docs/handoff/evidence/task-Q01.md`

Ignored recovery report: `.superpowers/sdd/windows-public-beta-2026-10-02/task-Q01-report.md`. Generated local screenshots/reports stay under `.test-temp/q01*`. No trackers, ledger, F01/F02 production files, package lock or pyproject dependencies were edited by Q01. No commit, staging, push, PR or remote CI trigger was performed by this worker.

Pending: independent code review; a real GitHub run on the final reviewed HEAD with run ID/log links; Python 3.13/GitHub Windows and Ubuntu service execution; external branch protection; physical Windows DPI/UAT; package install/upgrade/release gates from later tasks. Coordinator F01/F02 review fixes require their own covering evidence and must not be inferred from the historical full-suite result.
