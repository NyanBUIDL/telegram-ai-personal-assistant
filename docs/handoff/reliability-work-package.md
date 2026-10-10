# Gói bàn giao độ tin cậy, bảo mật và QA — NyanBUIDL

Ngày: 02/10/2026. Đây là kế hoạch triển khai, chưa phải xác nhận các lỗi đã được sửa hay beta đã đủ điều kiện phát hành.

## 1. Phạm vi và cơ sở kiểm chứng

Đích đã xác nhận: ứng dụng cài local trên Windows cho từng người; mỗi người đăng nhập tài khoản Telegram của mình. Giữ owner-only/default-deny, Credential Manager, dashboard loopback, xác nhận thao tác ghi/phá hủy và art hiện có. Gói này không thiết kế hosted SaaS, không đổi giao diện toàn bộ và không tái cấu trúc toàn bộ runtime.

Cơ sở: `RELEASE_READINESS_REVIEW_VI.md`, snapshot commit `7429fcffd61860e9502f066e0b6a921df72fe176`, mã `audit-snapshot/src`, migrations và tests. Mọi đường dẫn mã bên dưới là đường dẫn tương đối từ root repository sản phẩm, không phải yêu cầu sửa trực tiếp snapshot kiểm toán. Các file ghi rõ **mới** là phạm vi đề xuất; không khẳng định đã tồn tại.

Baseline báo cáo: 150 test pass; coverage package 42%, runtime 20%, control bot 13%; chưa chạy MySQL thật, Windows sạch, provider/Telegram thật hoặc UI browser. Snapshot tests dùng SQLite in-memory và `Base.metadata.create_all` trong `tests/conftest.py`, nên không chứng minh chuỗi Alembic hoặc khóa MySQL hoạt động. Không được đổi tests sang bỏ qua lỗi hoặc giảm số test nền để đạt cổng xanh.

Ưu tiên trong gói này:

- **P0:** chặn mời người dùng beta vì thu hồi quyền, mất/hỏng dữ liệu hoặc cài mới không hoạt động. Báo cáo gốc gọi vài điểm này P1; P0 ở đây là thứ tự cổng phát hành, không phải thay đổi kết luận CVSS hay khẳng định mức exploit.
- **P1:** bắt buộc trước beta có dữ liệu thật: recovery, backup, ngân sách, package và kiểm chứng môi trường thực.
- **P2:** cải thiện sau khi cổng nền ổn định; có thể trì hoãn khi không làm mất khả năng sửa lỗi/khôi phục. Cleanup/license vẫn là cổng bắt buộc nếu phân phối source công khai.

## 2. Quyết định lưu trữ và contracts đã chốt

Thiết kế chủ đạo: `docs/superpowers/specs/2026-10-02-windows-public-beta-design.md`. Đã chốt **SQLite nhúng cho cài mới**, tiếp tục hỗ trợ profile MySQL đã có và lựa chọn advanced; không tự chuyển database người dùng. PySide6 native launcher/setup/tray mở React dashboard bằng browser. Quyết định thiết kế chưa đồng nghĩa code đã hỗ trợ; RL-02/RL-03/RL-07/RL-08 trên SQLite file thật là cổng chặn hoàn tất installer. Không quảng bá rằng chỉ cần đổi URL là sẵn sàng, không tự đổi lại MySQL cho cài mới nếu các cổng fail.

Tên contracts dùng chung theo spec: `StorageService`, `JobRepository.claim/complete`, `RevocationService.revoke_source` với revocation epoch, `EmbeddingProfile`, `VectorRecoveryService.preview/enqueue`. DTO nằm ở `src/tg_assistant/contracts.py` **mới**; module nội bộ ở các mục dưới phải implement contracts này, không tạo interface khác tên cho frontend. Một active profile/một worker per Windows SID trong v1; atomic claim vẫn bắt buộc vì API, listener và scheduler cùng hoạt động.

Code có một số hỗ trợ SQLite: `Database` bỏ tham số pool MySQL khi URL bắt đầu bằng sqlite; `_upsert_message` có nhánh `sqlite_insert`; khóa BIGINT PK có variant. Nhưng `config.py`, bootstrap/setup và CLI backup còn thiên về MySQL; `aiosqlite` đang ở dependency dev; worker/action dùng `with_for_update(skip_locked=True)`; fixture không bật foreign keys. Cần đánh giá `FULLTEXT`, collation, JSON, autoincrement, datetime UTC, cascade, ALTER và batch migration theo dialect. Không coi SQLite in-memory cùng session là kiểm chứng nhiều tiến trình.

Guardrails cho SQLite:

1. Mỗi profile có backend và đường dẫn tường minh, data directory riêng; phát hiện profile MySQL cũ và giữ nguyên. Không tạo SQLite rỗng rồi âm thầm bỏ dữ liệu MySQL.
2. SQLite file trên ổ local được hỗ trợ, không mặc định đặt database/Qdrant trong thư mục đồng bộ cloud hoặc network share. Bật/kiểm tra WAL, `foreign_keys=ON` cho mọi connection, busy timeout hữu hạn. PR ghi rõ chính sách durability và lý do; không hạ synchronous để che lỗi hiệu năng.
3. Claim bằng atomic conditional UPDATE/CAS, lease token và fencing; không dựa vào row lock/skip_locked ở SQLite. Giao dịch ghi ngắn; không giữ transaction khi gọi Telegram/AI. Có retry bounded cho busy/deadlock và thông báo rõ khi hết retry.
4. Backup dùng cơ chế snapshot nhất quán của SQLite; không copy riêng `.db` khi WAL đang hoạt động. Restore đóng connection, giữ maintenance lock và kiểm tra integrity/foreign keys.
5. Ma trận SQLite và MySQL thật đều bắt buộc cho migration, claim, revocation, backup và ngân sách. Chỉ MySQL đang được hỗ trợ trong báo cáo hiện tại; phạm vi hỗ trợ SQLite chỉ được ghi vào release notes sau bằng chứng.

