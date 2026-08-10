# Security Best Practices Review

## Tóm tắt

Phạm vi rà soát: Python 3.12+, Telethon, aiogram, SQLAlchemy/MySQL, OpenAI SDK,
Qdrant local, CLI/subprocess và file Windows. Không có web server hoặc frontend.

Không còn phát hiện Critical/High đã biết sau vòng sửa này. Các biện pháp chính gồm:
owner-only, default deny, kiểm tra quyền Telegram thực tế, pending action một lần, redaction
trước search/RAG/audit, Credential Manager, session mã hóa, subprocess không dùng shell,
identifier SQL được allowlist và purge xác nhận đúng đường dẫn.

## Đã sửa

### SEC-001 — High — Secret có thể đi vào RAG/preview

Tác động trước sửa: tin nhắn chứa chuỗi giống token có thể được lặp lại trong bot,
embedding hoặc context gửi tới AI.

Khắc phục:

- Search redaction tại `src/tg_assistant/services/search.py:156`.
- Semantic RAG redaction tại `src/tg_assistant/ai/rag.py:68`.
- Digest redaction tại `src/tg_assistant/telegram/control_bot.py:386`.
- Reindex bỏ qua nội dung giống secret tại `src/tg_assistant/cli.py:433`.
- Pending action redaction và validation tại `src/tg_assistant/services/actions.py:42`.

### SEC-002 — High — Purge cần ràng buộc target mạnh hơn

Tác động trước sửa: cấu hình data directory quá rộng kết hợp xác nhận chung có thể xóa
quá nhiều dữ liệu local.

Khắc phục:

- Mỗi data root có marker riêng tại `src/tg_assistant/paths.py:33`.
- Purge kiểm tra marker, chặn đường dẫn rộng và yêu cầu gõ chính xác absolute path tại
  `src/tg_assistant/cli.py:670`.

### SEC-003 — Medium — Quyền Telegram có thể stale

Khắc phục: worker lấy lại quyền thực tế từ Telegram ngay trước send/edit/delete/pin và
Policy Engine kiểm tra lại. Telegram từ chối thì action chuyển `failed`, không retry vô hạn.

### SEC-004 — Medium — Session giải mã chưa siết quyền file

Khắc phục: file session mã hóa và file tạm giải mã đều được đặt permission best-effort
`0600`; khóa Fernet nằm trong Windows Credential Manager
(`src/tg_assistant/security.py:28`, `src/tg_assistant/security.py:85`).

### SEC-005 — Medium — Budget AI chỉ kiểm tra sau request

Khắc phục: dự phóng chi phí trước call và fail-closed với model chưa có bảng giá
(`src/tg_assistant/ai/budget.py:77`), giới hạn input/output/RPM và dùng `store=False`
(`src/tg_assistant/ai/engine.py:59`).

## Kiểm soát đã xác minh

- Bot owner-only: `src/tg_assistant/telegram/control_bot.py:70`.
- Action type allowlist: `src/tg_assistant/services/actions.py:11`.
- Audit payload/reason được redacted: `src/tg_assistant/runtime.py:214`.
- SQL identifier được kiểm tra regex trước khi nội suy; mật khẩu là bind parameter:
  `src/tg_assistant/setup/mysql.py:113`.
- MySQL user chỉ được grant trên database ứng dụng:
  `src/tg_assistant/setup/mysql.py:287`.
- Media default-off, allowlist MIME, size cap, hash và path containment:
  `src/tg_assistant/services/files.py:19`.
- Không có `eval`, `exec`, unsafe pickle/YAML hoặc `shell=True`.

## Rủi ro còn lại

### SEC-R01 — Moderate — Session plaintext khi tiến trình đang chạy

Telethon cần database session plaintext để hoạt động. Ứng dụng mã hóa khi shutdown sạch,
nhưng mất điện/kill cứng có thể để lại file tạm. Giảm thiểu: bảo vệ tài khoản Windows,
BitLocker/ổ đĩa, không chạy app dưới tài khoản dùng chung và dùng `tg-assistant stop`.

### SEC-R02 — Moderate — MySQL lưu nội dung nguồn nguyên bản

MySQL là source of truth nên tin Telegram gốc (kể cả tin người khác gửi có secret) có thể
được lưu trong chat đã cấp quyền. Nội dung đó bị chặn khỏi memory/embedding/RAG nhưng vẫn
tồn tại trong database để đồng bộ/audit. Giảm thiểu: bảo vệ MySQL local, backup và tài
khoản Windows; block/xóa dữ liệu chat khi không còn cần.

### SEC-R03 — Low — Secret detection là heuristic

Regex không thể nhận diện mọi loại secret và có thể false-positive. Luôn coi bot/database
là dữ liệu nhạy cảm; không gửi secret qua Telegram và dùng `tg-assistant reconfigure`.

### SEC-R04 — Low — DDL privilege ở runtime

User ứng dụng có CREATE/ALTER/DROP/INDEX để Alembic nâng cấp theo đặc tả. Đây là quyền
trong duy nhất database ứng dụng, không phải global. Mức bảo vệ cao hơn có thể tách
migration user và runtime user trong phiên bản sau.

## Xác minh

- Ruff security/static lint: pass.
- Compileall: pass.
- Pytest local/mock: 28 tests pass.
- Không dùng tài khoản Telegram thật hay gửi/xóa tin thật trong test.
