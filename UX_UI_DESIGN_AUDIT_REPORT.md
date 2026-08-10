# UX/UI Design Audit — Telegram AI Personal Assistant

**Ngày audit:** 29/07/2026 (Asia/Saigon)  
**Phiên bản quan sát:** v0.1.0  
**Phạm vi:** Dashboard owner tại runtime local  
**Kích thước đã kiểm tra:** 1440×900, 1024×768, 390×844, 360×800  
**Phương pháp:** Thao tác trực tiếp bằng trình duyệt; kiểm tra keyboard/focus, console, network, responsive và các trạng thái an toàn. Không sửa code, không xác nhận thao tác phá hủy, không thay provider/model đang hoạt động.

---

## 1. Executive summary

### Kết luận: **NOT READY**

Dashboard đã có nền tảng sản phẩm tốt: hệ thống điều hướng rõ, phong cách neo-brutalist nhất quán, cơ chế owner authentication/CSRF/loopback được giải thích tốt, các hành động nhạy cảm đi qua preview và PendingAction, phạm vi bulk selection trong Knowledge được ghi rõ, tài liệu local có focus trap và khôi phục focus đúng.

Tuy nhiên, bản hiện tại chưa nên coi là sẵn sàng cho vận hành ổn định vì còn 6 lỗi P1:

1. Local Model Manager bị vỡ layout và gần như không đọc được ngay ở desktop 1440 px.
2. Knowledge bị cắt nội dung ở 1440 px và đặc biệt nghiêm trọng tại 1024 px do overflow bị ẩn.
3. Learning Job báo `Completed · 0%`, làm mất độ tin cậy của trạng thái vận hành.
4. Khi network/SSE mất kết nối, dashboard vẫn hiển thị `LIVE` và `CONNECTED` bằng dữ liệu cũ.
5. Tại 360 px, topbar và popup tài liệu tràn ngang, làm mất một phần CTA/nút đóng.
6. Danh sách chọn chat model/embedding model không lọc theo capability, tạo rủi ro kích hoạt sai loại model.

### Tổng hợp mức độ

| Mức | Số lỗi | Ý nghĩa |
|---|---:|---|
| P0 | 0 | Không phát hiện lộ secret hoặc thực thi phá hủy không cần xác nhận |
| P1 | 6 | Cần sửa trước khi coi dashboard là công cụ vận hành đáng tin cậy |
| P2 | 10 | Làm flow khó hiểu, thiếu feedback hoặc tăng rủi ro thao tác |
| P3 | 4 | Lỗi consistency, wording và polish |

### Health theo khu vực

| Khu vực | Health | Nhận định |
|---|---|---|
| Authentication & security | Tốt, có điều kiện | Luồng rõ và CSRF hoạt động; thông báo lỗi login còn mơ hồ |
| Overview | Trung bình | Nắm được trạng thái tổng quan nhưng thiếu threshold và offline state |
| Groups & group detail | Khá | Chức năng đủ; mật độ điều khiển và responsive cần cải thiện |
| Knowledge & Learning Jobs | Kém | Overflow và dữ liệu tiến độ mâu thuẫn |
| AI & RAG | Khá, có điều kiện | Không lộ secret; guard khi đổi provider/model chưa đủ rõ |
| Local Models | Kém | Layout vỡ và lựa chọn capability chưa an toàn |
| Storage/Workers/Audit | Trung bình | Có dữ liệu thật nhưng thiếu context và progress/error feedback |
| Pending Action | Khá | Cơ chế an toàn đúng; bảng quá dày và ngôn ngữ chưa nhất quán |
| Documentation | Tốt trên desktop, kém ở 360 px | Focus tốt; modal mobile bị tràn và mất nút đóng |

---

## 2. Danh sách lỗi theo mức độ ưu tiên

## P1 — Nghiêm trọng

### P1-01 — Local Model Manager bị vỡ layout

- **Loại:** UI / responsive
- **Màn hình:** Local Models
- **Viewport:** 1440×900 và 360×800
- **Bước tái hiện:**
  1. Đăng nhập dashboard.
  2. Mở **Local Models**.
  3. Quan sát danh sách model ở trạng thái “Đã cài”.
- **Hiện trạng:** Các trường `Loại`, `Khả năng`, `Kích thước`, `RAM`, `VRAM`, `Độ phù hợp`, `Tham số`, `Quantization` bị dính thành một chuỗi. Thẻ model gần như mất toàn bộ cấu trúc cột/hàng và khoảng cách. Icon nằm tách khỏi nội dung.
- **Tác động:** Owner khó so sánh model, dễ đọc nhầm RAM/VRAM, capability hoặc mức phù hợp; màn hình quản lý model không đáp ứng tác vụ chính.
- **Đề xuất sửa cụ thể:**
  - Dùng grid có cột rõ ràng cho metadata; mỗi key/value là một ô độc lập.
  - Tại desktop dùng 3–4 cột; tại mobile chuyển thành danh sách key/value hai cột.
  - Giới hạn chiều rộng tên model, cho phép wrap có kiểm soát.
  - Đặt badge trạng thái và CTA trong header/footer card cố định.
