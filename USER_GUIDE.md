# Hướng dẫn sử dụng Telegram AI Personal Assistant

Telegram là nơi hỏi trợ lý; dashboard là nơi owner thiết lập, cấp quyền và kiểm tra vận hành. “Học” nghĩa là đồng bộ dữ liệu được phép vào SQLite, làm sạch, tạo embedding và lập chỉ mục truy xuất. Dữ liệu từ các nguồn đã được cấp quyền và lập chỉ mục đóng góp vào bộ não chung của owner; việc truy xuất vẫn theo quyền hiện tại. Ứng dụng không tự fine-tune model.

Hướng dẫn này dành cho bản xem trước riêng của owner trên Windows 11 x64. Bộ cài cuối cùng chưa sẵn sàng, chưa ký; cài đặt thực tế, trải nghiệm của owner và chạy tải thật đủ 72 giờ vẫn đang chờ kiểm chứng. Chưa có kết luận sẵn sàng beta hoặc phát hành công khai. Xem [phạm vi owner preview](docs/superpowers/specs/2026-10-10-owner-preview-amendment.md) và [quy trình trải nghiệm](docs/handoff/UAT_PROTOCOL.md).

## 1. Mở ứng dụng và thiết lập

Luồng sử dụng bình thường đi qua ứng dụng Windows, không cần terminal hoặc cài riêng Python, Node hay MySQL.

1. Mở **Telegram AI** trên Windows. Nếu ứng dụng đang dừng, bấm **Khởi động** và chờ kết quả thực tế.
2. Bấm **Thiết lập kết nối** để mở **Thiết lập Telegram AI**, rồi **Bắt đầu thiết lập** khi nút khả dụng. Làm theo bước tiếp theo và bấm **Kiểm tra lại và tiếp tục** sau mỗi thay đổi.
3. Trong **Đăng nhập Telegram**, mở cửa sổ **Đăng nhập Telegram • Telegram AI**. Nhập API ID/API hash tại đây; chọn **Tạo / làm mới QR** hoặc **Gửi mã tới điện thoại**. Nếu được yêu cầu, nhập OTP rồi **Xác nhận mã OTP**, hoặc mật khẩu hai bước rồi **Xác nhận 2FA**. Chỉ kết quả xác minh mới xác nhận tài khoản.
4. Trong **Kết nối bot**, mở **Kết nối và ghép bot • Telegram AI**. Dùng **Mở BotFather** để tạo bot nếu cần; nhập token vào cửa sổ Windows và bấm **Xác minh token**. Sau khi owner đã được xác minh, chọn **Tạo / làm mới liên kết ghép**, **Mở bot để ghép**, rồi Start bằng đúng tài khoản owner. Liên kết ghép dùng một lần, hết hạn sau 5 phút; tạo lại nếu hết hạn. Mã thủ công hiển thị trong cửa sổ này chỉ dành cho lệnh /pair của luồng ghép, không phải OTP hay API key.
5. Thiết lập AI theo mục 2. Có thể chọn **Tiếp tục khi chưa bật AI**; lựa chọn này không chứng minh AI hoặc chỉ mục đã sẵn sàng.
6. Bấm **Mở dashboard** từ ứng dụng Windows. Vé mở dùng một lần và hết hạn sau 30 giây; nếu không vào được, quay lại ứng dụng và mở lại. Phiên chỉ có quyền thiết lập chưa cho phép quản lý nguồn; cần owner đã xác minh và ghép bot.

API key, bot token, API hash, OTP và 2FA chỉ nhập vào các ô native của tài khoản Windows hiện tại (SID). Các ô secret luôn trống, không điền lại secret đã lưu. Không gửi secret vào chat, dashboard, địa chỉ trình duyệt, ảnh chụp hay yêu cầu hỗ trợ.

Dashboard chỉ phục vụ trên máy cục bộ. Menu chính gồm **Tổng quan**, **Kết nối**, **Nguồn Telegram**, **Tri thức**, **Công việc**, **Vận hành**, **Cài đặt & trợ giúp**. Trên màn hình hẹp, dùng **Mở menu**. Trong **Chức năng nâng cao** có **AI & RAG**, **Local Models**, **Bộ nhớ & lưu trữ**, **Audit Log** và các công cụ quản lý khác. Đọc lại hướng dẫn tại **Cài đặt & trợ giúp → USER_GUIDE.md**.

