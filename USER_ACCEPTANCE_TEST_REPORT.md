# USER ACCEPTANCE TEST REPORT — Telegram AI Personal Assistant

**Ngày kiểm thử:** 28/07/2026  
**Khung giờ:** 12:46–13:16 ICT (05:46–06:16 UTC)  
**Vai trò:** người dùng mới + QA/UAT độc lập  
**Môi trường:** local production-like, dashboard `http://127.0.0.1:8765`, MySQL thật, Qdrant local thật, Telegram thật; không dùng dữ liệu mock  
**Kích thước trình duyệt:** desktop 1440×1000; mobile 390×844  
**Kết luận phát hành:** **NOT READY**

## 1. Tóm tắt điều hành

Dashboard vận hành được, đăng nhập và bảo vệ phiên tốt, dữ liệu MySQL/Telegram là dữ liệu thật, SSE có cập nhật, pending action có preview/cancel/audit đúng, worker và scheduler khỏe. Luồng hỏi toàn cục và bản tin ngày đã dùng dữ liệu Telegram thật, ưu tiên cửa sổ 7 ngày, tạo câu trả lời hữu ích và có link Telegram mở được.

Tuy nhiên, mục tiêu “một bộ não thống nhất từ tất cả nguồn được cấp quyền” **mới đạt một phần, ước tính khoảng 65%**:

- Chỉ 100/298 nguồn bật `auto_knowledge`; 98 nguồn Learned, 2 nguồn `no_content`, còn 198 nguồn `not_learned`.
- Bộ lọc phạm vi `in:<chat_id>` chỉ áp dụng đúng ở nhánh keyword nhưng không được truyền vào nhánh vector. Một câu hỏi giới hạn vào Coin68 lại đính kèm nguồn từ nhiều group/channel khác.
- Dashboard chưa giải thích bản chất “học dữ liệu”, chưa hiển thị rõ tiến độ 98/298 và lý do 198 nguồn chưa học.

**Tổng ma trận kiểm thử (110 ca):** PASS = **75**, PARTIAL = **18**, FAIL = **11**, NOT TESTED = **6**.

Ba vấn đề quan trọng nhất:

1. **High — truy xuất sai phạm vi:** `in:-1001300523532` vẫn lấy dẫn chứng từ các nguồn khác.
2. **High — kho tri thức chưa bao phủ toàn bộ:** 198/298 nguồn chưa học, chỉ 100 nguồn bật auto-knowledge.
3. **Medium — khả năng quan sát/điều khiển learning gây sai lệch:** bộ lọc “Đã học” trả rỗng; job Completed vẫn có thể Retry; tiến độ tổng thể không được trình bày.

Không phát hiện mất dữ liệu, lộ secret, pending action còn treo, quyền thật bị thay đổi ngoài ý muốn hoặc model bị xóa.

## 2. Đánh giá mục tiêu sản phẩm

| Câu hỏi mục tiêu | Đánh giá | Bằng chứng thực tế |
|---|---|---|
| Dữ liệu nhiều group/channel đã hợp nhất thành một kho tri thức chưa? | **PARTIAL** | Một collection Qdrant đang hoạt động, payload giữ `chat_id/message_id`; MySQL có 298 nguồn. Tuy nhiên chỉ 100 nguồn bật auto-knowledge, 98 Learned. |
| Tin mới có tự động cập nhật không? | **PASS** | Trong lúc kiểm thử, tổng message tăng 85.547 → 85.579; J2TEAM tăng cả MySQL lẫn vector; latest message 13:15:37 ICT. |
| Truy xuất có đúng phạm vi không? | **FAIL** | Hỏi `in:-1001300523532` trả lời không có dữ liệu Coin68 nhưng đính kèm 10 nguồn ngoài Coin68. |
| Có ưu tiên 7 ngày gần nhất không? | **PASS** | `/ask` toàn cục chỉ dùng tin 22–25/07 khi hỏi ngày 28/07; code và truy vấn đều giới hạn rolling 7×24h. |
| Có loại trùng không? | **PASS/PARTIAL** | MySQL không có khóa `(chat_id,message_id)` trùng; digest ngày loại 8/296 tin. Chưa mở trực tiếp Qdrant đang bị process giữ lock để quét toàn bộ vector. |
| Tin tức có nguồn không? | **PARTIAL** | `/ask` có link trực tiếp; digest có 22 link tương ứng 22 dẫn chứng. Nhưng `/ask` toàn cục đính kèm S8–S10 dù nội dung chỉ tham chiếu S1–S7. |
| Quyền nguồn có được tôn trọng không? | **PARTIAL** | Policy lọc nguồn được phép; DB không có nguồn BLOCK còn vector. Chưa thể tạo nguồn bị chặn để thử live mà không đổi quyền thật. Sai phạm vi `in:` vẫn xảy ra giữa các nguồn đã được phép. |
| Người dùng có biết hệ thống đang học đến đâu không? | **FAIL** | UI chỉ nổi bật tổng 298 và job hiện tại; không tóm tắt 98 Learned / 198 Not learned / 2 No content, không giải thích nguyên nhân và khái niệm học. |

