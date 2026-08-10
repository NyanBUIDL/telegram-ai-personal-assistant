# USER ACCEPTANCE TEST REPORT V3 — Telegram AI Personal Assistant

**Ngày kiểm thử:** 29/07/2026  
**Khung giờ chính:** 14:20–14:45 ICT (07:20–07:45 UTC)  
**Vai trò:** User Acceptance Tester, Product QA và người dùng mới độc lập  
**Môi trường:** local production-like tại `http://127.0.0.1:8765`; Admin API, MySQL, Qdrant, Ollama và worker thật  
**Viewport:** 1440×1000, 1024×768, 390×844, 360×800  
**Nguyên tắc:** không sửa code; không đọc/ghi secret; không đổi provider/model; không reindex toàn kho; hành động phá hủy chỉ đến Preview/PendingAction rồi hủy  
**Kết luận phát hành:** **NOT READY**

## 1. Kết luận

V3 cải thiện mạnh phần vận hành: directory 298 nguồn đã có search/filter/sort/page-size/saved view/density/cột tùy chọn; chi tiết nguồn có AI mode, local-first preset, quota/retention; các flow BLOCK, leave, xóa dữ liệu và xóa model đều có impact preview; Knowledge có search, bulk selection, CSV, job card; Docs có reader responsive; PendingAction, Audit, Scheduler, SSE và CSRF guard đều hoạt động.

Tuy nhiên sản phẩm chưa sẵn sàng phát hành vì một lỗi toàn vẹn tri thức xảy ra ngay trong journey cốt lõi:

- Trước khi “Học nguồn này”, group test có **276 tin MySQL / 257 vector**.
- Job mới `8ad9c0cf-cf90-449a-b92c-0ab0badebb44` kết thúc sau khoảng 2 giây và UI báo **Hoàn tất 100%**.
- Cùng card lại hiển thị **Processed / total = 41 / 266**, **Tin đã sync = 0**, **Vector đã tạo = 41**.
- Sau job, MySQL vẫn **276** nhưng vector còn **41**. Reload và API chi tiết nguồn xác nhận đây là trạng thái thật, không phải lỗi render.

Như vậy một refresh incremental có thể làm giảm độ phủ vector khoảng **84%** (`257 → 41`) trong khi sản phẩm tuyên bố thành công 100%. Điều này trực tiếp đe dọa khả năng truy xuất đúng/ngữ cảnh, chống trùng và niềm tin của owner. Không thể hoàn nguyên trong UAT vì cần reindex hoặc can thiệp dữ liệu, đều nằm ngoài quyền được cấp.

### Điều kiện bắt buộc trước khi release

1. Sửa và có regression test cho refresh incremental: không được thay thế bộ vector hợp lệ bằng tập con nếu job chỉ xử lý một phần.
2. Chỉ báo `Completed 100%` khi invariant được thỏa: processed bằng total hoặc có giải thích rõ số filtered/duplicate/reused/skipped.
3. Thêm reconciliation MySQL ↔ vector, cảnh báo khi vector giảm bất thường và đường khôi phục an toàn cho nguồn bị ảnh hưởng.
4. Chạy lại đầy đủ `/ask` trong group test sau khi khôi phục vector để xác nhận scope, citation và không bịa.

## 2. Tóm tắt theo góc nhìn người dùng

### Điều người dùng làm được tốt

- Login bằng mã 8 số rõ ràng; reload giữ phiên; logout đóng phiên đúng.
- 30 giây đầu có thể thấy 298 nguồn, số tin/vector, pending, worker, RAM/VRAM/storage và trạng thái LIVE.
- Search tên, `@username`, Chat ID; filter/sort/tabs chạy server-side và trả dữ liệu hợp lý.
- Source detail giải thích tốt “học” là sync MySQL + làm sạch + embedding, không phải fine-tune.
- Local-first copy nói rõ không gửi toàn bộ lịch sử lên cloud; embedding mặc định là Ollama local.
- Hành động nhạy cảm cho biết phạm vi, số tin/vector/file, cảnh báo và cho phép Cancel.
- Docs reader dễ đọc, không tràn ở 390 px, ESC đóng và trả focus đúng.
- CSV xuất đủ 298 nguồn.
- PendingAction và Audit tạo chuỗi sự kiện create/confirm/cancel dễ kiểm tra.

### Điều khiến người dùng chưa thể tin cậy

