# F01 revocation evidence

Status: Code ready for independent task review. No worker commit or staging. Human/VM/real Telegram validation remains Pending.

## Implemented behavior

BLOCK and individual permission removal persist an incrementing source authorization epoch. Revocation disables permissions and learning intent, fences queued/running/paused source jobs and pending/confirmed/executing source actions, and applies the owner-selected keep/archive/delete memory action with an audit row. The public RevocationReport DTO is unchanged.

Workers retain their captured epoch and no longer implicitly ALLOW a source during refresh or queued learning. Action preview/confirmation/execution and explicit grant changes compare captured generations; a stale grant cannot override a committed BLOCK or subsequent regrant. Background job payloads capture source epochs, while durable external-effect markers prevent destructive job/action transport failures from entering blind retry queues. Migration 0006 cancels legacy work without snapshots and classifies already-running history_link_delete jobs as uncertain.

RAG snapshots every authorized source, guards model/embedding/context/cache boundaries, includes epochs in cache identity, and returns internal AuthorizedAnswer source metadata for final delivery guards. Owner /ask, owner natural-language/UI answers, and group replies check before each outgoing chunk. Source revocation prevents post-embedding vector upsert. AUTO link deletion retains standing confirmation/role/rights behavior, rechecks AUTO_MODERATION and DELETE_ANY_MESSAGES epochs before deletion, and records a lost external response as uncertain with requires_reconciliation rather than claiming the message was kept.

## Lock lifetime and real MySQL proof

Captured-epoch authorization rechecks use scalar policy/permission locking current reads, avoiding ORM identity caches and MySQL REPEATABLE READ consistent snapshots. Runtime and configured RAG/user-client guards execute inside fresh short Database.session contexts, whose commits release locks before awaited model, embedding, Telegram or vector operations. Initial snapshots without a supplied epoch stay unlocked. Explicit grant mutation uses source locks only for its local transaction. Action outcome/audit commits before the completion notification; network failure or task cancellation cannot turn that committed grant into failed work.

The gated embedding race runs against both file-backed SQLite and real local MySQL 8.4.11: a second connection commits BLOCK while the embedding call is waiting; the gate is then released; no vector upsert occurs; a fresh Database object still rejects the old epoch. A separate MySQL test establishes a REPEATABLE READ snapshot, commits BLOCK elsewhere, rejects the stale authorization and old expected_epoch grant. Another MySQL test revokes during the synthetic notification, proving grant locks have been released.

## Test evidence

Runtime used: C:/Users/brian/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe with scripts/check.py (adds ../audit-deps).

Prior worker/coordinator evidence: initial six intended RED assertion failures; focused 45 passed. Recovery focused suite: 68 passed. Broad recovery snapshot before the final resumed fixes: 255 passed, 10 MySQL migration cases skipped because TG_TEST_MYSQL_URL was not set. The coordinator owns the final whole-branch integration wave.

Additional intended RED cases and fixes:

- Owner /ask after revoke and between chunks: both leaked three chunks; per-chunk guards fixed them.
- AUTO permission disable during Telegram role lookup: still deleted message 1; captured-epoch guards fixed it.
- MySQL stale consistent read after committed BLOCK: incorrectly accepted epoch 0; current reads fixed it.
- MySQL grant notification lock lifetime: BLOCK timed out during notice; action result commit before notice fixed it.
- Legacy running delete migration: reported cancelled; now uncertain.
- Fixture safety: production/shared schemas and remote hosts were accepted by the URL helper; now rejected before metadata writes.
- AUTO delete lost response: audit said failed_kept; now uncertain.

Final command (outside sandbox, using authorized local synthetic fixtures):

```powershell
python scripts/check.py pytest tests/test_revocation_jobs.py tests/test_policy.py tests/test_group_ai_ask.py tests/test_sync.py tests/test_actions.py tests/test_rag_shared_corpus.py tests/test_search_budget_pairing.py tests/test_vector_reliability.py tests/test_knowledge_learning.py tests/test_knowledge_inventory.py -ra --basetemp=.test-temp/f01-report-final
```

Result: **118 passed in 30.61s**, exit 0. Includes all 19 individual permission revocations and queued blocked-job rejection across inherit/local_only/local_first/cloud_only/cloud_first/off without altering the configured mode. Notification network-error and CancelledError cases inspect committed rows in another session: action executed with timestamp, policy allowed, queued learning job preserved.

Task-scoped Ruff lint: **All checks passed**, exit 0. git diff --check: exit 0 (unrelated browser file line-ending warning belongs to concurrent U01 work).

## Fixture boundary and limitations

TG_TEST_F01_MYSQL_URL is separate from F02 TG_TEST_MYSQL_URL. Only mysql+asyncmy URLs on localhost, 127.0.0.1 or ::1, with a codex_f01_* schema or exact codex_ci_revocation schema, are accepted before Database construction or any metadata drop/create. The executed schema was codex_f01_epoch on 127.0.0.1:13307; F02 shared schema codex_migration_test is explicitly rejected. Tests leave the dedicated schema empty after metadata cleanup. Server shutdown belongs to the coordinator.

No real Telegram account, model service, credentials or external delivery was used. Once destructive external I/O has already started, remote state requires reconciliation; the work is not automatically requeued. Generalized operation/restore leases remain later task scope. The full whole-branch suite must be rerun by the coordinator after concurrent F02/U01/Q01 edits settle. Independent review is still Pending.
