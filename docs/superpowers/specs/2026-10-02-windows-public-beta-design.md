# Thiết kế bản Windows dành cho người dùng mới

Ngày: 02/10/2026. Trạng thái: bộ thiết kế để review/handoff; chưa triển khai.
Phạm vi đã được người dùng xác nhận: mỗi người tự cài ứng dụng Windows và quản lý tài khoản Telegram của mình.
Nguồn: repo NyanBUIDL/telegram-ai-personal-assistant, baseline 7429fcffd61860e9502f066e0b6a921df72fe176; đánh giá RELEASE_READINESS_REVIEW_VI.md.

## 1. Mục tiêu và định nghĩa hoàn thành

Biến MVP hiện tại thành bản beta Windows có thể tự cài, kết nối API, đăng nhập tài khoản Telegram, ghép bot và mở dashboard bằng GUI. Giữ nguyên phong cách retro neo-brutalist, ngôn ngữ tiếng Việt và nguyên tắc owner-only/default deny.

Mục tiêu đo bằng UAT, chưa phải kết quả đã đạt:
- Trên Windows sạch, người dùng không cần tự cài Python/Node/MySQL hay dùng terminal để hoàn thành luồng chuẩn.
- Với các credential đã chuẩn bị, ít nhất 4/5 người thử mới hoàn thành kết nối và câu trả lời đầu trong 15 phút mà không được hướng dẫn trực tiếp.
- Mở lại dashboard từ launcher trong tối đa 2 thao tác; không chạy CLI lấy mã.
- Tất cả thao tác có lỗi cho biết vấn đề và một bước sửa cụ thể; không toast-success trước khi backend xác nhận.
- Không có blocker bảo mật/quyền/cài mới/restore; mọi checklist Done có evidence.
- Beta-ready chỉ sau kiểm tra artifact Windows sạch, upgrade cũ, regression, UI/art và UAT; chưa coi code-ready là beta-ready.

## 2. Các phương án và lựa chọn

| Phương án | Lợi ích | Giá phải trả | Quyết định |
| --- | --- | --- | --- |
| Giữ setup CLI + MySQL thủ công | Ít thay đổi | Người mới khó tự cài, không đạt yêu cầu | Chỉ giữ đường advanced/legacy |
| Launcher GUI + SQLite mặc định + dashboard browser | Không cần database service, tận dụng backend/React | Phải xử lý concurrency, migration, packaging native | Chọn cho v1 |
| SaaS website đa người dùng | Không phải cài ứng dụng | Đổi mô hình session/credential/tenant, chi phí vận hành | Ngoài v1 đã chốt |

SQLite là quyết định cho cài mới, không phải tuyên bố code hiện đã hỗ trợ production SQLite hoàn chỉnh. MySQL của người dùng hiện hữu phải được giữ. Không tự động chuyển dữ liệu sang SQLite.

## 3. Kiến trúc

- PySide6 native launcher/setup/tray, đóng gói runtime; không yêu cầu Python/Node hệ thống. Dùng Qt Widgets và browser mặc định, không thêm QtWebEngine.
- Worker Python hiện hữu được tách dịch vụ theo trách nhiệm; giữ Telethon, aiogram, PolicyEngine, FastAPI, SQLAlchemy, Qdrant và React/Vite.
- SQLite + aiosqlite là storage mặc định cài mới; WAL, foreign_keys, busy timeout và atomic compare-and-set cho job/action. Một worker per Windows SID vẫn phải xử lý concurrency giữa API/scheduler.
- MySQL hiện hữu/advanced giữ driver và migration tương thích; selector cấu hình rõ ràng, không đổi backend khi restart.
- Dữ liệu mutable ở %LOCALAPPDATA%/TelegramAIPersonalAssistant; config, DB, session, vector, logs, backup tách khỏi thư mục cài. SQLite primary DB không nằm trong cloud-synced folder mặc định.
- Resource dashboard build, docs và Alembic đặt trong package; resolve qua importlib.resources/resource resolver có hỗ trợ frozen bundle.
- Local IPC dùng named pipe chỉ cho Windows SID hiện tại. Không mở broker secret-control ra LAN/HTTP.
- Admin HTTP tiếp tục loopback, kiểm tra Host/Origin, HttpOnly/SameSite cookie và CSRF. Port conflict có thông báo và chọn port loopback khả dụng, launcher truyền URL thực; không mở firewall.
- Chế độ setup hoạt động trước DB/account/pairing. Browser có thể xem hướng dẫn/trạng thái không nhạy cảm ở trạng thái chưa kết nối; các thao tác nghiệp vụ bị khóa theo capability.

## 4. Luồng người dùng