- Job học có thể làm mất phần lớn vector nhưng vẫn xanh và báo “100%”.
- Không có số filtered/duplicate/reused/skipped, nên owner không biết 41/266 là hợp lệ hay dữ liệu bị bỏ sót.
- Token telemetry tách `LOCAL/CLOUD/LEGACY` nhưng phần tổng “1 local · 0 cloud” không bao quát 162 request gắn `LEGACY`; người dùng dễ hiểu sai mức sử dụng cloud/lịch sử chi phí.
- Khi request ghi bị CSRF 403, backend chặn đúng nhưng UI đổi trạng thái optimistic, không báo lỗi dễ hiểu; phải reload mới trở lại trạng thái server.
- Menu mobile không đóng bằng ESC; nhiều touch target dưới 44 px.
- Pull model sai trả thông báo chung “HTTP 500”, không cho biết tên model không tồn tại/không hợp lệ hay cách sửa.
- `/ask` live không được chạy lại trong V3: dashboard không có send surface; tự dùng Telegram session hoặc tự động hóa cửa sổ Telegram đang mở sẽ vi phạm ranh giới không đọc session/không đụng chat ngoài test. Kết quả live V2 chỉ được dùng làm tham khảo, không được tính là PASS mới.

### Điểm UX 1–5

| Hạng mục | Điểm | Nhận xét |
|---|---:|---|
| Dễ hiểu với người không kỹ thuật | 3.5 | Copy tốt hơn rõ rệt; telemetry/job vẫn đòi hỏi kiến thức kỹ thuật. |
| Phân biệt an toàn và nguy hiểm | 4.5 | Preview/impact/cancel tốt; active model bị khóa xóa. |
| Hiểu trạng thái học | 2.0 | `100%` mâu thuẫn `41/266` và vector giảm mạnh. |
| Hiểu Local/Cloud và chi phí | 3.0 | Có nhãn LOCAL/LEGACY, nhưng tổng hợp không khớp các dòng telemetry. |
| Hiểu lỗi và cách khôi phục | 2.5 | 403 và pull model chỉ để lại lỗi chung; không có recovery cho vector giảm. |
| Tốc độ thao tác thường dùng | 4.0 | Search/filter/sort/page-size nhanh; saved view thiếu delete/rename. |
| Responsive | 4.0 | Không overflow page ở 1024/390/360; menu và touch target còn lỗi. |
| Accessibility | 3.0 | Modal/focus/label khá tốt; ESC menu và kích thước target chưa đạt. |

## 3. Bảng test case