- **Acceptance criteria:**
  - Không có text dính nhau tại cả 1440 và 360 px.
  - Mọi nhãn/giá trị phân biệt được bằng khoảng cách hoặc đường chia.
  - Card không tạo overflow toàn trang.
  - Người dùng có thể xác định model, capability, RAM/VRAM và trạng thái active trong tối đa 5 giây.
- **Bằng chứng:**
  - [Desktop 1440×900](output/playwright/audit/10-local-models-desktop-1440x900.png)
  - [Mobile nhỏ 360×800](output/playwright/audit/22-local-models-mobile-small-360x800.png)

### P1-02 — Knowledge bị cắt nội dung bởi overflow ẩn

- **Loại:** UI / responsive
- **Màn hình:** Kho tri thức
- **Viewport:** 1440×900 và 1024×768
- **Bước tái hiện:**
  1. Mở **Kho tri thức**.
  2. Quan sát KPI, toolbar và bảng.
  3. Tại 1024×768, kiểm tra phần bên phải màn hình.
- **Hiện trạng:**
  - Ở 1440 px, nội dung nội bộ rộng khoảng 1669 px trong khi phần main chỉ khoảng 1176 px.
  - Ở 1024 px, `body/main` rộng khoảng 1395 px nhưng viewport chỉ 1024 px và overflow ngang bị ẩn.
  - Các giá trị KPI/toolbar/cột bên phải bị cắt, không có thanh cuộn ngang ở cấp trang để truy cập.
- **Tác động:** Nội dung chính và CTA có thể biến mất tại viewport bắt buộc. Người dùng không biết dữ liệu tồn tại nhưng bị cắt.
- **Đề xuất sửa cụ thể:**
  - Loại bỏ `min-width` hoặc grid cố định gây tràn trong `.ops-stack`.
  - Cho KPI wrap theo 4/2/1 cột tùy breakpoint.
  - Đặt bảng trong container `overflow-x:auto` riêng, giữ page không tràn.
  - Toolbar phải wrap hoặc chuyển filter phụ vào popover/drawer.
- **Acceptance criteria:**
  - `document.body.scrollWidth === innerWidth` ở cả bốn viewport.
  - KPI và CTA không bị cắt.
  - Nếu bảng rộng, chỉ table container cuộn ngang và có dấu hiệu trực quan.
  - Không có nội dung quan trọng nằm ngoài vùng truy cập.
- **Bằng chứng:**
  - [Knowledge desktop 1440×900](output/playwright/audit/07-knowledge-desktop-1440x900.png)
  - [Knowledge laptop 1024×768](output/playwright/audit/16-knowledge-laptop-1024x768.png)

### P1-03 — Learning Job hiển thị “Completed · 0%”

- **Loại:** Dữ liệu/API + UX
- **Màn hình:** Kho tri thức → Learning Jobs
- **Viewport:** 1440×900; lỗi xuất hiện tương tự trong DOM mobile
- **Bước tái hiện:**
  1. Mở **Kho tri thức**.
  2. Cuộn đến **Learning Jobs**.
  3. Quan sát các job trạng thái Completed.
- **Hiện trạng:** Nhiều job có badge `Completed` nhưng phần mô tả và progress bar là `Completed · 0%`/`Tiến độ 0%`.
- **Tác động:** Owner không thể tin trạng thái job, không biết job thật sự hoàn thành hay dữ liệu tiến độ bị mất.
- **Đề xuất sửa cụ thể:**
  - Chuẩn hóa contract API: job `completed` phải trả progress 100% hoặc không trả progress nếu metric không có nghĩa.
  - Tách `phase`, `status`, `progress`, `processed/total` và `last_error`.
  - Nếu dữ liệu cũ không có progress, hiển thị “Đã hoàn tất” thay vì 0%.
- **Acceptance criteria:**
  - Không có job `completed` hiển thị dưới 100%.
  - Progress chỉ hiển thị khi mẫu số đáng tin cậy.
  - Job lỗi/tạm dừng/đang chạy có phase và timestamp cập nhật gần nhất.
- **Bằng chứng:** [Learning Jobs desktop 1440×900](output/playwright/audit/08-learning-jobs-desktop-1440x900.png)

### P1-04 — Mất network nhưng UI vẫn báo LIVE/CONNECTED

- **Loại:** UX state + API/SSE
- **Màn hình:** Tổng quan và shell toàn cục
- **Viewport:** 1440×900
- **Bước tái hiện:**
  1. Mở **Tổng quan** khi đang hiển thị `LIVE` và `CONNECTED`.
  2. Chuyển browser network sang offline.
  3. Chờ các request overview/workers/pending/audit thất bại.
  4. Quan sát header và widget trạng thái.
