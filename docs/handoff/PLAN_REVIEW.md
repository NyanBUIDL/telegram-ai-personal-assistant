# Self-review bộ handoff — 02/10/2026

Phạm vi kiểm tra: tài liệu, task graph và hợp đồng bàn giao. **Không triển khai hoặc nghiệm thu sản phẩm trong lượt lập kế hoạch.** Ba planning agent đã hoàn thành phụ lục Platform/Experience/Reliability, coordinator đã đối chiếu và sửa điểm không khớp.

## Requirement coverage

| Yêu cầu / blocker | Task có acceptance tương ứng |
|---|---|
| Người ngoài tự cài Windows, không terminal/prerequisite dev | S01, D01, P01, P02, Q02, Q03 |
| Dễ nối API cloud/local, không bắt Ollama cho cloud | V01, O01, O02, Q03 |
| Nối account Telegram và bot đúng owner, QR/OTP/2FA, resume | O01, O03, O04, Q02, Q03 |
| Dashboard một nút, không key trong browser, không replay | D02, U02, Q02 |
| Dễ quản lý nguồn/quyền/tri thức/job/budget/backup/lỗi | U03, U04, S03, V01–V03 |
| Giữ art/font/logo và accessibility thực | B00, U01, U02, U04, P01, Q02 |
| Migration fresh/legacy không duplicate table/mất dữ liệu | F02, S01, Q01, Q02 |
| BLOCK thắng job stale, atomic claim, uncertain side effect | F01, S02, Q02 |
| Vector actual points/edits/dedup/recovery không report giả | V01, V02, V03, Q02 |
| LOCAL ONLY chặn embedding/chat/shared cloud context | V01, V02, Q02 |
| Backup/restore/upgrade giữ dữ liệu và revision đúng | S03, P02, Q02 |
| Checklist/roadmap/agent handoff và thông báo đúng lúc | B00, R02, status.json, MASTER_CHECKLIST.md |

## Consistency fixes

- Chọn frontend generated.js + JSDoc/schema validation, loại ambiguity generated.ts trong spec/platform; JSON Telegram ID string.
- Embedding identity bao gồm sanitized endpoint_id/provider/model/version/dimension; cloud không mặc nhiên dùng Ollama; dedup physical reference không chia sẻ xuyên source/store trước beta.
- Setup session có nullable owner/setup-only authority; quản trị chỉ sau owner verified+paired, không tạo owner=0 giả.
- Canonical StorageService và helper/lease contracts khớp spec; work package là phụ lục, ownership/dependencies plan tổng có ưu tiên.
- Migration compatibility chạy trước 0002 đang fail; revision mới sau HEAD không được coi là đủ sửa migration cài mới.
- Không ép fake failing tests cho checkout/font/cleanup; inspection baseline có evidence phù hợp.
- Milestone là release gate; phụ thuộc task quyết định thứ tự thực, không promise deadline chưa benchmark.

## Structural verification

Command (Python runtime bundled, workspace root):

```powershell
& 'C:/Users/brian/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' docs/handoff/validate_handoff.py
```

Observed exit code: 0.

```text
PASS: 26 tasks; dependency DAG; plan/JSON/CSV/checklist agree; 21 local links
Product: implementation_started=False, beta_ready=False
```

Kết quả này chỉ chứng minh cấu trúc handoff: task IDs unique, dependencies tồn tại/không cycle, owner/dependencies/status đồng bộ, checkbox không đánh hoàn thành code và local links tồn tại. Không xác minh remote links, full source HEAD, UI, installer, credentials hoặc UAT. Các đường file mới là file dự kiến được tạo trong B00/task tương ứng.

## Đầu vào ở implementation

Thiết kế chờ human review. Sau review, B00 lấy checkout thật, đọc AGENTS tại HEAD, đối chiếu commit với audit và khóa schema/art baseline. Q02 cần clean Windows environment; Q03 cần participant/test account scope và thời gian soak thực. License fonts/Qt và signing nếu muốn phát hành signed artifact là gate thực, không bằng chứng đã đạt. Nếu thiếu đầu vào, report Pending/Blocked đúng task, không claim beta-ready.
