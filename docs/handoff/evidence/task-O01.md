# O01 independent persisted onboarding and measured health

<!-- five-current -->
## Integrated acceptance — 06/10/2026

Current status: **InReview**. The coordinator's [integrated review](review-five-completion.md) and [execution manifest](five-completion.json) supersede historical code-integration Pending statements below. SQLite-only source follows the directly approved amendment. Live accounts/provider/downloads, physical Windows/installer, licensing, human UAT and soak remain Pending; this is not beta-ready or publication. O04 bot setup and U03 first-answer UX remain separate roadmap work.
<!-- /five-current -->


Status: independent service and the scoped native/worker setup shell have passed independent review. Actual AI/Telegram/bot/source/answer producers and full onboarding acceptance remain Pending. O01 is InProgress; this is not task Verified or beta readiness. Historical sections below retain their original execution scope.

Owned new files:
- src/tg_assistant/services/onboarding.py
- src/tg_assistant/services/connections.py
- src/tg_assistant/admin_api/setup.py (read-only route installer, not installed in a running app)
- tests/test_onboarding.py

Existing contracts, generated frontend mirror, producer/auth/worker/desktop/runtime/shared API files were not edited. Root owns integration, whole-suite regression and Git. No real Telegram/provider account, paid call, Credential Manager write, commit/push/release was used.

The selected-backend AppSetting row stores only allowlisted nonsecret options, versioned stage evidence and fixed error codes. Every mutation reacquires the profile maintenance admission fence, uses SQLite BEGIN IMMEDIATE or MySQL row locks, and guards writes by persisted revision. MySQL 1213/1205 retries are bounded to three fresh transactions; rollback precedes each issuer recheck. Evidence is stage/profile/issuer/owner/time bound, consumed only after actual registered service recheck and prerequisite verification. No default account/bot/provider verifier reports success. Owner stays nullable before actual account verification; pairing must match the positive verified owner. Explicit AI skip permits limited ready only with actual storage/account/bot/pair/source prerequisites; first-answer success is prohibited while skipped. Normal ready needs actual first-answer evidence and current mandatory service health.

Connection health probes the selected database with SELECT 1 and accepts only trusted owning-service callbacks for other services. Chat and embedding health are separate. No probe/expired or future observation is unknown, unknown/checking/disconnected exposes no capabilities, and older concurrent probe results cannot overwrite newer disconnection. Exception text is never returned. The setup installer exposes GET /api/v1/setup/status and GET /api/v1/connections only, returns frozen DTOs/no-store, and sanitizes storage errors. Host/Origin/session/CSRF/SID and runtime wiring remain root's existing boundary/integration work; there is no completion POST.

## Reproducible scoped evidence

Python: .venv-q01/Scripts/python.exe, 3.12.14. All fixtures are isolated. Dual-backend runs configure the already authorized disposable loopback TG_TEST_MYSQL_URL; O01 MySQL fixtures create/drop only regex-validated codex_o01_<UUID> schemas. No fixed base schema is changed. Native/async/MySQL runs execute outside the known Windows sandbox socket restriction.