- **Hiện trạng:** Console ghi nhận các request thất bại `ERR_INTERNET_DISCONNECTED`, nhưng giao diện vẫn giữ `LIVE`, `CONNECTED` và số liệu cũ; không có “Đang kết nối lại”, timestamp stale, retry hay trạng thái offline.
- **Tác động:** Owner có thể tin nhầm dữ liệu cũ là realtime và bỏ qua sự cố backend/SSE.
- **Đề xuất sửa cụ thể:**
  - Quản lý state kết nối SSE: connected, reconnecting, stale, offline.
  - Sau N giây không nhận snapshot, thay `LIVE` bằng `STALE` và hiển thị “Cập nhật lần cuối”.
  - Có retry tự động với backoff và CTA retry thủ công khi API lỗi.
  - Giữ dữ liệu cũ nhưng phủ nhãn “Dữ liệu có thể đã cũ”.
- **Acceptance criteria:**
  - Trong tối đa 5 giây sau khi mất kết nối, không còn nhãn `LIVE`.
  - UI hiển thị lần cập nhật cuối và trạng thái reconnect.
  - Khi online trở lại, trạng thái tự phục hồi và có thông báo ngắn.
- **Bằng chứng:** [Overview offline nhưng vẫn hiển thị LIVE 1440×900](output/playwright/audit/24-overview-offline-no-feedback-desktop-1440x900.png)

### P1-05 — 360 px bị tràn topbar và modal, mất CTA/nút đóng

- **Loại:** UI / responsive / accessibility
- **Màn hình:** Shell toàn cục, Nhóm Telegram, popup tài liệu
- **Viewport:** 360×800
- **Bước tái hiện:**
  1. Resize về 360×800.
  2. Mở **Nhóm Telegram**; quan sát topbar.
  3. Mở **Tài liệu & Hệ thống** → `USER_GUIDE.md`.
- **Hiện trạng:**
  - Topbar rộng khoảng 374 px trên viewport 360 px và `body` ẩn overflow; CTA bên phải bị cắt.
  - Dialog có `scrollWidth` khoảng 384 px trong vùng nội dung 334 px; nút đóng bị đẩy khỏi màn hình.
  - ESC vẫn đóng được và trả focus đúng, nhưng người dùng touch không thấy/không chạm được nút đóng.
- **Tác động:** Mất CTA quan trọng và không thể đóng popup bằng touch theo cách hiển thị trên UI.
- **Đề xuất sửa cụ thể:**
  - Cho topbar co giãn; ẩn nhãn phụ trước, không cắt icon hành động.
  - Modal dùng `width:min(..., calc(100vw - 24px))`.
  - Toolbar modal wrap thành hai hàng hoặc chuyển copy/expand vào menu.
  - Giữ nút đóng sticky ở góc phải trong vùng nhìn thấy.
- **Acceptance criteria:**
  - `body.scrollWidth === 360`.
  - Tất cả nút topbar và modal nằm trọn trong viewport.
  - Modal đóng được bằng nút, click backdrop và ESC.
  - Nút đóng có touch target ít nhất 44×44 px.
- **Bằng chứng:**
  - [Nhóm Telegram 360×800](output/playwright/audit/20-groups-mobile-small-360x800.png)
  - [Popup tài liệu 360×800](output/playwright/audit/21-doc-reader-modal-mobile-small-360x800.png)

### P1-06 — Bộ chọn model không giới hạn theo capability

- **Loại:** UX safety + dữ liệu/backend
- **Màn hình:** Local Models → Chọn model hoạt động
- **Viewport:** 1440×900
- **Bước tái hiện:**
  1. Mở **Local Models**.
  2. Mở danh sách **Chat model** và **Embedding model**.
  3. So sánh với capability trên card.
- **Hiện trạng:** Cả hai select đều chứa toàn bộ 8 model. Model embedding `nomic-embed-text` xuất hiện trong Chat model; các model chat xuất hiện trong Embedding model.
- **Tác động:** Owner có thể chọn nhầm model không hỗ trợ tác vụ, làm AI/chat hoặc indexing lỗi; thay embedding còn có thể tạo tác động re-index.
- **Đề xuất sửa cụ thể:**
  - Backend trả capability chuẩn hóa: chat, embedding, tool-use, context size.
  - Chat select chỉ nhận model có chat capability; embedding select chỉ nhận model embedding.
  - Activation luôn có preview: thay đổi nào, số nguồn/vector bị ảnh hưởng, có re-index hay không.
- **Acceptance criteria:**
  - Không thể chọn model sai capability.
  - Model không tương thích bị loại khỏi select hoặc disabled kèm lý do.
  - Đổi embedding phải có impact summary và xác nhận rõ trước khi áp dụng.
- **Bằng chứng:** [Local Models desktop 1440×900](output/playwright/audit/10-local-models-desktop-1440x900.png)

## P2 — Trung bình

### P2-01 — Trạng thái lỗi đăng nhập mơ hồ và không tự xóa khi sửa mã