1. Tải installer -> cài per-user -> mở launcher.
2. Chọn cloud / local / chưa bật AI. Chỉ hiện tùy chọn thật được backend hỗ trợ.
3. Cloud: mở trang cấp key, nhập key trong native dialog, kiểm tra auth và capability model, lưu Credential Manager. Local: kiểm tra Ollama, hướng dẫn tải/cài có lựa chọn; hiển thị dung lượng và chờ tải model, có cancel/resume. Không tự cài/download model lớn mà người dùng chưa chọn.
4. Kết nối tài khoản Telegram: giải thích API ID/hash, mở my.telegram.org; nhập native, đăng nhập QR hoặc phone/OTP/2FA fallback. Lấy owner_id từ tài khoản đã xác minh. QR không bỏ qua API ID/hash.
5. Kết nối bot: nút mở BotFather, hướng dẫn /newbot, nhập token native, getMe xác minh, hiển thị tên bot không token.
6. Ghép owner: nút mở bot bằng deep link với nonce một lần, owner bấm Start; sender_id phải đúng owner_id. Có fallback mã pairing, hết hạn tạo lại. Không mặc định người đầu tiên bấm link là owner.
7. Chọn 1 nguồn đầu tiên -> xem quyền và dữ liệu sẽ dùng -> xác nhận -> đồng bộ/học -> đặt câu hỏi thử trong bot. Không tự ALLOW toàn bộ chat.
8. Mở dashboard từ launcher một nút; thấy tình trạng thực và việc cần làm tiếp.
9. Người dùng có thể lưu/thoát rồi tiếp tục setup. OTP/2FA/QR ticket không được persist; bước xác thực tạm phải bắt đầu lại, không báo giả là hoàn thành.

Luồng chuẩn không yêu cầu terminal. API key, bot token, API hash, OTP và 2FA chỉ nhập trong native dialog, không qua bot, browser query, log hoặc analytics. Khi nhập lại chỉ hiện ô trống; không prefill secret đã lưu.