## 2. Kết nối AI và chọn model

Mở **Thiết lập kết nối → Kết nối AI**, hoặc **Kết nối → Mở cấu hình AI trên Windows**. Cửa sổ **Kết nối AI • Telegram AI** có **Nhà cung cấp**, **Chức năng**, **Model**, **Endpoint**, **API key native** và **Kiểm tra và lưu**. Kiểm tra riêng **AI trả lời** và **Chỉ mục embedding (kiểm tra riêng)**; chat hoạt động không chứng minh embedding hoạt động. **Kiểm tra key đã lưu** không hiện key. **Lưu chưa xác minh (AI chưa sẵn sàng)** vẫn cần kiểm tra lại.

OpenAI/OpenRouter cần consent cloud rõ ràng. Ô **Tôi đồng ý kết nối cloud (chưa gửi nội dung nguồn).** cho phép kết nối; việc gửi nội dung vẫn phụ thuộc quyền nguồn, route, consent và ngân sách. Không tự bật cloud fallback khi local lỗi.

Với Ollama, dùng **Mở trang Ollama Windows** để cài theo trang chính thức nếu chưa có. Ứng dụng không tự cài Ollama. Chọn model, bấm **Xem dung lượng model**, đọc dung lượng, xác nhận tải rồi **Tải model đã chọn**. **Hủy tải** hủy download; tiến độ layer không phải xác nhận toàn bộ model hoàn tất. Tải lại có thể dùng phần dữ liệu Ollama còn hợp lệ.

**Chức năng nâng cao → AI & RAG** hiển thị provider được máy chủ xác nhận. Nếu đổi provider thất bại, provider đã xác nhận trước đó vẫn được hiển thị; kiểm tra kết nối native trước khi chọn lại. **Local Models** quản lý model đã cài, tải và lựa chọn chat/embedding. Đổi model hoặc chỉ mục cần preview, xác nhận và kiểm tra kết quả; không xem một lựa chọn vừa lưu là corpus đã được chuyển đổi. Không xóa model đang hoạt động.

## 3. Một nguồn, câu hỏi đầu tiên

Trong **Tổng quan**, **Nguồn Telegram** hoặc **Tri thức**, dùng khung **Một nguồn, câu hỏi đầu tiên**:

1. Tìm theo tên hoặc Chat ID, lọc loại nguồn, chọn **Chọn một nguồn**, rồi **Lưu nguồn đã chọn**. Kiểm tra dòng **Nguồn đã lưu**. Lưu lựa chọn chỉ ghi ý định; chưa ALLOW, cấp quyền hay tạo công việc.
2. Bấm **Xem và xác nhận quyền**. Đọc đúng nguồn/Chat ID, quyền sẽ cấp và gỡ, chế độ AI, route chat, embedding, consent, giới hạn học và thời điểm hết hạn; kiểm tra retention/quota hiện tại trong **Quản lý quyền nâng cao**. Mẫu knowledge có thể gỡ quyền đang bật; quyền group /ask hiện có được giữ, không tự bật. Bản preview không cấp consent cloud hoặc chứng minh model đã chạy thành công. Chỉ xác nhận khi phạm vi phù hợp; có thể hủy tại bước preview.
3. Sau xác nhận, chờ kết quả worker. Luồng này đồng bộ tối đa **1000 tin lịch sử gần nhất**, trong một khoảng lịch sử hữu hạn được chốt cho lần chạy. Đây không phải toàn bộ lịch sử nguồn. Theo dõi tin mới sau đó vẫn theo quyền, retention và quota đã xác nhận.
4. Xem đúng ID công việc của nguồn: tin đã sync, đã tạo mới/tái sử dụng, đã lọc, trùng lặp, bỏ qua và lỗi. **Chưa biết** hoặc thiếu tiến độ không có nghĩa là 0 hay 100%. Chạm quota có thể giữ phần đã lưu nhưng để công việc chưa hoàn tất; số còn lại có thể chưa biết. Đọc cảnh báo và kiểm tra giới hạn trước khi tiếp tục, không coi một lần chạy là đã học hết nguồn.
5. Bấm **Kiểm tra nguồn**. Chỉ khi dữ liệu chỉ mục hiện tại đã được xác minh và backend cho phép hỏi thử mới có **Mở bot để hỏi thử**. Trong chat riêng với bot, chính owner gửi **/ask in:CHAT_ID câu hỏi của bạn**, thay CHAT_ID bằng ID đã chọn đang hiển thị. Giữ nguyên đầy đủ ID, kể cả dấu âm.
6. Kiểm tra câu trả lời có trích dẫn nguồn và dòng kết quả từ backend. Mở bot, gửi câu hỏi hoặc job báo hoàn tất chưa đủ xác minh câu trả lời đầu tiên. Nếu kết quả chưa chắc chắn, kiểm tra nguồn và lần gửi trước; không lặp lại lần gửi cũ để sửa trạng thái.

