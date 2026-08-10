# Design QA

## Comparison target

- Source visual truth:
  - `assets/before/10-local-models-desktop-1440x900.png`
  - `assets/before/16-knowledge-laptop-1024x768.png`
  - `assets/before/08-learning-jobs-desktop-1440x900.png`
  - `assets/before/24-overview-offline-no-feedback-desktop-1440x900.png`
  - `assets/before/21-doc-reader-modal-mobile-small-360x800.png`
- Implementation: `http://127.0.0.1:4173/`
- Mục tiêu so sánh: đây là corrective redesign theo audit, không phải pixel clone của
  trạng thái lỗi. Visual truth dùng để giữ design language và chứng minh lỗi Before;
  acceptance criteria trong audit là truth cho bố cục/hành vi After.

## Viewport và normalization

| Khu vực | CSS viewport | Source pixels | Implementation capture | DPR | Comparison |
|---|---:|---:|---:|---:|---|
| Local Models | 1440×900 | 1440×ảnh full-page; crop top 1440×900 | 1440×1416; crop top 1440×900 | 1 | `evidence/comparison-models-1440x900.png` |
| Knowledge | 1024×768 | 1024×768 | 1024×1248; crop top 1024×768 | 1 | `evidence/comparison-knowledge-1024x768.png` |
| Learning Jobs | 1440×900 | 1440×900 | 1440×1356; crop top 1440×900 | 1 | `evidence/comparison-jobs-1440x900.png` |
| Connection | 1440×900 | 1440×900 | 1440×1327; crop top 1440×900 | 1 | `evidence/comparison-connection-1440x900.png` |
| Modal | 360×800 | 360×800 | 360×800 | 1 | `evidence/comparison-modal-360x800.png` |

Density được giữ ở 1 CSS px = 1 output px. Full-page implementation được crop ở
đúng chiều cao viewport trước khi ghép side-by-side.

## Full-view comparison evidence

- Models: Before dính toàn bộ metadata thành chuỗi; After có header, active badge,
  grid key/value và footer tách biệt.
- Knowledge: Before để content vượt khỏi vùng 1024; After giữ canvas đúng 1024, KPI
  4 cột và table/card nằm trong panel.
- Jobs: Before lặp lại `Completed · 0%`; After card completed có phase Hoàn tất,
  progress 100%, processed/total và duration.
- Connection: Before vẫn LIVE/CONNECTED khi network thất bại; After header, hero,
  state badge và metadata đều chuyển Offline, đồng thời hiện stale warning.
- Modal: Before đẩy nút close ra ngoài 360 px; After close nằm trong header,
  body/footer nằm trọn viewport.

## Focused region evidence

- Model metadata:
  `evidence/focused-model-cards-comparison.png`.
  Crop cho thấy trực tiếp text dính ở Before và lưới metadata có thể quét ở After.
- Modal header:
  `evidence/focused-modal-header-comparison.png`.
  Crop cho thấy close control bị đẩy khỏi vùng nhìn thấy ở Before và nằm trong khung
  với target 82×46 px ở After.
- Không cần crop bổ sung cho Jobs/Connection vì status/progress/stale label đã đủ lớn
  và đọc rõ trong full-view comparison.

## Required fidelity surfaces

### Fonts and typography

- Peter Obscure được lấy từ frontend hiện tại và chỉ dùng cho logo/main page title.
- Darley Sans được lấy từ frontend hiện tại và dùng cho navigation, panel title,
  data, table, form, badge và button.
- Không có fallback font xuất hiện trong browser capture.
- Long title wrap có kiểm soát ở 390/360; dữ liệu key/value không chồng lấp.

### Spacing and layout rhythm