| ID | User journey | Thao tác | Kỳ vọng | Kết quả thực tế | Trạng thái | Severity | Bằng chứng |
|---|---|---|---|---|---|---|---|
| A-01 | Login owner | Mở dashboard, dùng mã 8 số do CLI cấp | Vào được dashboard, không yêu cầu secret khác | Login thành công; form nhắc không nhập API key/OTP/password | PASS | — | `01-login-1440x1000.png`, URL `/` |
| A-02 | Giữ phiên | Reload sau login | Phiên còn hiệu lực khi backend chưa restart | Reload vẫn ở dashboard; `/auth/session` trả 200 | PASS | — | `02-overview-1440x1000.png` |
| A-03 | 401 | Truy cập session/overview khi chưa login và sau logout | Trả 401, UI về login | Cả session và overview trả 401; login form hiển thị | PASS | — | Browser/API check 14:45 ICT |
| A-04 | CSRF | Gửi lại chính preferences hiện tại nhưng bỏ `X-CSRF-Token` | Backend chặn 403; UI giải thích và rollback | Backend trả 403 đúng; khi gây lỗi qua thao tác density, UI vẫn đổi sang “Gọn”, không toast, console có unhandled error; reload mới khôi phục | FAIL | P1 | Console `403 /api/v1/preferences`; trạng thái server sau reload |
| A-05 | Secret boundary | Rà UI login, connection, AI, security, docs; tìm từ khóa secret | Không render API key, Telegram token, password, session | Không phát hiện secret; Connections ghi “Secret không hiển thị”; Security mô tả write-only secret | PASS | — | `03-connections-1440x1000.png`, trang Bảo mật |
| A-06 | Logout | Bấm Đăng xuất rồi gọi session/overview | Không còn truy cập dashboard/API | Login page hiện; cả hai API trả 401 | PASS | — | Browser/API check 14:45 ICT |
| B-01 | Tổng quan | Đọc KPI trong 30 giây đầu | Nắm được nguồn, message, `/ask`, pending, health, tài nguyên | 298 nguồn; khoảng 546–549 tin/24h; `/ask` 0; pending 0; lỗi/blocked 0; RAM khoảng 404 MB; MySQL 1.1 GB; vector 1.0 GB; VRAM 0 B | PASS | — | `02-overview-1440x1000.png` |
| B-02 | Kết nối | Mở Connections | Thấy health backend và service thật | Admin API ONLINE; MySQL 298; Qdrant 1.0 GB; provider/model và embedding hiển thị; SSE LIVE | PASS | — | `03-connections-1440x1000.png` |
| B-03 | SSE | Tạo preview BLOCK rồi quan sát badge pending không reload | Badge cập nhật realtime | Pending đổi 0→1 ngay, sau Cancel về 0 | PASS | — | `08-preview-block.png`; quan sát live sidebar |
| B-04 | Scheduler/worker | Mở Scheduler & Workers | Trạng thái, queue, job, RAM/CPU/VRAM hợp lý | Scheduler connected; 6 scheduled job; queue 0; RAM 386 MB; CPU 0%; VRAM 0 B | PASS | — | `17-workers.png` |
| B-05 | LIVE/OFFLINE/STALE | Đối chiếu status với API đang chạy và timestamp mới | LIVE khi backend đáp ứng; không tuyên bố stale sai | LIVE và timestamp cập nhật đúng khi backend online; không dừng backend để mô phỏng offline | PASS | — | Overview, Connections, Workers |
| C-01 | Search directory | Tìm `Coin68`, `@coin68`, Chat ID `-1001300523532` | Match tên/username/ID đúng | Lần lượt 34, 3 và đúng 1 nguồn Coin68 | PASS | — | `04-groups-1440x1000.png`; browser query evidence |
| C-02 | Filter/sort/page-size | Channel + ALLOW + Learned; sort tên tăng; 25 dòng | Server trả đúng tập và thứ tự | 51 nguồn, 25 dòng trang đầu; bắt đầu “5 Phút Crypto \| Channel” | PASS | — | `06-groups-filters-sort-pagesize.png` |
| C-03 | Tabs | Kiểm tra AI on, permission/no AI, inactivity, all | Count và nội dung tab hợp lý | AI on 2; permission/no AI 296; inactivity 22 có lý do/tuổi/tin và “Luôn giữ”; all 298 | PASS | — | Directory live |
| C-04 | Saved view | Tạo `UAT V3 TEMP`, reload preferences | View lưu và áp dụng; có thể quản lý | View lưu được nhưng không có delete/rename/reset rõ ràng; đã xóa qua preferences API trong cleanup | PASS WITH UX ISSUE | P3 | Preferences audit; `saved_views` cuối = 0 |
| C-05 | Density/columns | Thoáng→Gọn→Thoáng; ẩn/hiện Policy | Row/cột đổi đúng và lưu | Row cao 73→59→73; Policy ẩn/hiện đúng; cuối về default | PASS | — | Directory live |
| C-06 | Column chooser | Mở chooser rồi bấm “Quản lý” phía sau | Menu đóng hoặc không chặn thao tác ngoài dự kiến | Popover còn mở và intercept pointer; phải đóng thủ công | FAIL | P3 | Playwright click timeout/overlay intercept |
| C-07 | Responsive | Kiểm tra 1440, 1024, 390, 360 | Không page overflow/mất CTA/modal | `scrollWidth <= innerWidth` ở cả bốn; CTA/menu chính còn truy cập | PASS | — | `04-groups-1440x1000.png`, `20-knowledge-tablet-1024.png`, `21-knowledge-mobile-390.png` |
| C-08 | Mobile menu | Mở drawer ở 390/360; thử ESC | Drawer vừa màn hình và ESC đóng | Drawer vừa chiều ngang, có overlay close; ESC không đóng ở 360 | FAIL | P2 | `22-mobile-menu-390.png`, `23-mobile-menu-escape-fails-360.png` |
| C-09 | Touch targets | Đo control visible ở 360 | Target chính tối thiểu khoảng 44×44 | 56/118 control có một chiều <44; table/card toggle cao 38; search cao 25; checkbox 20×20 | FAIL | P2 | Browser geometry check 360×800 |
| D-01 | Channel đã học | Mở Coin68 | Thấy rights, policy, trạng thái và số liệu | ALLOW; channel; 1.011 MySQL; 1.009 vector; 474 KB; lần học 5 ngày trước | PASS | — | `07-source-detail-coin68.png` |
| D-02 | Group đã học + `/ask` | Mở Hội cựu chiến binh C-sủi | Group learned và `/ask` rõ ràng | Learned; `/ask` ON; 1.156 MySQL; 1.003 vector | PASS | — | `11-source-detail-learned-group-ask.png` |
| D-03 | Nguồn chưa học | Mở Coin68 Flash News | Phân biệt chưa học với lỗi/rỗng | “CHƯA HỌC”; 0 MySQL; 0 vector; không có job | PASS | — | Source detail live |
| D-04 | AI mode | Kiểm tra các lựa chọn trên source detail | Có inherit/local-only/local-first/cloud-only/cloud-first/off và fallback | Đủ lựa chọn; provider ưu tiên OpenAI/OpenRouter và fallback switch | PASS | — | `07-source-detail-coin68.png` |
| D-05 | Local-first tuning | Kiểm tra Saving/Balanced/Quality, filtering, top_k, token | Người dùng hiểu preset và dữ liệu cloud | Có preset; filtering; `RAG top_k`; context token; copy nói embedding Ollama và chỉ gửi đoạn RAG liên quan | PASS | — | `07-source-detail-coin68.png` |
| D-06 | Retention/quota | Đọc ngày/tin/vector/storage | Phạm vi và đơn vị rõ | Có retention days, message, vector, storage; đủ để hiểu giới hạn nguồn | PASS | — | `07-source-detail-coin68.png` |
| D-07 | BLOCK preview | Preview BLOCK Coin68 rồi Cancel | Hiện source/scope/warning, không thực thi | Preview đúng; pending realtime; Cancel thành công | PASS | — | `08-preview-block.png` |
| D-08 | Leave preview | Preview leave Coin68 rồi Cancel | Nêu dữ liệu giữ lại và quyền cuối | Nêu giữ 1.011 tin/1.009 vector; cảnh báo recheck quyền; Cancel | PASS | — | `09-preview-leave.png` |
| D-09 | Delete preview | Preview vectors-only và all; Cancel | Nêu count, scope, tính không thể hoàn tác; all cần confirm tên | Vectors-only nêu 1.011/1.009/0; all bị khóa đến khi nhập đúng “Coin68”; cả hai Cancel | PASS | — | `10-preview-delete-vectors.png`, Pending history |
| E-01 | Embedding local | Mở AI & RAG và Local Models | Embedding Ollama local và active rõ | `nomic-embed-text:latest` hiện LOCAL/EMBED ACTIVE | PASS | — | `14-ai-rag-telemetry.png`, `15-local-models.png` |
| E-02 | Token theo công năng | Đọc 24h usage | Có input/output/cost/error và công năng | 32.691 token; 162 embedding request/30.566 token; 1 AI answer/2.125 token; 0 lỗi | PASS | — | `14-ai-rag-telemetry.png` |
| E-03 | LOCAL/CLOUD/LEGACY | So summary với provider/model rows | Tổng và chi tiết nhất quán, nhãn rõ | Rows phân biệt LEGACY/LOCAL; nhưng summary “1 local · 0 cloud” không phản ánh 162 legacy request | FAIL | P2 | `14-ai-rag-telemetry.png` |
| E-04 | Token theo nguồn | Kiểm tra source telemetry | Có thể truy ngược nguồn tiêu tốn | Hiển thị Chat `-4006978081` với 3.498 token/1 request/$0 | PASS | — | `14-ai-rag-telemetry.png` |
| E-05 | Chống trùng | Tìm filtered/duplicate/reused/skipped trong job/detail | Owner thấy dữ liệu mới, trùng, tái sử dụng | Không có các số liệu này; không chứng minh được incremental/dedup từ UI | FAIL | P1 | `13-knowledge-learning-job.png` |
| F-01 | Tạo learning job | Trên group test: Học nguồn→Preview→Confirm | Có preview và tạo job riêng | PendingAction `4daf…` được confirm; job `8ad9…` được tạo | PASS | — | `12-learning-preview-test-group.png`, Audit |
| F-02 | Chuyển trạng thái | Quan sát queued→processing→completed | Các phase phản ánh worker thật | Thấy ĐANG CHỜ rồi job hoàn tất; job quá nhanh để quan sát syncing/embedding riêng | PASS WITH LIMITATION | — | SSE/Knowledge live |
| F-03 | Progress truthfulness | So % với processed/total | Không báo 100% khi xử lý chưa hết | “Hoàn tất 100%” đồng thời “41/266” | FAIL | P0 | `13-knowledge-learning-job.png`, `13-knowledge-after-learning.png` |
| F-04 | Count sau learning | So MySQL/vector trước và sau | Append/reuse hợp lý; không mất coverage | MySQL 276→276; vector **257→41** | FAIL | P0 | API source detail sau reload; `13-knowledge-after-learning.png` |
| F-05 | Pause/resume/retry | Thử khi trạng thái cho phép | Control chỉ hiện/hoạt động hợp lệ | Job hoàn tất trong ~2 giây; không có trạng thái đủ lâu; completed card không có retry | BLOCKED | — | Job duration 2 giây |
| F-06 | Bulk learning guard | Bấm “Đưa nguồn còn thiếu…” | Chỉ preview; không chạy 298 nguồn | Preview 200 nguồn, pending `8fb…`; đã Cancel, không tạo bulk job | PASS | — | Pending/Audit; 0 queued/running cuối phiên |
| G-01 | `/ask` chủ đề | Gửi trong owner-approved test group | Trả lời scoped, không bịa | Không chạy lại V3 vì không có send surface an toàn mà không dùng Telegram session/cửa sổ chat ngoài scope | BLOCKED | — | Constraint log; V2 chỉ là tham khảo |
| G-02 | `/ask @username` | Hỏi chủ đề của username trong group test | Sync đúng sender và đúng chat | Không chạy lại vì cùng hạn chế an toàn | BLOCKED | — | — |
| G-03 | `/ask` dữ liệu group | Hỏi dữ liệu của chính group | Chỉ truy xuất group đó | Không chạy lại; cần regression sau khi khôi phục vector | BLOCKED | — | Vector test group hiện chỉ còn 41 |
| G-04 | News/citation 7 ngày | Hỏi tổng hợp tin mới | Có link hoặc source + thời gian | Không chạy lại V3 | BLOCKED | — | — |
| G-05 | CoinGecko | Hỏi giá token nếu cấu hình | Hoạt động độc lập AI và không bịa | Trang chức năng báo AVAILABLE nhưng không gửi live | BLOCKED | — | Trang Chức năng Telegram |
| H-01 | Knowledge inventory | Lọc learned/not learned/error/no content | Trạng thái và count rõ | 98/298 learned; 200 chưa học/không nội dung; trạng thái nguồn riêng có thể lọc | PASS | — | `13-knowledge-after-learning.png` |
| H-02 | CSV export | Xuất toàn bộ | File có đủ 298 nguồn và trường hữu ích | CSV có đúng 298 data row; header gồm chat ID, name, type, username, status, reason, MySQL/content/embedding/last/note | PASS | — | `.playwright-cli/telegram-learning-sources-2026-07-29.csv` |
| H-03 | Docs reader | Mở USER_GUIDE desktop/mobile; ESC | Reader dễ đọc, đóng được, không overflow, focus restore | Đạt ở desktop và 390; ESC đóng; focus về card USER_GUIDE | PASS | — | `18-doc-reader-desktop.png`, `19-doc-reader-mobile-390.png` |
| H-04 | Ý nghĩa delete scope | So vector/index/content/media/all | Copy phân biệt phạm vi và impact | Source detail có scope và double confirm; vectors-only/all rõ; index/content/media cần copy ngắn hơn để người thường phân biệt | PASS WITH UX ISSUE | P3 | `10-preview-delete-vectors.png`, USER_GUIDE reader |
| I-01 | Model cards | Xem model Ollama | Có type/capability/size/active/fit | 8 card; chat/embedding, size, RAM/VRAM, fit, params, quantization; active rõ | PASS | — | `15-local-models.png` |
| I-02 | Delete model | Preview xóa non-active; thử active | Non-active có preview; active bị chặn; Cancel | deepseek preview đúng và Cancel; `qwen3`/embedding active disable nút xóa | PASS | — | `16-local-model-delete-preview.png` |
| I-03 | Activate model | Xem impact với lựa chọn hiện tại; ESC | Không thay model; cho biết reindex impact | Hiện 298 nguồn/75.213 vector; embedding không đổi nên Re-index KHÔNG; ESC đóng và focus restore | PASS | — | Activation impact modal |
| I-04 | Pull/cancel model | Nhập model test không tồn tại | Validation/cancel/download feedback rõ | POST trả HTTP 500 và toast chung “Admin API trả về HTTP 500”; không tạo model/download mới | FAIL | P2 | Console `500 /api/v1/ollama/models/pull`; model test absent |
| J-01 | Pending/Audit | Xem history và lọc pending | History đúng, pending cuối = 0 | 28 action history; create/confirm/cancel có correlation; filter Pending trả 0 | PASS | — | Pending page, Audit page |
| J-02 | Storage cleanup | Chạy dry run rồi Cancel | Không xóa; có impact | 0 expired messages/vectors/orphans/media; ghi “CHƯA XÓA DỮ LIỆU”; preview đã Cancel | PASS | — | Storage live |
| J-03 | Console/network | Kiểm tra errors/warnings | Không có lỗi bất thường ngoài test chủ đích | Sau reload sạch; lỗi 403 và 500 đều do test có chủ đích; 403 còn gây unhandled UI error | PASS WITH ISSUES | P1/P2 | Playwright console |
| J-04 | Cleanup | Đối chiếu pending/jobs/preferences/models/session | Không để artifact hoặc thay đổi cấu hình | Pending 0; queued/running/paused 0; saved view 0; prefs default; model test absent; logout 401; **vector test group chưa thể hoàn nguyên** | FAIL | P0 | Final API check 14:44–14:45 ICT |

