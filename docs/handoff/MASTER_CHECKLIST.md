# Master checklist — Windows public beta

Ngày lập: 02/10/2026. **Thiết kế đã duyệt; implementation đang thực hiện; beta chưa sẵn sàng.** Đây là checklist canonical 26 task. Plan tổng có các bước và acceptance cụ thể; work packages là phụ lục chuyên môn, không tạo thêm danh sách task cạnh tranh.

## Tài liệu bàn giao

- [x] Spec theo mô hình Windows mỗi người tự cài.
- [x] Plan triển khai, task ownership, dependencies, acceptance.
- [x] Phân tích Platform / Experience / Reliability.
- [x] Roadmap, art guideline, UAT protocol và evidence template.
- [x] Người dùng duyệt thiết kế/kế hoạch ngày 02/10/2026; implementation bắt đầu.

## Công việc sản phẩm

### M0 — Khóa baseline và contracts

- [x] **B00 — Chuẩn bị checkout thật và khóa contracts/baseline** · Coordinator · phụ thuộc: không · Verified.

### M1 — Sửa nền tảng và giữ art

- [x] **F01 — Thu hồi quyền thắng job đang chờ/đang chạy** · Reliability · phụ thuộc: B00 · Verified.
- [x] **F02 — Migration immutable và đường sửa schema cũ** · Platform · phụ thuộc: B00 · Verified.
- [x] **S01 — Storage profile SQLite mặc định và config per-user** · Platform · phụ thuộc: F02 · Verified.
- [ ] **S02 — Atomic claim/confirm/maintenance tương thích SQLite** · Reliability · phụ thuộc: S01, F01 · InReview.
- [x] **U01 — Khóa design tokens và baseline art** · Experience · phụ thuộc: B00 · Verified.
- [x] **Q01 — CI regressions/contracts/storage/frontend/package** · QA · phụ thuộc: B00 · Verified.
- [x] **R01 — Làm sạch source và quyền phân phối assets/dependencies** · Platform · phụ thuộc: B00 · Verified.

### M2 — Tri thức, bảo mật và desktop

- [ ] **S03 — Backup/restore có kiểm chứng và fence runtime** · Reliability · phụ thuộc: S02, V01 · Planned.
- [ ] **V01 — Profile embedding cloud/local và data boundary theo nguồn** · Reliability · phụ thuộc: S01, F01 · InProgress.
- [ ] **V02 — Incremental index, chỉnh sửa/xóa/dedup và coverage cùng contract** · Reliability · phụ thuộc: S02, V01 · Planned.
- [ ] **V03 — Recovery từng nguồn thực thi được và có rollback** · Reliability · phụ thuộc: V02, S02 · Planned.
- [ ] **D01 — Native launcher/tray và service lifecycle** · Platform · phụ thuộc: S01, S02 · Planned.
- [ ] **D02 — Một nút đăng nhập dashboard qua IPC ticket** · Platform · phụ thuộc: D01 · Planned.

### M3 — Kết nối và onboarding

- [ ] **O01 — Onboarding coordinator lưu/tiếp tục và health thực** · Platform · phụ thuộc: D02, V01 · Planned.
- [ ] **O02 — Kết nối API cloud/local qua native dialogs** · Platform · phụ thuộc: O01 · Planned.
- [ ] **O03 — Đăng nhập tài khoản Telegram bằng QR/OTP/2FA** · Platform · phụ thuộc: O01 · Planned.
- [ ] **O04 — Kết nối bot và ghép đúng owner bằng nút Start** · Platform · phụ thuộc: O03 · Planned.
- [ ] **U02 — Navigation/dashboard setup và connections dễ hiểu** · Experience · phụ thuộc: O01, U01 · Planned.

### M4 — Quản trị hoàn chỉnh

- [ ] **U03 — Nguồn/quyền/học/câu hỏi đầu có hướng dẫn** · Experience · phụ thuộc: O04, V03, U02 · Planned.
- [ ] **U04 — Quản lý, lỗi/recovery, backup và hỗ trợ trong UI** · Experience · phụ thuộc: U03, S03 · Planned.

### M5 — Đóng gói Windows

- [ ] **P01 — Build reproducible và đóng gói đủ resources** · Platform · phụ thuộc: D01, U04, R01 · Planned.
- [ ] **P02 — Installer per-user, nâng cấp/gỡ an toàn** · Platform · phụ thuộc: P01, S03 · Planned.

### M6 — Nghiệm thu và bàn giao beta

- [ ] **Q02 — Kiểm thử tích hợp artifact và security regression** · QA · phụ thuộc: F01, F02, S03, V03, O04, U04, P02, Q01 · Planned.
- [ ] **Q03 — UAT với 5 người mới và soak test** · QA · phụ thuộc: Q02 · Planned.
- [ ] **R02 — Bàn giao release candidate và báo hoàn tất đúng mức** · Coordinator · phụ thuộc: Q03, R01 · Planned.

## Release gates

| Gate | Chỉ đánh Verified khi | Hiện tại |
|---|---|---|
| G0 | Review thiết kế xong; B00 checkout thật, baseline và contracts có evidence | Verified |
| G1 | Migration SQLite/MySQL, BLOCK, atomic claims và art tokens pass; CI chạy baseline | Pending |
| G2 | Embedding profiles, LOCAL ONLY, index/recovery, backup/restore và ticket/lifecycle pass | Pending |
| G3 | Cloud không cần Ollama; QR/OTP/2FA, bot owner pairing, resume và dashboard entry pass | Pending |
| G4 | Đủ quản trị nguồn/quyền/tri thức/jobs/backup; art và accessibility có kiểm chứng | Pending |
| G5 | Installer từ clean build; clean Windows user, upgrade/reinstall/data preservation pass | Pending |
| G6 | QA artifact, 5 người thử mới, 72h soak và final review đạt; không còn P0/P1 chặn release | Pending |

Chỉ tick task khi status=Verified và có evidence đúng commit/artifact; commit code, mock screenshot hoặc test skipped không đủ. Gate kiểm lại các task nền và task phụ thuộc dù được hoàn thành ở milestone trước. Nếu hạ scope/OS support thì phải ghi quyết định review; không bỏ test để đổi Pending thành Pass.

Đồng bộ cùng một lần: file này, status.json và roadmap.csv. Evidence dùng evidence/task-ID.md; status.json lưu trạng thái chi tiết Planned/Ready/InProgress/InReview/Verified/Blocked và link chứng cứ. Khi design được review, B00 thành Ready; chỉ task có dependencies Verified được Ready. Chưa có lịch chạy nền hay automation; agent tiếp quản phải chủ động tiếp tục trong phiên làm việc.

