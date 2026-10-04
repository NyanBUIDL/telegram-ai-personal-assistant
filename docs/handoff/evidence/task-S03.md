# S03 portable backup and restore evidence

Status: owned service implementation verified and frozen. Independent review, complete coordinator regression baseline, Windows beta acceptance and human UAT pending.

Backup archives include the database and an actual revision/backend/profile/checksum manifest. Configuration, credentials, raw Telegram sessions, arbitrary custom settings and derived vector files are excluded. Restore preserves current consent/native identity and billing records, keeps newer revocations, and requires a real unexpired maintenance lock lease.

Final disposable Windows Python3.12 check: **25 backup/restore tests passed** across SQLite and MySQL, including live consistency, real foreign-key billing records, rollback after post-swap failure, revision0005 staged migration, real maintenance admission, hostile archive rejection and snapshot-only removal of live job-claim authority. Related storage/CLI run: **70 passed,1 separate opt-in storage test skipped**. Scoped Ruff and formatting checks passed. Disposable MySQL8.4.11 cleanup verified no remaining S03 schemas. No real Telegram accounts or production databases were used.

The ordinary sandbox full-suite attempt was stopped after repeated native desktop readiness failures. A focused native baseline recorded `runtime_readiness_unavailable` and a worker teardown timeout; the coordinator must verify the full suite outside that sandbox. No full-suite pass is claimed.

Vectors are explicitly degraded after restore and require reconciliation and owner-approved recovery; SQL import alone is not reported as ready. MySQL restore uses private staging schemas and requires appropriate schema-management privileges. Current native identity, consent/custom settings and billing authority are preserved rather than imported from an older archive.

## Native / CLI / Admin adapters

The launcher now opens a native backup modal; confirmed restore stops the owned worker gracefully, acquires the SID-wide instance lock and an actual maintenance lease, retains prebackup, and reports derived-index recovery required. ESC cannot interrupt a pending restore. CLI uses the same portable service. Admin backup creation requires owner session, CSRF and Origin; the browser cannot select filesystem paths. Backup listing labels manifest-only validation honestly.

Coordinator focused adapters:19 passed,0 skipped/errors (actualQt,SQLite archive,restore,worker lifecycle andAuthAPI). Independent service+adapter review Approved, no Critical/Important findings. Final combined-tree/CI checks pending. Physical Windows DPI/clean install/UAT remain release gates.


## Frozen integration — 2026-10-04

Independent scoped review approved. Combined-tree run recorded 781 passed, 11 skipped, 1 failed in 620.15s. Only the native Qt dashboard test failed; it was reproduced independently and diagnosed as QTest wait-loop GIL starvation. Production files stayed unchanged. Corrected real event-loop waits retain the 5s/3s deadlines, native click/ticket/redeem/replay assertions; ordered focused checks passed 26 cases. This supplements, and does not relabel, the historical full run.

Expanded required MySQL gate: 138 passed, 0 skipped/errors/failures, 234 deselected, 322.50s across all eight mandatory modules, including backup/restore and incremental indexing. Actual CI-channel Chromium: 5 passed in 41.0s. Python Ruff, generated contracts/art checks, frontend build and ESLint passed. Exact new-commit hosted CI remains the next integration check. Physical Windows/VM/DPI, UAT, packaging and distribution rights remain Pending.
