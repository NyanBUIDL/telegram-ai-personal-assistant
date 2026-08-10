# USER ACCEPTANCE TEST REPORT V2 — Telegram AI Personal Assistant

**Ngày kiểm thử:** 28/07/2026  
**Khung giờ chính:** 14:27–14:46 ICT (07:27–07:46 UTC)  
**Vai trò:** Senior Product QA, UX Auditor, người dùng mới và UAT tester độc lập  
**Môi trường:** local production-like tại `http://127.0.0.1:8765`; Admin API, MySQL, Telegram, Ollama và Qdrant thật  
**Viewport:** 1440×1000, 1024×768, 390×844, 360×800  
**Nguyên tắc:** không sửa code; thao tác phá hủy chỉ đến Preview/PendingAction/Cancel; không ghi secret  
**Kết luận phát hành:** **NOT READY**

## 1. Executive Summary

V2 đã cải thiện rõ rệt so với lần UAT trước. Lỗi nghiêm trọng nhất của V1 — `/ask in:<chat_id>` lấy dẫn chứng ngoài phạm vi — đã được sửa và được xác nhận bằng Telegram thật: câu hỏi giới hạn vào Coin68 chỉ dùng đúng `chat_id=-1001300523532`, trả một bài Coin68 trong cửa sổ 7 ngày và không trộn nguồn khác. Search `@username`, filter Learned, CSV 298 dòng, lịch sử PendingAction, mô tả permission/learning, VRAM, storage telemetry và layout mobile 390 px cũng đã được sửa.

Tuy nhiên, sản phẩm chưa đạt mục tiêu “dễ dùng cho người bình thường” và chưa hoàn chỉnh các flow quản trị được yêu cầu:

- Bộ não chung vẫn chỉ có **98/298 nguồn Learned (32,9%)**; 198 nguồn chưa học và 2 nguồn không có nội dung.
- Không có sort bảng, saved view, tùy chỉnh dashboard, chọn page size, ẩn/hiện cột hoặc density.
- Không có lọc/đề xuất nguồn không hoạt động, “Luôn giữ”, flow rời group/channel hoặc API tương ứng.
- Không có flow xóa dữ liệu đã học theo phạm vi.
- Kho tri thức không có search, checkbox, chọn theo trang/bộ lọc, retry/ghi chú riêng từng nguồn.
- Tài liệu chỉ là năm dòng tĩnh, không thể bấm để đọc.
- Local Model card mới đáp ứng phần nhận diện cơ bản; thiếu type rõ, RAM/VRAM ước tính, context window, last-used, số group sử dụng, machine fit, filter và tiến độ/cancel tải.
- Laptop 1024 px vẫn tràn ngang; mobile 360 px tràn nhẹ và một số touch target nhỏ hơn 44 px.
- Luồng học một nguồn thực thi thành công nhưng không tạo/hiển thị một job mới để theo dõi; `last_learned_at` và `last_job_id` không phản ánh lần chạy vừa xác nhận.

Không phát hiện mất dữ liệu, lộ secret, model bị xóa, provider bị đổi hoặc PendingAction còn treo. Cuối phiên có 0 PendingAction đang chờ và 200/200 learning job ở trạng thái Completed.

### Điểm quyết định phát hành

| Điều kiện | Kết quả | Nhận định |
|---|---|---|
| AI đúng phạm vi group/channel | **PASS** | Lỗi High V1 đã sửa; live Telegram chỉ dẫn Coin68. |
| Dẫn nguồn tin tức | **UX ISSUE** | Link đúng và mở được; một dẫn chứng duy nhất vẫn mang số `[S9]`. |
| Unified brain bao phủ nguồn được cấp quyền | **PARTIAL** | 98/298 Learned; 100 nguồn có AUTO_KNOWLEDGE. |
| Người mới thao tác không cần Chat ID | **PARTIAL** | Search/mở chi tiết tốt; các flow dọn dẹp/xóa/tài liệu/bulk còn thiếu. |
| Owner quản lý hàng trăm nguồn | **PARTIAL** | Search và phân trang có; sort/filter nâng cao/bulk selection/saved view thiếu. |
| An toàn thao tác nhạy cảm | **PASS** | Preview → Pending → Confirm/Cancel hoạt động; model delete/bulk/permission đã cancel an toàn. |
| Desktop/mobile dùng ổn | **PARTIAL** | 1440 và 390 tốt; 1024 và 360 có overflow. |
| Dữ liệu thật, API ổn định | **PARTIAL** | API nhanh, console sạch sau reload; log có worker exception gần thời điểm kiểm thử. |

## 2. Môi trường và dữ liệu kiểm thử

### 2.1 Checkpoint dữ liệu thật

| Chỉ số | Giá trị cuối |
|---|---:|
| Group/channel tham gia | 298 |
| Channel / group / supergroup | 90 / 107 / 101 |
| Nguồn ALLOW | 298 |
| Nguồn có AUTO_KNOWLEDGE | 100 |
| Learned / No content / Not learned | 98 / 2 / 198 |
| Coverage | 32,9% |
| Message trong MySQL | 85.688 |
| Message 24 giờ | 1.371 tại checkpoint DB |
| Message mới nhất | 28/07/2026 14:38:43 ICT |
| Message key trùng `(chat_id,message_id)` | 0 |
| Message theo inventory | 85.474 |
| Vector theo inventory | 74.945 |
| Learning jobs | 200 Completed; 0 trạng thái khác |
| PendingAction cuối phiên | 0 Pending |
| AI usage 24 giờ | 5 Answer; 394 Embedding tại màn hình AI |
| Ollama models | 8 model |
| Model active | `qwen3:8b` chat; `nomic-embed-text:latest` embedding |
| Storage | Application 1.099.136.200 B; vector 1.077.572.115 B; media 0 B |

Số message toàn DB cao hơn tổng inventory vì inventory là snapshot theo nguồn học và được refresh theo lịch, không phải counter realtime toàn bảng.

### 2.2 Dữ liệu không hoạt động dùng để đánh giá nhu cầu

