# V03 — durable source vector recovery (isolated packet)

<!-- five-current -->
## Integrated acceptance — 07/10/2026

Current status: **InReview**. The coordinator's [integrated review](review-five-completion.md) and [execution manifest](five-completion.json) supersede historical code-integration Pending statements below. SQLite-only source follows the directly approved amendment. Live accounts/provider/downloads, physical Windows/installer, licensing, human UAT and soak remain Pending; this is not beta-ready or publication. O04 bot setup and U03 first-answer UX remain separate roadmap work.
<!-- /five-current -->



Status: isolated freeze2 implemented and scoped GREEN; shared integration and independent review Pending. Not Verified or beta-ready.

Owned new files: services/vector_recovery.py, services/vector_paths.py, admin_api/recovery_routes.py, tests/integration/test_vector_recovery.py. Existing runtime.py, actions.py, vector_reliability.py and Admin app remain unchanged. No contract/schema/generated frontend edits; no source permission widening. Coordinator owns shared integration, Git and whole-tree gates.

Implemented server-owned expiring preview plans and one-use durable enqueue; real BackgroundJob/claim-token lease processing; staged same-profile Qdrant generations; unchanged valid points preserved across sources; provider calls only for missing/dirty canonical references; real SQL/point/hash/count/search verification before atomic pointer+metadata+registry/job activation. Current owner/SID/profile/path, registered identity, source epoch/grant/policy, full SQL/active-corpus fingerprint, persisted embedding consent/enabled/profile and existing budget authority are rechecked. Existing row intents and durable recovery row intents cannot bypass submitted/uncertain or unknown outcomes. Restart can reuse actual staged point only with settled reservation. Previous corpus bytes retained on failure and after verified switch.

Preview counts are captured from actual scoped point/SQL coverage without mutating active coverage/registry/index metadata or calling providers. Confirmation stores only server plan_id. Native secrets and arbitrary browser paths are never accepted. Generation selectors derive from generated UUIDs below the owned profile store root; foreign/missing/corrupt pointers, unsafe manifest, SID/owner mismatch, reparse/junction/symlink/hardlink aliases fail closed. Invalid pointer never silently creates an empty active corpus. Verified completion updates measured source inventory and durable audit; API job status excludes internal lease/request/path tokens and controls use existing authenticated session dependencies.

Actual boundary tests use isolated SQLite, unique regex-validated codex_v03_<UUID> MySQL schemas at coordinator-approved localhost fixture, actual local Qdrant, and httpx.MockTransport provider responses. No live Telegram accounts, production credentials, paid calls, privilege/owner changes, fixed/shared MySQL schema writes, full suite, staging, commit or push.

Meaningful RED:
- Existing confirmed runtime recovery returned approved_not_executed with zero real jobs: v03-red2.txt (3 failures, one actual old-path assertion and two missing-service assertions).
- Other-source concurrent edit and un-settled staged point activation: v03-negative-red2.txt (4 failures on SQLite/MySQL).
- Fresh preview bypass of previous uncertain private request: v03-intent-red.txt (2 failures).
- Internal pause_requested DTO state: v03-pause-red.txt (2 failures).
- Persisted embedding disable/profile change after provider response: v03-config-red.txt (4 failures).
- Stale queued job truth and stale process handle after committed activation: v03-stale-red.txt (4 failures).
- Budget denial outcome: v03-budget-red.txt (2 failures).
- Missing preview/completion audits: v03-audit-red.txt (4 failures).
- Missing-count preview/source inventory truth: v03-view-red.txt (4 failures).

Environment/fixture failures are separate: initial sandbox fixture hang; long pytest/Qdrant SQLite Windows path; incorrect policy primary-key fixture lookup; consent fixture initially changed cached settings rather than persisted authority. MySQL whole-JSON CAS comparison and resume DATETIME fractional rounding were fixed in new service scope. These are not relabeled as successful evidence.

Scoped prior GREEN: v03-final.txt 77 passed,2 integration-Pending deselected in121.96s; v03-final2.txt81 passed,2 deselected in110.33s. Final frozen command/hash packet pending latest run v03-final3.txt. The two excluded tests are actual existing runtime dispatch integration cases, intentionally not suppressed or claimed passing while producers are frozen. Private exact four-file integration patch/bases and AST/git apply --check are prepared for coordinator.

Limitations: process crash windows are simulated BaseException interruption with durable DB/ledger/vector files and reopened actual handles; human Windows/VM/real-account/UAT and release gates remain Pending. Non-current profile retrieval may degrade safely; old points are never queried with incompatible new embedding identity. Old generations are retained rather than automatically purged. Independent review and integrated producer/UI checks remain required.

Freeze1: v03-final3.txt observed81passed2integration-Pending deselected in364.35s; four-file Ruff check/format --check passed. Hash manifest v03-isolated-freeze.json. Shared producer files unchanged and private patch unapplied. Coordinator now requires compatibility extension for explicitly configured external owned vector roots before shared lookup integration; strict refusal from freeze1 must not silently disable preserved legacy locations. New path tests/implementation are in progress, so final independent-review packet remains Pending.

Freeze2 supersedes the strict external-root limitation: only exact explicit semantic roots from settings and UUID generation descendants are allowed outside data_dir. Existing roots need current profile/SID marker, native OS ownership and full tree reparse/junction/symlink/hardlink preflight, matching embedding manifest and actual SQL registry/profile/path binding. Read-only resolution/preview writes no marker/ACL/manifest and does not adopt or create missing/empty external roots. Such root creation remains a separate fenced operation. Production recovery creates only generated descendants below an already known and bound root.

Actual dual-backend external compatibility RED: v03-external-red2.txt2failures (owned external corpus rejected solely by location). Earlier exclusive .lock snapshot and initial junction creation command failures were fixture errors, corrected separately. Real owned external Qdrant retains the previous corpus and other-source retrieval after recovery/startup-pointer resolution. Real NTFS hardlink/junction and missing/corrupt/foreign manifest/registry cases fail closed preserving bytes; OS foreign-owner denial uses the existing native-boundary injection without privileges/ownership changes. Native foreign-owner human UAT remains Pending.

Final freeze2 scoped GREEN: v03-final4.txt observed exit0,101passed2integration-Pending deselected in394.91s. This includes20 external cases plus frozen reliability coverage, with actual SQLite/MySQL/Qdrant and synthetic provider transport. No skips counted as passes. Ruff/format--check passed for all four new Python files. Immutable hashes in v03-isolated-freeze2.json; service/routes/proposal patch remain freeze1 hashes, paths and tests superseded. Existing producer files unchanged; exact patch applies cleanly and awaits coordinator integration release. This scoped packet does not establish integrated runtime/UI behavior or final beta verification.
