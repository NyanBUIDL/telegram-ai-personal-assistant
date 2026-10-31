# Xử lý lỗi

## `start.bat` không tìm thấy Python

Cài Python 3.12/3.13 từ python.org và bật tùy chọn thêm Python vào PATH. Mở terminal mới,
chạy `python --version`. Nếu lệnh mở Microsoft Store hoặc không chạy được, kiểm tra PATH
và Python đã cài trước khi chạy lại `start.bat`.

## Python trong `.venv` không chạy được / báo đường dẫn máy khác

Virtual environment chỉ dùng trên máy đã tạo ra nó. Bản tải về hiện có thể chứa `.venv`
từ máy tác giả; chương trình sẽ báo rõ rồi dừng, giữ nguyên thư mục đó.
Đóng ứng dụng trước khi sửa. Tại thư mục gốc dự án, đổi tên `.venv` thành một tên chưa tồn tại
(ví dụ `.venv.old`), sau đó chạy `start.bat` để tạo môi trường mới. Không xóa database,
Credential Manager, thư mục dữ liệu hay session để khắc phục lỗi Python.

## Cài dependency thất bại

`start.bat` dừng ngay nếu tạo môi trường, cài package, kiểm tra dependency hoặc lệnh khởi động
thất bại. Đọc lỗi pip ngay phía trên, kiểm tra kết nối mạng và Python 3.12+, rồi chạy lại.
Script không tự nâng cấp pip mỗi lần chạy. Có thể kiểm tra riêng bằng:

```powershell
.\.venv\Scripts\python.exe -m pip check
```

## Bot đang chờ câu hỏi/từ khóa và không quay lại menu

Gửi `/cancel` để thoát bước nhập; gửi `/help` hoặc `/groups` để mở menu.
Các lệnh không bị đưa vào AI hay tìm kiếm như một câu hỏi. `/cancel <id>` hủy một
pending action cụ thể; nếu ID sai, hết hạn hoặc đã xử lý, bot trả hướng dẫn để thử lại.

## Dashboard không có giao diện sau khi tải mã nguồn

Tại `dashboard-prototype`, dùng Node.js 22+ và chạy `npm ci`, rồi `npm run build`.
Backend phục vụ giao diện từ `dashboard-prototype/dist/client`. Chạy ứng dụng và lấy
mã bằng `.\.venv\Scripts\tg-assistant.exe dashboard-code` từ thư mục gốc.
Khi phát triển giao diện với `npm run dev`, chỉ bind `127.0.0.1`; không mở port ra LAN.

## MySQL port FAIL

Chạy `tg-assistant doctor`, kiểm tra Windows service MySQL và cổng cấu hình. Ứng dụng
không tự bật service hay sửa firewall. Dùng `127.0.0.1`, không mở MySQL ra Internet.

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