Đối chiếu `MAX(telegram_messages.sent_at)` trên 298 nguồn:

| Điều kiện | Số nguồn |
|---|---:|
| Chưa có message trong MySQL | 197 |
| Message cuối quá 7 ngày | 44 |
| Quá 30 ngày | 36 |
| Quá 60 ngày | 31 |
| Quá 90 ngày | 27 |

Các số này **không đủ để tự đề xuất rời nguồn**: 197 nguồn chưa có message có thể là chưa sync/không đọc được lịch sử, không đồng nghĩa không hoạt động. Đây chính là lý do cần mô hình confidence và phân biệt activity với sync health.

### 2.3 Hiệu năng Admin API

Mỗi endpoint được gọi 5 lần trong cùng phiên local; đơn vị ms:

| Endpoint | HTTP | Min | Median | Max |
|---|---:|---:|---:|---:|
| `/api/v1/overview` | 200 | 30 | 32 | 36 |
| `/api/v1/groups?page=1&page_size=10` | 200 | 16 | 19 | 24 |
| `/api/v1/groups/-4006978081` | 200 | 6 | 7 | 16 |
| `/api/v1/knowledge/sources?page=1&page_size=50` | 200 | 14 | 15 | 19 |
| `/api/v1/learning-jobs?limit=200` | 200 | 11 | 16 | 135 |
| `/api/v1/ollama/models` | 200 | 8 | 8 | 11 |
| `/api/v1/storage` | 200 | 6 | 6 | 8 |

Không có lỗi console sau reload đã đăng nhập. Lỗi 401 duy nhất ở đầu phiên là request kiểm tra session trước khi login và biến mất sau reload authenticated.

## 3. Ma trận tính năng

### A. Dashboard và tùy chỉnh

| ID | Tính năng | Trạng thái | Bằng chứng | API liên quan | Mức độ | Đề xuất |
|---|---|---|---|---|---|---|
| A-01 | Login owner bằng mã local | PASS | Trang login che toàn dashboard; lệnh đúng được hiển thị | `/auth/session`, `/auth/login` | — | Giữ nguyên. |
| A-02 | Hướng dẫn lấy mã cho người mới | PASS | Hiển thị đúng `.\.venv\Scripts\tg-assistant.exe dashboard-code` và nhắc mở PowerShell tại project | — | — | Có thể thêm nút copy lệnh. |
| A-03 | Reload giữ phiên | PASS | Reload trở lại Overview, dữ liệu tải xong, không quay về login | `/auth/session` | — | Giữ nguyên. |
| A-04 | Không có mock/prototype | PASS | UI/API ghi LIVE; API `mock=false`; số liệu khớp MySQL | `/overview` | — | Giữ invariant bằng test. |
| A-05 | Loading/empty/error state | PARTIAL | Loading và empty state rõ; không tái tạo API 500 trong V2 | Nhiều GET | Low | Chuẩn hóa Retry cho mọi page. |
| A-06 | Sort từng cột | MISSING | Header bảng là text, không có sort control | Groups/Knowledge không nhận sort param | Medium | Thêm server-side `sort_by`, `sort_dir`, aria-sort. |
| A-07 | Giữ sort sau reload | MISSING | Không có sort state | Chưa có | Medium | Lưu query string + owner preference. |
| A-08 | Chọn 10/25/50/100 dòng | MISSING | Groups cố định 10; Knowledge cố định 50 | Backend có `page_size`, frontend không mở control | Medium | Thêm page-size selector. |
| A-09 | Ẩn/hiện cột và density | MISSING | Không có column chooser/density | Chưa cần API | Medium | Toolbar View options. |
| A-10 | Sắp xếp/ẩn widget Overview | MISSING | Widget tĩnh | Chưa có owner preference API | Medium | Widget manager + drag/drop có keyboard alternative. |
| A-11 | Saved view/reset UI | MISSING | Không có saved view hoặc reset | Chưa có | Medium | Lưu filter/sort/column layout theo owner. |
| A-12 | Khả năng đọc Overview | PARTIAL | Số liệu chính rõ; vẫn còn RAG, vector, SOURCE OF TRUTH, telemetry | `/overview` | Low | Tooltip/glossary ngắn, dùng tiếng Việt nhất quán. |

### B–D. Group/channel, inactivity và leave

| ID | Tính năng | Trạng thái | Bằng chứng | API liên quan | Mức độ | Đề xuất |
|---|---|---|---|---|---|---|
| B-01 | Hiển thị tên/username/Chat ID/type | PASS | Directory và detail hiển thị đủ; “không username” rõ | `/groups`, `/groups/{id}` | — | Giữ nguyên. |
| B-02 | Search theo tên | PASS | Tìm test group trả đúng 1 nguồn | `/groups?query=` | — | Giữ debounce hiện tại. |
| B-03 | Search `@username` | PASS | `@ThuanCapital` trả đúng nguồn | `/groups?query=` | — | Regression test. |
| B-04 | Search Chat ID | PASS | Backend và textbox hỗ trợ; detail không yêu cầu nhập lại | `/groups?query=` | — | Có thể thêm copy ID. |
| B-05 | Filter đã bật AI/chưa bật AI/tất cả | PASS | Ba tab hoạt động | `/groups?category=ai|permissions|all` | — | Đổi label ngắn hơn trên mobile. |
| B-06 | Filter group/supergroup/channel | MISSING | Không có control; backend không có param type | `/groups` | Medium | Multi-select type filter server-side. |
| B-07 | Filter ALLOW/BLOCK | MISSING | Không có control | `/groups` | Medium | Thêm policy filter. |
| B-08 | Filter learning/activity | MISSING | Không có learning status hoặc last-activity filter | `/groups` | Medium | Join inventory và activity server-side. |
| B-09 | Sort name/activity/message/vector/status | MISSING | API cố định `last_seen_at desc, title asc` | `/groups` | Medium | Sort toàn dataset tại backend. |
| B-10 | Mở chi tiết không cần Chat ID | PASS | Nút Quản lý mở đúng detail | `/groups/{id}` | — | Giữ nguyên. |
| B-11 | Permission descriptions | PASS | 19 permission có mô tả tiếng Việt và nhóm chức năng | `/groups/{id}/permissions` | — | Dịch tên action trong modal. |
| B-12 | Permission qua preview/cancel | PASS | Toggle `auto_task_suggestion` tạo PendingAction; Cancel; quyền cuối vẫn OFF | `/groups/{id}/permissions`, `/pending-actions/{id}/cancel` | — | Giữ nguyên. |
| C-01 | Filter “Không hoạt động” 7/30/60/90/custom | MISSING | Không có UI/API | Chưa có | Medium | Tạo activity endpoint có tiêu chí minh bạch. |
| C-02 | Phân biệt inactivity và sync failure | MISSING | Không có model đánh giá | Chưa có | High nếu tự động hóa | Dùng message age + sync state + observation coverage. |
| C-03 | Cleanup recommendations/confidence | MISSING | Không có màn hình/flow | Chưa có | Medium | Thêm recommendation, reason, confidence. |
| C-04 | Ignore/“Luôn giữ” | MISSING | Không có | Chưa có | Medium | Persistent owner preference. |
| D-01 | Nút rời group/channel | MISSING | Không có trong directory/detail | Chưa có Admin API | Medium | Flow riêng, không gọi Telegram ngay. |
| D-02 | Preview impact và lựa chọn giữ/xóa dữ liệu | MISSING | Không có | Chưa có | High nếu triển khai sai | Thiết kế 4 lựa chọn như yêu cầu, mặc định giữ dữ liệu. |
| D-03 | Guard owner/admin/audit/SSE | BLOCKED | Không có flow để kiểm tra; không rời nguồn thật | Chưa có | Critical nếu thiếu khi triển khai | Bắt buộc re-check quyền trước execute. |

