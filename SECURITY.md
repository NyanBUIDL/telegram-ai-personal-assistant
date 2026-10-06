# Bảo mật

## Secret

Secret được lưu trong Windows Credential Manager với service
`TelegramAIPersonalAssistant`. Cấu hình không bí mật nằm trong `config/settings.json`
của profile; ứng dụng không tự đọc `.env` từ thư mục cài đặt. Không gửi API hash,
bot token, OTP, 2FA, OpenAI key hay session qua Telegram.

Logging dùng processor redaction trước khi ghi JSON. Không bật debug payload của Telethon,
aiogram hoặc OpenAI trên dữ liệu thật.

## Ranh giới quyền

Policy Engine kiểm tra owner, allowlist, quyền con, quyền Telegram thực tế, rate limit,
tính an toàn và xác nhận. GPT không phải lớp phân quyền. Hành động phá hủy không được
thực thi từ output AI; nó phải thành pending action, được chủ sở hữu xác nhận và được
kiểm tra lại ngay trước lúc thực thi.

## Báo cáo sự cố

Nếu nghi lộ secret: dừng ứng dụng, thu hồi bot token/API key, chạy
`tg-assistant reconfigure`, rồi `tg-assistant logout` nếu session Telegram có nguy cơ bị
lộ. Kiểm tra audit/log đã redacted; không gửi log chưa kiểm tra cho bên thứ ba.