1. Initial absent-module run: pytest tests/test_onboarding.py -v --basetemp=.superpowers/sdd/windows-public-beta-2026-10-02/o01-red-tmp; exit 1, 23 setup errors. Kept as bootstrap history, not behavior RED acceptance.
2. Behavior RED with minimal unavailable interface skeletons: pytest tests/test_onboarding.py -v --basetemp=.superpowers/sdd/windows-public-beta-2026-10-02/o01-red-behavior-tmp; exit 1, 23 failed. Tests invoke missing status/resume/evidence/options/storage behavior.
3. First implementation GREEN attempt: 22 passed, 1 failed because the cross-profile fixture incorrectly reused the original profile's fence. Corrected the isolated fixture; production fence-mismatch rejection was retained.
4. Additional negative RED: unchanged signed source selection was discarded and invalid stage errors echoed input; exit 1, 2 failed, 26 passed. Both fixed. Disconnected identity management negative RED: exit 1, 1 failed, 28 deselected; fixed.
5. First contract GREEN: pytest tests/test_onboarding.py tests/test_contracts.py -v --basetemp=.superpowers/sdd/windows-public-beta-2026-10-02/o01-green2-tmp; exit 0, 95 passed.
6. Async route RED attempt inside sandbox stalled after collection. It is environment/stall evidence, not acceptance RED; exact owned Python PIDs 14640/8176 were verified by executable, command line and creation timestamp before stopping them. Outside-sandbox async routes subsequently passed.
7. Real concurrent SQLite/MySQL issuance: pytest tests/test_onboarding.py -k concurrent_evidence -v -o faulthandler_timeout=20 --basetemp=.superpowers/sdd/windows-public-beta-2026-10-02/o01-mysql-red-tmp; exit 0, 2 passed (no loss of evidence).
8. Forced MySQL first-row gap-lock deadlock RED: same command with -k first_row_deadlock and o01-mysql-deadlock-red-tmp; exit 1, 1 failed (1213), 1 backend-inapplicability skip. GREEN after bounded rollback/fresh admission/issuer recheck/revision guard: exit 0, 1 passed, 1 backend-inapplicability skip, in 0.40s.
9. Skip-first-answer and older-health-result RED: pytest tests/test_onboarding.py -k 'older_probe or skip_ai_cannot' -v --basetemp=.superpowers/sdd/windows-public-beta-2026-10-02/o01-red-refresh-tmp; exit 1, 2 failed, 1 unconfigured-MySQL skip. Fixed by denying skipped first-answer and per-service probe generation checks.
10. Final scoped related regression: .venv-q01/Scripts/python.exe -m pytest tests/test_onboarding.py tests/test_contracts.py tests/test_storage_profile.py tests/test_dashboard_launch_ticket.py -v -o faulthandler_timeout=45 --basetemp=.superpowers/sdd/windows-public-beta-2026-10-02/o01-regression-tmp; exit 0, 187 passed, 1 skipped, 39.45s. The sole skip is the SQLite instance of the MySQL-specific gap-lock control; actual MySQL control passed.
11. .venv-q01/Scripts/python.exe -m ruff check src/tg_assistant/services/onboarding.py src/tg_assistant/services/connections.py src/tg_assistant/admin_api/setup.py tests/test_onboarding.py; All checks passed. Formatting applied only to these owned files.

Full output resides in .superpowers/sdd/windows-public-beta-2026-10-02/o01-*.txt; immutable review snapshot/hash manifest follows in the private report.

Pending: independent review; root worker/API/native wiring; actual O02/O03/O04 producers and dialogs; GUI/art/keyboard/DPI/VM/UAT; full integrated/hosted gates. Current completed-evidence TTL semantics are being confirmed with root before final freeze.

## Final completed-history ruling and focused follow-up

The initial expiry description is superseded: pending evidence IDs remain strict 300-second one-use completion proof. Already completed nonsecret history is not erased by that TTL; each public status rechecks the registered actual owning service and current health. Native resume refreshes the registered actual probes, enters the profile write fence, and reconciles only completed records with matching trusted issuer/identity/fingerprint. It never renews pending IDs. Missing/unavailable/changed verifiers retain historical rows but withdraw current stage/readiness/management; completed-proof replay remains rejected. Selected-backend storage health and its trusted post-migration verifier perform actual SELECT 1; owning API/account/bot/source/answer verifiers remain dependency-injected and unavailable by default.

TTL/resume RED: .venv-q01/Scripts/python.exe -m pytest tests/test_onboarding.py -k 'resume_refreshes or resume_rejects_changed' -v -o faulthandler_timeout=20 --basetemp=.superpowers/sdd/windows-public-beta-2026-10-02/o01-ttl-resume-red-tmp; exit 1, 4 failed (both backends). Focused resume GREEN: same runner selecting -k resume and o01-ttl-resume-green-tmp; exit 0, 12 passed, 60 deselected in 1.55s. Additional completed-history-only TTL RED: exit 1, 2 failed, 72 deselected; fixed by separating consumed completion history from expiring pending proof. Corrupted ready fingerprint RED: exit 1, 2 failed, 74 deselected; fixed by checking ready proof against live owner and current prerequisite evidence fingerprint.

## Root integration recommendation (not performed in this task)