### E–F. Kho tri thức và xóa dữ liệu học

| ID | Tính năng | Trạng thái | Bằng chứng | API liên quan | Mức độ | Đề xuất |
|---|---|---|---|---|---|---|
| E-01 | Danh sách đủ 298 nguồn | PASS | Summary 298; CSV có 298 data rows | `/knowledge/sources` | — | Giữ nguyên. |
| E-02 | Breakdown coverage | PASS | 98/298, 100 auto, 85.474 message, 74.945 vector | `/knowledge/sources` | — | Đưa summary này lên Overview. |
| E-03 | Giải thích “học ≠ fine-tune” | PASS | Hero mô tả sync MySQL → làm sạch → embedding | — | — | Thêm link tài liệu. |
| E-04 | Filter trạng thái | PASS | Learned trả dữ liệu; No Content trả đúng 2 nguồn | `/knowledge/sources?source_status=` | — | Thêm badge count mỗi option. |
| E-05 | Trạng thái paused | PARTIAL | Job có paused nhưng source inventory không có source status paused | `/learning-jobs` | Low | Phân biệt source status và job status trong UI. |
| E-06 | Search nguồn | MISSING | Không có textbox | API chưa có query | Medium | Search tên/@username/ID server-side. |
| E-07 | Sort nguồn | MISSING | Không có sort; API snapshot order cố định | `/knowledge/sources` | Medium | Server-side sort. |
| E-08 | Filter type/AUTO_KNOWLEDGE | MISSING | Không có | API chưa có | Medium | Thêm filter tương ứng. |
| E-09 | Counts, last learned, error/reason | PASS | Bảng có MySQL/text/vector/size/time/reason | `/knowledge/sources` | — | Giữ nguyên. |
| E-10 | Export toàn bộ CSV | PASS | File có 298 dòng, cột tiếng Việt đầy đủ | `/knowledge/sources/export` | — | Thêm timestamp/timezone trong metadata. |
| E-11 | Học từng nguồn | PARTIAL | Detail group có nút; Knowledge không search/chọn; confirm thực thi sync 26/index 12 | `/groups/{id}/actions` | Medium | Cho thao tác ngay tại Knowledge và điều hướng tới job. |
| E-12 | Theo dõi job học từng nguồn | BUG | Action Executed nhưng không có job mới; `last_job_id/last_learned_at` vẫn là 24/7 | `/pending-actions`, `/learning-jobs` | Medium | Tạo job record hoặc progress event và cập nhật inventory atomically. |
| E-13 | Checkbox/select page/select filter | MISSING | Không có checkbox | Chưa có | Medium | Selection model rõ, giữ qua phân trang. |
| E-14 | Bulk học tất cả còn thiếu | PARTIAL | CTA tạo preview đúng 200 nguồn và Cancel được | `/knowledge/enable-all-action` | Medium | Cho chọn theo filter, không chỉ global. |
| E-15 | Pause/resume bulk | PARTIAL | Có Pause all/Resume all; không chọn subset | `/learning-jobs/pause-all`, `/resume-all` | Medium | Bulk theo selection/filter. |
| E-16 | Retry riêng nguồn lỗi/ghi chú | MISSING | No Content có reason nhưng không có CTA; hiện 0 Failed | `/learning-jobs/{id}/retry` chỉ theo job | Medium | Per-source retry và owner note. |
| E-17 | Không Retry job Completed | PASS | Completed rows không còn nút Retry | `/learning-jobs` | — | Regression test. |
| E-18 | Chống job trùng | PARTIAL | Bulk backend lọc scope; click học nguồn đã học thực thi trực tiếp, không tạo duplicate job | Pending worker | Medium | Hiển thị “đã học, đang refresh” và idempotency key. |
| F-01 | Xóa vector/index/MySQL/media/toàn bộ/reset | MISSING | Không có UI/API theo nguồn | Chưa có | High | Thiết kế scope enum và impact preview. |
| F-02 | Double-confirm toàn bộ | BLOCKED | Flow chưa tồn tại; không xóa dữ liệu thật | Chưa có | Critical nếu thiếu | Gõ lại tên/mã xác nhận + owner re-auth. |
| F-03 | Chặn khi learning chạy/orphan/audit/SSE | BLOCKED | Không có endpoint để kiểm tra | Chưa có | High | Worker re-check trạng thái và cleanup orphan. |

