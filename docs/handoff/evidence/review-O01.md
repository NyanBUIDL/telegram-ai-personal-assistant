# O01 independent service review evidence

Date: 2026-10-05. Independent-services verdict: **Approved** after fix round 1. The subsequent native/worker shell is also **Approved** after its scoped fix round 1, recorded below. Full onboarding producer integration remains **Pending, not Done**.

Scope: the new persisted onboarding coordinator, measured connections service, read-only uninstalled setup route installer and onboarding tests. Root's separate native setup UI/shared integration, actual Telegram/provider producers, Windows/VM/UAT and release gates are outside this review. No real account/API/credential/privilege changes, Git operations or full-suite reruns were performed by the reviewer.

The original packet manifest SHA256 `6fda9ac6920ecdec47d665af874d471bb0f0fbce86051c53ac8a0354c8e7c7c5` and all 18 packet/current entries were independently verified. Initial review found Important I1: configurable pending evidence could exceed 300 seconds, and completion could consume pending proof after service rechecks crossed the expiry deadline. A reviewer-owned disposable SQLite probe reproduced both cases; no production/test source was modified.

Fix manifest SHA256 `c53530ba1f97c241b7990e0cc2080bd9c83ce78cdd8152c19cf516a4071fa670` and all eight fix entries were independently verified. The reconstructed before/after diff exactly matches patch SHA256 `c625b8b08ff93ee847da60f1809d9fc7c22ec7b756b5ed6304aff713b26839ea`. I1 is **ADDRESSED**: maximum pending TTL is 300 seconds and completion rechecks expiry after all actual verification/prerequisite/owner calls immediately before consuming, under existing transaction/fencing. Failure rolls back without changing the stored row. No new Critical/Important issue was identified in the fix.

Reviewed final source hashes:

- services/onboarding.py: `c4ff4525b413b82c8f03bf54e80aad97749d2294f7df8629f2d4e4b59c969499`
- services/connections.py: `4dfe6d96e51c83126251a4d6d1a5815c750b8f222db17c0daa293a547611b3ad`
- admin_api/setup.py: `81406efba81932b05228c434180637a3ae66684f448c2e11c1a04b2d3521343a`
- tests/test_onboarding.py: `4bbe32a0b13d315fe53bf5105baed9e50fc9e80047e1cfe8d4fdb253cfe8b540`

Hash-verified author evidence: original final scoped run 141 passed/one SQLite applicability skip in 10.55s; historical related run 187 passed/one applicability skip in 39.45s. Fix RED: 12 expected failures/eight passing controls. Fix GREEN: 36 passed/60 deselected/no skips in 2.61s on actual isolated SQLite and disposable MySQL. Reviewer did not repeat these covered checks. The focused fix evidence includes exact expiry boundaries, latency during owning/prerequisite checks, unchanged-row rollback, completed-history/live trusted resume, pending nonrenewal and maintenance admission.

Approval covers the reviewed independent service implementation only. Missing owning producers remain unavailable, AI skip does not fabricate first-answer success, and the setup route installer remains unwired. Full integrated onboarding, management/auth boundary wiring, native dialogs/UI/DPI/art, real account/provider use and beta/release readiness require separate evidence before O01 can be Verified/Done.

## Subsequent native/worker review

The historical uninstalled-route statement above is superseded for the reviewed worker shell: the real loopback gateway installs the read-only setup routes, and actual native setup uses the selected migrated storage. The native review's Important startup-only-health finding was fixed through actual periodic resume and in-flight shutdown drain. Independent corrective review: **specification Approved; quality Approved; no new Critical/Important/Minor findings**. The original seven unaffected native entries stayed hash-identical; the worker before/after differential and new test hashes were independently checked.

Corrective packet SHA256 `c603091ef2fbd84ae8ee0db4b32ff8179e572daaa54cf85d2d07618c54babb88`; manifest SHA256 `650e84748b5e241a09c90990e909499665ff1adf8d262ba316345a65e14a9d65`. Reviewer read author RED four failures and final GREEN eight passes without rerunning covered tests. Provider/Telegram/bot/source/answer producer integration, full-task verification and release readiness remain outside this shell approval.