- Giữ border 2–3 px, square corner, hard shadow 4–7 px, paper canvas.
- Desktop model cards dùng lưới 2 cột; mobile dùng 1 cột.
- Knowledge KPI dùng 4/2/1 cột; mobile card mở rộng.
- Modal dùng grid header/body/footer, body là vùng cuộn nội bộ duy nhất.
- `document.body.scrollWidth === innerWidth` ở 1440, 1024, 390 và 360.

### Colors and visual tokens

- Palette giữ đúng ink/paper/teal/magenta/yellow.
- Green chỉ dùng cho live/completed; red cho offline/failed/expired; yellow cho
  warning/stale/active control.
- Contrast và focus ring rõ trên paper lẫn black surface.

### Image quality and asset fidelity

- Before images được sao chép nguyên bản từ audit, không resize trong asset nguồn.
- Prototype không cần illustration/logo raster mới.
- Không dùng emoji, CSS illustration, custom SVG hoặc placeholder art thay cho asset
  nguồn.

### Copy and content

- “Học” được mô tả đúng là Telegram sync → MySQL → cleanup → embedding/indexing;
  không ngụ ý auto fine-tune.
- Connection copy phân biệt API, SSE, stale snapshot và expired session.
- Progress copy phân biệt status/phase/progress, không dùng phần trăm giả.

### States and interactions

- Đã chạy: content/loading/error/empty.
- Models: expand/collapse, downloading, cancel, capability-disabled option, activation
  preview.
- Knowledge: search/filter, table/card, selection, sticky bulk toolbar.
- Jobs: queued/syncing/embedding/completed/failed/paused/unknown,
  pause/resume/retry.
- Connection: connecting/live/reconnecting/offline/expired, manual retry.
- Modal: short/long, close/cancel/confirm, backdrop, Tab/Shift+Tab, ESC và focus
  restore.

### Accessibility

- Semantic button, label, table header, progressbar và dialog attributes.
- Focus ring tương phản cao.
- Modal initial focus = `closeModal`.
- Shift+Tab từ control đầu wrap đến `confirmModal`; Tab wrap về `closeModal`.
- ESC đóng modal; focus restore về trigger `data-open-modal="long"`.
- Modal close box đo được 82.20×46 px tại 360×800.
- `prefers-reduced-motion` được hỗ trợ.

## Comparison history

### Pass 1 — blocked

- [P2] Mobile navigation bị cắt trong ảnh 390 px.
  - Fix: đổi primary navigation thành lưới 3 cột × 2 hàng tại ≤480 px.
- [P2] Control lab chiếm quá nhiều chiều cao và network control khó quét.
  - Fix: dùng grid 2 cột, đưa Data và status summary ra full row.
- [P2] Skip-link xuất hiện sai trong full-page capture và che activation/network
  controls.
  - Fix: bỏ lớp skip-link; giữ route focus vào `#main-content` và focus indicator cho
  mọi interactive control.
- [P2] Learning contract table bị cắt cột “Hiển thị” ở 390 px.
  - Fix: table-layout fixed, đặt tỷ lệ cột và cho text wrap ở ≤480 px.

### Pass 2 — passed

- Chụp lại đủ full view và focused region sau fix.
- Không còn P0/P1/P2 có thể hành động.
- Page overflow bằng 0 ở tất cả viewport kiểm thử.
- Console: 0 error, 0 warning.

## Browser QA result

- 1440×900: Summary, Local Models, Learning Jobs, Connection Offline.
- 1024×768: Knowledge reduced-column table và bulk toolbar.
- 390×844: Knowledge mobile card và Learning Jobs.
- 360×800: long modal và Local Models mobile.
- Primary interactions: passed.
- Console errors/warnings: 0/0.

## Follow-up polish

- [P3] Có thể thêm skip-to-content trở lại bằng pattern chỉ kích hoạt khi người dùng
  thật sự bắt đầu keyboard navigation, nếu prototype phát triển thành production
  shell.
- [P3] Có thể thêm trực quan scroll hint cho table khi người dùng ép Table mode trên
  mobile.

final result: passed
