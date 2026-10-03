# Bắt đầu handoff — Telegram AI Personal Assistant Windows beta

**Thiết kế đã được duyệt; implementation đang thực hiện trên branch `codex/windows-public-beta`; chưa sẵn sàng publish.** Ngày 02/10/2026. Người dùng đã chọn: mỗi người tự cài Windows app, dùng API/account Telegram/bot của mình, dễ mở và quản lý dashboard; giữ art hiện tại.

## Đọc theo thứ tự

1. [Roadmap](ROADMAP.md) — 7 milestone và đợt chia agent.
2. [Master checklist](MASTER_CHECKLIST.md) — 26 task để đối chiếu.
3. [Thiết kế đã đề xuất](../superpowers/specs/2026-10-02-windows-public-beta-design.md) — lựa chọn và contracts.
4. [Implementation plan](../superpowers/plans/2026-10-02-windows-public-beta-plan.md) — file, tests, acceptance, dependencies của từng task.
5. [Art guideline](ART_GUIDELINE.md), [UAT protocol](UAT_PROTOCOL.md), [Evidence template](evidence/README.md).

[status.json](status.json) và [roadmap.csv](roadmap.csv) là tracker máy đọc được. [Báo cáo audit](../../RELEASE_READINESS_REVIEW_VI.md) giải thích các blocker gốc. 150 tests audit pass chỉ là baseline; không chứng minh clean install/public readiness.

Cập nhật 03/10/2026: **8/26 task đã Verified:** B00, F01, F02, U01, Q01, R01, S01 và S02. S02 đã qua review và kiểm thử toàn bộ515pass; V01 đang nối AI/embedding vào runtime, D01 đang làm launcher Windows. Các nhãn trạng thái không có bằng chứng và phụ thuộc thừa đang được gỡ theo yêu cầu người dùng. [PR draft #1](https://github.com/NyanBUIDL/telegram-ai-personal-assistant/pull/1) chứa các thay đổi đã commit. Xem [review S01](evidence/review-S01.md), [review R01](evidence/review-R01.md) và [CI thực tế đã xanh ở commit ghi trong evidence Q01](evidence/review-Q01.md). CI Q01 không được coi là kiểm chứng cho những commit code mới hơn. G0 Verified; các gate phát hành khác còn Pending, chưa có installer đã nghiệm thu.

## Phân công và phụ lục

| Vai trò | Task canonical | Phụ lục chuyên môn |
|---|---|---|
| Coordinator | B00, R02; contracts, integration và review | Plan tổng |
| Platform | F02, S01, D01/D02, O01–O04, P01/P02, R01 | [Platform](platform-work-package.md) |
| Reliability | F01, S02/S03, V01–V03 | [Reliability](reliability-work-package.md) |
| Experience | U01–U04 | [Experience](experience-work-package.md) |
| QA / reviewer độc lập | Q01–Q03 và review từng task | UAT + evidence |

Các nhãn PL/RL/UX trong phụ lục là nhóm yêu cầu, không thứ tự chạy hoặc tracker khác. **Dependency/ownership/interfaces trong plan tổng và spec có ưu tiên.** Nếu phụ lục đề xuất helper khác tên canonical, adapter nội bộ gọi service canonical; không tạo HTTP DTO khác. Mỗi vòng tối đa 3 worker + coordinator; reviewer dùng slot được giải phóng, không cho agent tự sign-off code mình.

Mapping tham khảo: PL01→B00/S01/R01; PL02→S01/S02; PL03→F02; PL04→D02; PL05→O01–O04/V01; PL06→D01; PL07→P01/P02; PL08→S03. RL01→F01; RL02→F02; RL03→S02; RL04/05→V02; RL06→V03; RL07→S03; RL08→V01; RL09→P01/P02; RL10→Q02; RL11→Q01/Q02/Q03; RL12→R01. UX là phụ lục flow/art/nav/first-value/management của U01–U04 và O01–O04, không mở rộng scope ngoài spec.

## Điều kiện trước sửa code

- B00 đã tạo checkout Git thật tại `E:/ChatGPT Project/Telegram/repository`, đọc AGENTS.md và khóa baseline 7429fcffd61860e9502f066e0b6a921df72fe176. Contracts đã qua review độc lập và 216 tests pass; xem [evidence B00](evidence/task-B00.md). Tiếp tục từ tracker và execution ledger, không chạy lại task đã Verified hoặc sửa audit-snapshot như sản phẩm.
- Giữ công việc/dữ liệu/credential người dùng hiện hữu; không reset/force-push/master merge. SQLite cho cài mới; MySQL cũ không tự chuyển. Native PySide6 không QtWebEngine, React trong browser loopback.
- Handoff không chuyển credential. Unit/integration dùng fixture; account Telegram/API thật chỉ trong scope UAT đã cấp, secret do người dùng nhập native.
- Người dùng đã duyệt thiết kế/plan và agent-assisted execution ngày 02/10/2026. Giải quyết routine implementation choices trong phạm vi đã duyệt; không dừng hỏi từng task.

## Prompt chuyển cho agent tiếp quản

> Tôi duyệt thiết kế và kế hoạch Windows public beta trong docs/handoff/START_HERE.md và các spec/plan được liên kết. Triển khai 26 task theo dependencies, dùng Coordinator + Platform + Reliability + Experience, reviewer/QA độc lập luân phiên. Bắt đầu B00 bằng checkout thật và cập nhật baseline nếu HEAD đổi; không sửa audit-snapshot như sản phẩm. Mỗi người tự cài Windows app: native wizard kết nối API/Telegram/bot, dashboard một nút, SQLite mặc định và giữ MySQL cũ. Giữ đúng art guideline. Cập nhật checklist/status.json/roadmap.csv và evidence mỗi task; tiếp tục tự chủ trong phạm vi này. Không gửi/xóa Telegram thật, dùng key thật, mua certificate, publicize repo hoặc public release nếu chưa có phạm vi cụ thể. Báo beta-ready chỉ sau G0–G6 verified, nêu installer/hash, commit, tests/UAT/soak và giới hạn; nếu thiếu đầu vào thì báo rõ blocker. Không gọi ứng dụng xong khi chỉ xong tài liệu.

Nếu review làm đổi scope hoặc model nền tảng, sửa spec+plan+tracker trước giao task, không cho agent âm thầm chọn lại kiến trúc. Thông báo hoàn thành thuộc nghiệm thu trong phiên tiếp quản; bộ tài liệu này không tạo automation hoặc hứa tác vụ sẽ chạy ngầm sau khi kết thúc phiên.