Preview cũ có thể mất hiệu lực khi đổi nguồn, quyền, cấu hình AI/consent, ghép bot hoặc khởi động lại. Tải lại trạng thái rồi tạo preview hiện tại. Xác nhận mẫu quyền chỉ tạo công việc mới theo quyền sau khi áp dụng; không làm sống lại job hay câu trả lời cũ đã bị thu hồi. BLOCK thắng tiến độ hoặc preview cũ; BLOCK rồi ALLOW lại vẫn cần bằng chứng hiện tại.

## 4. Quản lý nguồn và dữ liệu

Mở **Nguồn Telegram**, tìm theo tên, @username hoặc Chat ID, rồi **Quản lý**. Chỉ bật ALLOW và các quyền cần dùng. Các thay đổi nhạy cảm có preview và owner xác nhận trong **Công việc / Hành động chờ**; xác nhận không đồng nghĩa worker đã hoàn tất.

Chi tiết nguồn giữ đủ 19 quyền, chia theo các nhóm:

| Nhóm | Quyền hiển thị |
| --- | --- |
| Đọc và đồng bộ | read_messages, sync_history, monitor_new_messages, search_messages |
| AI và tri thức | summarize, analyze_files, auto_knowledge, group_ai_ask |
| Công việc và memory | extract_tasks, create_memories, auto_task_suggestion |
| Gửi và chỉnh sửa | send_messages, edit_own_messages |
| Moderation | delete_own_messages, delete_any_messages, pin_messages, moderate_messages, auto_moderation |
| Tải dữ liệu | download_media |

Sáu chế độ AI của nguồn:

| Chế độ | Ý nghĩa |
| --- | --- |
| inherit — Theo cấu hình toàn cục | Dùng cấu hình chung trong giới hạn quyền nguồn. |
| local_only — LOCAL ONLY | Không gửi nội dung nguồn lên cloud chat hoặc cloud embedding, kể cả truy xuất chung và fallback. |
| local_first — Local trước, cloud fallback | Chỉ fallback cloud khi đã chủ động cho phép, có consent và ngân sách. |
| cloud_only — Cloud only | Chỉ dùng tuyến cloud được phép. |
| cloud_first — Cloud trước, local fallback | Ưu tiên cloud được phép, fallback theo cấu hình nguồn. |
| off — Tắt AI riêng nguồn | Tắt AI cho nguồn này. |

**Lưu AI route**, **Lưu hiệu quả AI** và **Lưu retention & quota** chỉ được coi là lưu khi có phản hồi máy chủ. **Bản nháp chưa lưu** có thể còn trên biểu mẫu sau lỗi; so sánh với **Cấu hình đã lưu** trước khi gửi tiếp. Nếu đã được chấp nhận nhưng tải lại thất bại, kiểm tra trạng thái mới, không suy ra thay đổi đã bị hoàn tác.

Retention/quota gồm **Số ngày giữ dữ liệu**, **Số tin tối đa**, **Số vector tối đa**, **Dung lượng tối đa (MB)**. Giới hạn áp dụng cho đồng bộ, học và dọn dữ liệu; tăng giới hạn không tự chứng minh phần lịch sử còn lại đã được xử lý.

**Group AI ask** cho phép gọi **@your_assistant_username /ask câu hỏi** trong group khi có quyền; thay username bằng tên bot đã xác minh. Câu hỏi group mặc định chỉ truy xuất nguồn của group đó. Không dùng group để nhập credential.

