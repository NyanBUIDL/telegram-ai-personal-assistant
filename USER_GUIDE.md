# Hướng dẫn sử dụng Telegram AI Personal Assistant

## Mục tiêu

Ứng dụng biến lịch sử chat được bạn cấp quyền trong các group và channel Telegram thành một kho tri thức chung. “Học” nghĩa là đồng bộ vào MySQL, làm sạch, tạo embedding và lập chỉ mục để AI truy xuất; ứng dụng không tự fine-tune model.

## Bắt đầu

1. Chạy `start.bat`.
2. Mở `http://127.0.0.1:8765`.
3. Lấy mã đăng nhập bằng `.\.venv\Scripts\tg-assistant.exe dashboard-code`.
4. Nhập mã 8 số. Phiên đăng nhập được giữ bằng cookie HttpOnly.

## Cấp quyền cho nguồn

Mở **Nhóm và channel**, tìm theo tên, `@username` hoặc Chat ID, rồi chọn **Quản lý**. Bật ALLOW trước, sau đó chỉ bật đúng quyền cần dùng. Các thay đổi nhạy cảm tạo bản xem trước và chờ owner xác nhận.

## Đưa nguồn vào bộ não chung

Mở **Kho tri thức**, lọc hoặc tìm nguồn, chọn từng nguồn hay chọn cả trang rồi bấm **Học nguồn đã chọn**. Mỗi nguồn có một learning job riêng với các pha: đang chờ, đồng bộ MySQL, tạo embedding và hoàn tất. Có thể pause, resume hoặc retry job lỗi.

## Hỏi AI

- Chat riêng với bot: `/ask câu hỏi`.
- Trong group đã bật quyền AI: `@your_assistant_username /ask câu hỏi`.
- Câu hỏi trong group mặc định chỉ truy xuất dữ liệu của group đó.
- Câu hỏi tin tức và câu trả lời trong bot riêng kèm nguồn. Tương tác AI ngoài bot không bắt buộc hiện dẫn chứng, trừ câu hỏi tin tức.
- Dữ liệu được ưu tiên trong 7 ngày gần nhất tính từ lúc hỏi.

## Chọn AI provider

Trong **AI & RAG**, chọn OpenAI, OpenRouter, Ollama hoặc Off. Khi Off, toàn bộ AI bị tắt. Mỗi group có thể đặt AI mode riêng, gồm local-only, local-first, cloud-only, cloud-first hoặc kế thừa cấu hình chung.

## Quản lý Ollama

Trong **Local models**, xem model đã cài, tải model mới, chọn model chat và embedding, hoặc tạo preview xóa model. Không thể xóa model đang hoạt động.

## Retention và quota

Trong chi tiết nguồn, đặt số ngày lưu, số message tối đa, dung lượng MB và số vector. Worker áp dụng các giới hạn này khi đồng bộ, học và cleanup.

## Rời group/channel

Chỉ rời khi đề xuất có lịch sử hoạt động đủ tin cậy hoặc khi bạn chủ động chọn. Hệ thống không đề xuất nguồn không có lịch sử, không tự rời, chặn nguồn bạn là creator và yêu cầu xác nhận riêng nếu là admin. Dữ liệu cũ mặc định được giữ; yêu cầu xóa cần bước xác nhận thứ hai.

## Xóa dữ liệu đã học

Các phạm vi gồm vector, chỉ mục tìm kiếm, nội dung MySQL, media, checkpoint hoặc toàn bộ. Hệ thống luôn hiển thị số tin/vector/tệp bị ảnh hưởng, chặn xóa khi learning job còn hoạt động và yêu cầu nhập đúng tên nguồn khi xóa toàn bộ.

## Pending action và audit

Mọi hành động nhạy cảm xuất hiện ở **Hành động chờ**. Kiểm tra preview, phạm vi và cảnh báo trước khi xác nhận. Worker kiểm tra quyền thực tế lần cuối rồi ghi kết quả vào Audit log.

## Xử lý sự cố

- Không đăng nhập được: tạo mã mới vì mã cũ hết hạn sau 5 phút.
- Nguồn luôn “đang chờ”: kiểm tra AI provider, worker và learning job.
- Không có câu trả lời: kiểm tra ALLOW, quyền đọc/tìm kiếm, phạm vi 7 ngày và số vector.
- Ollama không hoạt động: kiểm tra Ollama đang chạy và model đã được tải.
- API trả 401: đăng nhập lại. API trả 403: tải lại trang để nhận CSRF token mới.

## Riêng tư

API key, Telegram token, mật khẩu và secret chỉ được nhập qua terminal/setup, không được hiển thị hoặc lưu ở frontend. Dashboard chỉ phục vụ trên loopback `127.0.0.1`.
