# Telegram AI Personal Assistant

Trợ lý Telegram cá nhân chạy trên Windows 10/11. Ứng dụng dùng Telethon để đăng nhập
tài khoản Telegram của bạn và một bot riêng làm bảng điều khiển. Mọi chat mặc định bị
chặn; chỉ chat được allow và bật đúng quyền con mới được đọc hoặc tương tác.

## Nguyên tắc an toàn

- Không gửi API key, OTP, mật khẩu hoặc session cho Codex hay qua bot.
- Secret chỉ nhập trong cửa sổ Windows hoặc terminal và lưu bằng Windows Credential Manager.
- SQLite trong profile cá nhân là nguồn dữ liệu chuẩn; Qdrant local chỉ giữ vector và ID tham chiếu.
- GPT chỉ đọc ngữ cảnh đã qua kiểm tra quyền và chỉ đề xuất hành động Telegram.
- Xóa/kiểm duyệt luôn tạo `pending_action` có hạn dùng và cần `/confirm`.
- Chat ngoài allowlist chỉ được đọc metadata tối thiểu để hiện danh sách.

## Kiến trúc

Xem [sơ đồ hoạt động chi tiết](ACTIVITY_DIAGRAM.md) để theo dõi đầy đủ các luồng
khởi động, kiểm tra quyền, hỏi AI, học dữ liệu, pending action và worker nền.

```text
Control Bot ──► Policy Engine ──► Services ──► SQLite
                     │                ├──────► Qdrant local
Telegram MTProto ────┘                ├──────► OpenAI/OpenRouter API (tùy chọn)
                                      └──────► Ollama API local (tùy chọn)
```

Các lớp chính:

- `telegram/user_client.py`: xác thực MTProto, metadata, sync/checkpoint, listener và hành động.
- `telegram/control_bot.py`: giao diện owner-only, quyền, search, task, xác nhận.
- `policy.py`: default deny, ma trận quyền, quyền Telegram thực tế, rate limit.
- `db/models.py`: toàn bộ schema nghiệp vụ; `alembic/` quản lý migration.
- `services/`: search, task, memory, pending action và parser thời gian.
- `ai/`: Responses API, ngân sách, RAG và Qdrant local.
- `runtime.py`, `cli.py`: vòng đời ứng dụng và tiến trình nền Windows.

## Yêu cầu

