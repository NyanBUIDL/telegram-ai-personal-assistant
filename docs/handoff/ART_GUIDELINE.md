# Art guideline — giữ nhận diện hiện tại

Nguồn: `dashboard-prototype/AGENTS.md`, styles/views đã đọc ở audit commit 7429fcf; user yêu cầu có thể thiết kế lại toàn bộ nhưng art phải giống hiện tại. Snapshot audit chưa có toàn bộ asset/reference, nên B00 phải lấy bản gốc từ checkout thật, lưu hash và screenshot trước sửa. Tài liệu này định hướng, chưa phải evidence UI đã khớp.

| Token / vai trò | Giá trị đã khóa |
|---|---|
| Paper / background | #f3efdf |
| Panel | #fffdf5 |
| Ink / outline | #090909 |
| Teal accent | #00c8c8 |
| Magenta accent | #ef00c8 |
| Yellow accent | #ffd51f |
| Browser border | 3px, vuông, đen |
| Browser shadow | Hard shadow 7px, không blur |
| Font logo và main title | PeterObscure |
| Font UI/body/labels còn lại | DarleySans |

Giữ neo-brutalist retro: nền giấy, chữ đen, thẻ cạnh vuông, viền đậm, bóng cứng, accent có chủ đích. Tránh đổi thành generic SaaS cards tròn, gradient hoặc glass effect. Không tự thêm ảnh minh họa/logo làm đổi nhận diện. Status dùng text/icon kèm màu, không chỉ màu; body readability và contrast phải pass.

Có thể đổi information architecture, navigation, hierarchy, spacing và flow để người mới dễ dùng. Launcher Qt Widgets dùng palette/fonts/iconography tương ứng, metric logical DPI để không méo ở 100/125/150/200%; không ép hardcoded CSS px vào Qt. Dùng cùng ý nghĩa token trong React và native theme.

## Kiểm tra bắt buộc U01/U02/U04/P01

- [ ] Lấy `neo-brutalist-reference.png`, logo/fonts/icon bản gốc; lưu vị trí/hash ở evidence/task-B00.md.
- [ ] Inventory feature trước/sau; mọi tính năng baseline tìm được trong navigation mới.
- [ ] PeterObscure chỉ logo/main title; DarleySans controls/body; kiểm license redistribution trước bundle. Nếu license chưa rõ, báo blocker hoặc đề xuất font gần nhất để review, không tự đổi art.
- [ ] Screenshot thật browser 360/390/1280/1440 và Windows DPI 100/125/150/200%; verify không clipped text/dialog.
- [ ] Keyboard tab/focus visible, accessible labels, target >=44 CSS px; chart/state có text.
- [ ] Có screenshot empty/loading/ready/degraded/error/confirmation, dùng dữ liệu thật hoặc fixture được ghi nhãn QA, không trình bày mock metrics như trạng thái hệ thống.
- [ ] Người review so ảnh baseline với ảnh artifact; ghi điểm đổi layout và điểm giữ art. Build pass không thay thế visual review.

Đặc tả chi tiết hành trình và màn hình: [Experience package](experience-work-package.md).