### G. AI và hỏi đáp

| ID | Tính năng | Trạng thái | Bằng chứng | API liên quan | Mức độ | Đề xuất |
|---|---|---|---|---|---|---|
| G-01 | OpenAI/OpenRouter/Ollama/OFF | PARTIAL | UI có 4 lựa chọn; current OpenAI; không đổi provider thật | `/ai/config`, `/ai/provider` | — | Thêm preview tác động trước đổi embedding provider. |
| G-02 | AI mode riêng group/fallback | PASS | Detail có inherit/local/cloud/local-first/cloud-first/off và mô tả | `/groups/{id}/ai-route` | — | Cảnh báo reindex khi đổi embedding. |
| G-03 | `/ask in:<chat_id>` đúng scope | PASS | Message 430123→430124 chỉ dùng Coin68; DB xác nhận source message thuộc đúng chat | RAG private bot | — | Giữ integration test. |
| G-04 | Cửa sổ 7 ngày | PASS | Citation đăng 27/7 khi hỏi 28/7; DB timestamp khớp | RAG | — | Hiển thị window trong ack. |
| G-05 | Tin tức có nguồn mở được | PASS | `https://t.me/coin68/26454` đúng bài | RAG citation | — | Giữ direct link. |
| G-06 | Chỉ append nguồn được dùng | PASS | V2 chỉ append một source được body tham chiếu | RAG citation | — | Regression test. |
| G-07 | Số citation dễ hiểu | UX ISSUE | Chỉ có một dòng nhưng mang nhãn `[S9]` | RAG citation numbering | Low | Renumber appendix liên tục từ S1 sau khi lọc. |
| G-08 | Ack khi xử lý lâu | UX ISSUE | 16 giây từ request đến reply, không có “đang xử lý” | Control bot | Medium | Ack trong 1–2 giây, sau đó edit/send kết quả. |
| G-09 | Empty/no data/no permission khác nhau | PARTIAL | No-data text có; chưa live-test nguồn BLOCK an toàn | RAG/policy | Medium | Mã lỗi và copy riêng cho 3 trường hợp. |
| G-10 | Hỏi theo user trong group | BLOCKED | Không có tài khoản test thứ hai phù hợp; không gửi vào group công cộng | RAG/search | — | Test fixture riêng. |

### H. Local Models

| ID | Tính năng | Trạng thái | Bằng chứng | API liên quan | Mức độ | Đề xuất |
|---|---|---|---|---|---|---|
| H-01 | Card model và active state | PASS | 8 cards; CHAT ACTIVE/EMBED ACTIVE nhận biết ngay | `/ollama/models` | — | Nhóm chat/embedding. |
| H-02 | Name/family/size/params/quantization/update | PASS | Có trên card | `/ollama/models` | — | Giữ nguyên. |
| H-03 | Type chat/embedding/multimodal | PARTIAL | Chỉ active badge; inactive không có type rõ | `/ollama/models` | Medium | Chuẩn hóa capability metadata. |
| H-04 | RAM/VRAM/context/last-used/groups/fit/health | MISSING | Card không hiển thị | API chưa cung cấp đủ | Medium | Enrich model inventory. |
| H-05 | Filter Installed/Active/Fit | MISSING | Không có | Frontend | Medium | Filter chips. |
| H-06 | Download model | PARTIAL | Có input + nút tải; không tải model lớn thật | `/ollama/models/pull` | Medium | Progress, cancel, disk impact preview. |
| H-07 | Activate chat/embedding | PARTIAL | Có dropdown riêng; không đổi runtime thật | `/ollama/activate` | Medium | Preview reindex/impact trước activate. |
| H-08 | Không xóa active model | PASS | Nút delete disabled trên hai active model | `/ollama/models/delete-preview` | — | Backend cũng phải re-check. |
| H-09 | Delete preview/pending/cancel | PASS | Preview `glm-5:cloud`, modal đúng viewport, Cancel; model còn tồn tại | `/ollama/models/delete-preview`, Pending API | — | Việt hóa action title. |
| H-10 | Mobile card | PASS | 390 px không overflow; card 362 px trong viewport 390 | `/ollama/models` | — | Touch target tối thiểu 44 px. |

### I. Tài liệu và hệ thống

| ID | Tính năng | Trạng thái | Bằng chứng | API liên quan | Mức độ | Đề xuất |
|---|---|---|---|---|---|---|
| I-01 | Danh sách tài liệu | PARTIAL | Có 5 dòng README/Architecture/Activity/Security/Troubleshooting | Không có docs API | Low | Giữ catalog nhưng làm interactive. |
| I-02 | Bấm để đọc trực tiếp | MISSING | Row là `DIV`, tabIndex -1, không button/link | Chưa có | Medium | Docs endpoint sanitize Markdown. |
| I-03 | Popup/drawer, TOC, scroll, ESC, focus trap | MISSING | Không có dialog | Chưa có | Medium | Reuse accessible modal/drawer. |
| I-04 | Open full page/copy/link policy | MISSING | Không có thao tác | Chưa có | Medium | Open internal route; external `target=_blank rel=noopener`. |
| I-05 | Đủ 11 chủ đề trợ giúp người dùng | MISSING | Chỉ tên file kỹ thuật, không có onboarding/permissions/learning/ask/modes/retention/models/moderation/privacy trong UI | Chưa có | Medium | Information architecture theo task, không theo filename. |
| I-06 | Responsive docs list | PASS | 390 px không overflow; row 324 px | — | — | Giữ layout. |

### J–L. User state, responsive, accessibility và reliability