### 2.1 Checkpoint dữ liệu thật

| Chỉ số | Giá trị |
|---|---:|
| Group/channel đã tham gia | 298 |
| Nguồn ALLOW | 298 |
| Nguồn có quyền search | 298 |
| Nguồn có quyền summarize | 298 |
| Nguồn bật auto-knowledge | 100 |
| Knowledge status | 98 Learned; 2 No content; 198 Not learned |
| Telegram messages, checkpoint cuối | 85.579 |
| Messages 24h, checkpoint dashboard 12:54 ICT | 1.156 |
| Message keys bị trùng | 0 |
| Tổng message đếm trong knowledge inventory, checkpoint 13:00 ICT | 85.367 |
| Tổng vector theo knowledge inventory, checkpoint 13:00 ICT | 74.826 |
| Nguồn BLOCK vẫn có vector | 0 |
| Background jobs cuối phiên | 200 Completed; 0 Queued/Running/Paused/Failed |
| Pending actions cuối phiên | 0 Pending |
| AI usage 24h cuối phiên | 4 Answer; 357 Embedding |

## 3. Ma trận kiểm thử

| ID | Nhóm chức năng | Tác vụ | Kết quả | Ghi chú |
|---|---|---|---|---|
| A-01 | Đăng nhập | Mở dashboard chưa đăng nhập | PASS | Hiện trang owner authentication, không lộ dashboard. |
| A-02 | Onboarding | Người mới hiểu cách lấy mã | PARTIAL | Hướng dẫn rõ nhưng dùng lệnh phụ thuộc venv đã activate. |
| A-03 | Đăng nhập | Chạy đúng `.\.venv\Scripts\tg-assistant.exe dashboard-code` | PASS | Sinh mã 8 số, không ghi mã vào báo cáo. |
| A-04 | Đăng nhập | Nhập mã sai | PASS | Thông báo “Mã đăng nhập sai hoặc đã hết hạn.” |
| A-05 | Đăng nhập | Nhập mã đúng | PASS | Vào dashboard thành công. |
| A-06 | Phiên | Reload sau đăng nhập | PASS | Phiên còn hiệu lực; dữ liệu tải lại. |
| A-07 | Phiên | Xóa cookie rồi reload | PASS | Trở về màn hình đăng nhập; dashboard bị ẩn. |
| A-08 | Bảo mật | Quan sát secret/cookie/API key trên UI | PASS | Không thấy secret, OTP, key hoặc token. |
| A-09 | Responsive | Login mobile 390×844 | PASS | Nội dung đọc được, không tràn ngang. |
| B-01 | Khả năng hiểu | Đọc toàn bộ sidebar như người mới | PARTIAL | Có phân nhóm tốt nhưng nhiều mục thiên về vận hành kỹ thuật. |
| B-02 | Thuật ngữ | Group/channel, ALLOW/BLOCK, AI mode | PARTIAL | Group/channel quen thuộc; ALLOW/BLOCK và AI mode thiếu diễn giải. |
| B-03 | Thuật ngữ | Vector, embedding, RAG, Qdrant, LOCAL ONLY | PARTIAL | Không có tooltip/glossary cho người không kỹ thuật. |
| B-04 | Trạng thái | Learned/Pending/Paused/Failed | PARTIAL | Badge có màu nhưng không giải thích ý nghĩa và hành động kế tiếp. |
| B-05 | Learning | Giải thích học không mặc định là fine-tune | FAIL | Không có nội dung giải thích sync → clean → index → enrich. |
| B-06 | Ngôn ngữ | Kiểm tra tiếng Việt và encoding | FAIL | Một audit reason bị hỏng: `Owner y?u c?u c?p quy?n ??c...`. |
| C-01 | Tổng quan | Tổng nguồn và nguồn được quyền | PASS | 298, khớp MySQL/API. |
| C-02 | Tổng quan | Messages 24h | PASS | 1.156 tại 12:54 ICT, dữ liệu biến động theo realtime. |
| C-03 | Tổng quan | Số yêu cầu AI | PARTIAL | `/ask` hiển thị 0 trong khi AI & RAG có 342+ requests; phần lớn là embedding nhưng UI không nói rõ. |
| C-04 | Tổng quan | Pending action / learning job | PASS | 0 pending, 0 lỗi; khớp DB. |
| C-05 | Tổng quan | Provider/model đang hoạt động | PASS | OpenAI `gpt-5.6-terra`; chat local `qwen3:8b`, embedding `nomic-embed-text`. |
| C-06 | Tổng quan | RAM và storage | PASS | RAM khoảng 714 MB; data/vector khoảng 1 GB mỗi lớp. |
| C-07 | Tổng quan | VRAM | PARTIAL | Không có số VRAM thực tế trên các màn hình đã kiểm tra. |
| C-08 | Tổng quan | Scheduler/worker | PASS | Healthy, không có queue/running/failed cuối phiên. |
| C-09 | SSE | Dữ liệu tự cập nhật | PASS | Message/vector tăng trong phiên; pending badge đổi khi tạo/hủy action. |
| C-10 | SSE | Mất kết nối và reconnect | PASS | Reload cho thấy RECONNECTING rồi LIVE sau vài giây. |
| D-01 | Nguồn Telegram | Tìm theo tên | PASS | `J2TEAM` trả đúng nguồn. |
| D-02 | Nguồn Telegram | Tìm theo Chat ID | PASS | `-1001227549557` trả đúng J2TEAM. |
| D-03 | Nguồn Telegram | Tìm theo username có `@` | FAIL | `@ThuanCapital` trả 0; `ThuanCapital` trả đúng. |
| D-04 | Nguồn Telegram | Bộ lọc AI/có quyền/tất cả | PASS | 2 bật AI; 296 có quyền chưa bật AI; tổng 298. |
| D-05 | Nguồn Telegram | Phân trang | PASS | 30 trang, 10 nguồn/trang; Next hoạt động. |
| D-06 | Chi tiết nguồn | Tên/ID/type/policy/message/vector | PASS | Group test hiện ALLOW, Group AI ON, 263 MySQL/244 vector tại thời điểm mở. |
| D-07 | Permission | Tên và giải thích 19 quyền | PARTIAL | Hiển thị raw code như `read_messages`, `auto_knowledge`; không có mô tả từng quyền. |
| D-08 | Permission | Tạo preview, hủy, kiểm tra quyền/audit | PASS | `analyze_files` vẫn OFF; action Created/Cancelled có audit; pending về 0. |
| D-09 | AI theo nguồn | AI mode/provider/fallback/LOCAL ONLY | PARTIAL | Cấu hình và trạng thái thấy được; không đổi provider để tránh rủi ro không khôi phục. |
| D-10 | Policy | Retention/quota message/vector/storage | PASS | Có trường nhập và giá trị hiện tại; thay đổi cần pending action. |
| E-01 | Kho hợp nhất | Nhiều group/channel vào MySQL | PASS | 298 nguồn, 85.579 message cuối phiên. |
| E-02 | Kho hợp nhất | Bao phủ vào vector knowledge | PARTIAL | Có shared store nhưng chỉ 100 nguồn auto-knowledge, 98 Learned. |
| E-03 | Metadata | Nguồn, thời gian, message ID/link | PASS | RAG evidence giữ chat ID, title, timestamp, message ID và URL khi khả dụng. |
| E-04 | Incremental learning | Tin mới làm giàu kho hiện tại | PASS | J2TEAM MySQL 1.697→1.705 và vector 1.512→1.516 trong phiên. |
| E-05 | Chống trùng | Không tạo message/vector trùng | PARTIAL | MySQL duplicate key = 0; point ID dựa DB row. Chưa quét full Qdrant trực tiếp do store đang mở. |
| E-06 | RAG | Hỏi toàn cục | PASS | Trả lời crypto từ nhiều nguồn thật, 5.258 ký tự, 2 message. |
| E-07 | RAG scope | Hỏi `in:<channel>` | FAIL | Trộn J2TEAM/SD Capital/test group/5 Phút Crypto… ngoài Coin68. |
| E-08 | Group context | “Group này đang thảo luận chủ đề gì?” | PARTIAL | Nội dung đúng group test nhưng phản hồi sau 5 phút 52 giây. |
| E-09 | Sender scope | “@username đã nói gì?” trên Telegram thật | NOT TESTED | Không có tài khoản test thứ hai phù hợp để tạo update độc lập; không gửi vào group công cộng. |
| E-10 | Multi-source | Hỏi về một chủ đề/dự án dùng nhiều nguồn | PASS | Câu hỏi crypto tổng hợp được nhiều nguồn và phân loại chủ đề. |
| E-11 | Unauthorized source | Hỏi trực tiếp nguồn chưa được quyền | NOT TESTED | 298/298 nguồn hiện ALLOW; không thay đổi quyền thật chỉ để tạo ca âm. |
| E-12 | Recency | Cửa sổ mặc định 7 ngày | PASS | Kết quả nằm trong 22–25/07 khi hỏi 28/07; query có giới hạn 7×24h. |
| E-13 | Recency | Quét từ gần hiện tại về trước | PASS | MySQL order theo `sent_at DESC`; digest ngày dùng cửa sổ lịch Việt Nam. |
| E-14 | Chất lượng | Phân biệt facts/claims/opinions | PASS | Câu trả lời nêu rõ khi thông tin là cáo buộc/nhận định chưa được kiểm chứng. |
| E-15 | Thiếu dữ liệu | Không bịa khi không có dữ liệu | PARTIAL | Có câu “không tìm thấy”, nhưng ca scoped lại đính kèm nguồn ngoài phạm vi. |
| F-01 | Digest | `/digest today` rà mọi nguồn được quyền | PASS | 298 tham gia, 298 được quyền tổng hợp. |
| F-02 | Digest | Số liệu coverage | PASS | 24 nguồn active; 296 tin rà; 74 không phù hợp; 8 trùng; 214 unique; 93 vào AI. |
| F-03 | Digest | Loại trùng | PASS | Báo rõ 8 tin trùng bị loại. |
| F-04 | Digest | Phân loại và ưu tiên mới | PASS | Có diễn biến, thị trường, vĩ mô/pháp lý, DeFi, bảo mật, task/deadline. |
| F-05 | Digest | Danh sách nguồn và dẫn chứng | PASS | 24 nguồn phù hợp; 22 link Telegram trực tiếp. |
| F-06 | `/ask` news | Dẫn nguồn liên quan | PARTIAL | Có 10 link nhưng S8–S10 không được tham chiếu trong phần trả lời. |
| F-07 | Telegram format | Heading/chia chunk | PASS | Không hiện `##`; chia tại ranh giới section/bullet, không cắt giữa câu. |
| F-08 | Telegram link | Link bấm được/xác định nguồn | PASS | Dùng URL `t.me/.../<message_id>` trực tiếp. |
| G-01 | Knowledge | Danh sách status và số liệu nguồn | PASS | Hiện status, MySQL, vector, size, lần học, note/error. |
| G-02 | Knowledge filter | Lọc “Đã học” | FAIL | Trả empty dù có 98 nguồn `learned`. |
| G-03 | Knowledge filter | Lọc “Chưa học” | PASS | Trả dữ liệu, trang đầu 50/198. |
| G-04 | CSV | Cột, dữ liệu thật, UTF-8 | PASS | BOM UTF-8, cột đúng, tiếng Việt và dữ liệu nguồn thật. |
| G-05 | CSV | Xuất toàn bộ inventory | FAIL | File chỉ có 50 nguồn đang hiển thị thay vì 298; không có lựa chọn full export. |
| G-06 | Learning control | Pause all rồi Resume all | PASS | Cả hai API 200; cuối phiên 200 job đều Completed, không job paused. |
| G-07 | Learning job | Chỉ Retry job lỗi | FAIL | 200 job Completed đều có nút Retry và backend chấp nhận Completed. |
| G-08 | Learning insight | Biết tiến độ/lý do chưa học | PARTIAL | Không có 98/298 progress bar hoặc lý do tổng hợp cho 198 nguồn. |
| G-09 | Incremental | Tự tiếp tục với tin mới | PASS | Số MySQL/vector tăng sau khi learning được resume. |
| H-01 | Provider | Provider/model hiện tại | PASS | Provider và model hiển thị, không lộ key. |
| H-02 | Routing | OpenAI/OpenRouter/Ollama/off/local-first/fallback | PARTIAL | Có UI/cấu hình; không chuyển live vì thay provider có rủi ro cấu hình. |
| H-03 | Ollama | Danh sách model/kích thước/role | PASS | 8 model; phân biệt active chat và embedding. |
| H-04 | Ollama | Preview xóa và hủy | PASS | Preview `glm-5:cloud`, sau đó cancel; model vẫn tồn tại. |
| H-05 | Ollama | Không cho xóa model active | PASS | Nút xóa active chat/embedding bị disable. |
| H-06 | Ollama | Download/activate model lớn | NOT TESTED | Bị giới hạn an toàn; không tải model lớn, không đổi active model. |
| H-07 | Secrets | API key trên UI/frontend | PASS | Không phát hiện key/token/secret trong nội dung hiển thị. |
| I-01 | Moderation | Xem group đang bật auto moderation | PASS | Directory/detail có badge/toggle AUTO/OFF. |
| I-02 | Moderation | Logic link non-admin, owner/admin miễn | PASS | 133 test backend pass; test chuyên biệt xác nhận rule và audit. |
| I-03 | Moderation | Xóa tin thật trong group | NOT TESTED | Không tạo link spam/xóa nội dung thật theo quy tắc an toàn. |
| I-04 | Moderation | Lý do/audit cho live deletion | NOT TESTED | Không có live deletion an toàn để tạo audit thực tế. |
| J-01 | CoinGecko | Hỏi token phổ biến | PASS | BTC trả dữ liệu trực tiếp thành công. |
| J-02 | CoinGecko | Timestamp và nguồn | PASS | Có timestamp và dòng `Nguồn:` riêng, phân biệt Telegram. |
| J-03 | CoinGecko | Token không tồn tại | PASS | Trả lỗi rõ “Không tìm thấy coin…”. |
| J-04 | CoinGecko | Rate limit | NOT TESTED | Không cố tình dội request vào dịch vụ thật để tạo rate-limit. |
| J-05 | CoinGecko | Không lộ API key | PASS | Không lộ key trong output/log/report. |
| K-01 | Storage | Data/vector/media | PASS | 1 GB/1 GB/0 B từ API thật. |
| K-02 | Telemetry | RAM/VRAM | PARTIAL | RAM có; VRAM không thấy số liệu. |
| K-03 | Storage | Tăng trưởng | PASS | Application data tăng +394 B ở lần đo tiếp theo. |
| K-04 | Runtime | Worker/scheduler | PASS | Healthy; telemetry có timestamp. |
| K-05 | Jobs | Queue/running/paused/failed | PASS | Cuối phiên tất cả 200 Completed. |
| K-06 | Cleanup | Chạy dry-run preview | PASS | HTTP 200; 0 expired message/vector, 0 orphan, 0 media; không xóa dữ liệu. |
| K-07 | Cleanup | Owner confirmation | PASS | Chỉ có nút tạo PendingAction sau preview; không được xác nhận. |
| K-08 | Capacity | Phần trăm dung lượng | FAIL | Meter 45/65/35 và donut 72% là hằng số UI, không tính từ capacity thật. |
| L-01 | Pending | Action mới qua SSE | PASS | Badge pending tăng ngay khi tạo preview permission. |
| L-02 | Pending | Preview/scope/expiry/warning | PASS | Modal có phạm vi, backend preview, TTL và cảnh báo. |
| L-03 | Pending | Cancel action | PASS | Permission/model-delete action hủy thành công. |
| L-04 | Pending | Expired không tính đang chờ | PASS | DB có 4 Expired nhưng active pending = 0. |
| L-05 | Audit | Ai/tác vụ/nguồn/kết quả/thời gian | PASS | Created/Cancelled được ghi với actor, target, outcome, timestamp. |
| L-06 | Audit | Không chứa secret | PASS | Không thấy key/token/cookie/CSRF trong audit. |
| L-07 | Audit | Xem chi tiết lịch sử | FAIL | Nút “Xem” bị disable cho mọi action không còn Pending. |
| M-01 | State | Loading | PASS | Có Loading khi reload/navigation. |
| M-02 | State | Empty | PASS | Có empty state; đồng thời giúp phát hiện filter Learned sai. |
| M-03 | State | API 500 và Retry | PASS | Giả lập an toàn GET groups = 500; UI báo lỗi và Retry khôi phục 298 nguồn. |
| M-04 | State | 401 | PASS | Mất cookie đưa về login; `/auth/session` trả 401. |
| M-05 | State | 403 CSRF | PASS | POST pause-all thiếu CSRF trả 403 “CSRF token không hợp lệ”, không đổi state. |
| M-06 | Mobile | Sidebar/menu | PASS | Menu mobile mở/đóng và điều hướng được. |
| M-07 | Mobile | Bảng nguồn | FAIL | `scrollWidth=699` trong viewport 375; bảng bị cắt/tràn ngang. |
| M-08 | Interaction | Modal/form/pagination | PASS | Các control chính hoạt động, button có label. |
| M-09 | Accessibility | Focus/Escape/screen reader | PARTIAL | Login focus đúng, phần lớn label tốt; chưa kiểm tra đầy đủ Escape trên mọi modal. |
| M-10 | Integrity | Không MOCK/PROTOTYPE | PASS | Không thấy nhãn mock/prototype; API/MySQL/Telegram đều là thật. |
| M-11 | Integrity | Không số liệu giả | FAIL | Capacity meter hard-coded dù gắn nhãn LIVE TELEMETRY. |
| M-12 | Regression | Pytest/build/Sites tests | PASS | `pytest -q` pass 133; production build pass; Sites tests 4/4 pass. |

