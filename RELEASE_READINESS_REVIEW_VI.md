# Đánh giá mức độ hoàn thiện và khả năng phát hành

Ngày đánh giá: 02/10/2026, giờ Việt Nam.
Repository: NyanBUIDL/telegram-ai-personal-assistant.
Nhánh: master. Commit được kiểm tra: 7429fcffd61860e9502f066e0b6a921df72fe176.
Kết luận: MVP có nhiều chức năng thực tế, phù hợp tiếp tục sử dụng/thử nghiệm cá nhân; chưa đủ điều kiện phát hành stable cho người dùng mới. Nên hướng tới beta ứng dụng chạy local cho từng owner sau khi sửa các lỗi chặn bên dưới.

## Phạm vi và giới hạn

Đọc 43 file Python trong src, 27 file test, các migration, cấu hình đóng gói, tài liệu và các phần chính của dashboard React. Mã nguồn được đọc qua kết nối GitHub có quyền truy cập repository private, rồi tạo bản sao kiểm tra trong workspace.

Chạy bộ test hiện có trên Python 3.12.14 với dependency được giải quyết từ pyproject.toml, build/cài wheel vào thư mục dependency riêng, kiểm tra lint và bytecode compilation. Các kiểm tra bổ sung dùng SQLite trong bộ nhớ và mock; không đăng nhập Telegram, không gửi/xóa tin thật, không đọc credential ứng dụng, không thay đổi repository trên GitHub.

Chưa chạy cài mới với MySQL thực trên Windows sạch, chưa chạy UI browser, chưa gọi OpenAI/OpenRouter/Ollama thực, chưa kiểm thử load hoặc vận hành dài ngày. Đánh giá này không phải xác nhận ứng dụng production đang hoạt động.

## Kết quả kiểm chứng

| Kiểm tra | Kết quả |
| --- | --- |
| Bộ test hiện có | 150 passed, 34.93 giây |
| Line coverage toàn bộ package | 42% |
| Coverage runtime.py | 20% |
| Coverage control_bot.py | 13% |
| Coverage CLI / setup MySQL / wizard | 0% trong lần chạy này |
| Ruff src + tests | Pass |
| Ruff src + tests + alembic | 1 lỗi I001 ở migration 0005 |
| Compileall src + tests + alembic | Pass |
| Migration trên database SQLite trống | 0001 pass; 0002 fail vì knowledge_sources đã tồn tại |
| Tài nguyên trong bản wheel đã cài | Không có alembic.ini, migration scripts, dashboard |
| Thu hồi quyền khi learning job còn queued | Worker bật lại allowed=True và gọi sync_history |

Lần chạy test đầu có 5 lỗi do bản sao kiểm tra chưa có USER_GUIDE.md và đường dẫn .pth của pywin32 khi dùng pip --target. Sau khi hoàn thiện môi trường kiểm tra và tài liệu, toàn bộ 150 test pass. Không coi 5 lỗi môi trường đó là lỗi của repository.

Sandbox Windows chặn socket nội bộ của asyncio; kiểm tra async được chạy ngoài sandbox sau khi người dùng phê duyệt. Các tiến trình test bị treo trước đó đã được dừng.

## Điểm đã làm tốt

- Kiến trúc owner-only/default deny có PolicyEngine độc lập với AI.
- Quyền đọc, sync, tìm kiếm, gửi/xóa và các thao tác khác được tách rõ.
- Pending action, TTL, xác nhận owner, kiểm tra quyền Telegram và audit đã có code thực tế.
- Credential Manager, redaction, Responses API store=False, dashboard loopback, HttpOnly cookie và CSRF là các nền tảng tốt cho ứng dụng cá nhân chạy local.
- Có sync/checkpoint, MySQL làm nguồn dữ liệu chuẩn, Qdrant local, keyword + semantic retrieval, nguồn trích dẫn, giới hạn ngân sách/token.
- Có nhiều tiện ích vận hành: learning queue, pause/resume, provider selection, Ollama, quota/retention, metrics, backup/restore và doctor.
- Dashboard kết nối Admin API thực; tên thư mục dashboard-prototype không đồng nghĩa toàn bộ UI chỉ là mock.
- Luồng group /ask hiện tại giới hạn corpus vào group đang gọi. README và PRIVACY vẫn mô tả một số hành vi corpus hợp nhất cũ, nên cần cập nhật tài liệu.