Theo [Telegram API credentials](https://core.telegram.org/api/obtaining_api_id), user authorization cần api_id/api_hash. [QR login](https://core.telegram.org/api/qr-login) có token hết hạn và yêu cầu xác nhận từ Telegram đang đăng nhập. [Bot deep linking](https://core.telegram.org/bots/features#deep-linking) dùng start payload; token ghép owner phải nằm trong giới hạn payload và được kiểm tra bên server.

## 5. Đăng nhập dashboard dễ sử dụng

Launcher yêu cầu ticket qua IPC được xác thực Windows SID. Ticket ngẫu nhiên 256 bit, TTL 30 giây, chỉ dùng một lần, bound profile và audience dashboard. Launcher mở URL fragment; JS xóa fragment khỏi history ngay, POST redeem cùng origin; không đưa ticket vào query hoặc log, không có third-party script trên login page. Redeem atomically tạo cookie HttpOnly/SameSite và CSRF.

Ticket bị dùng lại/hết hạn không tạo session; UI có nút quay về launcher/lấy ticket mới. Đăng xuất thu hồi session và không tự đăng nhập lại cho tới khi owner bấm Open Dashboard. CLI code được giữ làm recovery, phải one-use thật; không thêm mật khẩu app vào luồng chuẩn.

Windows account là ranh giới trust; malware dưới cùng SID có quyền tương đương ứng dụng là giới hạn rõ ràng, không tuyên bố IPC ngăn được malware cùng tài khoản.

## 6. Kết nối và AI

Provider profile chứa chat provider/model, embedding provider/model/version/dimension và consent. Không giữ embedding Ollama-only làm điều kiện ngầm cho cloud:
- Cloud setup dùng cloud chat + embedding đã xác minh capability và owner đồng ý. Nếu provider không có embedding hợp lệ, hướng dẫn chọn provider embedding khác hoặc dùng keyword-only có nhãn giới hạn.
- Local setup dùng Ollama chat/embedding khi sẵn sàng; không phát sinh cloud fallback tự động.
- Health chat và embedding riêng: ready/degraded/disconnected/checking/unknown, checked_at và next_action; unknown/stale không được tô Online.
- Dữ liệu bị secret detector chặn khỏi model/embedding; không tự gửi toàn bộ lịch sử ra cloud; owner thấy phạm vi dữ liệu và chi phí trước học.
- LOCAL ONLY của nguồn là ràng buộc dữ liệu: không gửi nội dung nguồn đó qua cloud chat hoặc cloud embedding; shared-corpus retrieval phải lọc theo data route của từng nguồn trước khi tạo context.
- Fallback cloud là opt-in và phải vượt cả consent toàn cục, policy nguồn và budget; lỗi provider không được bỏ qua ranh giới này.
- store_id = fingerprint(provider, endpoint hợp lệ, model, version, dimension); thay profile tạo store khác, preview/reindex có xác nhận, không ghi đè corpus đang dùng.
- CoinGecko là optional; bỏ qua không làm onboarding thất bại.

## 7. Dashboard và art guideline

Navigation chính: Tổng quan; Kết nối; Nguồn Telegram; Tri thức; Công việc; Vận hành; Cài đặt & trợ giúp. Advanced permissions/logs/model chi tiết nằm trong panel có thể mở; không xóa chức năng hiện có.

Tổng quan tập trung: ứng dụng đang chạy hay không; Telegram/bot/chat AI/embedding đã kết nối hay chưa; nguồn đang học; việc cần xử lý; chi phí/dung lượng. Không dùng bảng số liệu để che trạng thái setup chưa hoàn thành.

Giữ art từ dashboard-prototype/AGENTS.md và styles:
- Paper #f3efdf, panel #fffdf5; ink #090909.
- Teal #00c8c8, magenta #ef00c8, yellow #ffd51f.
- Góc vuông, viền đen 3px, bóng cứng 7px; không thay bằng gradient/glass/rounded-card layout.
- PeterObscure chỉ logo/main title; DarleySans cho heading/body/table/form/control; kiểm tra font license trước distribution.
- Native launcher/dialog phải giữ cùng màu, type hierarchy, border/shadow spirit; adapted DPI cho native không ép mọi widget có cùng shadow.
- Focus rõ, bàn phím đầy đủ, dialog focus trap/ESC/restore focus, trạng thái không chỉ bằng màu.
- Browser 1280/1440 desktop và 360/390 mobile; Windows scale 100/125/150/200%, keyboard và long Vietnamese copy.
- Touch target tối thiểu 44 CSS px; bảng dense có view/card/scroll hợp lý, Chat ID luôn text.
- Visual baseline/screenshot diff kiểm tra style, đồng thời reviewer kiểm tra semantic/interaction; pixel diff không tự kết luận usability.

## 8. Quyền và độ tin cậy

- BLOCK/revoke có epoch; job/action stale không tự re-enable; đang chạy phải kiểm tra trước commit/index/delivery.
- Pending action atomically consume một lần; double click, retries và restart không double execution. Telegram side effect không luôn hỗ trợ exactly-once; trạng thái uncertain cần reconcile, không retry mù.
- MySQL/SQLite đều là source of truth; vector là derived index theo store_id; scope/dedup/coverage tính cùng eligibility rules.
- Edit/delete làm dirty/invalidate; checkpoint ID không thay thế queue reindex dirty rows.
- Recovery từng nguồn: preview số thiếu -> approve -> job append-only -> kiểm tra invariant -> retrieval; active store chỉ switch khi verified. Không báo completed khi partial unexplained.
- Backup manifest ghi schema revision thật/backend/profile/checksum; secrets excluded. SQLite online backup an toàn; MySQL snapshot có consistency. Restore fence worker/API -> backup trước -> staged validation -> atomic replace/swap phù hợp backend -> migration -> vector reconcile; không còn metadata indexed giả khi vector trống.
- Budget reservation atomically bao gồm pending request, concurrency/retry/cached/local; bảng giá versioned. Không claim token estimate chars/4 là chính xác.

## 9. Contracts dùng chung cho agent

Các DTO Python nằm ở src/tg_assistant/contracts.py; frontend mirror là dashboard-prototype/src/contracts/generated.js với JSDoc và schema validation, phù hợp frontend JavaScript hiện hữu. Không sinh secret field trong DTO. ID Telegram/chat/owner biểu diễn string decimal trong JSON để tránh mất precision JavaScript; Python dùng int nội bộ.

- ConnectionState = checking | ready | degraded | disconnected | unknown.
- ConnectionStatus(service, state, checked_at, code, message, next_action, capabilities): services telegram_account, control_bot, chat_ai, embeddings, storage, runtime.
- PublicProfile(profile_id, owner_id|null, storage_backend, setup_stage, version): một active profile per Windows SID trong v1; không multi-account switch.
- OnboardingStage = welcome | storage_ready | ai_configured | telegram_verified | bot_verified | owner_paired | source_selected | first_answer | ready.
- EmbeddingProfile(provider, endpoint_id|null, model, embedding_version, dimension, store_id, cloud_consent). endpoint_id là định danh endpoint sanitized, không URL có credential.
- OperationResult(operation_id, state, progress|null, code, message, next_action); state queued/running/paused/completed/completed_with_warning/failed/cancelled/uncertain.
- NativeCommand(name, request_id, profile_id, payload_nonsecret): open_connection_dialog, open_telegram_login, open_bot_dialog, issue_dashboard_ticket, runtime_start, runtime_stop; secret field never serialized through dashboard request.
- OnboardingCoordinator.status() -> OnboardingStatus; .resume() -> OnboardingStatus; .complete_stage(stage, verified_evidence_id) -> OnboardingStatus.
- CredentialConnectionService.validate_and_save(provider, secret_input, options) -> ConnectionStatus; secret_input chỉ process memory native service, không DTO.
- DashboardTicketService.issue(profile_id, windows_sid, now) -> LaunchTicket; .redeem(ticket, origin, now) -> AdminSession; storage atomic/hash-only.
- StorageService.open(profile) -> Database; .migrate() -> MigrationReport; .backup(destination) -> BackupManifest; .restore(source, maintenance_lease) -> RestoreReport.
- JobRepository.claim(job_type, worker_id, now, lease_seconds) -> JobLease|null; .complete(lease, result) -> bool; claims compare-and-set không dựa riêng skip_locked.
- RevocationService.revoke_source(chat_id, actor_id, memory_action) -> RevocationReport; epochs version jobs/context và kiểm tra đúng permission.
- VectorRecoveryService.preview(chat_id, store_id) -> RecoveryPlan; .enqueue(plan_id, owner_id) -> operation_id; plan stale phải preview lại.
- OnboardingStatus(profile, connections, stage_evidence_ids, next_action, disabled_capabilities); evidence chỉ backend/native service cấp sau kiểm tra, không tin browser tự báo stage complete.
- JobLease(id, claim_token, payload, expires_at, authorization_epoch); MaintenanceLease(profile_id, generation, holder, expires_at, state=writers_fenced). Lease/token nội bộ không lộ qua status endpoint. Dừng process chưa đủ proof writers_fenced; cần chặn API/scheduler/worker và writer legacy.
- AdminSession(session_id, profile_id, owner_id|null, authority, expires_at). authority=setup_only cho profile chưa paired, management chỉ sau verified owner+pairing; không tạo owner_id=0 giả. LaunchTicket(raw_ticket, expires_at) là nội bộ IPC, không public status DTO.
- RecoveryPlan(plan_id, chat_id, store_id, authorization_epoch, index_generation, expected_count, expires_at); preview không đổi active index, confirm phải kiểm tra lại owner/epoch/generation và thực thi job. Switch index chỉ sau verified build.
- RevocationReport(chat_id, authorization_epoch, cancelled_jobs, memory_action, operation_id|null); MigrationReport(previous_revision, current_revision, changed, code, next_action); BackupManifest(format_version, schema_revision, backend, profile_id, checksums, vector_state). Không backup Credential Manager, OTP/token hoặc raw Telegram session trong portable backup mặc định.
- GET /api/v1/setup/status, GET /api/v1/connections, POST /api/v1/auth/launch/redeem, GET /api/v1/operations/{id}. Setup public view sanitize và read-only; write cần SID-issued session/CSRF. Browser chỉ request mở native dialog qua command bridge không nhập secret.

Command bridge chỉ nhận allowlist NativeCommand, schema/size/profile validation và authenticated session+CSRF+Origin; không arbitrary executable/path/URL. Nhập secret chỉ trong native dialog của đúng SID. Ở beta, không chia sẻ physical vector reference xuyên nguồn/store: point identity scoped source+message+store, chỉ dedup trong scope với eligibility giống coverage; tránh lỗi xóa một nguồn làm mất tri thức nguồn khác.

Đây là interface mới dự kiến, không mô tả endpoint đã tồn tại. Parent review chốt schema trước khi frontend/backend triển khai; agent không tự đổi tên khác nhau.

## 10. Release gate và giới hạn

Beta phát hành sau G0-G6 trong roadmap. Không tự publish repo, rewrite Git history, dùng credential thật, mua certificate hoặc ký artifact bằng credential không được cấp. Release candidate local/PR review có thể thực hiện; tác vụ phát hành thật và QA tài khoản thật cần authorization cụ thể đúng phạm vi.

Qt/PySide6 và fonts cần distribution/license gate; đọc tài liệu chính thức và chọn package lock đã verify trên OS mục tiêu. [Qt for Python](https://doc.qt.io/qtforpython-6/) có các chế độ license khác nhau; không giả định có thể bundle tùy ý.

Ngoài phạm vi: SaaS, remote dashboard/LAN, Android/macOS/Linux, billing, marketplace provider, auto-create Telegram API credentials/bot, auto-conversion MySQL -> SQLite, fine-tuning, feature mới không phục vụ hành trình trên.