## 3. Danh mục giao việc và phụ thuộc

| ID | Ưu tiên | Kết quả | Phụ thuộc | Chủ trì đề xuất |
|---|---|---|---|---|
| RL-01 | P0 | BLOCK thắng queued/running/retry/restart | RL-03 cho claim đa tiến trình; có thể sửa worker không tự ALLOW ngay | Backend/policy |
| RL-02 | P0 | Alembic SQLite/MySQL cài mới và upgrade đúng, migration bất biến | StorageService/profile contract | Database |
| RL-03 | P0 | Claim/lease/maintenance không chạy hai worker hoặc ghi sau thu hồi | RL-02 nếu thêm schema | Backend/database |
| RL-04 | P1 | EmbeddingProfile/store_id, reuse có bằng chứng, checkpoint và retry | RL-01, RL-03 | RAG |
| RL-05 | P1 | Dedup có phạm vi, chỉnh sửa/xóa cập nhật index và retrieval | RL-04 | RAG/Telegram |
| RL-06 | P1 | Recovery từng nguồn thực thi có kiểm chứng | RL-04, RL-05 | RAG/API |
| RL-07 | P1 | Backup/restore nhất quán theo schema và worker | RL-02, RL-03, RL-06 | Operations |
| RL-08 | P1 | Hạn mức atomic và xử lý usage không rõ | RL-02, RL-03 | AI/database |
| RL-09 | P0 | Artifact Windows đủ tài nguyên, cài/upgrade không cần checkout | RL-02; phối hợp gói nền tảng | Packaging |
| RL-10 | P1 | Bảo mật owner/API/data có regression và bằng chứng | RL-01, RL-07, RL-09 | Security/QA |
| RL-11 | P0 | CI/release gates trên đúng commit/artifact | RL-01…RL-10 | Release/QA |
| RL-12 | P2 | Giảm nợ kỹ thuật có mục tiêu, docs và cleanup | Sau cổng nền hoặc song song docs | Maintainer |

RL-03 và RL-08 được làm chung một hợp đồng transaction/lease, tránh hai cơ chế khóa không tương thích. Khi chia PR, chỉ một người sở hữu migration tiếp theo và một người điều phối phần `runtime.py`; không chạy các tác vụ sửa cùng block mã song song. Các PR RAG phải gắn test retrieval, không chỉ test số lượng vector.

## 4. Chi tiết các work package

### RL-01 — Thu hồi quyền không bị job cũ ghi đè (P0)

**Bằng chứng:** `src/tg_assistant/runtime.py:1944–1945` gọi `set_allowed(True)`/`apply_template("knowledge")` trong worker. Nhánh `set_chat_allowed` khoảng 2357 hủy PendingAction nhưng chưa hủy learning jobs. Nhánh leave khoảng 2715 cũng cần dùng cùng primitive thu hồi. `PolicyEngine.set_allowed(False)` tắt permissions và ghi revoked_at.

**File sở hữu:** `src/tg_assistant/policy.py`, `src/tg_assistant/runtime.py` (enqueue learn, worker, startup recovery, BLOCK/leave), `src/tg_assistant/db/models.py`, migration tiếp theo nếu cần generation; `src/tg_assistant/admin_api/app.py` (resume/retry), `tests/test_knowledge_learning.py`, `tests/test_policy.py`, `tests/test_admin_api.py`; **mới** `tests/test_learning_revocation.py`.

**Hợp đồng:** chỉ thao tác owner cấp quyền tường minh mới ALLOW và áp template; refresh worker không cấp lại quyền. `RevocationService` quản revocation epoch (authorization revision); job/action/context chụp epoch khi tạo và kiểm tra current policy, owner, membership, permission và epoch khi claim, trước từng I/O/batch, trước upsert/commit/delivery. BLOCK trong một transaction ghi policy/epoch, hủy queued/paused/retry của chat và yêu cầu hủy running. ALLOW lại không hồi sinh job thuộc grant cũ; tạo job mới. Dùng fresh read, không tin ORM object trong transaction kéo dài.

**Ngữ nghĩa race:** request đã gửi ra ngoài trước khi BLOCK commit có thể chưa hủy được; ghi rõ giới hạn, nhưng kết quả về sau phải bị bỏ và không được publish/commit/index trở lại nguồn đã thu hồi. Cổng observable là sau BLOCK commit không bắt đầu I/O mới cho revision cũ, không ghi corpus mới cho revision cũ và không trả nội dung nguồn đó. Giữ fencing của RL-03 cho bước ghi cuối, không chỉ kiểm tra đầu job.

**Acceptance:** queue→BLOCK→worker: allowed=False, mọi permissions disabled, sync/embed/upsert=0, job cancelled/revoked và audit reason rõ; BLOCK tại barrier giữa sync/commit/embed/upsert: không có batch mới, kết quả batch đang bay bị bỏ; failed→BLOCK→retry, paused→BLOCK→resume, restart/stale lease sau BLOCK đều không gọi lại; ALLOW mới+job mới hoạt động; actor khác owner không tạo grant. RAG cache và keyword/semantic đều không trả nguồn BLOCK.

**Reviewer:** chạy cùng fixtures với event barriers thay vì sleep; kiểm tra DB policy/epoch, job terminal reason, call counters, Qdrant IDs và audit; lặp trên hai process/session MySQL thật và SQLite file thật. Phải có test chạy qua `_process_learning_jobs`, không chỉ test helper cancel.

### RL-02 — Migration bất biến, cài mới và schema cũ (P0)

**Bằng chứng:** 0001 import models hiện tại và `Base.metadata.create_all`; 0002 create knowledge_sources lần nữa. Trống→0002 đã fail trên SQLite. Thêm migration 0006 đơn thuần không sửa được vì chuỗi dừng ở 0002.