## Các điểm chặn phát hành

### 1. P1 — Learning job có thể vô hiệu hóa quyết định BLOCK mới hơn

Code: [runtime.py:1944](https://github.com/NyanBUIDL/telegram-ai-personal-assistant/blob/7429fcffd61860e9502f066e0b6a921df72fe176/src/tg_assistant/runtime.py#L1944), [xử lý block:2357](https://github.com/NyanBUIDL/telegram-ai-personal-assistant/blob/7429fcffd61860e9502f066e0b6a921df72fe176/src/tg_assistant/runtime.py#L2357).

Tình huống tái hiện:
1. Owner đã cho học một nguồn, job ở trạng thái queued.
2. Owner BLOCK nguồn đó.
3. Worker xử lý learning job còn lại.
4. Worker gọi set_allowed(..., True) và apply_template(..., "knowledge") trước sync.

Kết quả probe:
`allowed_after_job=True; sync_calls=[100]; job_status=completed`.

Xử lý BLOCK hiện hủy pending action của chat nhưng không hủy BackgroundJob learn_group. Vì vậy standing authorization cũ có thể ghi đè việc thu hồi quyền mới hơn.

Cần sửa:
- Thu hồi/cancel learning jobs khi block, và xử lý job đang running bằng cooperative cancellation.
- Worker phải kiểm tra authorization còn hiệu lực; không tự bật lại ALLOW cho nguồn đã revoked.
- Phân biệt cấp quyền ban đầu với refresh kiến thức.
- Có regression test: queue -> BLOCK -> worker; BLOCK giữa sync/embedding; retry job sau BLOCK; restart khi job bị thu hồi.

### 2. P1 — Migration cài mới tạo bảng trùng

Code: [migration 0001](https://github.com/NyanBUIDL/telegram-ai-personal-assistant/blob/7429fcffd61860e9502f066e0b6a921df72fe176/alembic/versions/0001_initial_schema.py#L18), [migration 0002](https://github.com/NyanBUIDL/telegram-ai-personal-assistant/blob/7429fcffd61860e9502f066e0b6a921df72fe176/alembic/versions/0002_knowledge_source_inventory.py#L18).

0001 import models hiện tại và gọi Base.metadata.create_all. Metadata hiện tại đã chứa knowledge_sources và các bảng bổ sung. Sau đó 0002 lại op.create_table("knowledge_sources") vô điều kiện.

Trên SQLite trống:
`0001 PASS; 0002 OperationalError: table knowledge_sources already exists`.

Đây là xung đột schema có thể thấy trực tiếp từ code; chưa chạy Alembic với MySQL thật. Các unit test tạo Base.metadata trực tiếp nên không đi qua chuỗi migration và không phát hiện vấn đề này.

Cần làm cho initial migration độc lập với models thay đổi theo thời gian; kiểm chứng cả upgrade từ trống và upgrade database phiên bản cũ bằng MySQL thực trong CI.

### 3. P1 nếu phát hành package — Wheel không chứa tài nguyên cần thiết

Code: [pyproject.toml](https://github.com/NyanBUIDL/telegram-ai-personal-assistant/blob/7429fcffd61860e9502f066e0b6a921df72fe176/pyproject.toml#L46), [paths.py](https://github.com/NyanBUIDL/telegram-ai-personal-assistant/blob/7429fcffd61860e9502f066e0b6a921df72fe176/src/tg_assistant/paths.py#L10), [config.py](https://github.com/NyanBUIDL/telegram-ai-personal-assistant/blob/7429fcffd61860e9502f066e0b6a921df72fe176/src/tg_assistant/config.py#L155).

Wheel chỉ lấy src/tg_assistant. Migration, alembic.ini, tài liệu và dashboard nằm bên ngoài package; các path lại dựa vào vị trí source checkout.

Build/cài wheel thành công không bảo đảm setup/dashboard hoạt động. Trong bản đã cài: migration_config=False; migration_scripts=False; dashboard=False.

Chọn rõ artifact phát hành:
- Nếu beta từ source: clean release archive với source, migration và dashboard đã build; kiểm thử clean installation.
- Nếu wheel/installer: đóng gói tài nguyên bên trong package và dùng resource paths phù hợp.
- Không phụ thuộc node_modules, .venv hoặc dist đã commit trên máy phát triển.

### 4. P1/P2 — Độ tin cậy tri thức và recovery chưa đủ bằng chứng

UAT V3 tháng 7 ghi nhận số vector 257 -> 41 trong khi báo hoàn thành. Báo cáo thiết kế kế tiếp cũng nói chưa chứng minh đây là xóa vật lý; có khả năng khác biệt active/legacy store. Không nên gọi đây là lỗi mất 216 vector đã tái hiện trên commit hiện tại.

Code hiện tại đã cải thiện:
- active semantic path/provider-independent store identity;
- vector store registry và coverage check;
- bộ đếm evaluated/indexed/reused/filtered/duplicate/skipped;
- completed_with_warning khi count giảm.

Nhưng recovery-preview vẫn chỉ tạo pending action. [runtime.py:2291](https://github.com/NyanBUIDL/telegram-ai-personal-assistant/blob/7429fcffd61860e9502f066e0b6a921df72fe176/src/tg_assistant/runtime.py#L2291) cố ý chuyển confirm thành approved_not_executed; không chạy recovery. CLI reindex có thể tạo lại vector nhưng không phải workflow phục hồi từng nguồn có kiểm chứng đầy đủ.

Các vấn đề cần regression:
- _index_knowledge_rows tin vào vector_status trong MySQL để reuse mà không kiểm tra point thực còn trong Qdrant; database restore sang kho vector trống là một trường hợp cần kiểm tra.
- Dedup dựa trên hash của các nguồn khác; cần bảo đảm nguồn A bị block/retention cleanup không làm nguồn B mất khả năng semantic retrieval cho nội dung trùng.
- coverage checker tính eligible theo từng message nhưng indexer loại duplicate; hai bên cần dùng cùng định nghĩa để tránh cảnh báo thiếu vector giả.
- edited message phải được invalidation/reembedding đúng; checkpoint theo ID mới không tự bao phủ mọi chỉnh sửa của message cũ.
- Cần test toàn luồng sync -> commit -> embed -> upsert -> restart/retry -> retrieve, thay vì chỉ các helper và upsert đơn lẻ.

### 5. P2 — Repository và quy trình release chưa được làm sạch

Git tree đang chứa .venv, dashboard-prototype/node_modules, __pycache__, cache, build/dist, ảnh dashboard và dấu vết kiểm thử dù .gitignore đã liệt kê nhiều mục đó.

.env hiện tại đã kiểm tra chỉ chứa các key cấu hình và marker nguồn credential; không phát hiện key/token thật trong file này. Không suy rộng thành lịch sử Git hoặc ảnh/log đều sạch.

Trước public source:
- Loại generated/dependency files khỏi tracked tree; .gitignore không gỡ file đã được theo dõi.
- Rà lịch sử commit, log, screenshot, CSV/export và dữ liệu nhận diện Telegram. Nếu tìm thấy secret thật thì rotate.
- Chọn license phù hợp; pyproject hiện ghi "Private use", chưa có LICENSE ở root.
- Kiểm tra quyền phân phối font/asset.
- Thêm CI backend test/lint, frontend build/lint, migration mới/nâng cấp, package asset smoke test.
- Khóa dependency của release đã kiểm chứng; hiện Python dùng các khoảng version rộng.
- GitHub Actions truy vấn được chỉ có dependency graph update; không có bằng chứng CI test/build cho HEAD. Không có release trong collection đã kiểm tra.

## Những cải thiện sau khi xử lý blocker

1. Giảm ma sát cài đặt: Python, MySQL, API ID/hash, bot token, pairing và Ollama là nhiều bước đối với người mới. Tối thiểu có installer/bootstrap đáng tin cậy, kiểm tra prerequisite và thông báo lỗi dễ sửa.
2. Tách runtime.py, control_bot.py và admin_api/app.py thành các module theo tác vụ; hiện nhiều logic worker, policy, Telegram I/O và UI nằm trong file rất lớn.
3. Cập nhật README/PRIVACY/ARCHITECTURE/security report theo hành vi hiện tại. Security report cũ nói không có web server/frontend và chỉ 28 test, nên không đại diện code hiện tại.
4. Backup/restore: manifest còn hardcode schema=0001 trong khi Alembic đến 0005; backup vector chỉ ghi metadata. Cần ghi revision thực, có checksum, quy trình restore vào DB trống, yêu cầu dừng worker và kiểm chứng reindex nếu không sao lưu vector.
5. Dùng tokenizer phù hợp khi cần hạn mức token chắc chắn; len(text)//4 là ước lượng. Kiểm tra concurrency/reservation để nhiều request không cùng vượt ngân sách.
6. Bảng MODEL_PRICES hardcode cần cập nhật/version hóa. Ví dụ Terra trong code là 2.5/15 USD mỗi triệu token, còn tài liệu OpenAI hiện là 2/12; đây là ước tính cao hơn 25%, có thể dừng sớm hơn dự kiến. [Nguồn OpenAI](https://developers.openai.com/api/docs/models/gpt-5.6-terra).
7. Kiểm thử giao diện 403/401, provider lỗi, model không tồn tại, hủy/pause job và offline; không coi báo cáo UAT cũ là kết quả live của hôm nay.

## Khả năng publish theo ba hướng

| Hướng | Nhận định |
| --- | --- |
| Chia sẻ source cho developer tự cài | Có thể phát hành alpha sau cleanup/license, nhưng nên sửa migration và BLOCK trước khi mời người khác chạy |
| Ứng dụng Windows cho từng người dùng | Hướng phù hợp nhất; cần sửa blocker, đóng gói, clean install/upgrade và beta có kiểm soát |
| Dịch vụ hosted cho nhiều người dùng | Chưa sẵn sàng; kiến trúc hiện phục vụ một owner chạy local, Credential Manager Windows, MySQL/Qdrant local và loopback dashboard |

Hosted SaaS cần thiết kế riêng về tenant isolation, credential/session custody, quota/billing và vòng đời mỗi tài khoản. Đưa dashboard lên hosting không tự biến backend này thành dịch vụ đa người dùng.

## Điều kiện để phát hành beta

- Queue/running/retry/restart không vượt quyết định BLOCK của owner.
- Cài từ artifact trên Windows sạch và MySQL trống thành công; upgrade database cũ thành công.
- Artifact có đủ dashboard, migrations và docs; start không phụ thuộc dependency đã commit.
- Test RAG incremental/recovery, chỉnh sửa/xóa và scope quyền đều đạt.
- Backup/restore được diễn tập và xác minh retrieval sau khôi phục.
- CI bắt buộc xanh trên đúng commit release.
- Kiểm thử với một nhóm nhỏ người mới: họ tự cài, pair, cho học một nguồn, hỏi AI, block, stop/restart, backup.
- Theo dõi ổn định nhiều ngày với mức sử dụng thực; công bố rõ giới hạn beta.

Ưu tiên kế tiếp là độ tin cậy, khả năng cài đặt và thu hồi quyền. Chưa cần thêm feature để dự án tiến gần phát hành hơn.