- **Loại:** UX + accessibility
- **Flow:** Đăng nhập
- **Viewport:** 1440×900
- **Hiện trạng:** Mã sai và mã hết hạn dùng chung thông báo “sai hoặc đã hết hạn”. Sau khi sửa input, error/`aria-invalid` vẫn giữ đến lần submit tiếp theo.
- **Tác động:** Người dùng không biết cần kiểm tra mã hay tạo mã mới; screen reader tiếp tục coi input không hợp lệ dù đã sửa.
- **Đề xuất:** Backend trả error code riêng cho invalid/expired; UI xóa lỗi khi người dùng bắt đầu sửa.
- **Acceptance criteria:** Có thông báo riêng cho sai mã, hết hạn, mất phiên, API không kết nối; lỗi inline được reset khi input thay đổi.
- **Bằng chứng:** [Login invalid 1440×900](output/playwright/audit/02-login-invalid-desktop-1440x900.png)

### P2-02 — Overview thiếu threshold và ưu tiên hành động

- **Loại:** UX / information hierarchy
- **Màn hình:** Tổng quan
- **Viewport:** 1440×900 và 1024×768
- **Hiện trạng:** RAM, VRAM, MySQL, vector hiển thị giá trị kỹ thuật nhưng không cho biết bình thường/bất thường. Biểu đồ và empty pending panel dùng nhiều chiều cao; thông tin cần hành động bị đẩy xuống dưới.
- **Tác động:** Owner thấy số nhưng không biết có cần xử lý hay không.
- **Đề xuất:** Thêm trạng thái Healthy/Warning/Critical, threshold, trend và CTA; rút gọn empty panel.
- **Acceptance criteria:** Mỗi tài nguyên có status và ngữ cảnh; lỗi/pending luôn được ưu tiên trước telemetry chi tiết.
- **Bằng chứng:**
  - [Overview desktop](output/playwright/audit/03-overview-desktop-1440x900.png)
  - [Overview laptop](output/playwright/audit/14-overview-laptop-1024x768.png)

### P2-03 — Group Directory có quá nhiều điều khiển cùng lúc

- **Loại:** UX / responsive
- **Màn hình:** Nhóm Telegram
- **Viewport:** 1440×900, 1024×768, 390×844
- **Hiện trạng:** Search, bốn tab, sáu filter/sort, saved view, density và column picker cùng xuất hiện trước bảng. Trên mobile người dùng phải cuộn qua một vùng toolbar rất dài mới đến dữ liệu.
- **Tác động:** Tăng tải nhận thức và làm chậm tác vụ “tìm rồi quản lý một nguồn”.
- **Đề xuất:** Giữ search + category + filter summary ở toolbar; đưa filter nâng cao vào drawer/popover; đưa saved view/density/columns vào menu “Tùy chỉnh”.
- **Acceptance criteria:** Bảng đầu tiên xuất hiện trong một viewport mobile sau phần header; filter đang áp dụng có chip và nút reset.
- **Bằng chứng:**
  - [Groups desktop](output/playwright/audit/04-groups-desktop-1440x900.png)
  - [Groups laptop](output/playwright/audit/15-groups-laptop-1024x768.png)
  - [Groups mobile](output/playwright/audit/19-groups-mobile-390x844.png)

### P2-04 — Pending Action quá dày, lẫn ngôn ngữ và lộ toàn bộ preview trong bảng

- **Loại:** UX / content design
- **Màn hình:** Hành động chờ
- **Viewport:** 1440×900
- **Hiện trạng:** Action name và status bằng tiếng Anh, preview bằng tiếng Việt, scope thường là raw Chat ID. Một số preview chứa toàn bộ nội dung message dài. Danh sách tải tối đa 500 record, không có pagination rõ.
- **Tác động:** Khó scan, khó phân biệt phạm vi và có nguy cơ hiển thị quá nhiều nội dung nhạy cảm trong bảng tổng quan.
- **Đề xuất:** Việt hóa action/status; dùng tên nguồn + ID phụ; preview chỉ 2 dòng; nội dung đầy đủ chỉ trong modal; pagination/filter theo loại và thời gian.
- **Acceptance criteria:** Mỗi row cao ổn định; action hiểu được không cần biết code; preview truncate; scope có friendly name.
- **Bằng chứng:** [Pending Actions desktop](output/playwright/audit/23-pending-actions-desktop-1440x900.png)

### P2-05 — Modal preview BLOCK không trả focus và CTA confirm chưa đủ tính cảnh báo

- **Loại:** Accessibility + UX safety
- **Màn hình:** Chi tiết nguồn → Preview BLOCK
- **Viewport:** 1440×900
- **Hiện trạng:** ESC đóng modal, nhưng focus không trở về nút đã mở preview. CTA confirm dùng màu teal giống hành động chính thông thường.
- **Tác động:** Người dùng keyboard mất vị trí; màu CTA không phản ánh hậu quả chặn nguồn.
- **Đề xuất:** Lưu trigger ref và restore focus; dùng variant warning/danger; ghi rõ “Xác nhận BLOCK” thay vì “Xác nhận”.
- **Acceptance criteria:** Sau ESC/close, focus quay lại trigger; CTA mang tên hành động cụ thể và màu cảnh báo.
- **Bằng chứng:** [Preview BLOCK desktop](output/playwright/audit/06-pending-preview-block-desktop-1440x900.png)

### P2-06 — Storage dry run chờ lâu nhưng không có tiến độ, timeout hoặc hủy

