# Windows Public Beta Implementation Plan

> **Amendment 06/10/2026:** The owner approved [SQLite-only and an empty new profile](../specs/2026-10-06-sqlite-only-amendment.md). It supersedes advanced MySQL support, MySQL connection/provisioning and dual-backend gates below. Existing MySQL data stays untouched. Earlier steps/results are historical; current execution/status is tracked in `docs/handoff/status.json`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. User selected agent-assisted execution. Steps use checkbox syntax for tracking.

**Goal:** Phát hành beta Windows mỗi người tự cài, tự kết nối API/Telegram/bot, mở dashboard dễ dàng và giữ art hiện tại.
**Architecture:** Native PySide6 launcher + React browser dashboard + local Python worker. SQLite-only; MySQL hiện hữu không được kết nối hoặc thay đổi; secret trong Credential Manager, dashboard loopback qua SID-authenticated IPC one-use ticket.
**Tech Stack:** Python 3.12/3.13 verified; PySide6 Qt Widgets; SQLAlchemy/Alembic/aiosqlite; Telethon/aiogram; FastAPI; Qdrant local; React/Vite; PyInstaller onedir/Inno Setup; pytest/Playwright.
**Spec:** ../specs/2026-10-02-windows-public-beta-design.md
**State:** Plan ready for review/handoff, product implementation NOT STARTED. Baseline repo commit 7429fcffd61860e9502f066e0b6a921df72fe176. Root workspace is NOT a Git checkout; audit-snapshot is a partial read-only audit copy.

## Global Constraints

- V1 Windows 10/11 x64, mỗi Windows SID có một active owner/profile; không SaaS/LAN/remote dashboard.
- Luồng chuẩn không yêu cầu tự cài Python/Node/MySQL hoặc terminal.
- SQLite-only; cài mới bắt đầu rỗng, cài lại không tự reset; không kết nối/chuyển/xóa MySQL cũ.
- API key/token/API hash/OTP/2FA native write-only; không browser/bot/log/query/analytics.
- Dashboard loopback, ticket 256 bit, TTL 30 giây, one-use, profile-bound; HttpOnly/SameSite/CSRF và Host/Origin guard.
- Pair nonce TTL 300 giây, one-use, verified Telegram owner_id; start payload <=64 ký tự.
- Paper #f3efdf/panel #fffdf5; ink #090909; teal #00c8c8/magenta #ef00c8/yellow #ffd51f; square 3px black borders/hard 7px shadows.
- PeterObscure logo/main title only; DarleySans all remaining UI; licensing trước public artifact.
- Touch targets >=44 CSS px; browser 360/390/1280/1440; Windows DPI 100/125/150/200%.
- LOCAL ONLY không gửi source content qua cloud chat hoặc embedding, kể cả shared retrieval/fallback.
- New/existing model/store identity bao gồm provider/model/version/dimension; không corpus overwrite ngầm.
- BLOCK thắng stale job/action/request; uncertain external side effect không retry mù.
- Mọi Done phải có command/test/artifact/evidence; human/VM test Pending không được tính pass.
- Giữ baseline features, 19 quyền hiện hữu và confirmation/audit; tách module theo task, không rewrite toàn bộ cùng lúc.
- Không trực tiếp merge master, publicize repo, force-push hoặc phát hành artifact ra ngoài khi chưa có review/authorization cuối.

## Review Focus

1. Cài từ installer không có Python/Node/MySQL, username/path tiếng Việt và port bị chiếm -> chạy được hoặc bước sửa rõ. Tests S01/D01/P02.
2. BLOCK trong lúc học hoặc AI đang chờ mạng -> không reallow/upsert/delivery. Tests F01/V02/Q02.
3. Mở dashboard bằng URL bị replay/sai SID/sai Origin; browser mở chậm -> không unauthorized session, recovery một nút. Tests D02/U02.
4. Cloud-only với LOCAL ONLY source trong shared corpus hoặc embedding profile đổi -> không rò nguồn hoặc trộn dimension. Tests V01/V02/Q02.
5. Mất điện/disk full giữa migration/restore/job; Telegram side effect timeout -> dữ liệu gốc recoverable, trạng thái uncertain/degraded đúng. Tests S02/S03/P02/Q02.

## Quy tắc triển khai từng task

Mỗi task có 5 bước test->red->implementation->green->review/commit; chỉ test meaningful behavior. Agent không chỉnh test expectation chỉ để xanh. Với baseline/checkout/font inventory/cleanup, dùng before-after inspection evidence; không chế failing test cho thao tác hành chính. Lệnh npm chạy trong dashboard-prototype, Python ở checkout root với venv dự án. Chạy mỗi verification command riêng để xác định exit code.