**Files:** `alembic/versions/0001_initial_schema.py`, `0002_knowledge_source_inventory.py`…`0005_vector_store_reliability.py` (review lịch sử/compatibility, không sửa rộng tùy tiện), `alembic/env.py`, `alembic.ini`, `src/tg_assistant/db/models.py`, `src/tg_assistant/setup/mysql.py`; **mới** `tests/integration/test_migrations.py`, fixtures schema lịch sử và tài liệu migration compatibility.

**Thiết kế:** dựng DDL cố định của revision 0001 từ schema thực ở revision đó, không import live metadata để create/drop. Sửa bootstrap chain trước beta, công bố rõ revision nào từng phân phối. Revision đã được phân phối không được đổi nghĩa âm thầm; nếu phải sửa 0001 vì bug hiện hữu, giữ revision IDs, đưa compatibility note và test mọi shape đã tồn tại. Database từng chạy 0001 live-metadata có thể chứa bảng của revisions sau dù alembic_version=0001: cần kiểm tra shape đầy đủ và đường normalization có version; không thêm `if table exists: pass` cho toàn bộ migrations, không stamp head để né lỗi, không drop data. Sau khi sửa chain, freeze hash migrations của release và chỉ thêm forward migrations.

**Acceptance:** DB trống→head thành công; mỗi revision lịch sử đã hỗ trợ→head, cả shape 0001 chuẩn và shape 0001 đã có bảng do create_all cũ; chạy upgrade head lần hai là no-op; giữ dữ liệu sentinel, constraints/indexes/foreign keys đúng, alembic_version=head; unknown/future/drift schema dừng với hướng sửa không phá hủy. Lint cả alembic (I001 0005 trong báo cáo), không chỉ src/tests. Không yêu cầu tự downgrade production; rollback release bằng quy trình backup/restore được diễn tập.

**Reviewer:** dùng Alembic thật trên DB isolated theo dialect, query catalog/PRAGMA và so schema expected; fixture lịch sử lấy từ DDL đã freeze, không `Base.metadata.create_all` mới nhất. Test username/path Unicode, datetime UTC roundtrip, BIGINT/chat ID âm, JSON booleans, cascade và FULLTEXT hoặc fallback hợp lệ. Lưu revision/hashes và schema diff đã redact.

### RL-03 — Atomic claim, lease và maintenance (P0)

**Files:** `src/tg_assistant/runtime.py` (learning/admin/action workers và stale recovery), `src/tg_assistant/db/base.py`, `db/models.py`, `admin_api/app.py`, `services/actions.py`; **mới** `src/tg_assistant/contracts.py`, `src/tg_assistant/services/job_coordination.py` (JobRepository), migration cho lease/maintenance nếu cần, `tests/integration/test_worker_coordination.py`.

**Hợp đồng:** `JobRepository.claim` trả `JobLease`; một job chỉ có một lease owner/token hợp lệ. Claim chuyển queued→running atomically và xác nhận affected_rows=1; complete trả false cho lease stale. MySQL có thể giữ row locks; SQLite phải có CAS/transaction thật, không skip_locked giả. Pending action atomically consume một lần theo cùng nguyên tắc. Lease heartbeat cho job dài; expiry chỉ cho phép worker mới claim, token cũ không ghi progress/final status hoặc upsert. Maintenance là durable fence chung cho sync/listener/learn/cleanup/reindex/restore/action writers; backup/restore không chỉ khóa `_knowledge_lock` trong một process. Launcher ngăn khởi chạy hai runtime cùng SID/profile và báo lỗi dễ hiểu; database fencing vẫn bảo vệ concurrency API/scheduler.

**Acceptance:** hai process tranh 1 job→1 sync/embed; crash worker→reclaim sau lease theo clock giả, không reclaim khi heartbeat còn sống; worker cũ về muộn→fenced; pause/resume/cancel/retry không reset generation hoặc hồi sinh revoked job; maintenance không nhận writer mới và drain bounded, timeout báo rõ; restart sau maintenance lỗi không tự mở writer khi restore dang dở. Retry lock busy/deadlock có số lần và thời hạn rõ, không treo vô hạn.

**Pending Telegram side effects:** hai click/two executors chỉ consume action một lần. Nếu Telegram thành công nhưng response/DB commit thất bại, action chuyển `uncertain` có operation id, execution token và dữ liệu reconcile tối thiểu; không tự retry send/delete/leave. Acceptance fault hook trước gửi, sau gửi trước ghi receipt, sau receipt trước commit; restart/restore không gửi lại action uncertain hoặc confirmed snapshot cũ. Exactly-once chỉ ghi khi có bằng chứng idempotency/reconciliation tương ứng API, không suy ra từ CAS database.

**Reviewer:** tests dùng separate connections/process, SQLite temp file + WAL hoặc MySQL service thật; kết quả terminal/audit token nhất quán; không thay database concurrency bằng fake Python lock. Đo transaction durations để chắc không giữ write transaction qua HTTP.

### RL-04 — Vector thật, checkpoint và idempotency (P1)

**Bằng chứng:** `_index_knowledge_rows` khoảng 1163 reuse từ vector_status/model/version nhưng chưa kiểm tra point hiện diện. Checkpoint embedding dùng ID, commit sau upsert; MySQL và Qdrant không chung transaction. `services/vector_reliability.py` đã có store registry; bảo tồn provider-independent identity và legacy_read_only. UAT cũ 257→41 chưa chứng minh xóa vật lý trên commit này.

**Files:** `runtime.py` (index/refresh/learning), `ai/vector.py`, `ai/engine.py`, `ai/router.py`, `services/vector_reliability.py`, `db/models.py`, `config.py`; `tests/test_vector_reliability.py`, `test_qdrant_local.py`, `test_knowledge_learning.py`; **mới** `src/tg_assistant/contracts.py`, `services/embedding_profiles.py`, `tests/integration/test_rag_lifecycle.py`, `tests/test_embedding_profiles.py`.

