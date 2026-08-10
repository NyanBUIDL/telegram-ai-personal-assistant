# Telegram AI — Design Audit HTML Prototype

Prototype HTML/CSS/JS độc lập chuyển năm nhóm đề xuất P1 trong
`UX_UI_DESIGN_AUDIT_REPORT.md` thành giao diện “Before → After” có thể thao tác.

## Chạy local

Từ thư mục gốc dự án:

```powershell
python -m http.server 4173 --bind 127.0.0.1 --directory design-audit-html
```

Mở:

```text
http://127.0.0.1:4173/
```

Prototype không gọi Admin API, không đọc `.env`, không ghi database và không thay đổi
`dashboard-prototype`.

## Nội dung

- `index.html`: sáu màn prototype và semantic structure.
- `styles.css`: design tokens, responsive rules, font face và modal behavior.
- `app.js`: state controls, mock state machine, table/card, selection, progress và focus trap.
- `assets/fonts/`: Peter Obscure và Darley Sans được sao chép từ frontend hiện tại.
- `assets/before/`: năm ảnh bằng chứng gốc lấy từ `output/playwright/audit`.
- `evidence/`: ảnh After và contact sheet Before/After dùng cho QA.
- `DESIGN_IMPLEMENTATION_SPEC.md`: đặc tả handoff frontend/backend.
- `design-qa.md`: kết quả visual QA và browser QA cuối.

## Cách dùng prototype

1. Chọn màn hình trên thanh điều hướng: Tổng hợp, Local Models, Knowledge, Learning
   Jobs, Kết nối hoặc Modal.
2. Dùng “Phòng test trạng thái” để chuyển Desktop/Mobile, loading/error/empty/content
   và online/offline.
3. Ở màn Tổng hợp, chọn một ưu tiên rồi chuyển Before/After.
4. Ở Local Models, mở chi tiết, xem downloading, hủy tải và xem preview activation.
5. Ở Knowledge, đổi Bảng/Card, lọc, chọn nguồn và kiểm tra bulk toolbar.
6. Ở Learning Jobs, thử đủ queued, syncing, embedding, completed, failed, paused và
   unknown.
7. Ở Kết nối, thử đủ Connecting, Live, Reconnecting, Offline và Session expired.
8. Ở Modal, mở bản ngắn/dài rồi thử Tab, Shift+Tab và ESC.

## Phạm vi

Đây là prototype frontend-only. Các giá trị hiển thị là fixture minh họa contract,
không phải dữ liệu production. Những phần cần backend hỗ trợ được ghi rõ trong
`DESIGN_IMPLEMENTATION_SPEC.md`.

## Viewport đã QA

- 1440×900
- 1024×768
- 390×844
- 360×800

Tiêu chí browser QA: `document.body.scrollWidth === innerWidth`, console không có
error/warning, modal không vượt viewport, touch target đóng modal lớn hơn 44 px, Tab
được giữ trong dialog, ESC đóng và focus quay về nút mở.