## 4. Lỗi chức năng

### V3-P0-01 — Refresh learning làm vector giảm 257 → 41 nhưng job vẫn thành công

**Tác động:** câu hỏi RAG có thể bỏ sót phần lớn nội dung đã từng truy xuất được. Owner không nhận cảnh báo và có thể tiếp tục tin bộ não đã được cập nhật tốt hơn.

**Cách tái hiện:**

1. Mở source `Test Message to 68 Trading Chat Bot`.
2. Ghi nhận 276 MySQL / 257 vector.
3. Chọn “Học nguồn này”.
4. Xem preview và Confirm.
5. Chờ job `8ad9…` hoàn tất.
6. Reload source detail.

**Actual:** 276 MySQL / 41 vector; `last_error = null`; job `Completed 100%`.

**Expected:** vector cũ hợp lệ được reuse/giữ nguyên hoặc thay thế bằng một tập hoàn chỉnh có giải thích; không được âm thầm giảm độ phủ.

### V3-P0-02 — Progress “100%” không trung thực

Card cùng lúc hiển thị:

- `Processed / total: 41 / 266`
- `Tin đã sync: 0`
- `Vector đã tạo: 41`
- `Hoàn tất: 100%`

Nếu 225 record còn lại là filtered/duplicate/reused, UI phải hiển thị và tổng kiểm tra phải cộng khớp. Nếu không, job không được đánh dấu 100%.

