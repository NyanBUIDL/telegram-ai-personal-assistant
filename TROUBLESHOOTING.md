# Xử lý lỗi

## `start.bat` không tìm thấy Python

Cài Python 3.12/3.13 từ python.org và bật tùy chọn thêm Python vào PATH. Xóa `.venv` lỗi
chỉ khi chắc chắn nó nằm trong thư mục dự án, rồi chạy lại `start.bat`.

## Profile database không mở được

Chạy `tg-assistant doctor`, kiểm tra quyền truy cập profile và dung lượng ổ đĩa.
Ứng dụng chỉ hỗ trợ SQLite và không cần dịch vụ SQL. Nếu profile cũ khai báo MySQL,
giữ nguyên dữ liệu cũ và chọn profile mới; không sửa thành SQLite để tự nhập dữ liệu.
Không xóa file khóa hoặc database khi worker còn chạy.

## Credential MISSING

Chạy `tg-assistant reconfigure`. Nhập secret trong terminal; không đặt vào `.env`.

## Pairing hết hạn

Chạy lại `tg-assistant start` để tạo mã mới. Sender của `/pair` phải có Telegram numeric
ID đúng bằng tài khoản MTProto vừa đăng nhập.

## FloodWait hoặc Telegram từ chối

Ứng dụng dừng retry không giới hạn và ghi lý do. Chờ thời gian Telegram yêu cầu. Nếu là
thiếu quyền admin/gửi/ghim/xóa, sửa quyền thật trong Telegram hoặc tắt quyền tương ứng;
không có cơ chế vượt quyền.

## AI không hoạt động

Bot local vẫn hoạt động. Chạy `tg-assistant doctor` để xem provider đang chọn và
credential tương ứng. Dùng `tg-assistant ai-provider openai|openrouter|off` để đổi
provider; API key được nhập ẩn. Đồng thời kiểm tra ngân sách ngày/tháng và model
trong cấu hình. Khi ngân sách đạt 100%, AI chủ động tạm dừng.

## Session không giải mã được

Không xóa credential/session tùy tiện. Nếu key Windows Credential Manager đã mất, session
không thể phục hồi; chạy `tg-assistant logout`/thiết lập lại và đăng nhập Telegram mới.