- **Loại:** UX state + backend support
- **Màn hình:** Bộ nhớ & lưu trữ
- **Viewport:** 1440×900
- **Hiện trạng:** Sau khi chạy dry run, nút giữ trạng thái “Đang dry run…” trong hơn 20 giây. Request chưa hoàn tất, không có progress, elapsed time, cancel hoặc thông tin đang quét phần nào. Cuối cùng tác vụ thành công.
- **Tác động:** Người dùng tưởng hệ thống treo và có thể reload/nhấn lại.
- **Đề xuất:** Job hóa dry run hoặc trả progress theo phase; hiển thị thời gian đã chạy, cancel, timeout và retry.
- **Acceptance criteria:** Sau 2 giây có progress/phase; tác vụ dài có cancel; lỗi có retry; không tạo request trùng.
- **Bằng chứng:** [Storage dry run đang chờ](output/playwright/audit/11-storage-dryrun-stuck-desktop-1440x900.png)

### P2-07 — Scheduler/Workers thiếu ngữ cảnh vận hành

- **Loại:** UX / backend support
- **Màn hình:** Scheduler & Workers
- **Viewport:** 1440×900
- **Hiện trạng:** Bảng chủ yếu hiển thị technical job name, next run, max instances và `SCHEDULED`; thiếu last run, duration, outcome, retry/error và mô tả tác vụ.
- **Tác động:** Owner không xác định được job có khỏe hay chỉ đang được lên lịch.
- **Đề xuất:** Thêm tên thân thiện, last run, duration, last outcome, next run, queue lag và drawer chi tiết.
- **Acceptance criteria:** Có thể xác định job thất bại/chậm trong một lần scan mà không đọc log kỹ thuật.

### P2-08 — Audit Log còn quá kỹ thuật và có dữ liệu mojibake

- **Loại:** Content design + dữ liệu legacy
- **Màn hình:** Audit Log
- **Viewport:** 1440×900
- **Hiện trạng:** Action/result bằng tiếng Anh, target/correlation là ID thô; một số lý do tiếng Việt cũ bị lỗi encoding kiểu `Hi?u ch?nh...`.
- **Tác động:** Audit khó hiểu và giảm niềm tin vào bằng chứng vận hành.
- **Đề xuất:** Mapping action/outcome sang tên người dùng; friendly target; sửa/migrate encoding legacy; hỗ trợ mở raw detail khi cần.
- **Acceptance criteria:** Không còn mojibake; mọi row có mô tả ngắn bằng tiếng Việt và raw ID ở lớp chi tiết.
- **Bằng chứng:** [Audit Log desktop](output/playwright/audit/12-audit-log-desktop-1440x900.png)

### P2-09 — Đổi provider/OFF thiếu preview và cảnh báo tác động rõ

- **Loại:** UX safety
- **Màn hình:** AI & RAG
- **Viewport:** 1440×900
- **Hiện trạng:** Provider được thể hiện bằng radio trực tiếp. UI không có impact summary ngay cạnh lựa chọn và không thấy cảnh báo nổi bật rằng `OFF` sẽ tắt toàn bộ AI; cloud fallback/per-group override chưa được giải thích tại điểm thao tác.
- **Tác động:** Owner có thể hiểu radio là filter hoặc vô tình thay global routing.
- **Đề xuất:** Chọn provider mở preview trước khi lưu; hiển thị group override, fallback, số nguồn bị ảnh hưởng và cảnh báo riêng cho OFF.
- **Acceptance criteria:** Thao tác đổi provider không áp dụng chỉ bằng một click; OFF cần confirm rõ; không hiển thị secret.
- **Bằng chứng:** [AI & RAG desktop](output/playwright/audit/09-ai-rag-desktop-1440x900.png)

### P2-10 — Một số touch target nhỏ hơn 44 px

- **Loại:** Accessibility / responsive
- **Màn hình:** Shell và Groups
- **Viewport:** 390×844
- **Hiện trạng đo được:** Nút mở menu khoảng 31×44 px, nút thông báo 42×44 px, input search thực khoảng 265×25 px.
- **Tác động:** Khó chạm chính xác trên mobile, đặc biệt với người dùng hạn chế vận động.
- **Đề xuất:** Mọi control tương tác có hit area tối thiểu 44×44; input tối thiểu cao 44.
- **Acceptance criteria:** Không còn phần tử tương tác visible có chiều rộng hoặc chiều cao dưới 44 px trên mobile.

## P3 — Nhỏ/polish

### P3-01 — Ngôn ngữ và capitalization chưa nhất quán

- `Learned`, `Not Learned`, `Completed`, `Cancelled`, `Executed`, `Expired`, `Recommended`, `Not Recommended`, `SCHEDULED` xuất hiện trong UI tiếng Việt.
- **Đề xuất:** Dùng từ điển trạng thái thống nhất; raw code chỉ hiện ở detail/tooltip.

### P3-02 — Giá trị read-only trên trang Kết nối dùng textbox