## 4. Danh sách lỗi

| ID | Mức độ | Màn hình/luồng | Vấn đề | Các bước tái hiện | Thực tế | Mong đợi | Bằng chứng |
|---|---|---|---|---|---|---|---|
| UAT-H01 | High | Telegram `/ask` scoped RAG | Bộ lọc `in:<chat_id>` không giới hạn nhánh vector | Private bot: `/ask in:-1001300523532 ... Chỉ dùng channel này` | Nói không có Coin68 nhưng phụ lục có 10 nguồn J2TEAM, SD Capital, test group, 5 Phút Crypto… | Keyword và vector cùng bị giới hạn đúng channel; nếu thiếu dữ liệu thì không kèm nguồn ngoài scope | Telegram 430113→430114; `src/tg_assistant/ai/rag.py` dùng `allowed` chung cho vector, trong khi parser scope chỉ nằm ở `services/search.py` |
| UAT-H02 | High | Unified knowledge | Kho tri thức chưa bao phủ tất cả nguồn được quyền | So sánh groups, permissions, knowledge statuses | 298 ALLOW/search/summarize nhưng 100 auto-knowledge; 98 Learned, 198 Not learned, 2 No content | Mục tiêu release phải có kế hoạch/tiến độ bao phủ tất cả nguồn được phép học | MySQL checkpoint 13:15 ICT |
| UAT-M01 | Medium | `/ask` news citations | Đính kèm nguồn không được dùng trong câu trả lời | Hỏi tin crypto 7 ngày | Body chỉ tham chiếu S1–S7; appendix vẫn có S8–S10 | Chỉ đính kèm dẫn chứng được tham chiếu hoặc liên quan trực tiếp | Telegram 430110→430111/430112 |
| UAT-M02 | Medium | Kho tri thức | Filter “Đã học” dùng sai status | Chọn “Đã học” | Empty state dù DB có 98 `learned` | Dùng `learned`, trả 98 nguồn | [Ảnh filter](output/playwright/uat-2026-07-28/.playwright-cli/page-2026-07-28T06-11-53-349Z.png); `KnowledgeView.jsx` option=`completed` |
| UAT-M03 | Medium | Learning jobs | Cho Retry job Completed | Mở Kho tri thức, xem 200 Completed | Mọi row có Retry; API cho `failed|completed` | Chỉ hiện/cho phép Retry job Failed phù hợp | `KnowledgeView.jsx`; Admin API `_update_job_state` |
| UAT-M04 | Medium | CSV knowledge | CSV chỉ xuất trang hiện tại | Trang inventory 298 nguồn → Xuất CSV | File có 50 data rows | Export toàn bộ 298 hoặc ghi rõ “export current page” ngay trên nút và cho lựa chọn full | [CSV](output/playwright/uat-2026-07-28/.playwright-cli/telegram-learning-sources-2026-07-28.csv) |
| UAT-M05 | Medium | Storage telemetry | Meter LIVE dùng số hard-coded | Mở Bộ nhớ & lưu trữ | Donut 72%; layer 45/65/35 không phụ thuộc bytes/capacity | Tính từ quota/capacity thật hoặc bỏ phần trăm | `OperationsViews.jsx`: `--value:72%`, `meter={45,65,35}` |
| UAT-M06 | Medium | Permission/Learning UX | Không giải thích permission và “learning” | Mở group detail và Kho tri thức | 19 raw permission codes; không phân biệt indexing với fine-tune | Mô tả tác động, dữ liệu truy cập, xác nhận, và glossary | Quan sát desktop |
| UAT-M07 | Medium | Pending/Audit | Không xem lại chi tiết action lịch sử | Mở Hành động chờ | “Xem” disable khi status khác Pending | Cho phép read-only detail ở mọi status | `OperationsViews.jsx`: `disabled={action.status !== "pending"}` |
| UAT-M08 | Medium | Group `/ask` | Phản hồi group quá chậm, không có tiến trình | Gửi `@BrianNguyen0x /ask Group này...` trong group test | 430115 lúc 13:06:34, reply 430116 lúc 13:12:26 — 352 giây | Phản hồi/ack trong vài giây; kết quả trong SLA hợp lý | Telegram timestamps |
| UAT-M09 | Medium | Dashboard learning | Không thấy tiến độ toàn corpus | Mở Tổng quan/Kho tri thức | Chỉ thấy total 298 và active job; không thấy 98/298 hay lý do 198 chưa học | Progress, breakdown, last successful sync, blockers, CTA | MySQL + UI |
| UAT-M10 | Medium | AI metrics | “AI requests” không định nghĩa, lệch `/ask` | So Tổng quan với AI & RAG | `/ask` 0; AI requests 342→361 do embedding/answer | Tách Answer/Embedding/Digest/Price và ghi kỳ đo | Dashboard + `ai_usage` |
| UAT-L01 | Low | Search groups | Placeholder nhận `@username` nhưng backend không normalize `@` | Tìm `@ThuanCapital`, rồi `ThuanCapital` | Có `@` = 0; không `@` = đúng | Hai cách đều hoạt động | Admin API username `ILIKE %query%` |
| UAT-L02 | Low | Mobile group directory | Tràn ngang và cắt cột | 390×844 → Nhóm Telegram | document 699 px trong client 375 px | Table scroll có chỉ báo hoặc card mobile, không cắt nội dung | [Ảnh mobile](output/playwright/uat-2026-07-28/.playwright-cli/page-2026-07-28T05-54-42-569Z.png) |
| UAT-L03 | Low | Audit Log | Một reason bị lỗi encoding | Mở Audit Log | `Owner y?u c?u c?p quy?n ??c...` | UTF-8 tiếng Việt đúng | [Ảnh audit](output/playwright/uat-2026-07-28/.playwright-cli/element-2026-07-28T06-16-00-484Z.png) |
| UAT-L04 | Low | Login onboarding | Lệnh ngắn không chạy trong PowerShell chưa activate venv | Chạy `tg-assistant dashboard-code` | Command not found; lệnh `.venv\Scripts\...exe` hoạt động | Hiển thị lệnh tuyệt đối theo project hoặc hướng dẫn activate | [Ảnh login](output/playwright/uat-2026-07-28/.playwright-cli/page-2026-07-28T05-46-54-094Z.png) |
| UAT-L05 | Low | Toàn UI | Trộn tiếng Việt và thuật ngữ kỹ thuật không giải thích | Đọc sidebar/cards/status | RAG, Qdrant, LIVE TELEMETRY, PendingAction, INHERIT, source of truth… | Việt hóa hoặc tooltip nhất quán | Quan sát usability |
| UAT-L06 | Low | Runtime/Models | Không hiển thị VRAM | Mở Tổng quan, Local Models, Scheduler | Có RAM/model/size nhưng không có VRAM thực | Hiển thị VRAM used/total hoặc “Không khả dụng” có lý do | Quan sát desktop |