File Python rút gọn như services/*, desktop/*, db/*, telegram/*, ai/* và runtime.py đều tương đối với src/tg_assistant/. File frontend rút gọn tương đối với dashboard-prototype/src/. Alembic versions nằm trong alembic/versions/. Đây là file ownership canonical; work packages có thể đề xuất helper name khác nhưng không tạo service/contract trùng.

Unit/contract tests không dùng account/API key thật. Integration MySQL dùng disposable DB riêng. Live Telegram/provider QA chỉ khi được owner cấp đúng test scope; secret nhập native/secure local, không gửi cho agent.

## Quyền sở hữu và song song

- Coordinator sở hữu contracts.py, generated.js, cross-agent API/schema và thứ tự merge.
- Reliability sở hữu runtime worker/policy/index/job/backup; Experience chỉ sửa bot UX sau khi đặt lịch với Reliability.
- Platform sở hữu launcher/setup/storage/bootstrap/auth/packaging; thay db/models/config được Coordinator serialize với Reliability.
- Experience sở hữu React/views/styles; backend route edit chuyển Coordinator/Platform theo contract.
- QA thực hiện review độc lập và release gates; không vừa implement vừa sign-off task mình viết.
- Tối đa 3 agent workers + Coordinator đồng thời. Khi cần reviewer mới, giải phóng/hoàn thành một worker trước; không spawn vượt slots.
- Hai task sửa cùng file hoặc cùng schema không chạy đồng thời; dùng worktree/branch riêng có snapshot contracts chung. Cross-cutting integration do Coordinator.
- Worker báo: task ID, files, tests/commands/exitcode, evidence, risks, next dependency. Status gồm Planned/Ready/InProgress/InReview/Verified/Blocked, không báo Done trước evidence.


## B00 — Chuẩn bị checkout thật và khóa contracts/baseline

**Owner:** Coordinator. **Milestone:** M0. **Depends on:** không.
**Files:** README.md, AGENTS.md, dashboard-prototype/AGENTS.md; docs/handoff/**; src/tg_assistant/contracts.py (mới); dashboard-prototype/src/contracts/generated.js (mới); tests/test_contracts.py (mới). Q01 sở hữu CI sau B00.
**Interfaces:** Consumes root spec contracts + dependencies; produces Tạo branch/worktree từ GitHub HEAD đã đối chiếu baseline; copy docs bàn giao; ghi art/user constraints trong AGENTS.md; chốt contracts.py và generated.js trước khi chia agent; không dùng audit-snapshot làm checkout.
**Acceptance:** Có Git checkout/branch rõ, baseline evidence và contracts versioned; không có product task tự đổi schema.

- [ ] **Step 1 — Baseline and contract tests:** ghi Git HEAD/ref và baseline pytest/build/lint thực ở checkout; 150 là kết quả audit tham chiếu, nếu HEAD đổi phải giải thích diff/count. Tạo tests/test_contracts.py chứng minh schema DTO không chứa secret, JSON Telegram IDs là string và generated.js khớp Python schema; lưu art/reference asset hash.
- [ ] **Step 2 — Verify baseline/red:** lưu baseline pass/fail/skipped riêng; test contracts mới phải fail vì schema chưa có hoặc chưa khớp. Checkout/art inventory dùng inspection evidence, không fake red.
- [ ] **Step 3 — Implement:** Tạo branch/worktree từ GitHub HEAD đã đối chiếu baseline; copy docs bàn giao; ghi art/user constraints trong AGENTS.md; chốt contracts.py và generated.js trước khi chia agent; không dùng audit-snapshot làm checkout.
- [ ] **Step 4 — Verify green:** chạy riêng python -m pytest, npm ci, npm run build, npm run lint và git status --short; npm ở frontend. Chạy thêm regression liên quan và cập nhật evidence/task-B00.md; ghi failure baseline có sẵn thay vì giấu vào task mới.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## F01 — Thu hồi quyền thắng job đang chờ/đang chạy

**Owner:** Reliability. **Milestone:** M1. **Depends on:** B00.
**Files:** src/tg_assistant/policy.py; services/revocation.py (mới); runtime.py; db/models.py; telegram/user_client.py.
**Interfaces:** Consumes root spec contracts + dependencies; produces RevocationService.revoke_source(chat_id, actor_id, memory_action) -> RevocationReport; snapshot epoch trong job/request, recheck trước từng external side effect; grant chỉ ở explicit confirmed setup flow.
**Acceptance:** Tái hiện probe cũ đổi thành allowed_after_job=False, sync_calls=[]; review race test và restart.

- [ ] **Step 1 — Write meaningful failing tests:** tests/test_revocation_jobs.py: test_queued_job_cannot_reallow_blocked_chat, test_revoke_during_embedding_prevents_upsert, test_revoke_before_delivery_is_silent, test_retry_after_restart_keeps_revoked. Assertions: BLOCK tăng authorization epoch, hủy/fence job stale; worker không gọi set_allowed True từ refresh; không có sync/upsert/delivery sau revoke đã commit; nếu I/O đã bắt đầu phải kết quả cancelled/uncertain đúng.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** RevocationService.revoke_source(chat_id, actor_id, memory_action) -> RevocationReport; snapshot epoch trong job/request, recheck trước từng external side effect; grant chỉ ở explicit confirmed setup flow.
- [ ] **Step 4 — Verify green:** python -m pytest tests/test_revocation_jobs.py tests/test_policy.py tests/test_group_ai_ask.py tests/test_sync.py -v. Chạy thêm regression liên quan và cập nhật evidence/task-F01.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## F02 — Migration immutable và đường sửa schema cũ

**Owner:** Platform. **Milestone:** M1. **Depends on:** B00.
**Files:** alembic/versions/0001_initial_schema.py; versions/0002-0005; alembic/env.py; setup/wizard.py; db/models.py.
**Interfaces:** Consumes root spec contracts + dependencies; produces Freeze 0001 historical explicit DDL; dialect-aware types và Alembic batch ALTER SQLite; thêm schema classification/repair trước migration đang fail, không đợi revision mới sau 0002 để sửa; không stamp head vô điều kiện; ghi actual revision.
**Acceptance:** Fresh install + legacy upgrade thực đạt cả hai dialect; không dùng Base.metadata.create_all để thay migration test.

- [ ] **Step 1 — Write meaningful failing tests:** tests/integration/test_migrations.py: test_empty_database_upgrade_head_sqlite_mysql, test_legacy_revision_upgrade_without_loss, test_unknown_schema_refused, test_repeated_upgrade_noop. Assertions: database trống đến head không duplicate table; DB0001/0004 giữ dữ liệu/IDs; hiện trạng create_all/head classified có kiểm tra schema trước stamp/repair; DB ngoài ứng dụng bị từ chối.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** Freeze 0001 historical explicit DDL; dialect-aware types và Alembic batch ALTER SQLite; classification/repair runner chạy trước 0002, snapshot trước repair; unknown schema refused nguyên vẹn; ghi actual revision. Không blanket table-exists skip.
- [ ] **Step 4 — Verify green:** python -m pytest tests/integration/test_migrations.py -v; chạy SQLite và MySQL disposable riêng trong CI. Chạy thêm regression liên quan và cập nhật evidence/task-F02.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## S01 — Storage profile SQLite mặc định và config per-user

**Owner:** Platform. **Milestone:** M1. **Depends on:** F02.
**Files:** src/tg_assistant/config.py; paths.py; db/base.py; services/storage.py (mới); pyproject.toml; cli.py.
**Interfaces:** Consumes root spec contracts + dependencies; produces StorageService.open(profile) -> Database; storage_backend sqlite|mysql; versioned non-secret config trong LocalAppData; app data ownership marker; resource resolver tách mutable và assets.
**Acceptance:** Ứng dụng mở được storage từ bất kỳ cwd và chuẩn bị empty/new/legacy profile có bằng chứng.

- [ ] **Step 1 — Write meaningful failing tests:** tests/test_storage_profile.py: test_new_install_uses_sqlite, test_existing_mysql_preserved, test_config_independent_of_cwd, test_windows_unicode_paths, test_sqlite_pragmas. Assertions: cài mới không yêu cầu MySQL/root; existing MySQL không đổi engine; path space/Vietnamese chuẩn; FK=on/WAL/busy_timeout; credentials không .env; aiosqlite runtime dependency.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** StorageService.open(profile) -> Database; storage_backend sqlite|mysql; versioned non-secret config trong LocalAppData; app data ownership marker; resource resolver tách mutable và assets.
- [ ] **Step 4 — Verify green:** python -m pytest tests/test_storage_profile.py tests/integration/test_migrations.py -v. Chạy thêm regression liên quan và cập nhật evidence/task-S01.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## S02 — Atomic claim/confirm/maintenance tương thích SQLite

**Owner:** Reliability. **Milestone:** M1. **Depends on:** S01, F01.
**Files:** services/jobs.py (mới); services/actions.py; runtime.py; admin_api/app.py; db/models.py; services/tasks.py.
**Interfaces:** Consumes root spec contracts + dependencies; produces JobRepository.claim(job_type, worker_id, now, lease_seconds)->JobLease|None; complete(lease,result)->bool; action transitions atomic; maintenance lease fence read/write/scheduler.
**Acceptance:** Nhiều connection thật cùng DB file SQLite/MySQL đạt, không chỉ test session memory đơn.

- [ ] **Step 1 — Write meaningful failing tests:** tests/integration/test_job_concurrency.py: test_two_workers_single_claim, test_double_confirm_single_execution, test_lease_expiry_and_restart, test_sqlite_busy_is_bounded, test_maintenance_blocks_claim. Assertions: UPDATE compare-and-set/lease không trùng claim, confirm; bounded retry busy; run/restart không double side effect; external unknown marked uncertain, không blindly retry send/delete.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** JobRepository.claim(job_type, worker_id, now, lease_seconds)->JobLease|None; complete(lease,result)->bool; action transitions atomic; maintenance lease fence read/write/scheduler.
- [ ] **Step 4 — Verify green:** python -m pytest tests/integration/test_job_concurrency.py tests/test_actions.py tests/test_knowledge_learning.py -v. Chạy thêm regression liên quan và cập nhật evidence/task-S02.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## S03 — Backup/restore có kiểm chứng và fence runtime

**Owner:** Reliability. **Milestone:** M2. **Depends on:** S02, V01.
**Files:** services/backup.py (mới); cli.py; admin_api routes mới; services/storage.py; launcher backup UI.
**Interfaces:** Consumes root spec contracts + dependencies; produces StorageService.backup(destination)->BackupManifest; restore(source,maintenance_lease)->RestoreReport; SQLite backup API, MySQL consistency options; restore repair indexed flags/checkpoints and reconcile.
**Acceptance:** Có restore drill evidence, retrieval sau restore hoặc degraded+recovery thực, không success chỉ vì import SQL.

- [ ] **Step 1 — Write meaningful failing tests:** tests/integration/test_backup_restore.py: test_live_sqlite_backup_consistent, test_mysql_snapshot_consistent, test_corrupt_checksum_no_mutation, test_restore_failure_keeps_original, test_vector_absent_not_marked_ready. Assertions: manifest actual backend/revision/checksum; no secrets; snapshot consistent; restore staging+prebackup; worker/API fenced; failure không mất nguyên bản; vector derived rebuild plan rõ.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** StorageService.backup(destination)->BackupManifest; restore(source,maintenance_lease)->RestoreReport; SQLite backup API, MySQL consistency options; restore repair indexed flags/checkpoints and reconcile.
- [ ] **Step 4 — Verify green:** python -m pytest tests/integration/test_backup_restore.py -v; restore drill trên copy disposable cả hai storage. Chạy thêm regression liên quan và cập nhật evidence/task-S03.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## V01 — Profile embedding cloud/local và data boundary theo nguồn

**Owner:** Reliability. **Milestone:** M2. **Depends on:** S01, F01.
**Files:** ai/engine.py; ai/router.py; ai/vector.py; ai/budget.py; config.py; services/vector_reliability.py; db/models.py; runtime.py.
**Interfaces:** Consumes root spec contracts + dependencies; produces EmbeddingProfile(provider,endpoint_id|null,model,embedding_version,dimension,store_id,cloud_consent); bind engine/store per profile; source routing/filter trước cloud; BudgetService reserve/reconcile idempotent có pricing version.
**Acceptance:** Cloud/local capability matrix thực/mock riêng; không hidden provider fallback và corpus mixing.

- [ ] **Step 1 — Write meaningful failing tests:** tests/test_embedding_profiles.py; test_cloud_profile_without_ollama, test_local_only_never_cloud_embeds_or_answers, test_shared_corpus_excludes_local_only_for_cloud, test_model_dimension_switch_new_store, test_budget_concurrent_reservations. Assertions: cloud chat+embedding hoạt động không Ollama; LOCAL ONLY không rò qua embed/context; fallback consent rõ; store fingerprint đúng model/version/dimension; pending budget reserved atomically.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** EmbeddingProfile(provider,endpoint_id|null,model,embedding_version,dimension,store_id,cloud_consent); bind engine/store per profile; source routing/filter trước cloud; BudgetService reserve/reconcile idempotent có pricing version.
- [ ] **Step 4 — Verify green:** python -m pytest tests/test_embedding_profiles.py tests/test_ai_router.py tests/test_search_budget_pairing.py -v. Chạy thêm regression liên quan và cập nhật evidence/task-V01.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## V02 — Incremental index, chỉnh sửa/xóa/dedup và coverage cùng contract

**Owner:** Reliability. **Milestone:** M2. **Depends on:** S02, V01.
**Files:** runtime.py index/refresh; telegram/user_client.py; ai/local_first.py; services/vector_reliability.py; db/models.py.
**Interfaces:** Consumes root spec contracts + dependencies; produces Tách index service theo store_id; dirty/outbox queue cho edit/delete; link duplicate reference/canonical theo source policy; có invariant evaluated=accounted, retention policy versions.
**Acceptance:** Case UAT count/drop được kiểm chứng đúng store; job thành công chỉ khi invariants/candidate accounting đạt.

- [ ] **Step 1 — Write meaningful failing tests:** tests/integration/test_vector_incremental.py: test_partial_batch_keeps_previous_vectors, test_reuse_requires_point_exists, test_edit_before_checkpoint_reindexed, test_deleted_vector_unsearchable, test_duplicate_in_source_b_survives_block_source_a, test_coverage_matches_dedupe. Assertions: không thay valid corpus bằng subset; reuse kiểm tra store/point/content hash; dirty rows sau edit được xử lý; duplicate không mất scope; filtered/reused/accounted giải thích completion; eligible/coverage cùng rule.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** Tách index service theo store_id; dirty/outbox queue cho edit/delete; link duplicate reference/canonical theo source policy; có invariant evaluated=accounted, retention policy versions.
- [ ] **Step 4 — Verify green:** python -m pytest tests/integration/test_vector_incremental.py tests/test_qdrant_local.py tests/test_rag_shared_corpus.py -v. Chạy thêm regression liên quan và cập nhật evidence/task-V02.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## V03 — Recovery từng nguồn thực thi được và có rollback

**Owner:** Reliability. **Milestone:** M2. **Depends on:** V02, S02.
**Files:** services/vector_recovery.py (mới); runtime.py recover_source_index; admin_api/app.py; services/actions.py.
**Interfaces:** Consumes root spec contracts + dependencies; produces VectorRecoveryService.preview(chat_id,store_id)->RecoveryPlan; enqueue(plan_id,owner_id)->operation_id; registry trạng thái building/ready; recovery job thực thay approved_not_executed.
**Acceptance:** Người dùng bấm preview->confirm->job->verified được, không còn recovery UI chỉ ghi audit.

- [ ] **Step 1 — Write meaningful failing tests:** tests/integration/test_vector_recovery.py: test_preview_readonly, test_approved_recovery_fills_missing, test_stale_plan_refused, test_pause_restart_recovery, test_failed_store_not_activated. Assertions: preview không mutate; confirm owner enqueue job thật; stale epoch/store/hash phải preview lại; restart idempotent; only missing/dirty upserts; retrieval verified trước active switch.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** VectorRecoveryService.preview(chat_id,store_id)->RecoveryPlan; enqueue(plan_id,owner_id)->operation_id; registry trạng thái building/ready; recovery job thực thay approved_not_executed.
- [ ] **Step 4 — Verify green:** python -m pytest tests/integration/test_vector_recovery.py tests/test_vector_reliability.py -v. Chạy thêm regression liên quan và cập nhật evidence/task-V03.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## D01 — Native launcher/tray và service lifecycle

**Owner:** Platform. **Milestone:** M2. **Depends on:** S01, S02.
**Files:** src/tg_assistant/desktop/app.py, tray.py, runtime_controller.py (mới); cli.py; paths.py; packaging hooks.
**Interfaces:** Consumes root spec contracts + dependencies; produces PySide6 Qt Widgets launcher; runtime controller reusable từ CLI; status process có readiness endpoint; user-data dirs owner ACL và bind loopback.
**Acceptance:** Desktop launcher dùng được với profile chưa setup và legacy, không secret in subprocess args.

- [ ] **Step 1 — Write meaningful failing tests:** tests/desktop/test_launcher.py: test_first_launch_setup, test_existing_profile_resume, test_singleton_same_sid, test_stop_restart, test_port_collision, test_no_console_required. Assertions: GUI mở trên Windows không console; một runtime/profile/SID; start/stop graceful; URL actual loopback port; đóng dashboard không kill worker; exit tray có lựa chọn dừng.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** PySide6 Qt Widgets launcher; runtime controller reusable từ CLI; status process có readiness endpoint; user-data dirs owner ACL và bind loopback.
- [ ] **Step 4 — Verify green:** python -m pytest tests/desktop/test_launcher.py -v; Qt QTest + Windows interactive smoke 100/150/200%DPI. Chạy thêm regression liên quan và cập nhật evidence/task-D01.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## D02 — Một nút đăng nhập dashboard qua IPC ticket

**Owner:** Platform. **Milestone:** M2. **Depends on:** D01.
**Files:** desktop/ipc.py (mới); admin_api/auth.py; admin_api/app.py auth routes; dashboard src auth bootstrap.
**Interfaces:** Consumes root spec contracts + dependencies; produces DashboardTicketService.issue(profile_id,windows_sid,now)->LaunchTicket; redeem(ticket,origin,now)->AdminSession; named pipe command allowlist; browser redeem POST only.
**Acceptance:** Mở dashboard 1 nút không CLI, không thêm password, negative security cases đạt trên Windows.

- [ ] **Step 1 — Write meaningful failing tests:** tests/test_dashboard_launch_ticket.py: test_wrong_sid_denied, test_ticket_once_30s, test_atomic_replay_race, test_bad_origin_host_denied, test_logout_requires_user_reopen, test_ticket_not_logged. Assertions: SID ACL enforced; 256bit TTL30s oneuse hashonly; localhost foreign process không tự mint; fragment erased trước fetch/resources; cookie/CSRF; fallback code oneuse.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** DashboardTicketService.issue(profile_id,windows_sid,now)->LaunchTicket; redeem(ticket,origin,now)->AdminSession; named pipe command allowlist; browser redeem POST only.
- [ ] **Step 4 — Verify green:** python -m pytest tests/test_dashboard_launch_ticket.py tests/test_admin_api.py -v; Playwright auth replay/expiry/403 tests. Chạy thêm regression liên quan và cập nhật evidence/task-D02.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## O01 — Onboarding coordinator lưu/tiếp tục và health thực

**Owner:** Platform. **Milestone:** M3. **Depends on:** D02, V01.
**Files:** services/onboarding.py, services/connections.py, contracts.py (mới); admin_api setup routes; desktop setup UI.
**Interfaces:** Consumes root spec contracts + dependencies; produces OnboardingCoordinator.status/resume/complete_stage theo spec; ConnectionStatus DTO; sanitized GET setup/status and connections, gated mutations/bridge.
**Acceptance:** Wizard complete/resume/error states UI lẫn backend phản ánh cùng contract.

- [ ] **Step 1 — Write meaningful failing tests:** tests/test_onboarding.py: test_resume_without_secrets, test_ready_requires_verified_capabilities, test_skip_ai_is_limited_not_broken, test_offline_save_resume, test_stale_health_is_unknown. Assertions: các stage theo spec persisted nonsecret evidence; TTL secret notpersist; health checking/unknown/degraded đúng; ready chỉ owner/account/bot/storage verified; save/resume không mất nhập liệu nonsecret.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** OnboardingCoordinator.status/resume/complete_stage theo spec; ConnectionStatus DTO; sanitized GET setup/status and connections, gated mutations/bridge.
- [ ] **Step 4 — Verify green:** python -m pytest tests/test_onboarding.py tests/test_contracts.py -v. Chạy thêm regression liên quan và cập nhật evidence/task-O01.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## O02 — Kết nối API cloud/local qua native dialogs

**Owner:** Platform. **Milestone:** M3. **Depends on:** O01.
**Files:** desktop/dialogs/provider.py (mới); services/connections.py; security.py; services/ollama.py; config.py.
**Interfaces:** Consumes root spec contracts + dependencies; produces CredentialConnectionService.validate_and_save(provider,secret_input,options)->ConnectionStatus; provider-specific auth/capability probes; known allowed endpoints; keychain write through native only.
**Acceptance:** Connect/test/change/disconnect OpenAI/OpenRouter/Ollama supported thật; không cần terminal hoặc hidden Ollama.

- [ ] **Step 1 — Write meaningful failing tests:** tests/test_provider_connection.py: test_invalid_key_actionable, test_validation_no_bill_by_default, test_save_only_valid_or_explicit_unverified, test_model_capability_not_guessed, test_local_pull_cancel, test_secret_never_http_log_config. Assertions: APIkey field trống khi edit; validation không tính phí mặc định; degraded auth/quota/model rõ; source consent beforecloud; local download owner selected+size/cancel; health chat/embed riêng.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** CredentialConnectionService.validate_and_save(provider,secret_input,options)->ConnectionStatus; provider-specific auth/capability probes; known allowed endpoints; keychain write through native only.
- [ ] **Step 4 — Verify green:** python -m pytest tests/test_provider_connection.py tests/test_api_key_setup.py tests/test_ollama_service.py -v; credential-free fake provider server E2E. Chạy thêm regression liên quan và cập nhật evidence/task-O02.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## O03 — Đăng nhập tài khoản Telegram bằng QR/OTP/2FA

**Owner:** Platform. **Milestone:** M3. **Depends on:** O01.
**Files:** desktop/dialogs/telegram.py (mới); telegram/user_client.py; services/telegram_login.py (mới); security.py.
**Interfaces:** Consumes root spec contracts + dependencies; produces TelegramLoginService begin_qr/submit_phone/submit_code/submit_password/cancel; serialized auth session lifecycle; no credential in exception/modelcontext.
**Acceptance:** GUI đủ QR và fallback; không claim autoAPIregistration hoặc email/OTP auto-read.

- [ ] **Step 1 — Write meaningful failing tests:** tests/test_telegram_login.py: test_qr_expiry_refresh, test_otp_invalid_retry_bounded, test_2fa_cancel_no_persist, test_floodwait_countdown, test_revoke_session_reconnect, test_owner_from_verified_account. Assertions: APIID/hash explained + deep link official site; QR refresh expires; phone/OTP2FA native only; bounded retries/FloodWait; owner identity getMe verified; restart not reuse OTP.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** TelegramLoginService begin_qr/submit_phone/submit_code/submit_password/cancel; serialized auth session lifecycle; no credential in exception/modelcontext.
- [ ] **Step 4 — Verify green:** python -m pytest tests/test_telegram_login.py tests/test_security_memory.py tests/test_sync.py -v; human-authorized test account live QA later. Chạy thêm regression liên quan và cập nhật evidence/task-O03.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## O04 — Kết nối bot và ghép đúng owner bằng nút Start

**Owner:** Platform. **Milestone:** M3. **Depends on:** O03.
**Files:** desktop/dialogs/bot.py (mới); telegram/pairing.py; telegram/control_bot.py; services/onboarding.py.
**Interfaces:** Consumes root spec contracts + dependencies; produces BotConnectionService.verify_token/create_pairing_link/get_pairing_status; /start payload routes pairing; retain manual code fallback; only verified owner full runtime activation.
**Acceptance:** Tạo/connect/pair bot không paste token qua chat, trạng thái UI cập nhật từ actual bot event.

- [ ] **Step 1 — Write meaningful failing tests:** tests/test_bot_pairing.py: test_getme_identity_display, test_wrong_sender_rejected, test_nonce_replay_expiry, test_pair_cancel_retry, test_token_revoked_disconnect, test_conflicting_poller_message. Assertions: BotFather guide thật; token native; nonce<=64chars ownerbound/useonce; sai sender không chiếmowner; revoke token health degraded; existing poller lỗi409 có hướng dẫn.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** BotConnectionService.verify_token/create_pairing_link/get_pairing_status; /start payload routes pairing; retain manual code fallback; only verified owner full runtime activation.
- [ ] **Step 4 — Verify green:** python -m pytest tests/test_bot_pairing.py tests/test_search_budget_pairing.py -v; getMe/updates mock + explicitly authorized live test. Chạy thêm regression liên quan và cập nhật evidence/task-O04.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## U01 — Khóa design tokens và baseline art

**Owner:** Experience. **Milestone:** M1. **Depends on:** B00.
**Files:** dashboard-prototype/src/styles.css; ui.jsx; desktop/theme.py (mới); dashboard-prototype/AGENTS.md; visual baselines.
**Interfaces:** Consumes root spec contracts + dependencies; produces Extract tokens nhưng preserve baseline; reusable native/browser components cùng visual language; one-source token JSON mirror, not full unrelated CSS rewrite.
**Acceptance:** Có trước/sau evidence và art signoff; không coi đổi palette/font là redesign trong scope.

- [ ] **Step 1 — Write meaningful failing tests:** dashboard tests/visual/art-baseline.spec.js; desktop screenshots/QTest. Assertions: paper/ink/teal/magenta/yellow đúng; square3px/hardshadow7px; fontroles; no replacement aesthetics; 360/390/1280/1440 layouts; 44px targets keyboard/DPI.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** Extract tokens nhưng preserve baseline; reusable native/browser components cùng visual language; one-source token JSON mirror, not full unrelated CSS rewrite.
- [ ] **Step 4 — Verify green:** npm run build; npm run lint; Playwright visual screenshots + human art checklist. Chạy thêm regression liên quan và cập nhật evidence/task-U01.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## U02 — Navigation/dashboard setup và connections dễ hiểu

**Owner:** Experience. **Milestone:** M3. **Depends on:** O01, U01.
**Files:** dashboard-prototype/src/App.jsx; views/SystemViews.jsx; views/OverviewView.jsx; api.js; hooks.js; generated contracts.
**Interfaces:** Consumes root spec contracts + dependencies; produces GET connections/setup contracts dùng health, bridge only opens allowlisted native dialogs with authenticatedCSRF; refresh status after verified result, disable unsupported capability truthfully.
**Acceptance:** Người mới hiểu thiếu gì và sửa ở đâu mà không mở docs dài/terminal.

- [ ] **Step 1 — Write meaningful failing tests:** dashboard tests/e2e/onboarding-dashboard.spec.js: test_new_user_next_action, test_connections_unknown_not_online, test_native_dialog_request_allowed_only, test_expired_session_recover, test_keyboard_navigation. Assertions: 7 mục navigation theo spec; progressive disclosure; empty/chưakếtnối state; next action rõ; no secrets form in browser; auth not loops; frontend errors rollback.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** GET connections/setup contracts dùng health, bridge only opens allowlisted native dialogs with authenticatedCSRF; refresh status after verified result, disable unsupported capability truthfully.
- [ ] **Step 4 — Verify green:** npm run build; npm run lint; npx playwright test tests/e2e/onboarding-dashboard.spec.js. Chạy thêm regression liên quan và cập nhật evidence/task-U02.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## U03 — Nguồn/quyền/học/câu hỏi đầu có hướng dẫn

**Owner:** Experience. **Milestone:** M4. **Depends on:** O04, V03, U02.
**Files:** dashboard views/GroupsView.jsx; KnowledgeView.jsx; telegram/control_bot.py UI messages only; onboarding firstsource.
**Interfaces:** Consumes root spec contracts + dependencies; produces First-value assistant chọn1nguồn->previewpermissions/cloudconsent->enqueue->operationstatus->Openbotquestiontest; use existing endpoints, add backend contract only coordinator approved.
**Acceptance:** Journey đầu hoàn thành tới answer verified; status không chuyển xanh khi unknown/partial.

- [ ] **Step 1 — Write meaningful failing tests:** dashboard tests/e2e/first-value.spec.js: test_pick_one_source_confirm, test_no_automatic_allow_all, test_job_accounting_visible, test_block_progress_cancels, test_recovery_verified. Assertions: source picker có search/type/filter; permission preset explained advanced19permissions preserved; confirmation showsscope; job filtered/reused/error explain; firstquestion sourcecitations truthful; BLOCK always wins.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** First-value assistant chọn1nguồn->previewpermissions/cloudconsent->enqueue->operationstatus->Openbotquestiontest; use existing endpoints, add backend contract only coordinator approved.
- [ ] **Step 4 — Verify green:** npx playwright test tests/e2e/first-value.spec.js; python -m pytest tests/test_group_ai_ask.py tests/test_revocation_jobs.py. Chạy thêm regression liên quan và cập nhật evidence/task-U03.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## U04 — Quản lý, lỗi/recovery, backup và hỗ trợ trong UI

**Owner:** Experience. **Milestone:** M4. **Depends on:** U03, S03.
**Files:** dashboard views/OperationsViews.jsx; AiViews.jsx; SystemViews.jsx; useDialogA11y.js; api.js; desktop dialogs.
**Interfaces:** Consumes root spec contracts + dependencies; produces Reusable operation/error/confirmation components; apply server truth rather than optimistic success; advanced actions preserve permissions and preview, human-readable labels not implementation jargon.
**Acceptance:** Owner quản lý thường ngày không terminal, errors không leave UIstate khác backend.

- [ ] **Step 1 — Write meaningful failing tests:** dashboard tests/e2e/management-recovery.spec.js: test_403_rollback_action, test_provider_failure_next_action, test_pause_retry_cancel, test_backup_restore_preview, test_focus_and_mobile. Assertions: source management, quota/retention/model switch/backups đều có actual result; network401/403/429/500 actionable; busy/error/uncertain states; logs export redacted; focus restored.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** Reusable operation/error/confirmation components; apply server truth rather than optimistic success; advanced actions preserve permissions and preview, human-readable labels not implementation jargon.
- [ ] **Step 4 — Verify green:** npm run build; npm run lint; npx playwright test tests/e2e/management-recovery.spec.js. Chạy thêm regression liên quan và cập nhật evidence/task-U04.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## P01 — Build reproducible và đóng gói đủ resources

**Owner:** Platform. **Milestone:** M5. **Depends on:** D01, U04, R01.
**Files:** pyproject.toml; build requirements/lock; packaging/assistant.spec (mới); resource resolver; scripts/build-windows.ps1 (mới); frontendbuild.
**Interfaces:** Consumes root spec contracts + dependencies; produces PyInstaller onedir với pinnedvalidatedtoolchain, QtWidgets hooks; pack importlibresources +dist/client; version/hashmanifest; development dependencies not shipped.
**Acceptance:** Artifact độc lập máy dev và tái build traceable commit/dependencyhash.

- [ ] **Step 1 — Write meaningful failing tests:** tests/test_package_assets.py; tests/desktop/test_frozen_smoke.py. Assertions: wheel/frozen có Alembic/docs/fonts/dashboard/Qtplugins; no .venv/node_modules/secret; chạy từ path khác; bundledruntime&platform dll verified; cleanbuildlock.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** PyInstaller onedir với pinnedvalidatedtoolchain, QtWidgets hooks; pack importlibresources +dist/client; version/hashmanifest; development dependencies not shipped.
- [ ] **Step 4 — Verify green:** build from fresh Windows runner; python -m pytest tests/test_package_assets.py; run frozenexe --diagnostics sanitized on VM. Chạy thêm regression liên quan và cập nhật evidence/task-P01.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## P02 — Installer per-user, nâng cấp/gỡ an toàn

**Owner:** Platform. **Milestone:** M5. **Depends on:** P01, S03.
**Files:** packaging/windows-installer.iss (mới); desktop update UI; scripts/sign-release.ps1 gated; docs/installupgrade.
**Interfaces:** Consumes root spec contracts + dependencies; produces Inno Setup per-user installer bundlesonedir resources; migrateundermaintenance; no silentautoupdate in v1; optionalexplicit download signedrelease and verify; code signing only with user-authorizedcert.
**Acceptance:** Real installer on cleanVM đạt; unsignedcandidate label rõ, publicdistribution certificate/license gates verified.

- [ ] **Step 1 — Write meaningful failing tests:** tests/windows/test_install_upgrade_uninstall.ps1: clean_standard_user, upgrade_previous_mysql, upgrade_sqlite, uninstall_preserves_data, opt_in_purge_only, offline_start, reboot_autostart_optin. Assertions: Windows10/11x64 standardaccount noPythonNodeMySQL; existingdata preserved; install/uninstall no hiddenpurge; autostartoptin; interrupted upgrade keepsrecoverablebackup; signingstatus honest.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** Inno Setup per-user installer bundlesonedir resources; migrateundermaintenance; no silentautoupdate in v1; optionalexplicit download signedrelease and verify; code signing only with user-authorizedcert.
- [ ] **Step 4 — Verify green:** VM scripts fresh&upgrade 100/150/200%DPI; artifact signatures/checksum inspection. Chạy thêm regression liên quan và cập nhật evidence/task-P02.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## Q01 — CI regressions/contracts/storage/frontend/package

**Owner:** QA. **Milestone:** M1. **Depends on:** B00.
**Files:** .github/workflows/ci.yml; tests/integration/**; dashboard tests/config; coverage config; artifactpolicy.
**Interfaces:** Consumes root spec contracts + dependencies; produces CI startsbaseline then expandswith taskgates; defaultmockproviders; livecredential tests manualnotpublicfork; artifactevidencepublished sanitized.
**Acceptance:** MandatoryCI on featurebranch and finalcandidate; dependencygraph success không được coi CItest.

- [ ] **Step 1 — Write meaningful failing tests:** CI Windows Python3.12/3.13; SQLite + disposable MySQL8.x; npm ci/build/lint; packageasset tests added as completed. Assertions: HEAD checks run pytest/ruffincludingalembic; migrationzero+upgrade; concurrency; contractschema; no productionsecrets; quality criticaltests required regardless aggregatecoverage.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** CI startsbaseline then expandswith taskgates; defaultmockproviders; livecredential tests manualnotpublicfork; artifactevidencepublished sanitized.
- [ ] **Step 4 — Verify green:** PR checks on exactHEAD green; log links/runIDs stored in tracker. Chạy thêm regression liên quan và cập nhật evidence/task-Q01.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## Q02 — Kiểm thử tích hợp artifact và security regression

**Owner:** QA. **Milestone:** M6. **Depends on:** F01, F02, S03, V03, O04, U04, P02, Q01.
**Files:** tests/e2e/desktop/**; docs/handoff/evidence/**; QA manifest; screenshotsanitizer.
**Interfaces:** Consumes root spec contracts + dependencies; produces Separate fastmockE2E and human-authorized sandboxTelegramtest; screenshot/log exports sanitized; capture commit/artifactsha/env/command/result.
**Acceptance:** Không có P0/P1; unresolved medium risks documented and beta scope explicit.

- [ ] **Step 1 — Write meaningful failing tests:** Full installer->resume->APIs->Telegrammock->pair->learn->ask->BLOCK->restart->backup->restore; replay/wrongSID/wrongowner/local-only/promptinjection/403/diskfull/networkloss. Assertions: critical happy&failure journeys verified exactartifact; source access isolation; no leaked secret canaries; crashrecovery truthful; no unsupported magicproviderstatus.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** Separate fastmockE2E and human-authorized sandboxTelegramtest; screenshot/log exports sanitized; capture commit/artifactsha/env/command/result.
- [ ] **Step 4 — Verify green:** Fullmatrix run + artifacthash matches P01/P02 + independentQA review. Chạy thêm regression liên quan và cập nhật evidence/task-Q02.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## Q03 — UAT với 5 người mới và soak test

**Owner:** QA. **Milestone:** M6. **Depends on:** Q02.
**Files:** docs/handoff/UAT_PROTOCOL.md; evidence/new-user-uat.md; metrics/soakchecks.
**Interfaces:** Consumes root spec contracts + dependencies; produces Recruit realtesters with owner approval; observers only record blockers/time/results; operational workload explicitlydefined hardware and synthetic/testsources; fixfailures then retestaffectedcases.
**Acceptance:** Human UAT thực đạt; agents không tự coi mình là 5 người dùng độc lập.

- [ ] **Step 1 — Write meaningful failing tests:** 5 newusers standardWindowsaccounts; validtestcredentials prepared; timedsetup; launchdashboard; manageblock/backup; 72h controlledsoak. Assertions: >=4/5 hoàn thànhfirstanswer<=15min không hướng dẫn trực tiếp (credentialcreationoutside excluded); dashboard<=2click; no secrets leaked; no uncontrolledjob/budget/vector drops in72h.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** Recruit realtesters with owner approval; observers only record blockers/time/results; operational workload explicitlydefined hardware and synthetic/testsources; fixfailures then retestaffectedcases.
- [ ] **Step 4 — Verify green:** Signed-off UAT evidence and soakmetrics; unable-to-run marked Pending/Blocked not Passed. Chạy thêm regression liên quan và cập nhật evidence/task-Q03.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## R01 — Làm sạch source và quyền phân phối assets/dependencies

**Owner:** Platform. **Milestone:** M1. **Depends on:** B00.
**Files:** .gitignore; trackedgeneratedfiles; LICENSE; THIRD_PARTY_NOTICES; fontsmanifest; docs/release.
**Interfaces:** Consumes root spec contracts + dependencies; produces Remove generatedfromindexreviewedbranch, keep requiredsourceassets; dependency SBOM/notices; identify previoussensitivecommits; rotate only if actualsecret found with owner context.
**Acceptance:** Cleanrelease tree; licensing unresolved blockedpublicrelease; no forcepush/publicvisibilitychange.

- [ ] **Step 1 — Write meaningful failing tests:** scripts/check-release-content.py; secret scan/currenttree and scopedhistoryreport; licenseassetcheck. Assertions: no trackedvenv/node_modules/cache/testdata; .env templateonly; tokenfindings don't printsecret; font/PySide/Qtlicense basis documented; destructivehistoryrewrite notautomatic.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** Remove generatedfromindexreviewedbranch, keep requiredsourceassets; dependency SBOM/notices; identify previoussensitivecommits; rotate only if actualsecret found with owner context.
- [ ] **Step 4 — Verify green:** git ls-files contentaudit + licensemanifestreview + secretcanaryscantest. Chạy thêm regression liên quan và cập nhật evidence/task-R01.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## R02 — Bàn giao release candidate và báo hoàn tất đúng mức

**Owner:** Coordinator. **Milestone:** M6. **Depends on:** Q03, R01.
**Files:** README.md; USER_GUIDE.md; PRIVACY.md; SECURITY.md; CHANGELOG.md; release_notes; docs/handoff/status.json.
**Interfaces:** Consumes root spec contracts + dependencies; produces Wholebranchreview fresh agent; create/attach reviewablePRs/artifact; tag/release/publication only after finalartifactreview and authorizedpublish; final completionmessage links proof.
**Acceptance:** Notify user beta-ready only when allgates satisfied; otherwise code-ready/blocked accurately with specificinputneeded.

- [ ] **Step 1 — Write meaningful failing tests:** G0-G6 evidencecheck; allDone taskhaveevidence; README documentedbehavior matches shippedtests; artifactsha+PRmatch. Assertions: preservedart, install/auth/API/bot/management mục tiêu đạt; baselinefeatures notremoved; testedlimits explicit; no falsely greenpendinghuman/cert gate.
- [ ] **Step 2 — Verify red:** chạy test mới riêng; lưu failure chứng minh behavior chưa đạt, phân biệt environment error.
- [ ] **Step 3 — Implement:** Wholebranchreview fresh agent; create/attach reviewablePRs/artifact; tag/release/publication only after finalartifactreview and authorizedpublish; final completionmessage links proof.
- [ ] **Step 4 — Verify green:** Checklistvalidator + exactHEAD CI + installer artifact evidence + independentreview. Chạy thêm regression liên quan và cập nhật evidence/task-R02.md; không lấy exit code của lệnh sau che failure trước.
- [ ] **Step 5 — Review and commit:** reviewer độc lập kiểm spec/negative cases; Coordinator inspect diff và integrate; commit riêng task; cập nhật MASTER_CHECKLIST.md, status.json và roadmap.csv bằng cùng ID.


## Self-review/handoff

Kế hoạch bao phủ install/storage, API cloud/local, Telegram login, bot pairing, dashboard auth/management, preserved art, reliability, packaging và release. Các assumptions và constraints nằm trong spec; không bắt implementer tự đoán mô hình SaaS hoặc đổi style.

B00 là nơi xác minh GitHub HEAD có thay đổi từ baseline và re-run baseline nếu có. Không patch trực tiếp audit-snapshot rồi coi là branch release. Các work package phụ ở docs/handoff để agent đọc domain sâu, nhưng spec và task IDs/definitions trong plan này có ưu tiên khi mâu thuẫn.

Sau review spec/plan, dùng agent-assisted execution theo lựa chọn của user. Product code chưa triển khai ở lượt lập kế hoạch này. Xem START_HERE.md cho prompt handoff và completion contract.