**EmbeddingProfile bắt buộc:** provider/model/embedding_version/dimension/store_id/cloud_consent theo spec; fingerprint store có provider + endpoint hợp lệ + model + version + dimension, không chứa key và không phụ thuộc chat-completion provider. Cloud user không cần Ollama chỉ để embedding: owner chọn cloud embedding, capability được probe riêng với chat health trước khi học. Nếu không có embedding hợp lệ, chỉ keyword-only với nhãn rõ hoặc chọn provider khác; không giả Online. Ollama là local path. Thay profile tạo derived store mới; preview/reindex được owner duyệt, active store chỉ switch sau verification. Không ghi đè hoặc trộn vectors/dimensions giữa stores. Schema row embedding-state/checkpoint/coverage phải được scoped store_id, không dùng một vector_status global để đại diện mọi profile.

**Ranh giới dữ liệu:** LOCAL_ONLY của từng source được lọc trước tạo cloud context và trước cloud embedding; global consent không ghi đè source policy. Fallback cloud phải qua consent toàn cục + quyền/data route nguồn + budget. Query shared-corpus kết hợp A LOCAL_ONLY và B CLOUD_OK không gửi A sang cloud; nếu kết quả local không đủ, trả degraded/không đủ bằng chứng hoặc làm local theo contract. Secret detector chặn cả hai paths; không silent cloud fallback.

**Hợp đồng:** reuse chỉ khi point đúng active store/model/version/dimension và current content hash; metadata MySQL không đủ. Không đặt completed khi điểm thiếu hoặc failed chưa được xử lý. Checkpoint chỉ biểu thị phạm vi dữ liệu đã được đánh giá an toàn; các row dirty/error/pending phải có đường retry riêng không bị ID checkpoint bỏ qua. Point ID ổn định theo row/reference; retry upsert không tăng điểm trùng. Không dùng tên thư mục/path hiển thị làm bằng chứng store identity nếu model/version thay đổi.

**Acceptance fault injection:** (a) crash sau sync commit trước embed; (b) sau provider response trước upsert; (c) sau upsert trước DB commit; (d) partial batch thất bại; (e) DB restored nhưng Qdrant rỗng; (f) file/vector missing; (g) provider/endpoint/model/version/dimension đổi; (h) restart/retry. Mỗi case eventual coverage đúng theo store_id, retrieval/citation đúng, không mất source rows, checkpoint không skip lỗi và không duplicate IDs; store legacy không bị ghi/xóa ngầm. Disable AI/offline không báo học thành công khi không có index. Capability 401/404/no embedding/wrong dimension/timeout→degraded, không queue học giả; cloud setup không Ollama vẫn embed+ask được với consent; LOCAL_ONLY→cloud embed/chat call capture không có nội dung source đó; consent revoke/BLOCK giữa batch→kết quả cũ bị bỏ. Đổi chat provider mà embedding profile giữ nguyên không tạo store mới.

**Reviewer:** đọc MySQL row states/hash/checkpoint và actual `reference_ids`/point payload Qdrant trên thư mục temp thật, restart bằng close/open; query fixture với embedding deterministic. Kiểm tra accounting `evaluated=indexed+reused+filtered+duplicate+skipped+failed`, coverage cùng định nghĩa expected; warning phải còn thấy trong API/dashboard sau restart. Log old/new store ID/model/coverage giúp phân biệt đổi active store với mất vật lý.

### RL-05 — Dedup, edit/delete và khoảng trống sync (P1)

**Files:** `ai/local_first.py`, `runtime.py` (candidate query/index), `telegram/user_client.py` (`_upsert_message`, edit/delete listeners, sync_history), `ai/rag.py`, `services/search.py`, `services/operations.py`, `services/vector_reliability.py`; `tests/test_sync.py`, `test_rag_shared_corpus.py`, `test_group_ai_ask.py`, `test_operations.py`, `test_vector_reliability.py`.

**Quyết định beta ưu tiên đơn giản:** không dedup physical reference xuyên nguồn hoặc xuyên store_id. Nội dung A/B trùng vẫn giữ reference thuộc từng nguồn/store; có thể reuse embedding theo hash nếu cache cùng EmbeddingProfile có version và hợp lệ về data route, nhưng mỗi nguồn có point/payload riêng. Không lấy embedding/cloud cache từ LOCAL_ONLY để hợp thức hóa cloud egress. Dedup trong cùng nguồn/store chỉ khi giữ citation/alias rõ; nếu chưa có alias bền vững, giữ từng message point. Không thêm shared canonical point/refcount redesign trước beta. Dùng cùng eligibility trong indexer/coverage, phân biệt filtered/duplicate/retained/deleted để không báo missing giả.

**Acceptance:** học A và B có text trùng; BLOCK/xóa/retention A→B vẫn semantic-search được, citation chỉ B; query group A không trích B theo hành vi hiện tại. Edit message ID cũ dưới checkpoint→hash đổi, dirty/reembed, point cũ không ảnh hưởng câu trả lời; edit trong khi embed→kết quả version cũ bị bỏ; delete/retention→không retrieval/cached citation và cuối cùng xóa orphan. Nội dung mới biến thành secret/empty/service message phải xóa/invalidate point cũ. Concurrency sync/listener không tạo duplicate row hoặc version_number lỗi.

**Sync acceptance bổ sung:** backlog mới lớn hơn limit 1000/limit cấu hình, nhiều trang, overlap backfill/live, FloodWait/restart và message có timezone naive: không tiến high-water qua các ID chưa đọc theo contract. Initial newest-window nếu cố ý chỉ lấy newest phải được ghi là window, không quảng bá lịch sử đầy đủ. Message cũ sửa khi app offline: xác định lookback/reconciliation policy bounded, báo phạm vi có thể stale; ID mới một mình không bảo đảm bao hết edits. Fixture chứa sentinel ở mỗi trang để reviewer thấy tin thiếu ngay.

### RL-06 — Recovery từng nguồn có thực thi và kiểm chứng (P1)