**Tổng lỗi:** Critical 0; High 2; Medium 10; Low 6.

## 5. Vấn đề đọc hiểu và UX

- “AI & RAG”, Qdrant, embedding, vector, LOCAL ONLY, cloud fallback, INHERIT, PendingAction, retention, quota, audit trail và telemetry không có giải thích ngắn.
- Raw permission code không cho người mới biết quyền đọc gì, ảnh hưởng phạm vi nào, có gửi dữ liệu lên cloud hay không.
- “Learned” dễ bị hiểu là fine-tune model. UI cần nói rõ đây chủ yếu là đồng bộ, làm sạch, loại trùng, embedding và lập chỉ mục.
- Badge màu không đủ: `no_content`, `not_learned`, `paused`, `failed`, `expired` cần nguyên nhân và CTA phù hợp.
- Tổng 298 nguồn tạo cảm giác “đã học hết” trong khi chỉ 98 Learned.
- Các nhãn `AI requests` và `/ask` không cùng định nghĩa; người dùng không thể giải thích chênh lệch.
- “Xem” bị vô hiệu hóa trên lịch sử audit/pending khiến người dùng không thể điều tra sự kiện đã hoàn tất.
- Mobile cần card layout hoặc vùng scroll ngang có affordance; hiện cột phía phải bị cắt.
- Một audit reason bị mất dấu thành dấu `?`; cần migration/sửa dữ liệu cũ và khóa UTF-8 ở điểm ghi.

