# Q01 — code review, remote verification pending

Reviewed commit: `067bf85d95f8865044e6b54cdd5010c77633d540`, 2026-10-03.
Independent coordinator code review: Approved, no Critical/Important code findings.
Evidence: [task-Q01.md](task-Q01.md).

The workflow and local runners implement Windows Python3.12/3.13, SQLite, disposable MySQL and frontend gates with sanitized artifacts. Local installed-environment, actual loopback MySQL, Chrome/Chromium and Qt logical-scale results passed as recorded in the implementation evidence. This satisfies code review only.

Q01 stays InReview until a real GitHub workflow completes on the reviewed HEAD with run ID and log links. No local command, parsed YAML, physical-DPI approximation or historical suite result substitutes for that run. Public release and beta readiness remain false.

## Actual GitHub run and upload correction

PR run [37115926569](https://github.com/NyanBUIDL/telegram-ai-personal-assistant/actions/runs/37115926569), source head831024631994d2092e594f8823031b32cced96bd, completed with failure. Both Windows3.12/3.13 suites, native scale steps, required real MySQL tests and Node/build/lint/Sites/browser steps succeeded. Every artifact upload failed because the sanitized target `.ci-artifacts` was hidden while `include-hidden-files` remained false. Windows3.12 log records340 passed/44 skipped (MySQL unavailable in that job); the separate MySQL job covers required cases.

The correction stages only the existing allowlist to non-hidden `ci-artifacts/`, ignores that generated folder, and retains hidden-file exclusion, strict empty-upload failure, read-only token permissions and seven-day retention. No raw logs, traces or private files are added. [The pinned action documentation](https://github.com/actions/upload-artifact/blob/ea165f8d65b6e75b540449e92b4886f43607fa02/README.md#uploading-hidden-files) confirms dot-prefixed folders are excluded. Existing real subprocess sanitizer/gate tests: **7 passed in2.53s**. The failing real run is the configuration regression evidence; no implementation-mirroring test was added. A new exact-head GitHub run must pass before Q01 is Verified.