**Bằng chứng:** `/api/v1/groups/{chat_id}/recovery-preview` tạo pending action; `_execute_actions` khoảng 2291 chuyển sang `approved_not_executed`. Đây là guard có chủ đích, chưa phải recovery hoàn chỉnh. Không xóa guard rồi gọi global reindex.

**Files:** `admin_api/app.py`, `admin_api/schemas.py`, `runtime.py` (recover_source_index), `services/vector_reliability.py`, `cli.py` (reindex); **mới** `services/source_recovery.py`, `tests/integration/test_source_recovery.py`; frontend chỉ các view coverage/recovery đã có trong `dashboard-prototype/src` và tests tương ứng, không thay art/theme.

**Hợp đồng:** `VectorRecoveryService.preview(chat_id, store_id)` read-only có expected/missing/orphan, source/store/model/version/hash/epoch; owner xác nhận plan riêng có TTL, scope source và cost/local/cloud mode rõ. Confirm không tự ALLOW; BLOCK hoặc preview hết hạn/schema/store/epoch thay đổi→reject/repreview. Job durable, pause/cancel/restart và RL-03 fencing; rebuild từ DB chuẩn vào store phù hợp, không sửa Telegram hoặc xóa source DB. Không chạy cleanup cả corpus để sửa một nguồn. Local/cloud recovery theo EmbeddingProfile đã chọn phải giữ LOCAL_ONLY/consent/budget; không tự cloud fallback trả phí. Nếu rebuild profile tạo store mới, active store switch chỉ sau verification, lỗi giữ store đang dùng.

**Acceptance:** nguồn có missing points→preview không đổi dữ liệu→confirm→recover→healthy và fixture retrieval đúng; running→cancel→restart giữ trạng thái chính xác; thất bại embedding hoặc disk-full→failed/warning, dữ liệu DB giữ; BLOCK sau preview/trong job→không ghi; recovery nguồn A không đổi B/legacy; retry idempotent. Final status chỉ completed khi verification sau rebuild pass; audit lưu before/after/delta và token job, không lưu secret hay toàn bộ text.

**Reviewer:** so export coverage trước/sau với actual points và query citations. UI không chỉ hiện toast thành công khi action mới approved; progress/failed/warning phải phản ánh backend thực. Nếu beta chưa làm được workflow này, release phải cung cấp đường recovery CLI từng nguồn có cùng guard và drill; không gọi nút preview là khôi phục đã hoàn tất.

### RL-07 — Backup/restore nhất quán và diễn tập (P1)

**Bằng chứng:** `cli.py:571–717` manifest hardcode schema=0001, backup vector chỉ metadata, restore yêu cầu schema substring, tạo pre-backup rồi import SQL; chưa có bằng chứng phối hợp worker. “Không chứa secret” quá rộng vì database text có thể là dữ liệu nhạy cảm người dùng.

**Files:** `cli.py`, `db/base.py`, `services/job_coordination.py`, `services/source_recovery.py`, `paths.py`, `security.py`; **mới** `services/backup_restore.py`, `tests/test_backup_manifest.py`, `tests/integration/test_backup_restore.py`; `USER_GUIDE.md`, `PRIVACY.md`, `TROUBLESHOOTING.md`.

**Manifest versioned:** app/build version, Alembic revision thực, backend/schema fingerprint, timestamp UTC, profile identifier không chứa credential, included/excluded components, file size/SHA-256, consistency mode, active store/model/version/dimension, `vectors_included=false` nếu chỉ metadata. Không coi checksum là chữ ký chống attacker. Không sao lưu Telegram session/API keys/Credential Manager theo mặc định; backup vẫn chứa nội dung Telegram và phải được mô tả như dữ liệu riêng tư. Quy định quyền file Windows/đường lưu; mã hóa backup nếu sản phẩm chọn hỗ trợ cần test riêng, không hứa chưa triển khai.

**Hợp đồng:** maintenance fence→drain writers→snapshot→verify checksum→publish archive atomically. MySQL single-transaction cần engine/DDL không thay giữa dump; SQLite dùng backup API snapshot hoặc đóng/checkpoint theo protocol, không copy DB khi WAL sống. Restore validation/checksum/schema/backend/quota trước mọi mutation; archive entries bounded, không zip-slip/extract path tùy ý. Blank target khác existing target: blank không phải pre-backup database chưa tồn tại; existing phải có pre-restore backup verified. Import vào staging/isolated DB nếu khả thi; lỗi không tự resume worker với schema nửa chừng. Đích dialect khác không tự chuyển. Config import theo whitelist/rebase path; không ghi đè loopback/credentials/profile người dùng âm thầm.

**Acceptance:** backup data sentinel/jobs/policy/AiUsage→restore DB trống→upgrade supported revision→reindex vector nếu không included→coverage+retrieval+BLOCK đúng; restore into existing target có pre-backup phục hồi được; corrupt/truncated/wrong/future schema/wrong backend/oversize/untrusted path→reject trước mutation; hết disk, mysqldump/mysql thất bại hoặc crash ở mỗi phase→partial artifact không được nhận là valid, maintenance reason còn rõ. Jobs restored không tự gửi/xóa Telegram/pending confirmed; invalidate/reconcile stale lease, pending actions và budget reservation trước resume.

**Reviewer:** diễn tập trên Windows VM và DB thật; so row counts + sentinel hashes + permission scope + actual Qdrant retrieval. Lưu manifest checksum, restore/recovery timings và rollback result đã redact. Release note ghi RPO/RTO đo được theo corpus fixture và hardware, không tự nhận zero data loss.

### RL-08 — Ngân sách/token khi request đồng thời (P1)

**Bằng chứng:** BudgetService kiểm tra tổng AiUsage, provider gọi sau đó mới record; `_request_lock` chỉ quản RPM trong từng engine, không bảo đảm tổng ngân sách. Token đang ước lượng len//4 và bảng giá hardcode.