| ID | Tính năng | Trạng thái | Bằng chứng | API liên quan | Mức độ | Đề xuất |
|---|---|---|---|---|---|---|
| K-01 | Desktop 1440×1000 | PASS | Overview/Groups/Models/Docs đọc tốt | — | — | Giữ breakpoint. |
| K-02 | Laptop 1024×768 | BUG | `clientWidth=1024`, `scrollWidth=1239` ở Groups | — | Medium | Collapse sidebar sớm hơn; table container scroll riêng. |
| K-03 | Mobile 390×844 | PARTIAL | Không global overflow; group table còn 4 cột, menu hoạt động | — | Low | Card/expand row cho cột ẩn. |
| K-04 | Mobile 360×800 | BUG | body/document scrollWidth 374 trong viewport 360; header action bị cắt | — | Medium | Giảm header min-width/padding; test 320–375. |
| K-05 | Touch target | UX ISSUE | Tab filter ~29 px, Manage ~34 px, menu width ~31 px | — | Low | Tối thiểu 44×44 CSS px. |
| K-06 | Modal mobile | PARTIAL | Dialog x=16..374, y=61..783 trong 390×844; ESC đóng; focus vào Close | — | Low | Thêm automated focus-wrap test. |
| K-07 | Form labels/status không chỉ màu | PASS | Switch/select có accessible name; badge có text | — | — | Tiếp tục kiểm tra contrast tự động. |
| K-08 | Tiếng Việt encoding | PASS | Không thấy mojibake trong V2 | — | — | Giữ UTF-8/utf8mb4. |
| L-01 | 298 nguồn không làm treo UI | PASS | Directory/Knowledge tải nhanh, API median 15–19 ms | Groups/Knowledge | — | Virtualize nếu tăng lên hàng nghìn. |
| L-02 | Sort/filter backend với dataset lớn | PARTIAL | Filter hiện có chạy server-side; sort không cấu hình được | Groups/Knowledge | Medium | Không sort client-only. |
| L-03 | SSE/request loop | PARTIAL | UI duy trì LIVE, không thấy vòng lặp request vô hạn; chưa đo reconnect dài hạn | `/events` | Low | Telemetry reconnect/backoff. |
| L-04 | Console sau login/reload | PASS | 0 error, 0 warning | — | — | CI E2E assertion. |
| L-05 | Storage telemetry thật | PASS | Bytes/growth khớp API; meter hard-code V1 đã bỏ | `/storage` | — | Thêm quota percent khi có denominator thật. |
| L-06 | VRAM | PASS | Hiển thị 0 B và API `vram_status=available` | `/overview`, `/storage` | — | Phân biệt 0 used với unavailable. |
| L-07 | Worker/scheduler hiện tại | PARTIAL | 6 scheduled jobs, queue 0; log trước phiên có DetachedInstanceError/network exception lúc ~14:03 | `/workers` + local logs | Medium | Sửa detached ORM object, health alert theo exception. |
| L-08 | Runtime RAM sau semantic query | PARTIAL | Từ khoảng 665 MB lên 1.596 GB; ổn định ở lần đo kế tiếp | `/storage` | Medium quan sát | Profile Qdrant/vector load, đặt baseline và alert. |
| L-09 | Regression tests liên quan | PASS | 14 test RAG/Admin API/Knowledge pass | Local pytest | — | Thêm E2E cho missing flows khi triển khai. |

## 4. Báo cáo từng user flow

### Flow 1 — Người dùng mới

**Kết quả: PARTIAL — 9 bước quan sát được.**

1. Mở dashboard và đọc lệnh lấy mã.
2. Lấy mã bằng PowerShell, đăng nhập.
3. Đọc Overview.
4. Mở Nhóm Telegram.
5. Search test group bằng tên.
6. Bấm Quản lý.
7. Toggle một permission OFF → thấy Preview → Cancel.
8. Bấm Học nguồn này → Preview → xác nhận.
9. Chờ worker và đối chiếu PendingAction Executed.

Điểm tốt: không cần nhớ Chat ID; permission có mô tả; Preview rõ; API phản hồi nhanh.  
Điểm gây nhầm: “Set Chat Permission” và “Enable Group Learning” vẫn là tiếng Anh; sau xác nhận học không tự chuyển tới job/progress; inventory không cập nhật `last_job_id/last_learned_at`.  
Terminal: chỉ cần cho login theo thiết kế hiện tại.  
Đề xuất rút ngắn: CTA “Cấp quyền & học” theo wizard 3 bước, rồi mở thẳng progress drawer.

### Flow 2 — Học riêng một nguồn

**Kết quả: PARTIAL.**

Flow yêu cầu không thể bắt đầu tại Kho tri thức vì không có search/chọn source. Tester phải quay lại Groups, search, detail và bấm Học nguồn này. Preview/confirm an toàn; worker đồng bộ **26** message và index **12** item. Counts của test group sau refresh tăng 264→276 message và 245→257 vector, nhưng không có job mới để theo dõi và timestamp học vẫn cũ.

Đề xuất: search + row checkbox/quick action trong Knowledge; action trả `job_id`; drawer realtime hiển thị sync/index/error/result.

### Flow 3 — Học đồng loạt

**Kết quả: PARTIAL — dừng an toàn ở Cancel.**

CTA global tạo preview cho đúng **200 nguồn** còn thiếu và PendingAction; Cancel thành công. Không thể “lọc chưa học → chọn tất cả theo filter” vì bảng không có selection. Nếu xác nhận, user cũng chưa có queue estimate/batch breakdown ngay trong preview.

Đề xuất: selection summary sticky: “198 Not learned + 2 No content”, ước tính message/vector/cost/time, skip nguồn đang queued/running.

### Flow 4 — Xử lý Failed/No Content

**Kết quả: PARTIAL.**

Filter No Content trả đúng 2 nguồn, mỗi dòng giải thích có thể trống/không đọc lịch sử/chỉ media. CSV xuất đủ 298. Không có Failed hiện tại nên retry lỗi không thể live-test; UI cũng không có retry/owner note riêng cho source.

Đề xuất: action menu “Sync lại / Học lại / Thêm ghi chú / Mở quyền Telegram”, phân biệt lỗi quyền, nguồn trống và lỗi worker.

### Flow 5 — Dọn dẹp group không hoạt động

**Kết quả: MISSING.**

