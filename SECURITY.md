# Bảo mật

## Secret

Secret được lưu trong Windows Credential Manager với service
`TelegramAIPersonalAssistant`. `.env` chỉ chứa cấu hình không bí mật. Không gửi API hash,
bot token, OTP, 2FA, MySQL password, OpenAI key hay session qua Telegram.

Logging dùng processor redaction trước khi ghi JSON. Không bật debug payload của Telethon,
aiogram hoặc OpenAI trên dữ liệu thật.

## Ranh giới quyền

Policy Engine kiểm tra owner, allowlist, quyền con, quyền Telegram thực tế, rate limit,
tính an toàn và xác nhận. GPT không phải lớp phân quyền. Hành động phá hủy không được
thực thi từ output AI; nó phải thành pending action, được chủ sở hữu xác nhận và được
kiểm tra lại ngay trước lúc thực thi.

Control bot chỉ cho owner điều khiển qua chat riêng có đúng Telegram ID; callback
từ group hoặc không gắn với tin nhắn riêng không được dùng để mở flow quản trị.
Đây là ranh giới riêng với tính năng hỏi AI trong group qua Telegram Client.

Bộ lọc lịch sử bằng AI cũng cần nguồn ALLOW và quyền đọc; runtime kiểm tra lại
global AI và route của nguồn trước khi gửi tới OpenAI. LOCAL ONLY, tắt AI hoặc
route không cho OpenAI không được vượt qua bằng nút lọc lịch sử.

Input AI tách câu hỏi khỏi nguồn không tin cậy bằng JSON và chỉ dẫn; mọi nguồn
vẫn phải qua kiểm tra quyền. Redaction dùng các mẫu secret đã biết, không bảo đảm
phát hiện mọi dữ liệu nhạy cảm. Tiêu chí lọc chứa secret đã nhận diện bị từ chối;
nội dung nguồn được redact trước khi cắt độ dài. Chỉ dẫn prompt không phải cơ chế
phân quyền và không bảo đảm miễn nhiễm prompt injection.

Kiểm tra citation chỉ xác nhận mã nguồn nằm trong context đã cấp quyền; không chứng minh
mọi nhận định của model là đúng. Câu trả lời trống, lỗi hoặc citation không hợp lệ không
được xem là câu trả lời thành công trong cache; chế độ có dẫn chứng không tự gán nguồn.

## Báo cáo sự cố

Nếu nghi lộ secret: dừng ứng dụng, thu hồi bot token/API key, chạy
`tg-assistant reconfigure`, rồi `tg-assistant logout` nếu session Telegram có nguy cơ bị
lộ. Kiểm tra audit/log đã redacted; không gửi log chưa kiểm tra cho bên thứ ba.