- **Loại:** Semantics / UI
- **Hiện trạng:** Trạng thái runtime và “Secret không hiển thị” được expose như textbox dù không chỉnh sửa.
- **Đề xuất:** Dùng `<output>`, description list hoặc readonly field có affordance rõ.

### P3-03 — Badge Knowledge bị chật và chữ tràn trên mobile

- **Hiện trạng:** Các badge như `32.9% ĐỘ PHỦ`, `MYSQL`, `QUEUE` có phần text sát/chồng khỏi khung.
- **Đề xuất:** Badge tự giãn theo nội dung; không khóa width 30 px; giảm font hoặc chuyển meta xuống dòng.
- **Bằng chứng:** [Knowledge mobile 390×844](output/playwright/audit/17-knowledge-mobile-390x844.png)

### P3-04 — Một số định dạng số/trạng thái gây nhiễu

- KPI pending hiển thị `00` thay vì `0`; card active/model label và mixed case chưa đồng nhất.
- **Đề xuất:** Chuẩn hóa format số và casing theo design tokens/content guideline.

---

## 3. Đánh giá từng flow A–K

| Flow | Kết quả | Quan sát chính |
|---|---|---|
| A. Đăng nhập | **Pass có điều kiện** | Hướng dẫn lấy mã và cảnh báo không nhập Telegram OTP/API key rất tốt. Sai/hết hạn chưa tách bạch. Reload giữ phiên hợp lý. 401 pre-auth và login sai được backend từ chối đúng. |
| B. Tổng quan | **Partial** | Hiểu được provider, message, pending và tài nguyên; thiếu threshold/action priority và stale/offline state. |
| C. Danh sách group/channel | **Pass có điều kiện** | Search tên/username/Chat ID, category, filter, sort, page size, pagination, saved view, density, column chooser và recommendation đều hoạt động. Toolbar quá dày; 1024 cần cuộn ngang bảng; mobile phải cuộn dài trước dữ liệu. |
| D. Chi tiết group/channel | **Pass có điều kiện** | Có ALLOW/BLOCK, 19 quyền, AI mode/fallback, retention/quota, `/ask`, AUTO link, sync, learning, MySQL/vector/storage và danger zone. Preview BLOCK an toàn; modal chưa restore focus và CTA chưa đủ cảnh báo. |
| E. Kho tri thức | **Fail** | Giải thích “learning không phải fine-tune” rất tốt; selection 50/298 rõ và bỏ chọn đúng. Nhưng desktop/laptop bị cắt và Learning Job `Completed · 0%`. |
| F. AI & RAG | **Partial** | Provider/model/embedding và secret boundary rõ. Chưa đủ guard/impact khi đổi provider hoặc OFF; re-index impact không nổi bật tại điểm thao tác. |
| G. Local Model Manager | **Fail** | Có filter, active status, size/parameter/quantization và preview delete; card vỡ layout và selector không lọc capability. |
| H. Storage, Workers, Audit | **Partial** | Có breakdown, dry run, scheduler, queue, RAM/VRAM và filter audit. Thiếu threshold, tiến độ tác vụ dài, last outcome/duration; log còn kỹ thuật và lỗi encoding legacy. |
| I. Pending Action | **Pass có điều kiện** | Preview, phạm vi, cảnh báo, confirm/cancel và expired/executed/cancelled đều có. Danh sách quá dày, mixed language và raw IDs. Preview test đã được hủy. |
| J. Tài liệu & hệ thống | **Pass desktop / Fail 360 px** | Popup, search, copy, expand, ESC, focus trap và focus restore hoạt động. Tại 360 px nút đóng bị đẩy khỏi viewport. |
| K. Trạng thái hệ thống | **Fail** | Có loading/disabled/empty ở một số màn hình; CSRF 403 hoạt động. Mất network không có stale/reconnect/error state, vẫn hiển thị LIVE. Retry/API error chưa nhất quán. |

---

## 4. Consistency audit

| Thành phần | Đánh giá | Vấn đề | Hướng chuẩn hóa |
|---|---|---|---|
| Button | Khá | Confirm nhạy cảm và primary cùng màu teal; target mobile có chỗ <44 px | Variant primary/secondary/warning/danger; hit area 44 px |
| Badge | Trung bình | Mixed English/Vietnamese, text overflow ở mobile | Token size linh hoạt, từ điển trạng thái thống nhất |
| Form | Khá | Label đầy đủ ở đa số nơi; login error không reset; read-only dùng textbox | Validation theo error code; dùng semantics read-only |
| Table | Trung bình | Groups có scroll container; Knowledge overflow ẩn; Pending/Audit quá dày | Table container riêng, sticky key columns, truncate + detail |
| Modal/drawer | Khá desktop | Docs focus tốt; group preview focus restore lỗi; docs 360 tràn | Modal responsive, focus restore bắt buộc, sticky close |
| Empty state | Khá | Copy rõ nhưng chiếm quá nhiều không gian ở Overview | Compact empty state, ưu tiên dữ liệu/action khác |
| Loading state | Trung bình | Có disabled label nhưng tác vụ dài thiếu progress/cancel | Skeleton ngắn; phase/progress/cancel cho tác vụ dài |
| Error state | Kém | Login mơ hồ; offline/SSE không phản ánh UI | Error code rõ, stale banner, retry |
| Typography | Khá | Peter Obscure đúng cho heading, Darley Sans đúng cho body; heading mobile wrap gắt | Giữ font pairing; giảm size/line-height theo breakpoint |
| Spacing | Trung bình | Một số panel quá cao; Local Models mất spacing hoàn toàn | Grid/spacing tokens có test responsive |
| Color | Khá | Neo-brutalist nhất quán; danger confirm chưa nổi bật | Dùng magenta/red cho destructive, vàng cho warning |

