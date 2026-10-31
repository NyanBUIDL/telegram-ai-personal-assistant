# Cycle 2 design and implementation plan — 2026-10-31

Base release3228e543 was pushed and draftPR3 opened at https://github.com/NyanBUIDL/telegram-ai-personal-assistant/pull/3. Remote SHA verified. Owner explicitly approved pushing/opening PR and further autonomous userflow/security/AI improvements. Work stays on codex/ease-of-use-2026-10-31; no merge/deploy, real Telegram/login/messages, paid services, credential setup, OS/network policy changes or user data deletion.

## Intended outcomes

A source's permission and AI route constrain every cloud classifier; recognized secrets are not forwarded. Untrusted retrieved content is identified as data and unusable AI answers cannot appear grounded or become cached successes. Failed model downloads and core tasks produce clear failures. Owner-facing screens communicate stale/unmeasured data honestly and selection matches the existing API maximum. First use and reminder times are predictable.

Preserve Python/MySQL/FastAPI/React stack, local credential store, default-deny/confirm boundaries and current visual style. No dependency/model upgrades. Existing code repairs are selected over new architecture or features. Owner delegated routine design choices, so execution proceeds without a design approval pause.

## Task A — Scoped access/privacy controls

Owner: security reviewer/implementer. Files: admin_api/app.py history-ai-delete-filter; runtime._classify_history_delete_with_openai; focusedtests.
- [x] RED reproductions for missing/BLOCK/no-read sources and recognized secret forwarding (synthetic only).
- [x] Authorize before reading candidate text and reauthorize at runtime; globaloff and route plan must allow OpenAI.
- [x] Reject secret-bearing owner instructions, redact source text at engine boundary before truncation (AI implementer owns engine).
- [x] Tests denied sources and policies never reach provider, safe allowedcase works, data remains unchanged.

This is a focused code/security review, NOT a formal repository security scan. The standard security-scan skill was read, but required managed start/draft/artifact tools are absent from tool discovery; no authoritative scan can be started/completed. Do not create a fabricated scan ID or canonical formal report. Continue independent source/test repairs and report this exact limitation.

## Task B — AI integrity and failure handling

Owner: AI implementer. Files: ai/engine.py, ai/rag.py, services/ollama.py, focusedtests.
- [x] RED→GREEN: structured question/untrusted_sources payload, explicit instructions, shared redaction; record actualusage and rejectblank/explicitfailed/incomplete output so configuredfallback can operate.
- [x] Reject blank/citation-only/invalid-reference or ungrounded answers before appendix/cache; no synthetic sources; version cache semantics. Citation checks validate reference membership, not factual truth or complete injection resistance.
- [x] Ollama pull requires terminal success and handles error200, malformed/progress/truncation/cancel with sanitized boundederrors.

## Task C — Lifecycle recovery

Owner: runtime/DevOps implementer. Files: runtime.Application lifecycle/scheduler wrappers, recovery helpers; cli.worker; tests.
- [x] RED→GREEN: coretask failure/return cancels siblings and propagates; startup failures enter cleanup.
- [x] Track/drain scheduledcoroutines before resources; attempt allclosers and preserve originalfailure.
- [x] Explicit stop is clean, unexpected applicationfailure reaches process exit; PID/marker cleanup stays safe.
- [x] Recover pause_requested to paused and preserve checkpoints until explicitresume.
- [x] Readiness handshake is deferred; no autorestartloop or infra changes.

## Task D — Trustworthy dashboard flow

Owner: frontend/UX implementer. Files: hooks, UI/views, mockedbrowser tests.
- [x] RED→GREEN: obsolete response cannot overwrite newerscope/unmountedstate; stale data shows error, lastsuccess and Retry.
- [x] Respect existing500-source selectioncontract, label batch/remaining truthfully and cap manual/page selection; no422 or false allselectedsuccess.
- [x] Measured health uses status+checkedat; missing/stale data is UNKNOWN; latency never hides failure.
- [x] Keep retrodesign and owner confirmation flows; no live UAT data.

## Task E — First use and reminder clarity

Owner: main PM/BA/developer. Files: timeparse.py, control_bot start/help/reminder output, tests.
- [x] Test evening parsing, invalid/unsupportedinput and weekday-number ambiguity beforefix.
- [x] Support documented calendar phrases/HH:MM/giờ and dayparts with explicit defaults; reject unknown phrases instead of silently scheduling currenthour. No general NLP/dateparser dependency.
- [x] Separate concise owner-only/start with firststep buttons from full/help; clear FSM and advertise/cancel.
- [x] Acknowledge reminder interpreted Vietnamtime; storage stays UTC.

## Integration and delivery

- [x] Independent review of finalauthored diff, fix validatedregressions with tests.
- [x] Full pytest/Ruff/pipcheck, frontendlint/build/Sites/mockedChrome regressions.
- [x] Commit/push testedbranch, update PR3 title/body around finalscope, verify remoteSHA and latestCI; fix failures introduced bychange.
- [x] Record testedvsuntested integrations, residualrisks, scopedsecurityreview status and nextacceptancestep. Stop at coherentsecondcycle complete or exactexternalblocker.

Final local verification:313 Python cases,13 Chrome browser regressions,4 Sites tests,lint/build/pipcheck/diffcheck pass. Independent review fixes cover SDK polling ownership, simultaneous stop/failure and sibling cleanup error. Wrong-target group detail mutation reproduced then fixed. Latest remoteCI is verified after publication; see PR3 and final delivery metadata.
