# Hướng dẫn sử dụng Telegram AI Personal Assistant

## Mục tiêu

Ứng dụng biến lịch sử chat được bạn cấp quyền trong các group và channel Telegram thành một kho tri thức chung. “Học” nghĩa là đồng bộ vào MySQL, làm sạch, tạo embedding và lập chỉ mục để AI truy xuất; ứng dụng không tự fine-tune model.

## Bắt đầu

1. Chạy `start.bat`, hoàn thành wizard và nhắn riêng `/pair ...` cho control bot.
2. Trong chat riêng với control bot, gửi `/start` để thấy ngay nút chọn nguồn, quản lý nhóm và AI.
3. Thử với một nguồn trước; xem quyền và xác nhận học, chờ tiến độ rồi tìm từ khóa đã đồng bộ.
4. Dùng `/cancel` để thoát bước nhập và `/help` để mở tài liệu đầy đủ. Bot bỏ qua lệnh quản trị
   trong group, kể cả khi người gửi là owner; group AI dùng Telegram Client riêng.

Dashboard là màn hình quản trị phụ: mở `http://127.0.0.1:8765`, lấy mã bằng
`.\.venv\Scripts\tg-assistant.exe dashboard-code` rồi nhập mã 8 số. Phiên dùng cookie HttpOnly.

## Cấp quyền cho nguồn

Mở **Nhóm và channel**, tìm theo tên, `@username` hoặc Chat ID, rồi chọn **Quản lý**. Bật ALLOW trước, sau đó chỉ bật đúng quyền cần dùng. Các thay đổi nhạy cảm tạo bản xem trước và chờ owner xác nhận.

## Đưa nguồn vào bộ não chung

Mở **Kho tri thức**, lọc hoặc tìm nguồn, chọn từng nguồn hay chọn cả trang rồi bấm **Học nguồn đã chọn**. Mỗi nguồn có một learning job riêng với các pha: đang chờ, đồng bộ MySQL, tạo embedding và hoàn tất. Có thể pause, resume hoặc retry job lỗi.

Mỗi đợt chọn tối đa 500 nguồn. Nút chọn theo bộ lọc chọn lô 500 nguồn đầu tiên và báo
số đã chọn/tổng cùng số còn lại; không có nghĩa đã chọn hết nếu kết quả nhiều hơn 500.
Sau restart, yêu cầu pause còn dở được giữ ở trạng thái paused; chỉ tiếp tục khi owner resume.

## Hỏi AI

- Chat riêng với bot: `/ask câu hỏi`.
- Trong group đã bật quyền AI: `@your_assistant_username /ask câu hỏi`.
- Câu hỏi trong group mặc định chỉ truy xuất dữ liệu của group đó.
- Câu hỏi tin tức và câu trả lời trong bot riêng kèm nguồn. Tương tác AI ngoài bot không bắt buộc hiện dẫn chứng, trừ câu hỏi tin tức.
- Dữ liệu được ưu tiên trong 7 ngày gần nhất tính từ lúc hỏi.

Nếu model trả lời trống, chưa hoàn tất hoặc trích dẫn mã nguồn không có trong context,
bot báo lỗi để thử lại. Có dẫn chứng không đồng nghĩa mọi nội dung AI đều chính xác.

## Nhắc việc theo giờ Việt Nam

Ví dụ: `/remind 9 giờ tối mai | Kiểm tra báo cáo` hoặc
`/task_add Hoàn thiện báo cáo | thứ Hai tuần sau lúc 9 giờ`.

- Chấp nhận `HH:MM`, `9h30`, `9 giờ`, hôm nay, mai/ngày mai, ngày kia, tuần sau,
  cuối tháng và thứ trong tuần (tên hoặc số 2–7).
- Nếu chỉ có ngày, dùng 08:00. Buổi không có giờ dùng sáng 08:00, trưa 12:00,
  chiều 14:00, tối 20:00, đêm 22:00.
- `thứ Hai` là lần thứ Hai tiếp theo; `thứ Hai tuần sau` là thứ Hai của tuần lịch tiếp theo.
- 12 giờ sáng/tối/đêm được hiểu là 00:00 của ngày đã chọn; dùng `00:00` để rõ nghĩa.
- Chưa hỗ trợ câu tương đối như `sau 2 giờ`. Chuỗi không hiểu hoặc mâu thuẫn sẽ bị từ chối,
  không âm thầm lên lịch theo giờ hiện tại.
- Xác nhận nhắc việc hiển thị thời gian đã hiểu với `Asia/Ho_Chi_Minh`; database vẫn lưu UTC.

## Dữ liệu cũ và trạng thái kết nối

Nếu refresh lỗi, dashboard giữ snapshot trước và hiển thị “Dữ liệu cũ”, thời điểm tải
thành công cuối và nút Thử lại. Kiểm tra scope trước khi thao tác. UNKNOWN nghĩa là
chưa có phép đo; STALE nghĩa phép đo cũ quá 5 phút, thời điểm lỗi hoặc snapshot lỗi.
Những nhãn này không thay cho `doctor` hay kiểm tra dịch vụ thật. Cấu hình AI có mặt
không đồng nghĩa provider đang kết nối được.

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