After the already approved selected storage has opened and migrated, construct a sync Engine for that same backend/profile (SQLite sqlite driver; MySQL existing pymysql URL resolved privately by StorageService; never send URL/password to DTOs). Preserve SQLite configure_sqlite hooks and the existing selected profile MaintenanceService. The root worker owns engine lifecycle/disposal and passes that engine/fence plus actual service probe/verifier getters into OnboardingCoordinator. storage_probe(engine) and storage_verifier(engine) are real SELECT 1 factories; storage verifier is for trusted post-migration use. Register welcome acknowledgment from the real native view, actual configured AI capability/consent state, verified live Telegram owner, getMe bot evidence, same-owner pairing, authorized confirmed source, and verified delivered answer only when those owning producers exist. Do not replace unavailable handlers with configuration-presence checks. Refresh real health and call trusted resume at native startup; read-only HTTP uses status, never resume (which reconciles under a write fence).

Install admin_api.setup.install_setup_routes(app, coordinator_getter) in the root's existing loopback Host/Origin/auth boundary after integration release. Register no browser completion POST or option mutation. Actual native dialogs may invoke trusted coordinator methods after producer checks. No evidence identifier itself authenticates a caller. Root's new unwired desktop setup view may consume the same getter; native async/nonblocking controller owns error handling and available dialog actions. All actual API/worker/native registration remains deferred until separate root integration review.

Final frozen service/contracts command: .venv-q01/Scripts/python.exe -m pytest tests/test_onboarding.py tests/test_contracts.py -v -o faulthandler_timeout=20 --basetemp=.superpowers/sdd/windows-public-beta-2026-10-02/o01-freeze-green-tmp, with approved disposable MySQL fixture configured; outside sandbox. Result: exit 0, 141 passed, 1 skipped, 10.55s. Sole skip is SQLite instance of MySQL-only first-row gap-lock control; actual MySQL control passed. Final owned-file Ruff check: All checks passed. Source is frozen pending independent root review; source hashes live in the private immutable O01-independent-review-snapshot/manifest.json.

## Native and worker integration — 2026-10-05

The actual Windows launcher opens the native setup view once the worker has measured readiness. The setup context uses the selected, already migrated SQLite/MySQL database and real profile maintenance fence, checks the Alembic revision, and disposes its engine after background work drains. The native start action verifies welcome and storage through trusted services; it does not complete unavailable producer stages. The worker now installs the read-only setup/connections routes within its existing Host/Origin boundary. Unsupported dialog handlers stay disabled until their owning integrations exist.

Native focus restoration was reproduced as a real failure: closing setup during an in-flight launcher refresh left its opener disabled. The fix restores focus after the next completed ready measurement. A further meaningful RED exposed an enabled start button after both supported stages were already complete; the button now disables when no supported start work remains. Existing art/font assets stayed unchanged. Logical scale 100/125/150/200% checks passed; physical Windows DPI/UAT remains Pending.

Recorded integration batch: **177 passed, one SQLite applicability skip, 149.87s** across setup/context/worker/launcher/backup/runtime gateway/onboarding/tickets. The actual MySQL control passed. After the start-button correction, direct affected final cases: **13 passed, zero skips, 20.03s**. These are scoped runs, not a full-branch acceptance claim.

Independent native review found startup-only health measurements expired permanently after 60 seconds. Meaningful fix RED: four failures on actual SQLite/MySQL. The worker now periodically calls the actual owning coordinator's resume in a background thread, with no mutation on HTTP reads. It shields and drains an in-flight measurement before disposing resources; failures log only a fixed code. Final affected health/worker batch: **eight passed, zero skips, 25.13s**; Ruff passed. Independent scoped re-review approved both specification and quality, with no new findings. Final reviewed worker SHA256 `bc3c2089a6b35673c45f50adb49665bdf3445f5cbd0c853e34aedbcbd744f6f8`; health test SHA256 `f2f344c40acf177e6942fa21b0d327ef31b66a613bdd018bd2cd2f1fcf4e31f8`.

O02 native integration is proceeding separately and needs its own review. Provider measurement TTL must stay tied to the provider's actual observation. Actual Telegram login, bot verification/pairing, authorized source choice and delivered first answer, browser navigation, installer/upgrade/restore/human gates and exact integrated-head CI are still Pending.