## 6. Chức năng còn thiếu hoặc chưa hoạt động đúng

1. **Scope contract thống nhất:** parser phải tạo một scope object dùng chung cho keyword, vector, citation và AI routing. Hiện `in:` chỉ hạn chế keyword.
2. **Hoàn tất unified brain:** cần workflow cấp quyền/học cho 198 nguồn còn lại hoặc diễn giải rõ nguồn nào cố ý không học.
3. **Learning progress thật:** dashboard thiếu tỷ lệ Learned, backlog, no-content, lỗi, checkpoint và estimated remaining.
4. **Export toàn inventory:** chưa có endpoint/export server-side cho đủ 298 nguồn.
5. **Read-only historical action detail:** chưa thể xem preview/payload redacted của action Cancelled/Executed/Expired.
6. **Capacity/VRAM telemetry thật:** phần trăm storage đang là hằng số; VRAM không được cung cấp.
7. **Trợ giúp người dùng mới:** thiếu glossary và mô tả “học ≠ fine-tune”.

Không coi các ca bị giới hạn an toàn (xóa moderation thật, cleanup confirm, model download/delete, đổi provider) là chức năng thiếu.

## 7. Đề xuất sửa theo ưu tiên

### High

1. Trong `RagService.answer`, parse câu hỏi một lần, tạo `effective_chat_ids`, rồi dùng danh sách này cho:
   - `SearchService.keyword`;
   - truy vấn `recent_reference_ids`;
   - `vectors.search(allowed_chat_ids=...)`;
   - `policy.ai_route`;
   - kiểm tra citation cuối cùng.  
   Thêm integration test: mọi evidence của `in:X` phải có `chat_id == X`; khi X không có dữ liệu phải trả thiếu dữ liệu và **0 citation ngoài X**.
