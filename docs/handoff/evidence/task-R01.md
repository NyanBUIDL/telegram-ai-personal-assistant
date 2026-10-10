# R01 — source hygiene, masked audits and distribution gates

Execution date: 2026-10-03. Code ready; final independent review and public rights remain pending. The worker stopped at its usage limit; the coordinator preserved its work and completed the checks below. No public release, history rewrite, credential rotation or new source license was performed.

The prior hygiene commit81a40535 removed28,087 tracked generated/private-runtime paths from the feature-branch index while retaining local files/history. The new read-only scanner inspects tracked index text and explicitly bounded source-history blobs. It reports only kind/path/commit/SHA256, never matched secret values, and never invokes Settings, SecretStore or provider validation. Exact path/kind/hash dispositions identify reviewed synthetic test/CI literals; generic filenames or placeholder shapes cannot approve an unknown candidate.

The history audit spans26827cfb231373ce0f326553067f8f1ad849649d through the immutable head named in docs/release/scoped-history-audit.json. It records a historical.env pathname exposure. Provider-key SOURCE selectors initially looked like credential assignments; safe field/selector analysis and regression tests corrected that false positive. No actual unclassified credential value was identified in the stated roots/patterns. This is bounded evidence, not proof that excluded binary/generated/other history is clean.

The original font/reference hashes are preserved in assets-manifest.json. THIRD_PARTY_NOTICES and a312-component development/source CycloneDX inventory document current dependencies;37 license records require resolution against the final shipped distributions. This is not an artifact SBOM or complete LGPL notice bundle. Exact Darley/LNTH-Peter rights, project redistribution terms, Qt replacement/source/notices, final artifact inventory, real CI/Windows/UAT and final release authorization remain explicit gates. Public audit intentionally fails; no licensing assumption replaces missing permission.

## Executed checks

- Worker RED/GREEN scanner/canary/history/asset tests were retained; fresh coordinator run of tests/test_release_content.py passed34 cases.
- Coordinator RED: unknown tracked placeholder-shaped key caused audit exit0. Minimal fix requires an exact reviewed disposition; GREEN34 cases includes the new fail-closed CLI case. No matched value is emitted.
- Fresh installed environment: `.venv-q01/Scripts/python.exe scripts/ci_checks.py --report .test-temp/r01-final/summary.json --basetemp=.test-temp/r01-final/pytest`, both guarded disposable MySQL URLs set, Qt offscreen, isolated ART_EVIDENCE_DIR — **380 passed,4 intentional dialect skips in129.39s**, exit0. Includes both final F02 semantic fixes, F01 restart fix and R01 tests. No audit dependencies or real credentials/Telegram/model calls.
- Scoped Ruff on scanner/tests passed. Current index/content, public-gate and bounded-history reports are retained under docs/release; their exact snapshot bounds/counts are authoritative.
- A newly staged scanner-test credential-URL canary was classified by its exact known path/kind/hash, not by blanket test-folder suppression.

The interrupted worker's bootstrap full-suite run had5 CI-helper subprocess failures because scripts/check.py's in-process sys.path did not reach child Python processes; this is an environment invocation defect, not product RED. The fresh installed-environment run above resolves it. That earlier native test also overwrote the historical scale1 PNG; the coordinator restored only that known test-generated change from U01's approved commit3323cdeb and routed subsequent images to the temporary artifact directory. Font/reference binaries and the approved U01 image remain unchanged.

This task does not grant public redistribution or claim beta readiness. The pending owner LICENSE preference is recorded separately; current metadata remains Private use until an explicit choice arrives.

## Independent review fix — merge commits

The fresh reviewer found one Important issue: `diff-tree` omitted merge changes. A real disposable Git repository test creates two parents, adds a synthetic canary only while completing their merge, then removes it before HEAD. RED: the scanner incorrectly exited0; GREEN after comparing merges against each parent with `-m`: **35 passed in2.43s**, scoped Ruff passed. Existing blob deduplication prevents duplicate reports. The regression asserts the exact merge commit and masked output. No real credentials or repository history were changed. Scoped second-reader review approved the correction; its context limitation and continuing release gates are recorded in [review-R01.md](review-R01.md).