Không có filter, recommendation, confidence, Ignore, Luôn giữ hoặc preview leave. DB cho thấy 31 nguồn có message cuối quá 60 ngày nhưng 197 nguồn chưa có message; dùng dữ liệu hiện tại để tự đề xuất rời sẽ có false positive rất cao.

### Flow 6 — Xóa dữ liệu học

**Kết quả: MISSING.** Phần thực thi phá hủy bị BLOCKED theo nguyên tắc an toàn.

Không có nút, scope selector hoặc impact preview. Không thực hiện bất kỳ xóa thật nào. Đây là flow cần thiết trước khi owner có thể quản lý lifecycle và quyền riêng tư dữ liệu.

### Flow 7 — Local model

**Kết quả: PARTIAL.**

Mở cards, nhận biết ngay chat/embedding active, xem metadata và preview xóa `glm-5:cloud`; ESC đóng modal và PendingAction được Cancel; model vẫn tồn tại. Không có filter, machine fit, context, resource estimate, progress/cancel pull hoặc group usage. Không tải/activate model lớn để tránh thay đổi runtime.

### Flow 8 — Đọc tài liệu

**Kết quả: MISSING.**

Mở Tài liệu & Hệ thống được nhưng năm row không tương tác bằng chuột hay bàn phím. Không có popup, nội dung, TOC, link, copy, full-page hoặc ESC/focus trap để kiểm tra.

## 5. Gap Analysis bắt buộc

| Chức năng | Đã có? | Backend API | Frontend kết nối | Còn thiếu | Rủi ro | Ưu tiên |
|---|---|---|---|---|---|---|
| Sort bảng | Chưa | Chưa có sort params; chỉ order cố định | Chưa | Sort name/type/status/message/vector/activity toàn dataset, persist state | Owner ra quyết định trên thứ tự không kiểm soát | P0 |
| Saved view & tùy chỉnh dashboard | Chưa | Chưa có owner preference API | Chưa | Saved filter/sort/columns, widget layout, density, reset | Quản trị 298 nguồn tốn thao tác lặp | P1 |
| Lọc group không hoạt động | Chưa | Chưa | Chưa | 7/30/60/90/custom, last message/sync/count/interaction | False positive nếu nhầm chưa sync với inactive | P0 |
| Đề xuất thoát group/channel | Chưa | Chưa | Chưa | Reason, confidence, Ignore, Luôn giữ | Rời nhầm nguồn quan trọng | P0 |
| Rời group/channel dashboard | Chưa | Chưa | Chưa | Impact preview, 4 data options, owner/admin guard, worker/audit/SSE | Critical: rời nhầm hoặc mất quyền truy cập | P0 |
| Học từng nguồn | Có một phần | Generic group PendingAction có | Chỉ ở detail group | Search/chọn tại Knowledge, job/progress và result timestamp | User tưởng đã học nhưng không biết kết quả | P0 |
| Học hàng loạt | Có một phần | Global enable-all action có | CTA global có | Checkbox, page/filter selection, subset pause/resume, estimate | Tác động 200 nguồn thiếu kiểm soát chi tiết | P0 |
| Xóa dữ liệu đã học | Chưa | Chưa | Chưa | 6 scopes, impact, double confirm, recoverability, orphan cleanup | High/Critical nếu xóa sai | P0 |
| Popup tài liệu | Chưa | Không có docs content API | Row tĩnh | Drawer/popup, TOC, link, copy, full page, keyboard | Người mới phải tự tìm Markdown | P1 |
| Local model cards | Có một phần | Inventory/pull/activate/delete-preview có | Có card và active selector | Capabilities, resources, fit, filter, pull progress/cancel, groups | Chọn model không phù hợp hoặc đổi embedding thiếu hiểu biết | P1 |
| Mobile usability | Có một phần | Không phụ thuộc API | 390 tốt | Sửa 360 overflow, 1024 overflow, touch target 44 px, card expansion | Cắt action và thao tác nhầm | P0 |

## 6. Responsive và accessibility

| Viewport | Kết quả | Quan sát |
|---|---|---|
| 1440×1000 | PASS | Không overflow; hierarchy rõ; bảng rộng nhưng dùng được. |
| 1024×768 | BUG | Groups có document scrollWidth 1239; sidebar + table vượt viewport. |
| 390×844 | PARTIAL | Không overflow toàn trang; menu hoạt động; group table giảm còn 4 cột; model/docs cards vừa viewport. |
| 360×800 | BUG | body scrollWidth 374; nút header PendingAction bị cắt. |

Accessibility đã kiểm tra:

- Form và switches có accessible name; status có text, không chỉ màu.
- Dialog có `role=dialog`, focus ban đầu vào Close và ESC đóng.
- Dialog model nằm trọn trong 390×844.
- Chưa xác minh đầy đủ vòng focus trap bằng Tab/Shift+Tab trên mọi modal.
- Một số tab/button thấp hơn 44 px; cần tăng touch target.
- Docs row không focusable nên không thể sử dụng bàn phím.

## 7. Danh sách lỗi theo mức độ

### Critical

Không phát hiện lỗi Critical trong chức năng hiện có. Các flow leave/delete chưa tồn tại nên guard Critical của chúng bị BLOCKED, không được coi là đã an toàn.

### High

| ID | Vấn đề | Bằng chứng | Tác động |
|---|---|---|---|
| V2-H01 | Unified brain mới bao phủ 98/298 nguồn | MySQL/API: 98 Learned, 198 Not learned, 2 No content; chỉ 100 AUTO_KNOWLEDGE | Mục tiêu cốt lõi “tất cả nguồn được cấp quyền” chưa đạt. |

### Medium