2. Tạo migration/owner workflow để đưa toàn bộ nguồn được phép vào auto-knowledge theo batch có quota; thêm dashboard coverage 98/298 và danh sách 198 nguồn chưa học với lý do.

### Medium

3. Chỉ append citation được AI tham chiếu, tương tự logic `referenced_item_indexes` đang dùng cho digest.
4. Sửa option Knowledge từ `completed` thành `learned`; thêm test UI/API cho mọi status thực của `knowledge_sources`.
5. Xóa `completed` khỏi điều kiện Retry ở frontend và Admin API; chỉ Failed mới được retry, kèm reason.
6. Thêm endpoint CSV streaming toàn bộ inventory hoặc hộp chọn “Trang hiện tại / Tất cả 298”.
7. Thay meter hard-coded bằng `used/quota` thật; nếu không có quota thì chỉ hiển thị bytes và growth, không vẽ phần trăm.
8. Cho phép mở action detail read-only ở mọi status; chỉ nút Confirm/Cancel mới phụ thuộc Pending.
9. Tách AI telemetry theo operation: Answer, Embedding, Digest; thống nhất kỳ 24h và tooltip.
10. Group `/ask` cần immediate acknowledgement (“Đã nhận, đang rà 7 ngày…”) và đo latency từng phase; điều tra vì sao self-authenticated update mất gần 6 phút.