### V3-P1-01 — Không có telemetry incremental/dedup/reuse

Không thấy `filtered`, `duplicate`, `reused`, `skipped`, checkpoint hoặc count vector trước/sau trong job detail. Đây là thiếu sót quan trọng vì mục tiêu sản phẩm nhấn mạnh incremental và hạn chế trùng.

### V3-P1-02 — CSRF 403 không có feedback/rollback thân thiện

Backend chặn write đúng. Frontend lại:

- đổi density optimistic từ Thoáng sang Gọn;
- không toast giải thích phiên/CSRF đã stale;
- để unhandled error trong console;
- chỉ trở lại server state sau reload.

Người dùng có thể tưởng thay đổi đã lưu.

### V3-P2-01 — Pull model lỗi 500 chung chung

Tên model test không tồn tại trả HTTP 500. UI không phân biệt invalid model, network/Ollama error hay registry không có model; không có hướng sửa. Model không được cài và không để download active.

### V3-P2-02 — Tổng token Local/Cloud không bao quát LEGACY

Provider/model rows cho thấy 161 request `unknown · text-embedding-3-small · LEGACY`, một request Ollama LOCAL và một request chat LEGACY. Card tổng lại ghi `1 local · 0 cloud`, dễ khiến owner đánh giá sai lịch sử cloud/cost.