**AUTO xóa link mới của non-admin** cần preview và xác nhận owner một lần khi bật; kiểm tra quyền Telegram thực tế. Rời nguồn và xóa dữ liệu cũng cần preview phạm vi. Nguồn bạn là creator bị chặn rời; admin cần xác nhận riêng. Dữ liệu cũ không tự bị xóa khi rời; yêu cầu xóa là bước xác nhận riêng. Khi xóa toàn bộ, đọc số tin/vector/tệp và nhập đúng tên nguồn; không xóa khi learning job còn hoạt động.

## 5. Theo dõi học, công việc và trạng thái

Trong **Tri thức**, **Tạm dừng** job đang chờ đưa về paused; job đang chạy có thể trả pause_requested trước khi dừng. Chỉ **Tiếp tục** khi paused; kết quả trở về hàng đợi queued. **Thử lại** dành cho job failed sau khi đã xử lý nguyên nhân. Luồng learning không có nút hủy job; **Hủy** hành động chờ và **Hủy tải** model là các thao tác riêng.

completed_with_warning nghĩa là đã giữ phần hoàn tất nhưng còn phần chưa xử lý. uncertain hoặc **CẦN ĐỐI CHIẾU INDEX** yêu cầu đối chiếu trước khi gửi lại. Dùng **Đối chiếu job** khi UI báo chưa rõ kết quả thao tác; tại nguồn dùng **Kiểm tra coverage**, rồi **Preview khôi phục index** khi cần. Job hoàn tất không tự chứng minh độ phủ vector hoặc hỏi AI sẵn sàng.

Trong **Kết nối**, kiểm tra riêng ứng dụng, tài khoản Telegram, bot, AI chat, embedding và lưu trữ. **CHƯA KIỂM TRA**, **ĐANG KIỂM TRA**, **DỮ LIỆU CŨ** hoặc trạng thái có giới hạn không phải Online. Thiếu hoặc quá cũ thời điểm kiểm tra cần **Kiểm tra lại**; snapshot được giữ sau lỗi chỉ là dữ liệu cũ. Nhãn SSE **LIVE** chỉ phản ánh luồng realtime, không chứng minh mọi provider đang hoạt động. **Vận hành** phân biệt cấu hình scheduler với số liệu thực đã đo; **Chưa biết** không phải 0.

Nút mở cấu hình từ dashboard chỉ gửi yêu cầu đến ứng dụng Windows. Thông báo đã xếp hàng (queued) không chứng minh cửa sổ đã mở, kết nối thành công hoặc công việc hoàn tất. Quay lại cửa sổ native và kiểm tra kết quả.

## 6. Sao lưu hằng ngày và khôi phục

Sau một ngày làm việc hoặc trước thay đổi lớn, mở **Chức năng nâng cao → Bộ nhớ & lưu trữ → Sao lưu và khôi phục**. Bấm **Tạo bản sao lưu**, chờ kết quả, rồi **Tải lại danh sách sao lưu**. Khi lỗi hoặc báo đang bận, kiểm tra lại danh sách trước khi tạo tiếp. Danh sách này chỉ liệt kê archive do dashboard quản lý, không phải mọi tệp bạn từng lưu bằng cửa sổ Windows.

**CHỈ MANIFEST** (manifest_only) chỉ cho biết đã đọc manifest/metadata: chưa kiểm tra checksum toàn bộ nội dung, DB ở vùng staging hay khôi phục thử. Không dùng nhãn này để kết luận archive an toàn. Backup portable không chứa API key, token, OTP hoặc phiên Telegram; cần giữ backup ở nơi bạn kiểm soát.

Để tự chọn nơi lưu, dùng **Telegram AI → Dữ liệu và sao lưu → Tạo bản sao lưu…** và cửa sổ **Lưu bản sao lưu**. Khôi phục luôn qua ứng dụng Windows:

1. Mở **Dữ liệu và sao lưu → Khôi phục từ bản sao lưu…**.
2. Trong **Chọn bản sao lưu**, chọn tệp .zip.
3. Đọc **Xác nhận khôi phục dữ liệu**: dữ liệu profile hiện tại sẽ được thay thế, giữ bản sao trước khi khôi phục; credential/phiên Telegram và quyền đã thu hồi thuộc thiết bị hiện tại được giữ. Chọn **Hủy, giữ dữ liệu hiện tại** nếu chưa muốn thay đổi. Preview này mô tả hậu quả, chưa xác minh archive.
4. Chỉ bấm **Xác nhận khôi phục** khi đồng ý. Ứng dụng dừng runtime, giữ khóa phiên và chặn mọi tiến trình ghi dữ liệu, kiểm tra checksum, chuyển đổi schema và tính toàn vẹn trong vùng tạm trước khi thay dữ liệu. Không chỉ dựa vào nhãn “đã dừng” để kết luận các tiến trình ghi đã bị chặn. Chờ kết quả; không đóng cưỡng bức khi đang xử lý.
5. Sau thành công, runtime vẫn đang dừng và chỉ mục AI cần kiểm tra/khôi phục. Dùng **Khởi động**, kiểm tra kết nối và tại nguồn **Kiểm tra coverage → Preview khôi phục index**, xác nhận phạm vi rồi chờ kết quả trước khi hỏi lại.