### Low

11. Normalize query username bằng `lstrip("@")`.
12. Chuyển group table mobile sang card hoặc giữ table với scroll indicator/sticky first column.
13. Sửa audit row mojibake và kiểm tra toàn bộ đường ghi/đọc là UTF-8/utf8mb4.
14. Trên login, hiển thị đúng lệnh `.\.venv\Scripts\tg-assistant.exe dashboard-code`.
15. Thêm glossary/tooltip, Việt hóa status và hiển thị “VRAM không khả dụng” nếu không lấy được.

## 8. Kết luận phát hành

### NOT READY

Core platform đã có nền tảng tốt: local security, MySQL sync, shared vector store, 7-day RAG, digest, citation links, permission/pending/audit, scheduler/worker và regression tests đều hoạt động. Nhưng hai điều kiện cốt lõi của release chưa đạt:

- phạm vi truy xuất có thể bị phá vỡ giữa keyword và vector;
- unified brain mới học 98/298 nguồn.

Sau khi sửa UAT-H01, có kế hoạch hoàn tất/giải thích coverage 298 nguồn, và sửa các lỗi learning visibility/filter/retry, nên chạy lại toàn bộ E/F/G trước khi cân nhắc `READY WITH MINOR FIXES`.

## 9. Bằng chứng và trạng thái hệ thống