## 5. Lỗi UX/UI, responsive và copywriting

### P2

- ESC không đóng mobile navigation drawer ở 360 px.
- 56/118 control visible trên Knowledge mobile có ít nhất một chiều dưới 44 px; đáng chú ý checkbox 20×20, toggle Bảng/Card cao 38 px và search cao 25 px.
- Learning card dùng màu/trạng thái thành công dù số liệu nội bộ mâu thuẫn; không có cảnh báo vector giảm.

### P3

- Column chooser intercept thao tác “Quản lý” phía sau cho đến khi đóng thủ công.
- Saved view tạo được nhưng không có quản lý delete/rename/default rõ ràng.
- Summary “Chưa học / không có nội dung” gộp hai trạng thái có nguyên nhân xử lý khác nhau.
- Các scope delete có trong docs/detail nhưng “vector”, “index”, “content MySQL” vẫn cần microcopy một câu “mất gì/còn gì/khôi phục thế nào”.
- Local Model “Recommended” dựa trên RAM/VRAM ước tính nhưng không giải thích vì sao các cloud manifest vài trăm byte lại được coi là model installed/recommended.

## 6. Rủi ro dữ liệu, quyền riêng tư, Local-first và token

### Rủi ro dữ liệu

- **Critical:** refresh có thể làm giảm vector thật mà không rollback.
- Không có invariant dashboard như `vector_after >= reusable_before + new - removed_by_policy`.
- Không có alert khi `vector_count` giảm mạnh trong khi MySQL count không giảm.
- Không có reconciliation hoặc nút “Khôi phục index từ MySQL” theo từng nguồn qua preview.
- Coverage hiện chỉ 98/298 nguồn learned; 200 nguồn không đóng góp đầy đủ vào bộ não.