| ID | Vấn đề | Tái hiện/kết quả |
|---|---|---|
| V2-M01 | Sort/filter/page customization thiếu ở backend và frontend | Mở Groups/Knowledge: header không sort; page size cố định; API không có sort params. |
| V2-M02 | Học nguồn thành công nhưng không có progress/job mới | Confirm test group; action Executed sync 26/index 12; `last_job_id` vẫn job 24/7. |
| V2-M03 | Laptop 1024 px tràn ngang | Groups `clientWidth=1024`, `scrollWidth=1239`. |
| V2-M04 | Mobile 360 px tràn và cắt header | viewport 360, body 374. |
| V2-M05 | `/ask` chờ 16 giây không ack | 430123 lúc 14:40:19, reply 430124 lúc 14:40:35. |
| V2-M06 | Worker từng có exception gần phiên test | Log có `DetachedInstanceError<KnowledgeSource>` và asyncmy network error khoảng 14:03; hiện đã tự phục hồi. |
| V2-M07 | Runtime RAM tăng mạnh sau semantic ask | Khoảng 665 MB trước, 1.596 GB sau; lần đo sau ổn định nhưng cần profile. |
| V2-M08 | Knowledge bulk chỉ global, không theo filter/selection | Preview 200 nguồn có; không có checkbox/select all filtered. |

### Low

| ID | Vấn đề | Tái hiện/kết quả |
|---|---|---|
| V2-L01 | Citation đơn mang số `[S9]` | Scoped reply chỉ có một source nhưng numbering không renumber. |
| V2-L02 | Modal title còn tiếng Anh kỹ thuật | “Set Chat Permission”, “Enable Group Learning”, “Delete Ollama Model”. |
| V2-L03 | Touch target nhỏ | Filter tabs ~29 px, Manage ~34 px, menu width ~31 px. |
| V2-L04 | 390 mobile ẩn nhiều cột mà không có expand detail ngay row | Chỉ còn source/policy/learning/action. |
| V2-L05 | Thuật ngữ vận hành còn dày | RAG, vector, PendingAction, INHERIT, SOURCE OF TRUTH. |

## 8. Danh sách tính năng đang thiếu

1. Server-side sort và persistence.
2. Saved views, column chooser, density, widget customization và reset.
3. Filter type/policy/learning/activity đầy đủ cho Groups.
4. Inactivity model, cleanup recommendation, Ignore và Luôn giữ.
5. Leave group/channel end-to-end với impact và guard.
6. Search/selection/bulk theo filter trong Knowledge.
7. Per-source retry và owner note.
8. Delete learned data theo 6 phạm vi, double confirmation và orphan cleanup.
9. Docs reader popup/drawer và tài liệu hướng theo task.
10. Local model capability/resource/fit/usage metadata, filter và pull progress/cancel.
11. Responsive fix cho 1024 và 360.

## 9. Đề xuất thiết kế cụ thể

### Groups

- Một toolbar thống nhất: Search, Type, Policy, AI, Learning, Activity, Saved view, Columns.
- Header sort có ba trạng thái: none/asc/desc; query string là source of truth.
- Row click mở detail drawer; CTA nguy hiểm nằm trong “More”, tách màu và có mô tả.
- Quick filters: “Chưa học”, “No content”, “Không hoạt động 60 ngày”, “Tôi là admin”, “Không sync gần đây”.

### Knowledge

- Sticky selection bar: số source, tổng message/vector, estimate time/cost và xung đột queued/running.
- Click source mở drawer gồm status timeline, permission, last sync, last learning job, error, retry và note.
- Sau confirm, chuyển trực tiếp sang progress drawer và dùng SSE cập nhật từng phase.

### Inactivity/Leave

- Score phải dựa trên message age, sync coverage, interaction, role admin/owner và pattern đăng định kỳ.
- Mặc định “Không đề xuất rời” nếu thiếu lịch sử hoặc sync stale.
- Preview hiển thị rõ data giữ lại/xóa, recoverability và quyền AI dùng dữ liệu cũ.

### Docs

- Điều hướng theo câu hỏi: “Bắt đầu”, “Cấp quyền”, “Học dữ liệu”, “Hỏi AI”, “Local model”, “An toàn dữ liệu”.
- Drawer có TOC, search trong tài liệu, copy link, open full page, ESC và focus trap.

### Local Models

- Nhóm Chat / Embedding / Multimodal.
- Badge “Phù hợp máy / Có thể chậm / Không khuyến nghị”, giải thích bằng RAM/VRAM và context.
- Pull job có progress bytes, speed, ETA, cancel và disk impact.

## 10. Thứ tự triển khai đề xuất

1. **P0 — Core coverage:** hoàn tất/giải thích 298 nguồn; selection/batch learning và progress job đáng tin cậy.
2. **P0 — Data lifecycle safety:** delete learned data và leave flow với impact/guard/audit.
3. **P0 — Owner scale:** server-side sort, filters, page size; sửa overflow 1024/360.
4. **P0 — Reliability:** sửa detached ORM errors, đo RAM vector load và ack `/ask`.
5. **P1 — Personalization:** saved views, columns, density, dashboard widgets.
6. **P1 — Docs:** in-app reader và task-based content.
7. **P1 — Local model UX:** capability/fit/progress/filter.

## 11. Quick wins

- Renumber citation sau khi lọc để một source luôn là S1.
- Gửi ack `/ask` ngay khi bắt đầu.
- Việt hóa ba action title trong modal.
- Thêm page-size select vì backend đã hỗ trợ.
- Thêm search param cho Knowledge và reuse search component Groups.
- Tăng min-height/min-width button lên 44 px.
- Chuyển sidebar sang mobile mode sớm hơn 1200 px để giải quyết laptop.
- Hiển thị “Lần refresh gần nhất” riêng với “Lần học full gần nhất”.
- Link hero “Học là gì?” tới docs drawer.
- Thêm badge counts vào status filter.

## 12. Thao tác bị BLOCKED vì có thể phá hủy dữ liệu

- Rời group/channel thật.
- Xóa message Telegram, MySQL, vector, media, knowledge card hoặc memory.
- Xác nhận preview xóa model Ollama.
- Download model lớn hoặc dừng một download thật.
- Đổi active chat/embedding model hoặc provider AI.
- Xác nhận bulk learning 200 nguồn.
- Auto-delete moderation thật.
- Live-test nguồn BLOCK bằng cách đổi policy nguồn thật.
- Full focus loop của docs popup vì popup chưa tồn tại.

Các preview model/bulk/permission đã được Cancel. Học test group là thao tác không phá hủy trên nguồn test được chỉ định và đã được xác nhận để kiểm tra worker.