---

## 5. Accessibility

### Điểm tốt

- Navigation có accessible name.
- Đa số input/select có label.
- Focus keyboard trên navigation có outline đen 3 px và halo vàng rõ.
- Popup tài liệu:
  - Có focus trap.
  - Đóng bằng ESC.
  - Focus trở về đúng card tài liệu.
- Modal chi tiết PendingAction đã đóng bằng ESC và trả focus về nút “Xem”.
- Icon button có accessible name như “Thông báo”, “Đăng xuất”, “Đóng trình đọc”.

### Vấn đề

1. Touch target mobile chưa đạt 44×44 ở một số control.
2. Preview BLOCK không trả focus về trigger.
3. Popup tài liệu 360 px mất nút đóng khỏi viewport.
4. Badge text bị chật/chồng, giảm khả năng đọc.
5. Nội dung tài liệu render như một khối generic dài; heading Markdown chưa trở thành cấu trúc heading semantic trong accessibility tree.
6. Bảng dày với raw UUID/Chat ID làm screen reader phải đọc lượng nội dung lớn.

### Acceptance accessibility tối thiểu

- Không có control visible dưới 44×44 px ở mobile.
- Mọi modal trap focus, ESC close và restore focus.
- Markdown render thành heading/list/code semantic.
- Trạng thái không chỉ truyền đạt bằng màu.
- Kiểm tra contrast và keyboard flow bằng axe/Lighthouse trước release.

---

## 6. Responsive audit

| Viewport | Kết luận | Quan sát |
|---|---|---|
| 1440×900 | **NOT READY** | Overview/Groups/AI tốt; Knowledge bị cắt ngang; Local Models vỡ layout. |
| 1024×768 | **NOT READY** | Sidebar chuyển thành drawer hợp lý; Overview tốt. Groups cần cuộn ngang để đến CTA. Knowledge rộng ~1395 px nhưng page ẩn overflow, mất nội dung. |
| 390×844 | **READY WITH CONDITIONS** | Overview và Knowledge top có cấu trúc 1 cột tốt. Groups toolbar quá dài. Một số badge/touch target nhỏ. |
| 360×800 | **NOT READY** | Topbar tràn và cắt CTA; Docs modal tràn, mất nút đóng; Local Models vẫn vỡ layout. |

### Khuyến nghị responsive

- Breakpoint 1024 không nên giữ layout desktop cố định của Knowledge.
- Table trên mobile:
  - Groups: ưu tiên card có thể mở rộng thay vì cố giữ toàn bộ cột.
  - Knowledge: row card với nguồn, trạng thái và CTA; metadata chi tiết mở rộng.
- Filter mobile dùng drawer full-width; header chỉ giữ search + filter count.
- Topbar mobile giới hạn title 1–2 dòng và ưu tiên menu/notification/pending icon.

---

## 7. Những phần đang tốt và nên giữ

1. **Neo-brutalist system**: nền beige, viền đen, hard shadow, teal/magenta/vàng tạo nhận diện mạnh.
2. **Font pairing**: Peter Obscure cho heading và Darley Sans cho nội dung/điều khiển phù hợp định hướng.
3. **Security explanation**: loopback only, HttpOnly cookie, CSRF, HMAC code và secret boundary được truyền đạt rõ.
4. **Login education**: nói rõ không nhập API key, Telegram OTP hoặc password.
5. **Learning definition**: giải thích chính xác sync → clean → embedding → index, không gọi là fine-tune.
6. **Bulk selection scope**: phân biệt rõ chọn 50 nguồn trên trang và 298 kết quả lọc; bỏ chọn hoạt động đúng.
7. **Danger zone separation**: BLOCK, leave và delete knowledge được tách khỏi thao tác thường.
8. **PendingAction architecture**: preview chưa phải thực thi; owner confirmation và worker re-check là mô hình đúng.
9. **Active model protection**: nút xóa model active bị disabled.
10. **Documentation reader accessibility**: focus trap, ESC và focus restore hoạt động tốt.
11. **Session behavior**: reload vẫn giữ phiên hợp lý; request ghi thiếu CSRF bị 403.
12. **Inactive recommendations**: hiển thị số ngày không hoạt động và số tin đã quan sát, có “Luôn giữ”.

---

## 8. Quick wins