### Quyền riêng tư

- Điểm tốt: dashboard loopback; cookie HttpOnly; CSRF; secret không có read endpoint/render; destructive actions qua preview.
- Copy local-first nói rõ không gửi toàn bộ history, chỉ đoạn RAG liên quan.
- Rủi ro: telemetry legacy cho thấy embedding cloud đã từng tồn tại nhưng summary không giúp owner hiểu dữ liệu nào/bao nhiêu đã đi cloud.
- Bulk preview cần nêu rõ provider embedding dự kiến cho từng batch trước khi confirm, không chỉ “MySQL rồi embedding”.

### Chi phí token

- 24h ghi nhận 32.691 token và $0.0075, nhưng 30.566 token thuộc embedding.
- 161 request embedding được gắn `unknown · text-embedding-3-small · LEGACY`; cần migration/backfill attribution để chi phí đáng tin.
- Cần drill-down theo source/job/time window và phân biệt “legacy historical” với “request có thể phát sinh lại”.

## 7. Cải tiến ưu tiên theo tác động người dùng

### Must fix before release

1. Sửa vector replacement/partial indexing; thêm transaction/checkpoint và rollback nếu count/invariant bất thường.
2. Sửa progress: `processed + filtered + duplicate + reused + skipped + failed = total`.
3. Hiển thị trước/sau cho MySQL, vector, reused, new, removed và lý do removed.
4. Thêm alert “vector giảm X%” và chặn success nếu vượt ngưỡng không được owner xác nhận.
5. Khôi phục/reindex riêng source test qua preview sau khi code được sửa; chạy lại `/ask` scope/citation/no-hallucination.
6. Sửa frontend write error handling: toast dễ hiểu, rollback optimistic state, hướng reload/re-login cho 401/403.

### Should fix soon

1. Backfill provider/model attribution và làm tổng LOCAL/CLOUD/LEGACY cộng khớp chi tiết.
2. Validation pull model trước khi tạo download; chuyển lỗi Ollama/registry thành 4xx hoặc message cụ thể.
3. Cho owner xem/cancel download job rõ ràng, progress byte/ETA/error/retry.
4. ESC đóng mobile drawer; nâng touch target lên tối thiểu 44 px.
5. Quản lý saved view: delete/rename/default/reset.
6. Cho popover tự đóng khi click ngoài và không chặn row action ngoài ý muốn.
7. Tách “Chưa học” và “Không có nội dung” trong summary card.
8. Tạo test harness hoặc documented manual UAT flow cho `/ask` trong đúng owner-approved test group, không cần truy cập session.

### Nice to have