**Files:** `ai/budget.py`, `ai/engine.py`, `ai/router.py`, `ai/local_first.py`, `db/models.py`, migration kế tiếp; `tests/test_search_budget_pairing.py`, `test_ai_rate_limit.py`, `test_ai_router.py`, `test_ollama_engine.py`; **mới** `tests/integration/test_budget_concurrency.py`.

**Hợp đồng:** atomically reserve trước outbound request cho USD, daily/monthly/token, group/feature/provider/fallback count; dùng request id idempotent, amount projected + reserved + committed so với cap. Chốt timezone kỳ ngân sách (code hiện UTC, UI phải nói rõ; ngày của Asia/Saigon khác UTC). Reservation commit transaction ngắn trước I/O; reconcile actual usage đúng một lần, xử lý crash/timeout/unknown usage riêng. Chưa biết provider đã tính tiền thì không tự release và retry làm chi tiêu không bị tính; giữ khoản ước tính hoặc reconcile rồi cho operator thấy. Local USD=0 nhưng token limits vẫn theo policy. Unknown model/pricing fail-closed và hướng cập nhật; fallback không né reservation.

**Acceptance:** hai request mỗi request hợp lệ đơn lẻ nhưng tổng vượt cap→chỉ số request phù hợp được gửi (barrier hai DB sessions/process); hai engine/provider/fallback cùng chủ budget; duplicate request id→một reservation/record; provider timeout sau nhận, exception trước gửi, cancellation, restart/stale reservation và midnight/month boundary→không cap bypass hoặc double record. Quota giảm khi outstanding còn lớn→chặn mới, không hủy accounting cũ. Test Unicode tiếng Việt, emoji, long prompt, instructions/context overhead; dùng tokenizer/provider-aware accounting nếu muốn gọi token cap là chắc chắn. Nếu chỉ estimate, UI ghi ước tính và safety margin có test; không hứa cost cap bằng hóa đơn tuyệt đối khi usage/giá có sai lệch.

Cloud embedding capability probe cũng có cap nhỏ/request timeout và accounting; embedding batch/recovery/resume đều reserve theo EmbeddingProfile, không chỉ answer. Cache hit có actual no-outbound cost=0 nhưng giữ policy/epoch checks; cloud fallback sau local failure không double reserve/double charge cùng attempt, và không gửi source LOCAL_ONLY. Test capability model supports chat nhưng không embedding, auth valid nhưng model unavailable, returned dimension mismatch; không queue hoặc tiêu budget học tiếp dưới nhãn ready giả.

**Reviewer:** actual provider call counters dưới fake provider + MySQL/SQLite atomic store thật, reservation state và spend formula. Bảng giá version+ngày nguồn chính thức+model aliases; kiểm tra giá hiện tại lúc làm PR, không lấy giá trong báo cáo làm truth mới. Bỏ assertion giá lỗi thời phải kèm evidence và vẫn giữ test unknown-model fail-closed.

### RL-09 — Artifact Windows và tài nguyên runtime (P0)

**Bằng chứng:** wheel chỉ package `src/tg_assistant`; project_root dựa `parents[2]`; alembic.ini/migrations/dashboard nằm ngoài, kiểm tra bản cài thiếu cả ba. Snapshot dashboard chỉ có một phần source/tests; không suy ra artifact frontend hoàn chỉnh từ snapshot.

**Files:** `pyproject.toml`, `paths.py`, `config.py`, `cli.py`, `setup/wizard.py`, `setup/mysql.py`, `alembic/env.py`; thư mục resource package/launcher **mới** do gói nền tảng chọn; dashboard build config/lock/package files thực trong repository; **mới** `tests/integration/test_installed_artifact.py`, `scripts/release-smoke.ps1`.

**Hợp đồng:** installer per-user bundle PySide6 Qt Widgets launcher/setup/tray + Python runtime + React dashboard browser; không QtWebEngine. Wheel có thể là thành phần installer nhưng không đại diện toàn bộ ứng dụng. Bundle migrations/config/docs/dashboard build, dùng resource resolver/importlib.resources hỗ trợ frozen bundle, runtime data vào user-data riêng. Không cần source checkout, Python/Node/npm/MySQL trên máy người dùng cài mới, .venv/node_modules/dist đã tracked hoặc dependency dev. SQLite + aiosqlite ở dependency sản phẩm, RL-02/03/07/08 là cổng chặn installer complete. MySQL profile nâng cao yêu cầu app account least privilege, không root dùng thường ngày; secrets không qua command-line/logs. Offline start có diagnostics và không tự gửi thông tin ra cloud.

**Acceptance:** VM Windows sạch standard user không Python/Node/MySQL trước cài (theo đích installer)→install→migrate→dashboard assets load→restart; profile MySQL nâng cao bằng MySQL thật→migrate/connect không lưu root credential; username/path có dấu/khoảng trắng, non-default data dir, port occupied, Ollama absent, credential access failure, antivirus file lock→lỗi có bước sửa; upgrade từ beta trước giữ data/config/art; uninstall có lựa chọn giữ data và không drop MySQL tự động. Chạy installed artifact trong cwd không có repository để phát hiện fallback source.

**Reviewer:** manifest file tree + checksum, smoke từ artifact đã tải trên VM, network/browser console, launcher/doctor logs đã redact; dashboard ảnh trước/sau cùng viewport giữ art/layout, chỉ đổi trạng thái/error cần thiết. Package build pass chưa đủ cho cổng này.

### RL-10 — Bảo mật và QA hành vi người dùng (P1)

**Files:** `policy.py`, `security.py`, `logging.py`, `telegram/pairing.py`, `telegram/control_bot.py`, `admin_api/auth.py`, `admin_api/app.py`, `admin_api/schemas.py`, `ai/rag.py`, `services/search.py`; tests hiện `test_admin_api`, `test_actions`, `test_policy`, `test_security_memory`, `test_group_ai_ask`, `test_rag_shared_corpus`; **mới** `tests/test_release_security.py`, browser/UAT scenarios có synthetic data.