## 13. Ảnh và bằng chứng

- [Overview desktop](output/playwright/uat-v2-2026-07-28/.playwright-cli/page-2026-07-28T07-28-18-126Z.png)
- [Bulk learning preview 200 nguồn](output/playwright/uat-v2-2026-07-28/.playwright-cli/page-2026-07-28T07-29-22-322Z.png)
- [Groups laptop 1024 overflow](output/playwright/uat-v2-2026-07-28/.playwright-cli/page-2026-07-28T07-30-51-542Z.png)
- [Groups mobile 390](output/playwright/uat-v2-2026-07-28/.playwright-cli/page-2026-07-28T07-31-02-532Z.png)
- [Groups mobile nhỏ 360](output/playwright/uat-v2-2026-07-28/.playwright-cli/page-2026-07-28T07-31-12-497Z.png)
- [Local Models mobile](output/playwright/uat-v2-2026-07-28/.playwright-cli/page-2026-07-28T07-31-32-842Z.png)
- [Model delete preview mobile](output/playwright/uat-v2-2026-07-28/.playwright-cli/page-2026-07-28T07-31-46-767Z.png)
- [Knowledge No Content](output/playwright/uat-v2-2026-07-28/.playwright-cli/page-2026-07-28T07-40-47-449Z.png)
- [Docs desktop — rows tĩnh](output/playwright/uat-v2-2026-07-28/.playwright-cli/page-2026-07-28T07-41-16-793Z.png)
- [Docs mobile 390](output/playwright/uat-v2-2026-07-28/.playwright-cli/page-2026-07-28T07-41-25-545Z.png)
- [CSV đủ 298 nguồn](output/playwright/uat-v2-2026-07-28/.playwright-cli/telegram-learning-sources-2026-07-28.csv)

## 14. So sánh với báo cáo UAT trước

| Hạng mục V1 | V2 | Kết quả |
|---|---|---|
| `/ask in:` trộn nguồn ngoài scope | Chỉ dùng Coin68 đúng chat ID | **FIXED** |
| Appendix có S8–S10 không được body dùng | Chỉ append source được dùng | **FIXED** |
| Search `@username` trả 0 | `@ThuanCapital` hoạt động | **FIXED** |
| Filter Learned rỗng vì dùng sai status | Learned trả dữ liệu | **FIXED** |
| Completed job vẫn có Retry | Không còn Retry | **FIXED** |
| CSV chỉ 50 dòng | CSV 298 dòng | **FIXED** |
| Storage meter 45/65/35 và donut 72% hard-code | Chỉ hiển thị bytes/growth thật | **FIXED** |
| Permission chỉ là raw code | Có mô tả tiếng Việt cho 19 quyền | **FIXED** |
| Không giải thích learning | Có giải thích sync/clean/embedding, không fine-tune | **FIXED** |
| Không xem action lịch sử | Nút Xem hoạt động với Cancelled/Executed | **FIXED** |
| Login chỉ dẫn lệnh không chạy | Hiển thị đúng lệnh venv | **FIXED** |
| Không thấy VRAM | Hiển thị 0 B | **FIXED** |
| Mobile 390 tràn bảng 699 px | Không global overflow, table mobile 4 cột | **FIXED/PARTIAL** |
| Group `/ask` mất 352 giây | Scoped private `/ask` 16 giây | **IMPROVED**, chưa có ack |
| Coverage 98/298 | Vẫn 98/298 | **NOT FIXED** |
| Dashboard customization/sort/inactivity/leave/delete/docs | Chưa có | **NOT FIXED/NEW SCOPE CONFIRMED** |

V2 giải quyết phần lớn lỗi chất lượng cụ thể của V1, đặc biệt là contract phạm vi AI. Khoảng cách còn lại hiện thiên về product completeness, data lifecycle và owner-scale UX hơn là lỗi truy xuất cơ bản.

## 15. Hoàn nguyên và kết luận cuối

### Trạng thái cuối phiên

- 0 PendingAction đang chờ.
- Permission test `auto_task_suggestion` vẫn OFF.
- 200 learning jobs đều Completed.
- Provider vẫn OpenAI.
- `qwen3:8b` và `nomic-embed-text:latest` vẫn active.
- `glm-5:cloud` vẫn tồn tại; không model nào bị xóa.
- Bulk learning 200 nguồn và model delete đã Cancel.
- Học test group Executed: sync 26, index 12; không xóa dữ liệu.
- Dashboard session được logout sau khi thu thập checkpoint.
- Không còn file Telegram session tạm của tester.
- Có một test message scoped và reply trong private control bot (430123–430124).
- Một message thử nhầm vào Saved Messages (430121) được giữ nguyên vì quy tắc cấm xóa Telegram thật; không dùng làm bằng chứng sản phẩm.

### Kết luận

**NOT READY.**

Sản phẩm hiện đã đủ tốt để owner kỹ thuật theo dõi hệ thống, search group, quản lý quyền, chạy preview an toàn và hỏi AI có dẫn nguồn đúng phạm vi. Nó **chưa đủ dễ dùng và chưa đủ hoàn chỉnh cho người dùng bình thường** vì 6/8 hành trình mục tiêu chỉ đạt một phần hoặc chưa tồn tại; đặc biệt là owner-scale sort/filter/bulk, inactivity/leave, xóa dữ liệu đã học, in-app documentation và learning progress.

Điều kiện tối thiểu để chuyển sang **READY WITH CONDITIONS**:

1. Có progress/job đáng tin cậy cho học từng nguồn và bulk theo selection/filter.
2. Có server-side sort/filter/page controls dùng được với 298 nguồn.
3. Có spec/API/UI an toàn cho leave và delete learned data, ít nhất đến Preview/Cancel.
4. Sửa overflow 1024/360 và touch targets.
5. Có docs reader tối thiểu cho onboarding, permissions, learning, `/ask`, modes, retention, models và PendingAction.
6. Điều tra worker exception và RAM spike; thêm ack `/ask`.