1. Timeline learning theo phase với thời gian từng bước và throughput.
2. Health score theo nguồn: freshness, MySQL/vector parity, last successful retrieval.
3. Export job diagnostics và reconciliation CSV.
4. Microcopy “mất gì/còn gì/khôi phục thế nào” cho từng delete scope.
5. Tooltip giải thích machine-fit, cloud manifest và ước tính RAM/VRAM.
6. Automated visual/accessibility regression ở 1440/1024/390/360.

## 8. Hành động test và trạng thái hoàn nguyên

| Hạng mục | Hành động trong UAT | Trạng thái cuối |
|---|---|---|
| Pending BLOCK Coin68 | Tạo preview rồi Cancel | Cancelled |
| Pending leave Coin68 | Tạo preview rồi Cancel | Cancelled |
| Pending delete vectors Coin68 | Tạo preview rồi Cancel | Cancelled |
| Pending delete all Coin68 | Nhập đúng tên để xem impact rồi Cancel | Cancelled |
| Pending delete model `deepseek-v2:16b` | Tạo preview rồi Cancel | Cancelled; model còn nguyên |
| Pending bulk learning 200 nguồn | Tạo preview rồi Cancel | Cancelled; không tạo bulk job |
| Storage cleanup | Chỉ dry run rồi hủy preview | Không xóa dữ liệu |
| Saved view `UAT V3 TEMP` | Tạo để test persistence | Đã xóa; `saved_views = 0` |
| Filter/density/columns/page-size | Thay đổi để test | Trả về All/Thoáng/đủ 6 cột/10 dòng |
| Test model `uat-v3-model:tiny` | Gửi pull với tên không tồn tại | HTTP 500; model không tồn tại; không có active download mới |
| Provider/model | Chỉ đọc và xem impact | Không đổi: OpenAI; embedding `nomic-embed-text:latest`; active local model giữ nguyên |
| Leave/delete/moderation production | Không thực thi | Không có thay đổi |
| Session dashboard | Login, reload, logout | Đã logout; session/overview trả 401 |
| Learning source test | Confirm job `8ad9…` | Job completed; không còn queued/running/paused |
| Dữ liệu group test | 276 MySQL / 257 vector trước job | **276 MySQL / 41 vector sau job; chưa thể hoàn nguyên an toàn** |

### Xác nhận cleanup cuối phiên

- PendingAction đang chờ: **0**
- Learning job queued/running/paused: **0 / 0 / 0**
- Saved view: **0**
- Modal/drawer còn mở: **0**
- Model test tồn tại: **không**
- Provider/model bị đổi: **không**
- Dữ liệu bị xóa có chủ đích: **không**
- Group/channel bị rời: **không**
- Moderation production bị bật: **không**
- Session dashboard: **đã logout**
- Hoàn nguyên dữ liệu nguồn test: **không đạt** do lỗi sản phẩm làm vector giảm; không tự reindex vì trái ràng buộc UAT

## 9. Danh mục bằng chứng

Thư mục: `output/playwright/uat-v3-2026-07-29/`

- `01-login-1440x1000.png`
- `02-overview-1440x1000.png`
- `03-connections-1440x1000.png`
- `04-groups-1440x1000.png`
- `06-groups-filters-sort-pagesize.png`
- `07-source-detail-coin68.png`
- `08-preview-block.png`
- `09-preview-leave.png`
- `10-preview-delete-vectors.png`
- `11-source-detail-learned-group-ask.png`
- `12-learning-preview-test-group.png`
- `13-knowledge-learning-job.png`
- `13-knowledge-after-learning.png`
- `14-ai-rag-telemetry.png`
- `15-local-models.png`
- `16-local-model-delete-preview.png`
- `17-workers.png`
- `18-doc-reader-desktop.png`
- `19-doc-reader-mobile-390.png`
- `20-knowledge-tablet-1024.png`
- `21-knowledge-mobile-390.png`
- `22-mobile-menu-390.png`
- `23-mobile-menu-escape-fails-360.png`
- `.playwright-cli/telegram-learning-sources-2026-07-29.csv`

## 10. Release decision

**NOT READY.**

Các flow quản trị và an toàn đã tiến bộ đáng kể, nhưng sản phẩm chưa thể được tin cậy như “bộ não trợ lý” khi chính thao tác học incremental có thể làm giảm mạnh vector, báo sai 100% và không có recovery. Sau khi sửa P0, cần khôi phục source test, chạy reconciliation toàn bộ nguồn đã học và thực hiện lại regression `/ask` trên group test trước khi cân nhắc `READY WITH CONDITIONS`.