### 9.1 Ảnh/file

- [Login desktop](output/playwright/uat-2026-07-28/.playwright-cli/page-2026-07-28T05-46-54-094Z.png)
- [Login mobile](output/playwright/uat-2026-07-28/.playwright-cli/page-2026-07-28T05-55-05-273Z.png)
- [Local Models mobile](output/playwright/uat-2026-07-28/.playwright-cli/page-2026-07-28T05-54-23-838Z.png)
- [Group table mobile overflow](output/playwright/uat-2026-07-28/.playwright-cli/page-2026-07-28T05-54-42-569Z.png)
- [API 500 + Retry](output/playwright/uat-2026-07-28/.playwright-cli/page-2026-07-28T06-05-31-131Z.png)
- [Knowledge “Đã học” empty](output/playwright/uat-2026-07-28/.playwright-cli/page-2026-07-28T06-11-53-349Z.png)
- [Audit encoding](output/playwright/uat-2026-07-28/.playwright-cli/element-2026-07-28T06-16-00-484Z.png)
- [Knowledge CSV](output/playwright/uat-2026-07-28/.playwright-cli/telegram-learning-sources-2026-07-28.csv)

### 9.2 Endpoint/status

| Request | Status | Kết quả |
|---|---:|---|
| Dashboard root | 200 | Tải login/dashboard |
| Login sai | 401/visible validation | Thông báo đúng, không tạo session |
| GET groups bị route test ép lỗi | 500 | Error state + “Thử lại”; route được gỡ và data trở lại |
| POST `/api/v1/learning-jobs/pause-all` thiếu CSRF | 403 | “CSRF token không hợp lệ”; state không đổi |
| Pause all hợp lệ | 200 | Sau đó Resume all |
| Resume all hợp lệ | 200 | Không còn Paused |
| Storage cleanup dry-run | 200 | Tất cả impact = 0; không tạo/confirm cleanup |

### 9.3 Telegram thật

| Luồng | Request | Response | Kết quả |
|---|---:|---:|---|
| `/ask` crypto toàn cục 7 ngày | 430110 | 430111–430112 | Relevant, 5.258 ký tự, 10 citation; 3 citation không được body dùng |
| `/ask in:-1001300523532` | 430113 | 430114 | Fail scope: trộn nguồn ngoài Coin68 |
| Group test current context | 430115 | 430116 | Đúng ngữ cảnh, latency 352 giây |
| `/digest today` | 430117 | 430118–430120 | 298/298 nguồn; 296 tin; 8 trùng; 22 link |

### 9.4 Thay đổi đã thực hiện và khôi phục

- Tạo rồi **hủy** pending action đổi permission `analyze_files`; quyền thật cuối phiên vẫn OFF.
- Pause learning rồi **Resume**; cuối phiên 200 jobs đều Completed, không có Paused/Queued/Running/Failed.
- Tạo rồi **hủy** preview xóa `glm-5:cloud`; cuối phiên model vẫn tồn tại. `qwen3:8b` và `nomic-embed-text:latest` vẫn active/present.
- Chạy storage **dry-run**; không tạo hoặc xác nhận cleanup; không xóa dữ liệu.
- Route 500 chỉ nằm trong Playwright browser và đã được gỡ.
- Session dashboard thử nghiệm đã được xóa; reload cuối phiên trở về login.
- Tải một file CSV bằng trình duyệt.
- Gửi ba request trong private chat bot và một request vào group test được chỉ định. Các message 430110, 430113, 430115, 430117 và reply tương ứng là thay đổi dữ liệu Telegram/MySQL có chủ đích; không xóa vì quy tắc cấm xóa dữ liệu thật.
- Cuối phiên: **0 PendingAction đang chờ**, permission được khôi phục, learning được resume, không model bị xóa, không provider bị đổi.

### 9.5 Phần không thể kiểm tra an toàn

- Live query từ một nguồn BLOCK/chưa được quyền: tất cả 298 nguồn hiện ALLOW; không đổi quyền thật.
- Live “@username đã nói gì?” với người gửi độc lập: không có tài khoản test thứ hai phù hợp; không gửi vào group công cộng.
- Live auto-delete moderation và audit xóa: không tạo/xóa spam thật.
- CoinGecko rate-limit: không dội request vào API thật.
- Download/activate model lớn, confirm model delete, confirm storage cleanup: bị cấm bởi quy tắc an toàn.
- Chuyển OpenAI/OpenRouter/Ollama/off: không thực hiện vì không bảo đảm khôi phục provider/key/runtime mà không gián đoạn.
- Full direct Qdrant duplicate scan: local collection đang được process ứng dụng giữ; đánh giá dựa trên DB uniqueness, point-ID implementation và inventory count.