**Acceptance:** owner pairing single-use/expiry; sai owner/không pair không làm job hoặc truy dữ liệu; dashboard 401 hết session, 403 CSRF thiếu/sai, invalid Host/Origin, API từ LAN không truy cập, cookies HttpOnly/expiry; path traversal static/export không ra ngoài root. Những hành vi middleware chưa rõ phải thử thực, không suy từ tên cookie. Logout/credential replace không để session quyền cũ chạy action. Pending send/edit/delete cần đúng owner+TTL+scope+rights khi execute; retry không gửi/xóa hai lần nếu I/O thành công nhưng commit lỗi (ghi lại giới hạn exactly-once của Telegram nếu không chứng minh được). Group /ask chỉ current group; nguồn BLOCK/deleted/secret không lộ qua cache/search/citations/export.

Phối hợp gói nền tảng cho native setup/IPC/ticket: chỉ SID hiện tại được named-pipe command; dashboard launch ticket entropy 256 bit, TTL30s, bound profile/audience, hash-only và redeem atomic một lần. Hai browser redeem cùng ticket→một session; expired/replay/wrong profile/origin→reject; fragment xóa ngay và không trong URL query/log/Referer/history sau redeem. Native secret nhập không đi DTO/bridge/browser/bot; test redaction với API hash/key/token/OTP/2FA synthetic, crash log và input-resume không persist OTP/2FA/QR ticket. Setup public status read-only sanitize; write vẫn session/CSRF và capability gate trước owner_paired. Credential Manager failure không fallback plaintext file. Thêm **mới** `tests/test_dashboard_launch_ticket.py`, `tests/integration/test_native_ipc_security.py`; module paths native/ticket do gói nền tảng sở hữu theo spec, tránh gói QA tự tạo implement song song.

UI matrix: 401/403, AI off, local model missing, cloud invalid key/model/429/timeout/5xx, internet off, MySQL/SQLite busy/down, Qdrant lock/missing, pause/cancel/retry, server stop/restart, SSE reconnect. Không toast completed khi API action mới queued; dữ liệu rỗng có trạng thái hợp lý; dùng cấu trúc lỗi được dịch dễ sửa, giữ art đang có. Tests backend mock cover response contract; browser thật và provider smoke trả kết quả riêng.

**Reviewer:** network traces redact, test call counts/no side effects, audit outcome, ảnh UI cùng viewport. Scan artifact/log/export/fixtures/history trước public distribution bằng tooling phù hợp; không đọc credential của owner kiểm toán. `.env` sạch ở snapshot không chứng minh lịch sử sạch; nếu phát hiện secret thật phải xử lý rotation với owner. Không mở dashboard ra Internet để “test nhiều người dùng”.

### RL-11 — CI, ma trận kiểm thử và release gates (P0)

**Files mới đề xuất:** `.github/workflows/backend.yml`, `migrations.yml`, `frontend.yml`, `release.yml` (hoặc hợp nhất nếu đơn giản hơn), lock dependency release, `scripts/release-smoke.ps1`, `docs/release-checklist.md`, fixture/test integration ở trên. Không thực hiện GitHub mutation trong gói lập kế hoạch này.

| Tầng | Môi trường/dữ liệu | Bắt buộc chứng minh | Không được suy rộng |
|---|---|---|---|
| T0 baseline | Python 3.12 release runtime; SQLite in-memory/mocks hiện có | 150 test nền còn pass, test mới đúng contract, Ruff src/tests/alembic, compile | Chưa chứng minh migration/MySQL/concurrency/Windows |
| T1 dialect | SQLite file thật WAL; MySQL phiên bản tối thiểu và pinned release version thực | Alembic empty/upgrade/drift; FK/JSON/time; multi-process claim/budget/revocation | Docker MySQL trên Linux chưa là Windows install |
| T2 RAG lifecycle | DB thật + Qdrant local thật; deterministic embed fake | Sync→commit→embed→upsert→crash/retry→retrieve; edits/dedup/retention/recovery | Chưa chứng minh chất lượng model/provider thật |
| T3 artifact | Windows sạch đúng support matrix, standard user; installer thật/hash thật | Cài/upgrade/bootstrap/resources/docs/loopback/start-stop/backup-restore | Cài dev editable không tương đương artifact |
| T4 browser/UAT | Dashboard bundle phát hành, browser thật; synthetic Telegram sandbox | Full onboarding, errors, block/pause/recovery, API/UI state nhất quán | Backend HTTP mock chưa chứng minh DOM/accessibility/assets |
| T5 live smoke | Tài khoản test riêng được người vận hành cung cấp; nguồn test/cap nhỏ | Telegram login/pair/learn/ask/block; provider được chọn và Ollama; không dùng corpus thật của owner | Không chứng minh soak hoặc mọi provider/model |
| T6 soak | Windows chạy liên tục 72h với corpus fixture và thao tác định kỳ | Không tự re-ALLOW, stalled lease, crash không recovery, missing vector tăng không lý do; budget/cpu/rss/job age ghi nhận | Không gọi 72h là chứng minh stable nhiều tháng |

MySQL tests phải thật sự kết nối MySQL và xuất dialect/version; dịch vụ unavailable→fail required job, không silently skip hoặc đổi SQLite. SQLite production phải file-backed, bật foreign keys/WAL và nhiều process. Windows support đề xuất ban đầu Windows 11 x64; nếu gói nền tảng chọn khác phải chốt OS/arch/version support rồi điền đúng matrix, không tự tuyên bố Windows 10/ARM64 có hỗ trợ. Frontend phải `install` theo lock clean + build/lint + các test hiện có; nếu repository chưa có script nào thì bổ sung script trước khi gắn CI, không hứa gọi lệnh giả.

**Cổng beta, tất cả trên cùng commit và artifact:**