Nếu báo dữ liệu hiện tại được giữ nguyên, kiểm tra lỗi trước khi thử lại. Nếu chưa xác minh được dữ liệu sau khôi phục, giữ chế độ bảo trì và các bản sao lưu; không khởi động hoặc gửi lại để ép hoàn tất. Dashboard không nhận đường dẫn hay upload tệp khôi phục.

## 7. Lỗi và yêu cầu hỗ trợ

| Lỗi/trạng thái | Việc cần làm |
| --- | --- |
| 401 — phiên hết hiệu lực | Mở lại dashboard từ Telegram AI trên Windows; kiểm tra owner đã ghép. |
| 403 — không đủ quyền | Kiểm tra quyền nguồn và phiên owner trên Windows, rồi tải lại trạng thái. Không tự bật thêm quyền để vượt lỗi. |
| 409 — trạng thái thay đổi/chưa sẵn sàng | Kiểm tra Kết nối, tải lại nguồn/công việc; đọc preview hiện tại trước khi thử lại. |
| 429 — giới hạn yêu cầu | Chờ một lúc rồi tải lại; kiểm tra quota/ngân sách trước khi tiếp tục. |
| 500 — máy chủ chưa xác nhận | Kiểm tra ứng dụng và tải lại trạng thái; không gửi lại thao tác khi kết quả chưa rõ. |
| 503 — dịch vụ/bảo trì | Chờ thao tác Windows hoặc bảo trì hoàn tất, rồi kiểm tra lại. |
| Mất mạng/không đọc được phản hồi | Giữ thông tin lần gửi, đối chiếu trạng thái mới; không suy ra thao tác đã thất bại hoặc tự gửi lại. |
| Không có câu trả lời | Kiểm tra nguồn được chọn, ALLOW/quyền, pairing, chat và embedding riêng, coverage, quota và cảnh báo. |

Nếu cần hỗ trợ, ghi bước đang làm, thời điểm, mã lỗi và trạng thái; che thông tin riêng trước khi gửi ảnh. Trong **Chức năng nâng cao → Audit Log**, đặt bộ lọc/trang cần xem rồi **Xuất CSV**. CSV được đọc mới theo **trang và bộ lọc hiện tại**, tối đa 100 sự kiện, không giới hạn theo ngày; gồm đúng bốn cột occurred_at, action, target_type, outcome. Kiểm tra trình duyệt đã tải tệp; thông báo bắt đầu tải chưa chứng minh hệ điều hành đã lưu thành công.

CSV hỗ trợ này không phải toàn bộ audit/log: không xuất ID, nội dung tự do, credential, cấu hình hay tin Telegram. Không gửi raw log, DB, backup hoặc session để thay thế CSV; không có cam kết rằng mọi tệp bất kỳ đã được làm sạch secret.

## 8. Dừng và giữ dữ liệu

Ứng dụng dùng một owner/profile cho mỗi SID Windows; dữ liệu SQLite, cấu hình, vector, log và backup nằm ngoài thư mục cài đặt, mặc định dưới %LOCALAPPDATA%/TelegramAIPersonalAssistant. Không đưa DB đang dùng vào thư mục đồng bộ cloud. Profile mới bắt đầu trống; không xóa profile hiện có để sửa setup và không kết nối/migrate/xóa dữ liệu MySQL cũ.

Đóng cửa sổ có thể để ứng dụng tiếp tục chạy dưới khay hệ thống. Khi muốn kết thúc, dùng **Dừng an toàn**, hoặc menu khay **Thoát… → Dừng an toàn rồi thoát**. Giữ các bản sao lưu trước khi cập nhật, khôi phục hoặc xóa dữ liệu.
