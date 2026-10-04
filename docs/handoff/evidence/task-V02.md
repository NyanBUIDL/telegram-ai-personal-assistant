# V02 — Incremental vector index evidence

Status: Ready for independent review. Integration and release verification pending.

The index now records durable edit/delete invalidations, revisits messages below the source/store checkpoint, checks actual point scope and content hash before reuse, and keeps previously valid vectors during partial embedding failure. Point identities include embedding store, Telegram source and message. Deduplication retains a canonical reference only within the same source/store. Coverage shares content, retention, filtering, routing and input-size eligibility; completion accounts for every candidate and reports reconciliation when coverage is incomplete. Policy versions are fingerprinted. Dirty repairs can replace points at full quota without adding new vectors.

Migration0009 adds the durable coalescing outbox flag; migrations0001–0008 remain immutable. Portable restore invalidates derived readiness and checkpoints, retains unresolved provider intents/budget ledger and requires explicit verified recovery. No public DTO changes or automatic store activation.

## Verification

| Check | Result |
| --- | --- |
| Incremental integration, local Qdrant and shared RAG |40 passed |
| Existing embedding fences, knowledge selection, store/profile regression |74 passed;36 environment-dependent skips |
| Real migration0008→0009 and repeated upgrade |2 passed, SQLite and MySQL |
| Ruff lint and formatting, owned files |Passed |
| Independent review/full integrated suite |Pending coordinator |
| Windows clean install, live Telegram UAT and release gates |Pending |

The required integration suite uses real SQLite/MySQL storage, isolated disposable schemas, local Qdrant files and synthetic model HTTP transports. No live account, credential or billed provider request was used. Meaningful failing tests were observed before fixes for missing-point reuse, delete removal, checkpoint edits, duplicate coverage, edit-admission fencing, uncertain restore replay, quota repair and oversized eligibility. Environment setup failures were kept separate from behavioral failures.

Commands: python -m pytest tests/integration/test_vector_incremental.py tests/test_qdrant_local.py tests/test_rag_shared_corpus.py -v; python -m pytest tests/test_runtime_embeddings.py tests/test_knowledge_learning.py tests/test_knowledge_inventory.py tests/test_vector_reliability.py tests/test_embedding_profiles.py -v. Disposable MySQL test configuration is required to exercise MySQL cases. Detailed private evidence is retained in the execution ledger.

Integration adjunct: two older revocation fixtures now use real isolated Qdrant stores and assert actual zero persisted vectors after source revocation. Production verification was retained. Exact negative epoch/repeatable-read cases and oversized eligibility passed6 tests across SQLite/MySQL. Independent review receives the fixture-only adjunct; production files remain frozen.

Independent review round1 corrected quota coverage and dirty-queue progress. A shared deterministic quota plan preserves existing verified knowledge first and accounts denied admissions separately from missing vectors. Dirty deletions and repairs now precede admission work; deferred rows no longer fill every dirty batch and are retried when capacity returns. Reviewer probes reproduced six failures on SQLite/MySQL before fixes. The final focused index/Qdrant/RAG/knowledge/epoch suite passed61 tests using real disposable dual-dialect storage. Scoped independent re-review is pending; no release-readiness claim is made.

Independent review round2 corrected mixed dirty/clean admission order and checkpoint gaps in both scheduled refresh and learning jobs. Prioritized deletions cannot advance checkpoints beyond untouched pending rows, and older pending admissions remain reachable after legacy checkpoint gaps. Eight real SQLite/MySQL caller regressions failed before the fix and then passed. The final scoped suite passed75 tests, including quota/starvation, capacity return, unresolved provider no-retry, actual Qdrant coverage and negative source epoch cases. Lint and formatting passed. One selection-only fixture was corrected to identify historically accounted rows; its expected behavior remains unchanged and asserts no vector readiness. Sources are frozen for scoped independent re-review; full integration and release gates remain pending.


## Frozen integration — 2026-10-04

Independent scoped review approved. Combined-tree run recorded 781 passed, 11 skipped, 1 failed in 620.15s. Only the native Qt dashboard test failed; it was reproduced independently and diagnosed as QTest wait-loop GIL starvation. Production files stayed unchanged. Corrected real event-loop waits retain the 5s/3s deadlines, native click/ticket/redeem/replay assertions; ordered focused checks passed 26 cases. This supplements, and does not relabel, the historical full run.

Expanded required MySQL gate: 138 passed, 0 skipped/errors/failures, 234 deselected, 322.50s across all eight mandatory modules, including backup/restore and incremental indexing. Actual CI-channel Chromium: 5 passed in 41.0s. Python Ruff, generated contracts/art checks, frontend build and ESLint passed. Exact new-commit hosted CI remains the next integration check. Physical Windows/VM/DPI, UAT, packaging and distribution rights remain Pending.
