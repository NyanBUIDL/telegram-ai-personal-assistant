# V02 independent scoped review — fix round 2/5

Verdict: Approved for the scoped V02 implementation review. Prior Important mixed dirty/clean admission and checkpoint deadlock: ADDRESSED. No new Critical or Important findings in the changed scope. This is not release, UAT, S03 restore, or V03 recovery approval.

## Scope and method

Reviewed the immutable 338-line `review-V02-fix2.diff`, comparing the exact fix1 snapshot to fix2, plus the authorized selection-only fixture change in `tests/test_knowledge_learning.py`. Read the author's final fix2 report section and the recorded RED, GREEN, and final evidence. Independently checked all four current file SHA256 hashes against the freeze values; all matched. No source/test edits, staging, commit, push, agents, broad test runs, or additional probes. The author's targeted negative tests resolve the unanswered regression from fix1.

## Prior Important finding — ADDRESSED

`src/tg_assistant/runtime.py:232` now gives clean and dirty new admissions the same oldest-ID priority, while preserving dirty deletion and existing repair priority. This aligns bounded admission selection with the unchanged shared quota planner instead of repeatedly selecting a newer dirty candidate that the plan refuses.

`runtime.py:275` adds `knowledge_checkpoint_after`. A higher evaluated row ID cannot advance the checkpoint over a lower untouched pending or dirty candidate within the active retention window. The helper remains monotonic for existing checkpoints and handles empty evaluated batches without advancing them. Both production callers use it: periodic refresh at `runtime.py:1918` and learning completion at `runtime.py:2632`.

`runtime.py:247` also keeps pending rows reachable below an already overadvanced legacy checkpoint when positive admission capacity is available. Exhausted capacity retains the invalidation-only query. Previously accepted quota accounting, deferred-admission handling, and deletion-drain progress remain intact.

Meaningful tests exercise actual callers, not only an isolated helper:

- `tests/integration/test_vector_incremental.py:653`: mixed clean/dirty bounded admission on SQLite/MySQL and refresh/learning callers; actual canonical point, one provider call, healthy coverage, and learning accounting assertions.
- `tests/integration/test_vector_incremental.py:719`: higher dirty deletion cannot advance either caller's checkpoint over a lower clean pending row; the subsequent cycle builds the point.
- `tests/integration/test_vector_incremental.py:775`: pending admission is reachable below an existing checkpoint, the checkpoint does not decrease, and exhausted capacity does not select settled deferrals.
- `tests/integration/test_vector_incremental.py:801`: submitted/uncertain reservations encountered through the new below-checkpoint fallback still raise `AiUncertainError`; transport call count remains one and no vector is created.

The narrow `test_knowledge_learning.py` fixture now explicitly marks pre-checkpoint rows as previously accounted. Its selection assertion remains unchanged, it makes no point-readiness claim, and actual pending legacy gaps are covered separately above. This does not weaken the production verification contract.

## New findings

Critical: None.

Important: None introduced by the scoped fix.

Minor/out-of-scope: No new items. Earlier cross-task limitations remain pending with their respective owners.

## Evidence and practical limits

Recorded `v02-review2-red.txt`: eight meaningful failures across both backends and both callers, 38 deselected, 11.99 seconds. Recorded `v02-review2-green.txt`: the same eight cases passed, 38 deselected, 13.51 seconds. Recorded `v02-review2-final.txt`: 75 passed in 127.21 seconds, including the actual dual-backend incremental cases, local Qdrant, RAG, knowledge selection, and revocation negatives. The reviewer inspected the evidence and test implementation but did not independently rerun these commands; no independent full-integration result is claimed.

The fix changes query/checkpoint logic and tests only. The quota planner, restore invalidation body, provider intent guards, source/epoch/lease/current-store fences, and budget services are unchanged in this round. No activation or external retry authorization is introduced.

Cannot verify in this review: S03 writer fencing/staged restore/swap/caller integration; V03 approved recovery and verified activation; current combined-tree full integration; Windows clean-install/upgrade/VM/UAT/release gates. Their absence from this scoped packet is not treated as task-local fix breakage.

## Verified freeze hashes

- `src/tg_assistant/runtime.py`: `DBDC6464E5588A0FDDAB939F6D8347AD07FA20F0D973490D68A12460837530CE`
- `src/tg_assistant/services/vector_reliability.py`: `88C959D2A49837EB3A225C1BFBE396BFDB6EB87B4D7CB83F1A865243312CBD17`
- `tests/integration/test_vector_incremental.py`: `3F6FE7DD4110A5D12F2B6B1C9ECC3EEBABDC0F91CB10EEFB5F366C2209E4457D`
- `tests/test_knowledge_learning.py`: `0EF6769D2EB29899B6121B0764D392BD15588E77110EA476327FD9412041A700`