1. RL-01/02/03 và RL-09 pass; không open defect thu hồi quyền/mất dữ liệu/cài mới chặn.
2. 150 tests nền pass + regression mới; không skip/xfail case bắt buộc; lint gồm migrations, backend build/install smoke, frontend build/lint, dependency lock và package resources pass.
3. Required jobs T1–T4 pass cho mọi backend/OS được công bố; backup/restore/retrieval drill RL-07 có evidence; cap concurrency RL-08 pass.
4. T5 với tài khoản test riêng và T6 hoàn tất. Pilot 5 người mới: ít nhất 4/5 hoàn thành kết nối và câu trả lời đầu trong 15 phút khi credential đã chuẩn bị, không hướng dẫn trực tiếp; tiếp tục ALLOW một nguồn→học→ask có citation→BLOCK→restart→backup. Cloud users không cài Ollama bắt buộc; local path riêng khi owner chọn. Ghi số bước fail/timing và lỗi hữu hình; không yêu cầu người mới cung cấp log nguyên chứa token/session.
5. Release artifact checksum/build provenance/version/schema/known limits, instructions rollback và license/assets review gồm Qt/PySide6 và fonts; scans sạch trong phạm vi đã tuyên bố. Có blocker→không tạo nhãn stable; beta giới hạn được công bố cụ thể. Release evidence phải có mixed LOCAL_ONLY/CLOUD_OK outbound capture, ticket replay/race, uncertain Telegram side effect/restart, SQLite storage/claims và MySQL upgrade/profile-preservation.

Reviewer nhận một evidence folder theo commit: `summary.md` có test counts/pass/fail/skips và giới hạn, JUnit, lock/hash/runtime/DB versions, manifests/schema diff, fixture coverage before/after, crash/restore drill, artifact checksum, UI screenshots redact. Artifact kiểm thử phải có SHA trùng artifact phát hành; build lại khác dependency phải chạy lại cổng liên quan. Không lấy kết quả UAT tháng 7 hay baseline 150 của kiểm toán làm test live cho HEAD mới.

### RL-12 — Cleanup và nợ kỹ thuật có mục tiêu (P2)

**Files:** `.gitignore`, tracked generated tree, `README.md`, `PRIVACY.md`, `ARCHITECTURE.md`, `SECURITY.md`, `USER_GUIDE.md`, `TROUBLESHOOTING.md`, `pyproject.toml`, `LICENSE` **mới** theo quyết định owner. Chỉ tách helper coordination/recovery/backup đã cần trong RL-03/06/07; không rewrite runtime/control_bot/admin_api toàn bộ để đạt coverage đẹp.

**Acceptance:** docs mô tả current-group /ask, local storage/backend được hỗ trợ, backup exclusions/reindex, grant/revoke semantics và failure recovery thực; security report không giữ “không có web/frontend/28 tests”. Release tree không chứa .venv/node_modules/cache/test data/sessions/credential; artwork/fonts có quyền phân phối rõ và giữ bản đang dùng. Cleanup tracked files/historical secret là công việc repository riêng có review, không xóa art đang cần dựa vào tên file.

**Reviewer:** diff docs so với behavior tests, manifest tree và asset inventory. Coverage ưu tiên tăng tại worker/CLI/setup/recovery qua test nhánh rủi ro; không đặt 100% toàn bộ code làm điều kiện rewrite. Khi đổi tên/tách module, bộ test nền giữ ý nghĩa và ID evidence giúp so baseline.

## 5. Chuẩn bàn giao mỗi PR

Mỗi PR dẫn ID RL, problem→behavior sau sửa, file scope thật, migration/data compatibility, tests mới và lệnh đã chạy; phân biệt mock, integration, Windows VM và live smoke. Reviewer tái chạy bằng fixture synthetic; không cần key/session thật để duyệt logic. Bằng chứng live nằm ở kênh bảo mật được phép, chỉ metadata/redacted result vào repo.

Thứ tự triển khai: gỡ tự-ALLOW và thêm regression RL-01 trước; implement backend/artifact theo spec đã chốt; freeze/repair migrations RL-02; claim/fencing RL-03; EmbeddingProfile và RAG RL-04/05; recovery RL-06; backup RL-07 và budget RL-08; artifact RL-09; security/CI/UAT RL-10/11. Docs/lock/asset inventory có thể làm song song; contracts/schema phải được điều phối trước khi sửa shared runtime.

Không tuyên bố hoàn tất nếu chỉ status job xanh: bắt buộc policy còn đúng, source rows còn đúng, point thật đúng, retrieval/citation đúng và dữ liệu sau restart/restore đúng. Mọi testcase race phải có barrier/fault hook deterministic và hậu kiểm, không dùng chờ ngẫu nhiên làm bằng chứng.

## 6. Rủi ro cần giữ hiển thị cho người quyết định

- SQLite giảm onboarding nhưng mở thêm matrix durability/concurrency/migration/backup; không tự chuyển MySQL profile cũ. Prototype không qua cổng thì installer/beta vẫn chưa hoàn tất; không tự thay quyết định thiết kế bằng MySQL bắt buộc người mới.
- Một check quyền trước HTTP chưa đủ chống BLOCK đồng thời; revision fencing tại write và fresh read giữa batches là bắt buộc. I/O đã bay không thể bảo đảm hủy ở phía Telegram/provider.
- MySQL/Qdrant không chung transaction; recovery/idempotency là contract sản phẩm, không giả transaction toàn cục.
- Backup hiện chỉ metadata vector và có thể chứa nội dung nhạy cảm trong database; cần nói rõ rebuild + credential re-entry, không gọi zip hiện tại là full recovery.
- Pricing/token estimates không phải hóa đơn thực; test concurrency vẫn bắt buộc để không vượt cap do race nội bộ.
- 150 tests là baseline kiểm toán ở snapshot, chưa phải release gate đã chạy cho commit tương lai. Các sửa package/setup/frontend cần artifact/Windows clean-machine evidence, không được thay bằng test helper.