- Windows 10/11.
- Python 3.12 trở lên (nên dùng Python 3.12 hoặc 3.13 ổn định).
- SQLite được tạo trong profile ứng dụng; không cần cài máy chủ database.
- Telegram API ID/API Hash từ [my.telegram.org](https://my.telegram.org).
- Bot token tạo bằng `@BotFather`.
- OpenAI/OpenRouter API key hoặc Ollama local là tùy chọn; không cấu hình AI thì
  đồng bộ, quyền, keyword search, task và audit vẫn hoạt động.
- Có thể bỏ qua AI chưa cấu hình. API key được nhập trong cửa sổ Windows hoặc
  terminal và giữ trong Windows Credential Manager. Cấu hình không bí mật nằm
  trong `config/settings.json` của profile; ứng dụng không tự đọc `.env` trong
  thư mục cài đặt.

## Cài và chạy lần đầu

Mở PowerShell tại thư mục dự án:

```powershell
.\start.bat
```

Script phát triển kiểm tra Python, tạo `.venv` và chạy luồng CLI. Để dùng launcher
Windows và thiết lập bằng cửa sổ native trong bản source, cài thêm dependency desktop
và build dashboard bằng Node.js 24 tại thư mục dự án:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[desktop]"
npm --prefix dashboard-prototype ci
npm --prefix dashboard-prototype run build
.\.venv\Scripts\tg-assistant-desktop.exe
```

Bản installer cho người dùng ngoài vẫn đang được chuẩn bị; xem
[checklist](docs/handoff/MASTER_CHECKLIST.md).

Luồng thiết lập trên launcher:

1. tạo SQLite rỗng trong profile mới và chạy migration;
2. mở dashboard qua launcher bằng phiên đăng nhập local;
3. chọn nhà cung cấp/model, nhập API key trong cửa sổ Windows và kiểm tra kết nối;
4. nhập Telegram API ID/API hash rồi đăng nhập tài khoản qua QR hoặc số điện thoại,
   OTP và 2FA trong cửa sổ Windows;
5. nhập token bot, kiểm tra bot và đối chiếu owner/pairing (chưa hoàn tất — O04);
6. chọn nguồn dữ liệu và thử câu trả lời đầu tiên (chưa hoàn tất — U03).

Launcher source hiện có các bước 1–4. Luồng native kết nối bot/ghép owner và
hoàn tất onboarding ở bước 5–6 chưa được nghiệm thu; dashboard phản ánh các bước
còn thiếu thay vì hiển thị setup hoàn tất.

Kết nối tài khoản Telegram và kết nối bot là hai bước riêng. Luồng thiết lập chưa
cho phép bỏ qua owner/pairing bằng trạng thái giả. Cài mới không mang theo database,
session hay API key của người phát triển. Cài lại không tự xóa profile đang có.

Ứng dụng chỉ hỗ trợ SQLite. Profile khai báo MySQL bị từ chối bằng thông báo an toàn;
ứng dụng không kết nối, nhập hay xóa database MySQL cũ.

## Lệnh CLI

```text
tg-assistant start        setup nếu cần rồi chạy nền
tg-assistant run          chạy foreground
tg-assistant stop         dừng an toàn
tg-assistant restart      dừng rồi chạy lại
tg-assistant status       PID và trạng thái
.\.venv\Scripts\tg-assistant.exe dashboard-code
# Mã đăng nhập dashboard local, hiệu lực 5 phút
tg-assistant logs         log nền gần nhất
tg-assistant doctor       kiểm tra Python/SQLite/credential/session
tg-assistant sync         đồng bộ chat đã được cấp quyền
tg-assistant reindex      tạo lại embedding của chat được phép
tg-assistant backup       backup không chứa secret
tg-assistant restore FILE kiểm tra rồi restore có xác nhận
tg-assistant autostart enable|disable|status
tg-assistant ai-provider openai|openrouter|ollama|off
tg-assistant coingecko-key nhập/đổi CoinGecko API key
tg-assistant reconfigure  nhập lại secret trong terminal
tg-assistant logout       thu hồi/xóa session sau xác nhận
tg-assistant uninstall    hướng dẫn gỡ an toàn
tg-assistant purge        xóa dữ liệu local/credential qua cụm xác nhận
```

## Dashboard quản trị local

Backend quản trị chạy cùng worker tại `http://127.0.0.1:8765` và không lắng nghe mạng LAN.
Sau khi chạy `start.bat`, lấy mã đăng nhập một lần trong terminal:

```powershell
.\.venv\Scripts\tg-assistant.exe dashboard-code
```

Mã có hiệu lực 5 phút. Phiên đăng nhập dùng cookie `HttpOnly`; mọi thao tác ghi còn yêu cầu
CSRF token. Dashboard cung cấp tổng quan vận hành, ba danh sách group/channel, tìm kiếm và
phân trang, ma trận quyền, AI mode theo group, retention/quota theo nguồn, tiến độ học,
pause/resume/retry job, chọn OpenAI/OpenRouter/Ollama/off, quản lý model Ollama, RAM/VRAM,
tăng trưởng dung lượng, cleanup preview, pending action và audit. Xóa model hoặc dọn dữ liệu
chỉ được thực thi sau bước preview và owner xác nhận.

Kiểm tra nhanh backend:

```powershell
.\.venv\Scripts\tg-assistant.exe doctor
```

## Cấp quyền chat

Mọi chat bắt đầu ở trạng thái BLOCK. `/groups` chỉ hiển thị 10 group/supergroup
tương tác nhiều nhất và hỏi có xuất toàn bộ danh sách ra CSV hay không. Xếp hạng
ưu tiên số tin owner đã gửi trong dữ liệu đồng bộ, sau đó tổng số tin và hoạt động
gần nhất. File CSV được tạo trong bộ nhớ, gửi trực tiếp qua control bot và không
được ghi tạm xuống ổ đĩa. Dùng Chat ID trong kết quả để cấp quyền:

Trong Telegram, có thể dùng flow không cần nhập ID: `/groups` mở màn hình quản lý
gồm ba danh mục `Nhóm đã bật AI`, `Nhóm có quyền khác (chưa bật AI)` và `Tất cả
nhóm`. Chọn một group → `Thiết lập chống spam` → xác nhận → `Đồng bộ 1.000 tin mới
nhất` → xác nhận → `Quét các tin chứa link` → chọn tin → xác nhận xóa. Gói chống spam bật quyền
đọc/giám sát, đồng bộ, tìm kiếm, moderation và `delete_any_messages`, nhưng không
bật tự động xóa; mỗi tin phá hủy vẫn cần owner xem trước và xác nhận. Menu chỉ
Mỗi danh mục hiện 10 group mỗi trang và có nút `Trang trước`/`Trang sau`, nên không cần nhập ID
cho group nằm ngoài top 10. Nút `Tìm nhóm` nhận một phần tên, `@username` hoặc
Chat ID rồi trả tối đa 10 kết quả có nút mở flow trực tiếp; người dùng chỉ nhập từ
khóa thường, không phải cú pháp lệnh.

Lần đồng bộ đầu lấy tối đa 1.000 tin mới nhất tính từ thời điểm hiện tại trở về
trước. Các lần sau chỉ lấy tin có ID mới hơn checkpoint. Kết quả quét link luôn
sắp xếp theo `sent_at` giảm dần, tức tin gần hiện tại nhất xuất hiện trước.

### AUTO xóa link non-admin

Trong màn hình quản lý một group, nút `Bật AUTO xóa link non-admin` tạo một
pending action có cảnh báo và cần owner xác nhận. Sau khi worker áp dụng, bot chỉ
xóa tin **mới** của thành viên thường khi phát hiện URL/link ẩn, `http(s)`,
`www`, `t.me`, `telegram.me`, `tg://` hoặc tên miền dạng `example.com`. Tin
không có link được giữ; link của owner, creator/admin hoặc admin ẩn danh cũng được
giữ. `@username` đơn thuần và địa chỉ email không được coi là link. Rule không
quét xóa ngược lịch sử. Telegram role và quyền xóa được kiểm tra trước mỗi quyết
định; nếu không xác minh được, hệ thống fail-safe bằng cách giữ tin và ghi audit.
Nút `Tắt AUTO xóa link non-admin` thu hồi `auto_moderation` nhưng giữ các quyền
moderation thủ công.

### Thành viên group hỏi AI

Trong màn hình quản lý một group đang ở `ALLOW`, nút `Bật thành viên hỏi AI` tạo
pending action để owner xác nhận. Sau khi bật, thành viên trong group dùng:

```text
@your_assistant_username /ask <câu hỏi>
```

Telegram Client `@your_assistant_username` trả lời ngay dưới tin nhắn gọi. RAG chỉ truy xuất
group/channel đã bật `auto_knowledge` trong kho dự án hợp nhất và còn quyền tìm kiếm;
mặc định tập trung vào 7×24 giờ gần nhất. Group dùng tính năng phải có quyền
`group_ai_ask`; group `BLOCK` hoặc chưa bật quyền được bỏ qua im lặng. Câu hỏi chứa
secret không được gửi tới AI, độ dài tối đa là 1.500 ký tự và mỗi thành viên được
giới hạn 3 câu/phút. Trong group, câu hỏi mang tính tin tức, cập nhật mới, diễn biến,
thông báo hoặc thị trường hôm nay sẽ kèm mã `[S#]` và phần `DẪN CHỨNG`; câu hỏi kiến
thức thông thường sẽ ẩn nguồn. Khi hỏi trực tiếp bot, dẫn chứng luôn được giữ đầy đủ.

Các câu hỏi chỉ ngữ cảnh hiện tại như `chủ đề đang thảo luận là gì?`, `nhóm này đang
nói gì?`, `mọi người đang bàn gì?` hoặc `tóm tắt hội thoại` được định tuyến riêng:
AI chỉ đọc tối đa 100 tin gần nhất đã đồng bộ trong 7 ngày của chính group gọi lệnh,
loại tin gọi `/ask`, và không truy xuất group/channel khác. Câu hỏi tổng hợp như
`cập nhật mới nhất từ tất cả dự án` vẫn dùng toàn bộ kho đã học.

Truy vấn có mention tài khoản và ý định lịch sử, ví dụ
`@your_assistant_username /ask @a_member đã nói về chủ đề gì`, được xử lý theo sender:
Telegram Client phân giải `@a_member` thành Telegram `sender_id`, lấy tối đa 100 tin
gần nhất của sender đó trong đúng group, đồng bộ các tin lấy được vào SQLite, rồi chỉ
đưa những tin có cùng `sender_id` cho AI. Tin của thành viên khác và nguồn ngoài
group không được đưa vào ngữ cảnh.

Màn hình xác nhận cảnh báo rằng nội dung tổng hợp từ các nguồn dự án đã học có thể
xuất hiện trong group được cấp quyền; câu trả lời tin tức sẽ kèm phần dẫn chứng. Nút
`Tắt thành viên hỏi AI` thu hồi standing authorization này mà không xóa kho kiến thức.

```text
/group_allow <chat_id>
/permission_template <chat_id> read_only
/permission_template <chat_id> knowledge
/permission_template <chat_id> task_management
/permission_template <chat_id> moderation
/permission_set <chat_id> <permission> <on|off>
/permissions <chat_id>
/group_block <chat_id>
```

`/group_allow` không tự bật quyền con. Mẫu moderation không bật `delete_any_messages`.
Khi block, các quyền con bị tắt ngay và request mới bị Policy Engine từ chối.

## Search, task, memory và AI

- `/help` → `Mở trợ lý AI` mở flow nút bấm gồm: hỏi AI, tìm trong Telegram,
  kiểm tra giá CoinGecko, tóm tắt hôm nay/hôm qua, học từ group,
  xem trạng thái/ngân sách và bật/tắt AI.
- `/price BTC` hoặc câu hỏi tự nhiên như `giá ETH bao nhiêu?` trả giá USD/VND,
  biến động 24 giờ, vốn hóa, khối lượng, thời gian cập nhật và link nguồn CoinGecko.
  Trong group đã bật quyền hỏi AI, thành viên có thể dùng
`@your_assistant_username /ask giá SOL bao nhiêu?`. Tra giá hoạt động độc lập với AI/RAG.
- Trong flow, người dùng chỉ nhập câu hỏi hoặc từ khóa tự nhiên sau khi bấm nút;
  mỗi kết quả có nút hỏi/tìm tiếp và quay lại menu.
- `Học từ group/channel` là flow RAG có xác nhận và hỗ trợ chọn đồng thời nhiều
  group, supergroup và channel: đánh dấu riêng lẻ, chọn cả trang hoặc chọn toàn bộ
  tối đa 500 nguồn mỗi đợt. Tất cả nguồn được hợp nhất trong cùng một kho kiến thức:
  SQLite lưu dữ liệu gốc và collection `telegram_messages` của Qdrant lưu vector.
  Sau một lần xác nhận, mỗi nguồn được đưa vào một
  background job riêng để không chặn bot.
  Job bật allowlist + mẫu quyền `knowledge`, đồng bộ tối đa 1.000 tin mới nhất
  vào `telegram_messages` và commit SQLite trước. Sau đó worker đọc lại bản ghi đã
  commit, bỏ qua chuỗi giống secret, tạo embedding và upsert vector vào kho Qdrant
  local. Khi học tiếp, checkpoint chỉ chọn tin chưa được lập chỉ mục; bản ghi cũ
  không bị nhân đôi. Nếu embedding lỗi, dữ liệu SQLite vẫn được giữ và riêng bước embedding
  được thử lại.
  Màn hình `Tiến độ học` tổng hợp số job chờ/đang chạy/tạm dừng/hoàn tất/lỗi,
  đồng thời có nút dừng an toàn và tiếp tục cả hàng đợi. Worker tiếp
  tục lập chỉ mục tối đa 100 tin mới mỗi chu kỳ 5 phút. Chặn group sẽ làm dữ liệu
  của group đó không còn được truy xuất.
- `Xuất kiểm kê nguồn học` tạo CSV UTF-8 với trạng thái đã học, chưa học,
  không lấy được nội dung, đang xử lý và lỗi. Chat ID được xuất dạng text để Excel
  không làm tròn. Người dùng điền `CO` ở cột `can_hoc`, thêm `ghi_chu` và gửi lại
  file; bot kiểm tra file, lưu ghi chú trong `knowledge_sources` và chỉ tạo hàng
  đợi sau khi người dùng bấm xác nhận.
- Đây không phải fine-tuning model. Model mặc định `gpt-5.6-terra` không hỗ trợ
  fine-tuning; RAG phù hợp với nội dung group thay đổi liên tục và giữ được nguồn
  chat/message trong lúc truy xuất.
- `Tìm trong Telegram` chỉ truy vấn dữ liệu local đã cấp quyền, không gọi AI.
  Mặc định bot tập trung vào cửa sổ trượt 7×24 giờ tính từ thời điểm hỏi; bộ lọc
  `after:`/`before:` có thể chọn khoảng ngày khác.
- `Tổng hợp toàn bộ hôm nay/hôm qua` dùng đúng ngày lịch theo múi giờ Việt Nam.
  Hệ thống rà toàn bộ tin trong SQLite từ mọi group/channel có quyền `summarize`,
  lọc nội dung quá ngắn/không phù hợp/secret, loại bản đăng trùng hoặc gần trùng,
  phân bổ ngữ cảnh công bằng giữa các nguồn rồi yêu cầu AI phân loại. Kết quả luôn
  có báo cáo phạm vi, số tin đã loại, danh sách nguồn đã tổng hợp và dẫn chứng.
  Nguồn chưa được học/đồng bộ được báo rõ, không được tính là đã rà.
- `/search <query>` chỉ tìm chat có `search_messages` và mặc định dùng cùng cửa sổ
  trượt 7×24 giờ.
- Câu trả lời RAG gắn mã `[S1]`, `[S2]` vào ngữ cảnh và luôn nối phần
  `DẪN CHỨNG`. Nguồn public dùng `https://t.me/<username>/<message_id>`;
  supergroup/channel riêng tư dùng `https://t.me/c/<internal_id>/<message_id>`.
  Nếu không tạo được link, bot ghi tên group/channel và thời gian đăng theo giờ Việt Nam.
  Keyword và semantic retrieval đều bị giới hạn vào 7×24 giờ gần nhất; dữ liệu cũ
  vẫn còn trong SQLite/Qdrant để dùng khi chọn khoảng ngày khác hoặc cho nghiệp vụ khác.
- `/task_add <nội dung>` tạo task thủ công; `/task_list`, `/task_done <id>`.
- Memory có phạm vi `private`, `chat:<id>` hoặc `global`; secret không được lưu.
- AI mặc định dùng `gpt-5.6-terra`, tác vụ nhanh `gpt-5.6-luna`, chế độ sâu
  `gpt-5.6-sol`; tất cả đổi được bằng cấu hình.
- Provider mặc định là OpenAI trực tiếp. Chạy `tg-assistant ai-provider openrouter`
  để chọn OpenRouter và nhập key ẩn; OpenRouter dùng
  `openai/gpt-5.6-terra` và `openai/text-embedding-3-small`. Provider được lưu
  trong `config/settings.json` của profile, còn cả hai API key chỉ nằm trong Windows Credential Manager.
  Nếu provider đang chọn bị mất key sau khi reset, `start` sẽ hỏi lại giống flow
  credential Telegram.
- Chạy `tg-assistant ai-provider ollama` để chọn model chat và embedding local.
  Mặc định dùng `qwen3:8b` và `nomic-embed-text:latest` qua API
  `http://127.0.0.1:11434/v1`; Ollama không cần API key và chi phí API là 0 USD.
  Kho vector Ollama nằm riêng ở `qdrant_ollama`, không ghi đè kho 1.536 chiều của
  OpenAI/OpenRouter. Khi chuyển giữa cloud và Ollama, chạy lần lượt
  `tg-assistant stop`, `tg-assistant reindex`, `tg-assistant start`.
- Có thể quản lý hoàn toàn trong Telegram tại `Mở trợ lý AI` →
  `Nhà cung cấp AI` → `Quản lý Ollama local`: xem model trên máy, chọn model
  chat, kiểm tra/chọn embedding, tải model mới, xóa model qua bước xác nhận và
  kích hoạt Ollama ngay. Tải model chạy nền; bot báo lại khi hoàn tất.
- Màn hình `Nhà cung cấp AI` có bốn lựa chọn áp dụng ngay: `OpenAI trực tiếp`,
  `OpenRouter`, `Ollama local` và `Tắt toàn bộ AI`. Chế độ tắt dừng mọi suy luận
  và embedding AI nhưng vẫn giữ SQLite, tìm kiếm local, CoinGecko, đồng bộ và
  chống spam. Provider cloud chỉ bật được khi key tương ứng đã nằm an toàn trong
  Windows Credential Manager; bot không nhận API key qua Telegram.
- Mỗi group đã ALLOW có mục `AI mode, fallback & quota`. Các mode gồm:
  theo provider toàn cục, `LOCAL ONLY`, local trước rồi fallback cloud,
  cloud only, cloud trước rồi fallback local hoặc tắt AI riêng group. Có thể chọn
  OpenAI/OpenRouter làm cloud đích; tắt AI toàn cục luôn ưu tiên cao nhất và dừng
  mọi route.
- Cùng màn hình này cho phép đặt retention 7/30 ngày và preset hạn mức
  message/vector/MB. Worker áp quota lúc đồng bộ/lập chỉ mục; cleanup theo giờ
  xóa dữ liệu hết retention, phần vượt quota và vector không còn message gốc.
- `Vận hành & lưu trữ` hiển thị RAM/CPU/VRAM của tiến trình, process con,
  dung lượng data/vector/media, tăng trưởng giữa hai mẫu và trạng thái hàng đợi.
  Cleanup thủ công luôn chạy dry-run để xem trước trước khi nút xác nhận xuất hiện.
- Scheduler ghi lag/missed/error; learning worker ghi thời gian sync, embedding,
  tổng thời gian, retry và fallback provider. Mẫu vận hành được lưu trong
  `runtime_metrics` để dashboard có lịch sử thay vì chỉ đọc log.
- CoinGecko dùng Demo API và key chỉ được lưu trong Windows Credential Manager.
  Nếu key bị mất sau khi reset, `start` sẽ hỏi nhập ẩn; có thể đổi riêng bằng
  `tg-assistant coingecko-key`.
- Khi đạt 100% ngân sách ngày/tháng, lớp AI dừng nhưng bot local tiếp tục.
- Worker học mặc định dùng tối đa 60 request/phút, kiểm tra job mới mỗi 2 giây và
  tạm hoãn refresh nguồn đã học cho tới khi hàng đợi ban đầu hoàn tất. Batch vẫn
  giới hạn 12.000 token/request để giữ khoảng trống dưới hạn mức token/phút.

## Backup và restore

Trong launcher Windows, chọn **Dữ liệu và sao lưu** để tạo hoặc khôi phục bản sao lưu.
Luồng CLI `tg-assistant backup` dùng cùng dịch vụ, hỗ trợ SQLite; ZIP gồm
dữ liệu database và manifest có revision/checksum. Credential, API key, session
Telegram và chỉ mục vector không nằm trong bản sao lưu.

Khôi phục yêu cầu xác nhận, dừng và chặn các writer, kiểm tra archive trong database
tạm và tạo bản sao hiện trạng trước khi thay đổi database. Quyền truy cập hiện tại
được giữ; dữ liệu khôi phục cần đối chiếu lại chỉ mục vector. Runtime không tự bật
lại sau khôi phục. Dashboard cho phép tạo và xem danh sách bản sao lưu; chọn tệp
để khôi phục được thực hiện trong cửa sổ Windows.

## Kiểm thử

```powershell
python -m pytest
python -m ruff check .
```

Unit test dùng SQLite/mock, không đăng nhập Telegram thật và không gửi/xóa tin thật.
Kiểm thử tích hợp dùng SQLite riêng và transport giả lập; không cần máy chủ SQL.

## Dữ liệu và giới hạn

Dữ liệu mặc định nằm trong `%LOCALAPPDATA%\TelegramAIPersonalAssistant`. Session Telethon
chỉ tồn tại dạng plaintext khi tiến trình đang sử dụng và được mã hóa khi đóng sạch.
Máy bị tắt đột ngột có thể để lại file session tạm; hãy bảo vệ tài khoản Windows và ổ đĩa.
Không dùng ứng dụng để vượt quyền Telegram, truy cập Secret Chat hoặc né bảo vệ nội dung.

Đọc thêm: [SECURITY.md](SECURITY.md), [PRIVACY.md](PRIVACY.md),
[ARCHITECTURE.md](ARCHITECTURE.md), [TROUBLESHOOTING.md](TROUBLESHOOTING.md) và
[security_best_practices_report.md](security_best_practices_report.md).
