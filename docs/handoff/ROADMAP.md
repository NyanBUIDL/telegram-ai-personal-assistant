# Roadmap — từ MVP đến Windows public beta

Cập nhật 09/10/2026. **18/26 task đã Verified; O01/O02/O03/U02/V03 đã được nghiệm thu code trên SQLite-only.** ID/dependency/status canonical nằm trong [status.json](status.json), [CSV](roadmap.csv), [checklist](MASTER_CHECKLIST.md) và [plan chi tiết](../superpowers/plans/2026-10-02-windows-public-beta-plan.md). [Điều chỉnh SQLite-only](../superpowers/specs/2026-10-06-sqlite-only-amendment.md) đã được owner duyệt. Bằng chứng code/review/kiểm thử nằm trong [hồ sơ tích hợp](evidence/review-five-completion.md). O04 đang InReview: CI candidate 4e42 lỗi một launcher fixture trên Python 3.13; [bản sửa fixture đang kiểm thử](evidence/task-O04.md). U03/U04/P01/P02/Q02/Q03/R02 còn Planned; G1–G6, installer, tài khoản thật, licensing, UAT và soak chưa được nghiệm thu. Beta-ready/published vẫn false.

| Mốc | Kết quả bàn giao | Task bắt buộc tại mốc | Chủ trì / song song |
|---|---|---|---|
| M0 | Checkout thật, baseline, contracts và art reference | B00 | Coordinator |
| M1 | Migration/SQLite/CAS/BLOCK an toàn, art tokens, CI và cleanup | F01, F02, S01, S02, U01, Q01, R01 | Platform + Reliability + Experience; QA review luân phiên |
| M2 | AI/embedding profile, index/recovery, backup, launcher/auth | S03, V01, V02, V03, D01, D02 | Reliability và Platform |
| M3 | Native wizard: API → Telegram → bot → dashboard; resume | O01, O02, O03, O04, U02 | Platform + Experience |
| M4 | Chọn nguồn, học/hỏi lần đầu, đầy đủ quản trị và lỗi | U03, U04 | Experience, backend theo contract |
| M5 | Artifact và installer Windows reproducible, upgrade có recovery | P01, P02 | Platform + QA |
| M6 | QA artifact, usability/soak, review cuối và beta-ready report | Q02, Q03, R02 | QA độc lập + Coordinator |

Task ở mốc sau có thể bắt đầu sớm khi dependencies Verified và file ownership không trùng; tuyệt đối không dùng số thứ tự milestone thay dependency. Ví dụ U01 có thể làm song song sửa migration, nhưng U03 cần O04/V03/U02; installer P02 cần S03 đã verified.

## Các đợt giao agent

1. **Khởi tạo:** Coordinator làm B00 và kiểm lại GitHub HEAD so với audit commit; nếu code mới thì refresh gap analysis trước chia việc. Chốt contracts, screenshot/art assets và full feature inventory.
2. **Nền tảng:** Platform làm F02 → S01; Reliability F01 → S02; Experience U01. Q01/R01 chạy khi có slot và owner của shared file rảnh. Coordinator serialize thay đổi models/config/schema.
3. **Core:** Reliability V01 → V02 → V03 và S03; Platform D01 → D02. Review race, backup, local-only và auth độc lập trước nối wizard.
4. **Connections:** Platform O01 → O02/O03 → O04; Experience U02 theo backend schema đã khóa. O02/O03 chỉ song song nếu tách file, coordinator vẫn giữ integration. Không tự đăng ký Telegram API/bot cho người dùng.
5. **Management:** Experience U03 → U04. Reliability/Platform review endpoint/quyền/actions; giữ 19 quyền và chức năng baseline. Mỗi destructive action có preview/confirm và trạng thái thật.
6. **Release candidate:** Platform P01 → P02; QA Q02 → Q03; Coordinator R02 sau mọi gate. Đây là installer local/PR có thể review, chưa đồng nghĩa public release hoặc repo public.

## Cách đối chiếu tiến độ

- Planned: chưa làm. Ready: dependencies Verified và đã được giao. InProgress: đang sửa. InReview: có code/evidence cần review. Verified: acceptance và reviewer pass. Blocked: có điều kiện cụ thể chưa giải quyết.
- Cuối task cập nhật cùng ID ở checklist/JSON/CSV, ghi commit và evidence/task-ID.md. Không tính tỷ lệ dòng code hay số commit thành mức hoàn thiện.
- Báo tiến độ bằng task Verified/26, gate Verified/7 và các blocker; kèm commit/artifact của evidence. Các con số này đo roadmap, không phải phần trăm production readiness.
- Nếu test cần máy Windows sạch, người thử thật hoặc account UAT chưa có thì giữ Pending và ghi đầu vào còn thiếu. Không giả lập thành pass.
- Chỉ báo **beta-ready** khi G0–G6 Verified; báo riêng **published** sau hành động phát hành đã được giao và kiểm chứng. Không cam kết agent chạy ngầm sau khi phiên dừng.

## Thứ tự ưu tiên nếu cần thu hẹp

Không hạ migration/BLOCK/auth/atomic claims/backup/LOCAL ONLY để ra bản sớm. Có thể hoãn polish phụ, analytics hoặc tính năng mới ngoài baseline với quyết định review ghi rõ. Model chat/off và keyword-only có nhãn khả năng thật; không được tính là hoàn thành first AI answer của luồng nghiệm thu cloud/local AI.