1. Sửa CSS grid/card của Local Models.
2. Xóa min-width gây overflow ở Knowledge; thêm table scroll container.
3. Với job completed, ép UI hiển thị 100% hoặc ẩn progress nếu API không có số.
4. Việt hóa toàn bộ status/action name phổ biến.
5. Truncate preview PendingAction còn 2 dòng.
6. Đổi CTA modal thành “Xác nhận BLOCK”, “Xác nhận xóa…”.
7. Thêm `last updated` cho mọi widget LIVE.
8. Compact empty state trên Overview.
9. Tăng hit area mobile lên 44 px.
10. Sửa modal width tại 360 px và giữ close button sticky.
11. Reset login error khi input thay đổi.
12. Cho phép xóa/đổi tên saved view; hiện tại chỉ có lưu và áp dụng.

---

## 9. Thay đổi cần backend hỗ trợ

1. **Learning job contract:** status, phase, progress, processed, total, last_error, updated_at.
2. **Realtime health:** trạng thái SSE, heartbeat/last snapshot, reconnect reason.
3. **Model capability metadata:** chat/embedding/tool-use/context; compatibility reason.
4. **Activation preview:** provider/model/re-index impact và số nguồn/vector bị ảnh hưởng.
5. **Storage dry-run job:** progress theo phase, cancel, timeout, retry.
6. **Operational thresholds:** health level cho RAM, disk, queue lag, vector growth.
7. **Workers detail:** last run, duration, outcome, retry count, error summary.
8. **PendingAction pagination/detail:** row summary riêng, payload/nội dung đầy đủ qua detail endpoint.
9. **Human-readable audit metadata:** friendly action/target labels song song raw IDs.
10. **Login error codes:** invalid, expired, session missing, backend unavailable.
11. **Legacy encoding migration:** sửa các trường reason bị mojibake.
12. **Saved-view management:** update/delete/rename thay vì chỉ ghi đè theo tên.

---

## 10. Thứ tự triển khai đề xuất

### Now — Trước release

1. Sửa Local Models layout.
2. Sửa Knowledge overflow ở 1440/1024.
3. Sửa `Completed · 0%`.
4. Thêm stale/offline/SSE reconnect state.
5. Sửa topbar và docs modal tại 360 px.
6. Lọc model selector theo capability và thêm activation preview.
7. Chạy regression ở cả bốn viewport.

### Next — Sau khi chặn P1

1. Tái cấu trúc toolbar Groups/Knowledge.
2. Thêm threshold/trend/action state cho Overview/Storage.
3. Rút gọn và Việt hóa PendingAction/Audit.
4. Bổ sung progress/cancel cho storage dry-run.
5. Bổ sung last outcome/duration/error cho Workers.
6. Chuẩn hóa modal destructive và focus restore.
7. Nâng toàn bộ touch target lên 44 px.

### Later — Polish và mở rộng

1. Card/expand pattern riêng cho bảng mobile.
2. Job detail drawer/timeline theo nguồn.
3. Quản lý saved view đầy đủ: rename/delete/default.
4. Tùy chỉnh dashboard widget có guard và reset.
5. Automated visual regression + axe accessibility CI.

---

## 11. Console, network và security checks

- Hai lỗi 401 ban đầu là trạng thái dự kiến:
  - kiểm tra session trước đăng nhập;
  - thử mã đăng nhập sai.
- Đăng nhập đúng trả 200; reload giữ phiên.
- Request logout không có CSRF header trả **403**, đúng kỳ vọng.
- Không thấy API key, Telegram token, password, session token hoặc cookie được render trên UI.
- Khi offline, các request overview/workers/pending/audit thất bại ở network/console nhưng UI không phản ánh — đã ghi nhận P1-04.
- Không phát hiện warning console ngoài các lỗi có chủ đích khi kiểm tra authentication/offline.

---

## 12. Trạng thái hoàn nguyên sau audit

- PendingAction do audit tạo: **đã hủy**.
- Bulk selection Knowledge: **đã bỏ chọn**, còn 0 nguồn.
- Saved view `UX AUDIT TEMP`: **đã xóa bằng cách khôi phục preferences ban đầu**.
- Density: **đã trả về `comfortable`**.
- Visible columns: **đã trả về cấu hình ban đầu**.
- Group search/filter/category: **đã trả về mặc định “Tất cả nhóm” và query rỗng**.
- Không có learning job thử nghiệm nào được tạo.
- Không đổi AI provider, chat model hoặc embedding model.
- Không xóa model, không cleanup storage, không rời group/channel, không xóa dữ liệu.
- Storage chỉ chạy **dry run**; hệ thống báo hoàn tất và không xóa dữ liệu.
- Không sửa mã nguồn trong quá trình audit.

---

## 13. Danh mục ảnh bằng chứng

Toàn bộ ảnh được chụp trong lần audit này tại:

`output/playwright/audit/`

Các ảnh trọng yếu:

- Login: `01`, `02`
- Overview: `03`, `14`, `18`, `24`
- Groups/detail/preview: `04`, `05`, `06`, `15`, `19`, `20`
- Knowledge/jobs: `07`, `08`, `16`, `17`
- AI/Local Models: `09`, `10`, `22`
- Storage/Audit/Pending: `11`, `12`, `23`
- Documentation modal: `13`, `21`

