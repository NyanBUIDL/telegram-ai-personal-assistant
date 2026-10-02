# Gói bàn giao trải nghiệm và art — Telegram AI Personal Assistant v1 Windows

Ngày: 02/10/2026. Trạng thái: đặc tả đề xuất để triển khai, chưa phải kết quả UAT hoặc phê duyệt phát hành.

## 1. Kết quả cần đạt và phạm vi đã chốt

Mỗi người dùng cài ứng dụng trên máy Windows của mình, kết nối tài khoản Telegram của mình và bot của mình, chọn AI provider bằng API key riêng hoặc model local, rồi dùng trợ lý hằng ngày trong Telegram. Launcher PySide6 với setup/tray và runtime đóng gói hướng dẫn thiết lập, mở dashboard React trong browser local bằng một nút. Cài mới dùng SQLite nhúng; installation MySQL hiện có tiếp tục được hỗ trợ ở chế độ nâng cao và không tự chuyển dữ liệu. Người dùng mới không phải biết Python, CLI, Chat ID, MySQL/root password hay lệnh PowerShell để hoàn tất happy path.

Telegram là giao diện trợ lý chính; dashboard là bảng quản trị phụ cho kết nối, nguồn, quyền, việc học dữ liệu, vận hành và xác nhận. Được phép tổ chức lại toàn bộ trải nghiệm, nhưng giữ art hiện tại. V1 không có hosted sign-up, đăng ký email, tài khoản SaaS, nhiều tenant, thanh toán hoặc dashboard mở qua LAN/Internet. Một active profile per Windows SID; không thêm multi-account switch hoặc CTA đổi account vào v1.

“Kết nối API dễ” trong v1 nghĩa là kết nối provider đã hỗ trợ bằng key của người dùng: OpenAI, OpenRouter, Ollama hoặc tắt AI. Không ngầm mở rộng thành API công khai để bên thứ ba điều khiển Telegram hay connector arbitrary endpoint. Nút thêm provider mới chỉ xuất hiện khi có backend tương ứng.

Secret được nhập trong dialog native PySide6 đáng tin cậy của launcher, write-only, và chuyển tới lớp lưu secret local qua boundary native đã review; raw secret không đi qua HTTP Admin API. Đây là thiết kế thay UX terminal hiện tại, cần implementation và security review trước khi dùng. Dashboard chỉ hiện trạng thái/cấu hình và nút mở dialog native. Không đưa stored API key, API hash, bot token, OTP, password hoặc session Telegram vào dashboard, query string, lịch sử trình duyệt, localStorage, log hoặc ảnh nghiệm thu.

### Cơ sở đã đọc

- Snapshot local tại `audit-snapshot`, đối chiếu repository `NyanBUIDL/telegram-ai-personal-assistant`, commit `7429fcffd61860e9502f066e0b6a921df72fe176`.
- `README.md`, `USER_GUIDE.md`, `TROUBLESHOOTING.md`, `src/tg_assistant/setup/wizard.py`, `src/tg_assistant/admin_api/auth.py`, `src/tg_assistant/admin_api/app.py`.
- `dashboard-prototype/src/App.jsx`, `src/api.js`, `src/hooks.js`, `src/views/GroupsView.jsx`, `src/views/KnowledgeView.jsx`, `src/views/AiViews.jsx`.
- Art guideline và source bổ sung đọc qua GitHub: [AGENTS.md](https://github.com/NyanBUIDL/telegram-ai-personal-assistant/blob/7429fcffd61860e9502f066e0b6a921df72fe176/dashboard-prototype/AGENTS.md), [styles.css](https://github.com/NyanBUIDL/telegram-ai-personal-assistant/blob/7429fcffd61860e9502f066e0b6a921df72fe176/dashboard-prototype/src/styles.css), [SystemViews.jsx](https://github.com/NyanBUIDL/telegram-ai-personal-assistant/blob/7429fcffd61860e9502f066e0b6a921df72fe176/dashboard-prototype/src/views/SystemViews.jsx).
- `RELEASE_READINESS_REVIEW_VI.md` dùng làm ràng buộc phát hành: chưa kiểm thử browser, cài Windows sạch và provider/Telegram thật trong lần audit trước. Không được gọi layout hoặc luồng dưới đây là đã kiểm chứng thị giác.

Snapshot thiếu một số file frontend, asset và cấu hình. Agent triển khai phải lấy full checkout đúng baseline rồi đọc `AGENTS.md` áp dụng trước khi sửa. Không dùng snapshot audit làm release package.

## 2. Chênh lệch hiện tại → trải nghiệm mục tiêu

| Khu vực | Bằng chứng hiện có | Thay đổi cần triển khai |
| --- | --- | --- |
| Cài/thiết lập | `start.bat`, wizard terminal, phụ thuộc Python/MySQL | Launcher PySide6/runtime đóng gói/SQLite mới; check thật, tiến độ, sửa lỗi theo bước; giữ MySQL advanced |
| Đăng nhập dashboard | `App.jsx` yêu cầu mã 8 số từ CLI | Một nút “Mở bảng quản trị” trên launcher; trao phiên local có TTL và dùng một lần; mã GUI làm fallback |
| Kết nối | `ConnectionsView` có Admin API/MySQL/Qdrant/provider/SSE, chưa có flow nhập credential | Card account/bot/AI với trạng thái backend kiểm chứng và nút mở bước sửa trong launcher |
| Health | MySQL có thể được ghi ONLINE vì overview tải được; Qdrant vì có metric | Không suy diễn trạng thái riêng từng dịch vụ; hiển thị Chưa xác minh/Cũ khi thiếu probe |
| Navigation | 14 mục, có nhiều thuật ngữ vận hành tiếng Anh | 7 mục chính; kỹ thuật và cấu hình nâng cao bên trong mục tương ứng |
| Quyền và học | Nhiều quyền, job, preview, ALLOW/BLOCK đã có | Progressive disclosure, rõ phạm vi trước xác nhận, thu hồi hiện ngay và không bị job cũ bật lại |
| Tài liệu | README và guide còn mô tả corpus không nhất quán theo audit | Copy chỉ theo contract đã chốt và regression test; group `/ask` v1 trong chính group |

## 3. Hành trình lần đầu

Thứ tự coordinator dùng chung: `welcome → storage_ready → ai_configured → telegram_verified → bot_verified → owner_paired → source_selected → first_answer → ready`. Tất cả milestone “verified” cần evidence backend; AI bỏ qua được ghi cấu hình Off hợp lệ, không fake health. Cho phép lưu/resume ở mọi điểm; UI vẫn phản ánh setup còn thiếu. Tên DTO, state và endpoint theo [spec kiến trúc](../superpowers/specs/2026-10-02-windows-public-beta-design.md), không tạo enum song song riêng cho frontend.

### F0 — Mở ứng dụng và kiểm tra máy

Màn hình đầu có tên sản phẩm, một câu “Trợ lý Telegram chạy trên máy của bạn”, nút “Bắt đầu” và lựa chọn “Tôi đã thiết lập trước đây”. Không dồn nguyên kiến trúc vào hero. Tóm tắt riêng tư ngắn: lịch sử chỉ đọc ở nguồn được cấp quyền; provider cloud nhận ngữ cảnh khi sử dụng AI cloud; không fine-tune model.

Launcher kiểm tra package/runtime, vùng dữ liệu ghi được, SQLite/migration, cổng dashboard và thành phần vector. Mỗi check có trạng thái đang kiểm tra/đạt/cần xử lý và nút hành động cụ thể. Không đánh dấu “Xong” chỉ vì file tồn tại hoặc process đã launch. Cài mới không hỏi database engine/root password và không phụ thuộc MySQL service. Khi phát hiện installation MySQL cũ, hiện “Tiếp tục dùng dữ liệu hiện có”, probe đúng backend đó và giữ cấu hình; chỉ trong Advanced mới có chọn/cấu hình MySQL. Không tự convert hoặc merge database.

Nếu phải cài một dependency, hiện tên, nguồn, dung lượng dự kiến khi biết, lý do và tiến độ trước thao tác. Nếu cần quyền Windows, giải thích đúng tác vụ; không dùng admin cho toàn bộ phiên ứng dụng khi chỉ một bước cần. Thao tác setup restartable và không ghi đè database đã có.

### F1 — Chọn AI và ngân sách

Các card lựa chọn OpenAI/OpenRouter/Ollama/“Thiết lập sau”. Giải thích bằng tác dụng: cloud cần key và có chi phí sử dụng riêng; local cần model/hardware; bỏ qua vẫn quản lý nguồn và tìm keyword được. AI Off là trạng thái hợp lệ, không phải lỗi setup.

Cloud: nhập key native → kiểm tra credential/model → chọn chat model và cloud embedding theo danh sách backend hỗ trợ → đặt giới hạn ngày/tháng → xem và đồng ý phạm vi gửi dữ liệu → lưu và áp dụng. Cloud path không bắt cài Ollama. Consent nói rõ đoạn nội dung từ nguồn được cấp quyền có thể gửi tới cloud để embedding và làm ngữ cảnh trả lời, hai tác vụ có chi phí; không gọi embedding là hoạt động chỉ local khi dùng cloud. Bài kiểm tra có thể phát sinh gọi provider phải nói trước; không tự gửi lịch sử Telegram để “test key”. Key mới thất bại không xóa key đang hoạt động. Đổi provider không tự bật fallback cloud cho nguồn `local-only`; khi cloud không được phép, báo capability thiếu thay vì gửi lén.

Ollama: kiểm tra daemon → danh sách model đã cài → chọn chat/embedding → nếu chưa có, đưa tới luồng tải có dung lượng, tiến độ, hủy, retry. Chỉ render RAM/VRAM/ước lượng model-fit khi backend có số thật; ghi “Chưa xác minh” khi thiếu. Không giả vờ download hay dùng toast làm kết quả cuối. Thiếu embedding thì chưa đánh dấu RAG sẵn sàng; keyword search vẫn có thể hoạt động. Đổi embedding profile cần preview model/dimension/nguồn ảnh hưởng, chi phí nếu cloud và kế hoạch reindex; owner duyệt trước, giữ index cũ theo migration plan cho tới khi index mới sẵn sàng.

Không chọn tên model, mức giá hoặc ngưỡng ngân sách cố định trong spec này. Backend phải cung cấp preset hỗ trợ; UI cho người dùng duyệt chi phí/giới hạn trước kích hoạt cloud.

### F2 — Kết nối tài khoản Telegram

1. Hiện rõ hai vai trò: “Tài khoản của bạn đọc nguồn được cho phép”; “Bot riêng là nơi nói chuyện với trợ lý”. Không gọi cả hai là “Telegram connected”.
2. Form native: API ID và API hash, hướng dẫn mở trang lấy credential và giải thích vì sao cần; không có nút “tạo tự động”. Cho phép paste, label luôn hiện; field secret che ngay sau khi nhập. Sau khi có app credential hợp lệ, cung cấp QR nếu Telethon/Telegram chính thức hỗ trợ cho build đã kiểm thử; QR không loại bỏ yêu cầu API ID/hash. Có lựa chọn OTP bằng số điện thoại có mã quốc gia và 2FA làm fallback. Tham chiếu: [Obtaining API ID](https://core.telegram.org/api/obtaining_api_id), [QR login](https://core.telegram.org/api/qr-login).
3. Gửi yêu cầu đăng nhập → trạng thái “Đang chờ mã Telegram”. Nói mã có thể đến trong ứng dụng Telegram theo kết quả backend, không cam kết chỉ SMS.
4. OTP trong form native; chỉ hiện bước 2FA nếu Telegram yêu cầu; không lưu OTP/2FA password. Trở về/sửa số điện thoại được, nhưng hủy challenge cũ trước khi tạo challenge mới.
5. Thành công chỉ sau backend xác nhận identity: tên/@username và numeric owner ID. Đủ để xác nhận account, không hiển thị session hoặc số điện thoại đầy đủ trên dashboard.

Chưa có API ID/hash hoặc chưa xác thực account thì cho “Lưu và thiết lập sau”, mở dashboard giới hạn với nhãn “Chưa kết nối Telegram”; giữ bot/AI đã cấu hình nhưng không cho pairing owner, liệt kê nội dung, học hoặc dùng trợ lý hoàn chỉnh. Không phát minh identity bot-only cho v1. Telegram bị FloodWait thì hiện thời điểm thử lại từ backend, disable gửi mã trong thời gian chờ và không tăng retry tự động. QR hết hạn có refresh theo challenge thật, không giữ QR dùng lại sau thành công; OTP/2FA vẫn luôn có đường quay lại.

### F3 — Kết nối bot và ghép owner

1. Nút “Mở BotFather” và hướng dẫn tối đa ba bước: tạo bot → sao chép token → nhập token vào launcher. Không dựng tính năng tạo bot tự động khi chưa có contract.
2. Launcher kiểm tra token qua backend; chỉ sau thành công mới hiện tên, @username, link mở bot. Token write-only, không hiện cả phần đuôi token đã lưu.
3. Nút “Mở bot để ghép tài khoản”. Pairing nonce dùng một lần có hạn; account gửi pairing phải đúng owner identity vừa xác thực. Tự điền deep link nếu cơ chế pairing đã hỗ trợ và được review; nếu chưa, mã pairing và nút sao chép xuất hiện trong launcher.
4. Launcher chờ event/backend polling hữu hạn rồi hiển thị “Bot đã ghép với tài khoản …”. Mở link chưa phải pairing thành công.
5. Sai account: thông báo “Hãy mở bot bằng tài khoản đã kết nối”, không thay owner theo người gửi `/pair`. Hết hạn: tạo nonce mới, không yêu cầu nhập lại token còn hợp lệ.

### F4 — Cấp quyền nguồn và tạo giá trị đầu tiên

1. Hiện group/channel account đã tham gia theo metadata; mặc định BLOCK. Có search theo tên/@username/Chat ID, loại nguồn, danh mục, phân trang 10 dòng.
2. Chọn một hoặc nhiều nguồn. Trạng thái chọn toàn trang phải nói “10 nguồn trên trang này”; chọn tất cả qua nhiều trang cần CTA và tổng riêng, không đổi nghĩa checkbox âm thầm. Giới hạn bulk theo backend, hiển thị trước xác nhận.
3. Mặc định đề xuất mẫu “Đọc và học” đủ cho knowledge. Xem preview liệt kê nguồn, quyền sẽ bật, giới hạn sync thực tế, phạm vi thời gian và provider xử lý. Không bật gửi/xóa/moderation/member `/ask` cùng bước học.
4. Owner xác nhận → mỗi nguồn có job riêng. Hiển thị đã xếp hàng, đồng bộ, lập chỉ mục, hoàn tất, tạm dừng, lỗi hoặc bị thu hồi. “Đang học” phải giải thích là đồng bộ/làm sạch/embedding, không fine-tune.
5. Bật AI hỏi trong group là opt-in tách biệt, phải nói câu trả lời group v1 dùng ngữ cảnh được phép của chính group và thành viên có thể thấy nội dung trả lời. Chỉ viết như vậy sau contract và test scope đã chốt.
6. “Mở trợ lý trong Telegram” mở bot. Hướng dẫn một câu hỏi mẫu dựa trên nguồn owner vừa chọn, không chứa nội dung giả. Chỉ ghi “Đã trả lời câu đầu tiên” khi có event thật; nếu không có event, chỉ ghi “Đã mở bot”.

### F5 — Hoàn tất và quay lại

Màn hình hoàn tất có ba thông tin: tài khoản/bot đang dùng; AI đang dùng hoặc chưa bật; số nguồn được cấp quyền theo backend. Hai CTA: chính “Mở trợ lý Telegram”, phụ “Mở bảng quản trị”. Tóm tắt: ứng dụng cần chạy để trợ lý hoạt động; lựa chọn chạy khi đăng nhập Windows phải là toggle rõ trạng thái thực, không checkbox trang trí.

Thiết lập được lưu theo từng bước không bí mật. Đóng/restart launcher quay về bước chưa hoàn tất gần nhất, revalidate kết nối trước khi dùng lại. Không lưu form OTP/password trong checkpoint. Người dùng có thể hoàn tất setup trước khi có nguồn đã học hoặc AI cloud; chỉ không quảng cáo RAG/AI ready khi các dependency chưa đạt.

## 4. Đăng nhập dashboard local bằng một nút

### Luồng ưu tiên

1. Trong launcher đã thiết lập, owner bấm “Mở bảng quản trị”. Launcher kiểm tra worker/Admin API, khởi động qua lifecycle service nếu đang dừng và hiển thị tiến độ.
2. Native launcher chứng minh quyền phiên Windows/local owner cho backend qua giao thức local đã được security work package phê duyệt. Loopback hoặc biết port không đủ để cấp phiên.
3. Backend cấp ticket ngẫu nhiên 256 bit qua named pipe chỉ Windows SID hiện tại; TTL 30 giây, dùng một lần, bound profile/audience dashboard. Store hash-only và redeem atomic theo spec kiến trúc.
4. Launcher mở URL fragment; JS xóa fragment khỏi history ngay, POST redeem same-origin lấy cookie HttpOnly/SameSite và CSRF, rồi mở route owner vừa dùng. Trang login không có third-party script. Không đưa ticket/code/session vào query string, access log, telemetry hoặc localStorage. Ticket là bootstrap tạm, không raw Telegram/provider credential.
5. Grant hết hạn/đã dùng/không đúng owner → màn hình recover, không tự bỏ auth. Restart backend xóa phiên in-memory hiện tại; UI hướng owner trở về launcher.

Nút mở dashboard không cấp quyền đọc Telegram mới và không tự xác nhận pending action. “Đăng xuất dashboard” chỉ thu hồi browser session, không đăng xuất account Telegram hoặc dừng worker. Sau khi logout, chỉ click mới trong launcher mới tạo phiên mới; không loop tự đăng nhập lại khi owner chủ động logout.

### Fallback và direct URL

Nếu không thể triển khai bootstrap an toàn trong milestone đó, launcher hiện mã dashboard và nút sao chép, có countdown theo `expires_at` backend; browser có field mã 8 số hiện có. Happy path fallback vẫn không cần terminal. Mã hiện tại đổi theo cửa sổ 300 giây, không bảo đảm mã mới còn đủ 5 phút; copy không ghi “còn 5 phút” nếu chưa tính thời gian còn lại. Cơ chế HMAC hiện có không dùng một lần; không gọi nó one-time cho tới khi backend đổi.

Fallback v1 phải được nâng cấp để consume mã một lần đúng spec auth; giữ CLI recovery nếu cần, nhưng không biến CLI thành đường chuẩn. Countdown/use-once đều do backend quyết định.

Mở `127.0.0.1` trực tiếp khi chưa có phiên: màn hình “Mở ứng dụng Telegram AI để đăng nhập”, một CTA “Mở ứng dụng” nếu app protocol đã đăng ký và một lựa chọn “Nhập mã từ ứng dụng”. Không gắn API key/OTP field vào login dashboard. Hết phiên khi đang sửa: giữ draft không bí mật trong memory, yêu cầu đăng nhập lại, tải dữ liệu mới rồi cho owner xem lại trước gửi; không replay mutation tự động.

## 5. Card kết nối và trạng thái chuẩn

### Anatomy dùng chung

Card có icon + tên dịch vụ; badge bằng chữ; mô tả tác dụng; identity/config không bí mật; “Kiểm tra lần cuối …”; CTA chính; liên kết trợ giúp theo lỗi. Một card không có quá hai CTA nổi bật. Detail kỹ thuật nằm trong disclosure. Credential hiện “Đã lưu”/“Chưa có”, không biểu diễn độ dài thật hay fragment.

| Card | Identity/config được hiển thị | CTA chính | Thao tác phụ |
| --- | --- | --- | --- |
| Tài khoản Telegram | Tên/@username, owner ID, tình trạng session | Kết nối lại trong ứng dụng / Kiểm tra | Ngắt kết nối có giải thích ảnh hưởng; không multi-account switch |
| Bot trợ lý | Tên/@username, paired owner, trạng thái pairing | Mở trợ lý / Ghép owner | Thay token trong launcher; kiểm tra token |
| OpenAI/OpenRouter | Provider, model, credential present, giới hạn ngân sách | Kết nối/Thay key trong ứng dụng | Kiểm tra; chọn provider/model; tắt AI |
| Ollama | Endpoint local không bí mật, model chat/embedding, download/job state | Kiểm tra / Quản lý model | Khởi động hoặc hướng dẫn sửa daemon |
| Hệ thống local | Worker/API/database/vector health đã probe | Chẩn đoán | Log đã redaction, backup/restore trong luồng riêng |

Contract dùng chung: `ConnectionState = checking | ready | degraded | disconnected | unknown`; `ConnectionStatus(service, state, checked_at, code, message, next_action, capabilities)`. Service gồm `telegram_account`, `control_bot`, `chat_ai`, `embeddings`, `storage`, `runtime`. Các label Chưa cấu hình/Hết hạn/Lỗi/Off là copy từ state+code, không tạo enum thứ hai. `retry_at` nếu cần phải bổ sung nhất quán trong schema dùng chung trước implementation. Provider card nhận chat và embedding service health riêng; unsupported model/profile có code rõ. SSE connectivity là trường riêng, không quyết định health tất cả dịch vụ.

UI phân biệt “Kết nối được” với “Sẵn sàng hỏi AI”: AI ready cần credential/provider, model và ngân sách; RAG ready còn cần embedding/vector/nguồn được phép. Bot paired không có nghĩa account MTProto hoạt động. Provider Off hiển thị “Đã tắt”, không “Lỗi”. Snapshot cũ luôn có timestamp và nhãn “Dữ liệu lần cuối”; `unknown` không render xanh. Worker dừng không tiếp tục hiển thị `RUNTIME ACTIVE` như nội dung tĩnh.

Nếu backend chưa có health endpoint thật: hiện “Chưa có kiểm tra kết nối này”, không nút test giả. UI agent phải ghi rõ dependency API chưa đủ; API agent cung cấp schema trước mutation UI.

## 6. Cấu trúc dashboard và nội dung

| Mục chính | Nội dung | Mapping hiện tại |
| --- | --- | --- |
| Tổng quan | Readiness, sự cố cần xử lý, nguồn/jobs, pending count; CTA mở Telegram | `overview`, quick summary `workers/actions` |
| Kết nối | Account, bot, AI provider, sức khỏe máy; mở launcher sửa | `connections`, phần thiết lập `ai-rag/security` |
| Nguồn Telegram | Directory, chi tiết, cấp/thu hồi quyền, `/ask`, moderation | `groups`, policy per source |
| Tri thức | Job học, inventory, coverage, source filters | `knowledge`, RAG diagnostics |
| Công việc | Pending actions cần duyệt và task/reminder nếu API thật khả dụng | `actions`; giữ task/reminder Telegram nếu chưa có dashboard API |
| Vận hành | Worker/jobs, lưu trữ, audit, AI provider/model/chi phí | `workers/storage/audit/ai-rag/models` |
| Cài đặt & trợ giúp | Kết nối advanced, bảo mật, policy giải thích, tài liệu, backup/restore | `security/documentation/policy`, settings disclosure |

“Mở trợ lý Telegram” luôn hiện tại top bar/launcher; không tạo dashboard chatbot thứ hai trong v1. Giữ route/state mapping cũ hoặc migration/deep-link redirect để các liên kết hiện có không rơi vào màn hình trống. Back button và filter trong nguồn/job hoạt động theo URL/state có chủ ý; quay lại danh sách giữ query, trang và scroll.

Nguồn chi tiết ưu tiên identity + ALLOW/BLOCK + read/learn readiness trước permissions kỹ thuật. Preset người dùng hiểu được và “Quyền nâng cao” chứa đủ 19 permission backend, sáu AI mode, quota/retention. Preset không che sự khác biệt quyền thực tế trong Telegram. Đổi BLOCK tạo trạng thái “Đang thu hồi” nếu còn job đang dừng; kết thúc mới ghi thu hồi hoàn tất. Worker không được bật ALLOW lại do job/retry cũ.

Copy mặc định tiếng Việt, câu hành động cụ thể: “Học 3 nguồn đã chọn”, “Dừng an toàn”, “Tiếp tục lập chỉ mục”, “Mở ứng dụng để thay API key”. Các mã kỹ thuật/ID giữ nguyên và có copy button. “Hành động chờ” không dùng chung với “job đang chạy”; yêu cầu phải phân biệt pending → owner confirmed → queued/executing → completed/failed/expired/cancelled.

### Inventory tính năng và nhãn capability

Không hiển thị `AVAILABLE`/`ACTIVE` tĩnh cho mọi tính năng như danh sách `TelegramFeaturesView` hiện tại. Baseline có code không đồng nghĩa installation hiện tại dùng được. Các nhãn dùng copy: “Sẵn sàng”, “Chưa kết nối”, “Cần cấp quyền”, “Cần cấu hình AI”, “Chế độ giới hạn”, “Tạm dừng”, “Đang xử lý”, “Nâng cao” hoặc “Chưa hỗ trợ trong giao diện này”, dựa vào capability/backend thật.

| Nhóm tính năng đã có trong sản phẩm | Nơi tiếp cận v1 | Điều kiện/nhãn cần phân biệt |
| --- | --- | --- |
| Hỏi AI, tóm tắt/digest, dẫn chứng | Bot Telegram; dashboard AI/vận hành | Account+bot paired+chat provider; RAG cần embedding/nguồn; Off/budget/policy/source time window |
| Keyword search, memory, task/reminder | Bot Telegram là chính | Không ngầm cần AI cloud; chưa account/paired phải khóa nghiệp vụ; không thêm dashboard editor nếu chưa có API |
| CoinGecko price | Bot Telegram; cấu hình optional | Missing key/provider quota nói riêng; optional không block setup |
| Directory, quyền/preset, group `/ask`, chống spam AUTO | Nguồn Telegram | Metadata/account ready; default BLOCK; quyền thực tế và standing authorization cho AUTO |
| Sync/backfill/learning/coverage/recovery | Nguồn/Tri thức | Queued/running/paused/partial/failed/revoked; learning là indexing; bulk limits thật |
| Export lịch sử/kiểm kê, retention/quota/cleanup | Nguồn/Tri thức/Vận hành | Eligibility/counts/output thật; cleanup cần preview+confirm; Chat ID text |
| Local models/routing/budget/embedding profile | Vận hành/Kết nối advanced | Chat và embedding readiness riêng; native key input; download/reindex có plan |
| Pending/audit/worker/storage/security/backup/restore | Công việc/Vận hành/Cài đặt | Session/capability, trạng thái uncertain và maintenance; không viết toast-only action khi API thiếu |

Nhãn “Nâng cao” là mức disclosure, không phải trạng thái kết nối. Khi dashboard chưa có endpoint cho chức năng Telegram đang có, link mở bot hoặc guide; không tuyên bố tính năng không tồn tại trong sản phẩm và không thêm UI control giả.

## 7. Lỗi và recovery bắt buộc

Thông báo lỗi có ba phần: chuyện gì xảy ra; tác vụ nào còn dùng được; một bước sửa tiếp theo. Raw stack trace không xuất hiện trong main flow. Mã lỗi an toàn và correlation ID trong “Chi tiết chẩn đoán” giúp support mà không chứa secret. Retry không tạo job/mutation trùng; mutation có timeout cần đọc trạng thái server trước gửi lại.

| Trigger | UI bắt buộc | Recovery và giới hạn |
| --- | --- | --- |
| API ID/hash/phone sai | Error gắn đúng bước, giữ field không bí mật | Sửa field; không mất bot/AI đã cấu hình |
| OTP sai/hết hạn | Báo challenge cần mới; label field rõ | Xin mã mới khi backend cho phép; không dùng retry vô hạn |
| 2FA cần hoặc sai | Form password native có lỗi tại field | Nhập lại; không lưu; không hỏi password qua bot/dashboard |
| Telegram FloodWait | Countdown/thời điểm được thử lại thật | Disable resend/retry liên quan; vẫn xem dashboard/read snapshot |
| Bot token lỗi hoặc bị thu hồi | Bot disconnected; account vẫn trạng thái riêng | Thay token ở launcher; kiểm tra và pairing lại nếu cần |
| Pair sai owner/hết hạn | Nêu đúng account cần dùng/mã hết hạn | Mở đúng Telegram account hoặc tạo nonce mới |
| Key cloud sai/401/quota/rate limit | Phân biệt credential với ngân sách/quota/provider busy | Thay key, tăng giới hạn do owner, hoặc chờ; không tự bật provider khác |
| AI Off | Empty state hợp lệ, hướng dẫn tính năng còn dùng được | “Thiết lập AI” hoặc tiếp tục keyword/tasks |
| Ollama thiếu daemon/model/embedding | Phân biệt ba nguyên nhân | Mở bước sửa; download có retry/cancel; không xóa model đang hoạt động |
| Worker/API dừng hoặc port bị chiếm | Launcher không mở tab trắng; dashboard ghi offline | Nút khởi động/kiểm tra xung đột; không mở firewall hoặc đổi port âm thầm |
| DB/migration lỗi | Block setup; tóm tắt không mất dữ liệu | Chẩn đoán hoặc rollback theo packaging plan; không recreate DB tự động |
| SSE mất kết nối | Banner trạng thái, snapshot last success | Backoff hữu hạn; retry thủ công; disable thao tác cần state mới |
| Browser 401/403 | 401: đăng nhập lại; 403: phân biệt CSRF/quyền thật | Refresh session/token nếu hợp lệ; không replay write tự động |
| Nguồn mất quyền Telegram hoặc BLOCK | Badge và job state bị thu hồi/chặn | Cấp quyền thật nếu owner chọn; không bật lại ALLOW từ retry |
| Sync xong, embedding lỗi | Hiện hai pha riêng; dữ liệu gốc còn | Retry embedding từ checkpoint; không đồng bộ/nhân đôi cả lịch sử |
| Không có kết quả AI | Giải thích nguồn/time window/permission/coverage | CTA tới nguồn hoặc học; không tạo câu trả lời/nguồn giả |
| Pending hết hạn/quyền thay đổi | Preview không còn xác nhận được | Tạo preview mới sau kiểm tra; không confirm payload cũ |
| Secret store/session hỏng | Native chẩn đoán, không gợi ý xóa tất cả | Thiết lập lại connection có preview ảnh hưởng; giữ dữ liệu chưa cần xóa |
| Restore/đổi account | Báo ràng buộc identity và credential máy Windows | Tái kết nối owner; không coi backup có secret portable |

Hành động nhạy cảm vẫn bắt buộc preview + owner confirmation. Modal hiển thị tên/ID nguồn, quyền/dữ liệu ảnh hưởng, tổng counts backend, thời hạn, cảnh báo và CTA mô tả hành động. Xóa toàn bộ yêu cầu nhập tên nguồn; không dùng nút “OK”. Detail payload kỹ thuật thu gọn. Nếu backend thiếu preview/count, không suy ra 0 và không mở xác nhận phá hủy.

## 8. Art guideline cần giữ nguyên

| Token/đặc điểm | Nguồn hiện có | Yêu cầu preservation |
| --- | --- | --- |
| Nền giấy | `--paper: #f3efdf`, `--paper-2: #fffdf5`; background `#d7d3c8` | Duy trì hierarchy nền giấy/panel/khung |
| Mực | `--ink: #090909` | Chữ, viền, shadow chủ đạo màu mực |
| Accent | teal `#00c8c8`, teal-dark `#008f90`, magenta `#ef00c8`, yellow `#ffd51f`; green `#16c98d` | Dùng màu gốc, mỗi màu có vai trò rõ; badge còn phải có chữ/icon |
| Viền/shadow | `--line: 3px solid`, shadow `7px 7px 0`, small `4px 4px 0` | Khung dày, góc vuông, bóng cứng; compact variation nhất quán |
| Font logo/title | `public/fonts/PeterObscure.ttf` | Chỉ logo và tiêu đề chính mỗi trang |
| Font UI | `public/fonts/DarleySans-Regular.otf` | Mọi heading panel/body/nav/form/button/table/metric |
| Reference | `reference/neo-brutalist-reference.png` trong guideline | Agent visual QA lấy asset từ full checkout; không tự tạo reference mới |

Được đổi bố cục, IA, mật độ và copy theo hành trình mới. Không biến dashboard thành glossy glassmorphism, gradient mềm, card bo tròn chung chung hay typography display ở control dày đặc. Decorative poster chỉ dùng ở welcome/login và không đẩy CTA chính ra dưới fold trên màn hình nhỏ. Error/focus không chỉ dựa vào magenta/teal. Nếu token muted hiện tại không đạt contrast với nền, thêm token text accessible tối thiểu và ghi lý do; không đổi toàn palette.

Checklist visual trước merge:

- [ ] Font bundled load được offline; tiếng Việt và dấu không bị cắt; fallback UI được kiểm tra.
- [ ] PeterObscure chỉ logo/main page title; bảng/form/modal dùng DarleySans.
- [ ] Khung vuông, black rules, hard shadows và palette giấy/accent giữ được ở launcher và web.
- [ ] Cấp bậc CTA chính/phụ/nguy hiểm giống nhau giữa launcher, dashboard và modal.
- [ ] Ảnh before/after đúng route cùng viewport, chứa fake fixture hoặc dữ liệu đã ẩn; không chứa credential.
- [ ] Review 320/375, 768, 1024, 1440 px và Windows scale 125%/150%; không cắt focus/shadow/CTA.
- [ ] Không thêm mock operational values, concept banners hoặc toast-only controls.
- [ ] Runtime badge và inventory feature nhận capability thật; thiếu data có unknown/stale/empty, không mặc định xanh hoặc AVAILABLE.

## 9. Accessibility và responsive

Đây là tiêu chí thiết kế/nghiệm thu đề xuất, chưa phải chứng nhận accessibility hiện tại.

- Tất cả happy path/recovery chạy bằng keyboard: Tab/Shift+Tab, Enter/Space, Escape phù hợp; thứ tự focus theo bố cục. Focus visible tối thiểu 2px, contrast rõ, không bị hard shadow che.
- Label bền vững cho form; hint/error liên kết qua accessible description. Không dùng placeholder thay label. Paste/API token dài không làm phá layout; OTP paste toàn chuỗi được.
- Modal trap focus, có accessible name/description, focus về trigger khi đóng. Escape không tự confirm hoặc commit; với native secret dialog, đóng phải xóa giá trị chưa gửi.
- Contrast mục tiêu: text thường ít nhất 4.5:1; text lớn ít nhất 3:1; controls/focus quan trọng ít nhất 3:1. Đo thực tế trên token và trạng thái disabled/error/selected, không giả định palette đạt.
- Hit target tối thiểu 44×44 CSS px cho CTA/icon hành động; không ép hàng table dày đến mức target không dùng được. Icon-only có tên đọc được và tooltip hữu ích.
- Toast không phải nơi duy nhất chứa lỗi/kết quả. Error tại region liên quan; live progress `aria-live=polite`, không announce mỗi tick/download byte. Lỗi cần phản hồi ngay dùng alert có kiểm soát.
- Reduced motion bỏ hiệu ứng trang trí và animation lặp; spinner có text trạng thái. Không dùng flashing.
- Native window resize tới kích thước tối thiểu được hỗ trợ mà không mất nút Next/Back/Cancel; zoom 200% trong browser không che form hay modal. Tên nguồn/ID dài wrap hoặc có copy, không cắt identity không thể xem.
- Desktop ≥1024: sidebar cố định và khu nội dung linh hoạt; 768–1023: sidebar thu gọn có label khi mở; <768: drawer keyboard-accessible, card xếp một cột. Launcher wizard một cột với tiến độ gọn ở màn nhỏ.
- Không cuộn ngang toàn trang ở 320 CSS px. Bảng dữ liệu có vùng cuộn riêng được đặt tên; summary/action quan trọng vẫn nhìn được. Chi tiết nguồn đổi sang label/value khi cần.
- Mobile layout dùng được trong browser trên máy Windows; v1 không hứa điện thoại truy cập dashboard loopback trên PC. Không thêm QR “mở dashboard trên điện thoại” trái boundary local.

## 10. Hợp đồng backend/UI phải bàn giao trước implementation

Các tên dưới đây là contract cần bổ sung, không phải endpoint hiện hữu được xác nhận:

| Contract | Trường/tín hiệu tối thiểu | Chủ sở hữu |
| --- | --- | --- |
| Setup state | completed steps, next step, identity safe, dependency checks, resumable state/version | Launcher + setup service |
| Connection health | status/reason/check timestamp/retry time/capabilities, state account/bot/provider riêng | Backend |
| Credential operations | Set/test/replace native write-only, result safe; không endpoint đọc credential | Native + security |
| Dashboard launch/auth | Authenticated local request, grant exchange, TTL/use-once, revoke, logged-out behavior | Auth/security |
| Learning authorization | Policy version/revocation, job phase, cancellation, counts, retries/checkpoint | Runtime/policy |
| Events | Connection/job/action/session events có sequence/timestamp, snapshot reconciliation | Backend + frontend |
| Error taxonomy | Stable reason code, safe message, retryable, next action, correlation ID | Backend + UX |

UI phải dựa vào schema và trạng thái kiểm chứng, không tự triển khai validation thay backend. “Kiểm tra kết nối” chỉ kết luận đúng capability đã test, không đánh đồng credential present với provider healthy. Không UI-only disable rồi coi đó là enforcement.

## 11. Work package giao agent và thứ tự thực hiện

| ID | Đầu ra hữu hình | Phạm vi file dự kiến | Phụ thuộc / cách chứng minh |
| --- | --- | --- | --- |
| UX-01 | IA/flow và inventory screen-state, copy deck VN | Gói này; thêm `docs/ux/screen-state-inventory.md` và copy deck khi triển khai | Contract backend, giữ 7 mục và Telegram primary; review trạng thái/error đầy đủ |
| UX-02 | Design tokens/components dùng chung launcher-web | `dashboard-prototype/src/styles.css`, `src/ui.jsx`, bundled fonts; launcher UI area do kiến trúc chốt | Full source/art asset; screenshots same viewport, keyboard/contrast |
| UX-03 | PySide6 setup/tray + connections edit/recovery | `src/tg_assistant/setup/wizard.py`, `cli.py`, `runtime.py`, `security.py`, `config.py`, `db/`; native launcher module mới theo architecture | SQLite/migration + MySQL compatibility, packaging/lifecycle/credential contracts; resume, cancel, invalid OTP/token/provider |
| UX-04 | One-click dashboard launch + fallback GUI code | `admin_api/auth.py`, `admin_api/app.py`, `schemas.py`, launcher auth; `src/App.jsx`, `src/api.js` | Auth/security review; use-once/expiry/wrong owner/logout/direct URL tests |
| UX-05 | Cards kết nối, navigation, overview readiness | `src/views/SystemViews.jsx`, `OverviewView.jsx`, `App.jsx`, `hooks.js`, `api.js`, health backend | Health schema; không suy diễn ONLINE; offline/stale/unknown fixtures |
| UX-06 | Quyền/nguồn/học/pending rõ scope và recovery | `GroupsView.jsx`, `KnowledgeView.jsx`, `AiViews.jsx`, `OperationsViews.jsx`; policy/runtime | Revocation bug fixed; scope/regression tests; không duplicate jobs/retry mutation |
| UX-07 | Tài liệu người mới + screenshots + UAT | `USER_GUIDE.md`, `README.md`, `TROUBLESHOOTING.md`, `PRIVACY.md`, dashboard tests | Build chạy được; tài liệu không yêu cầu terminal happy path; phù hợp corpus đã chốt |

Thứ tự: UX-01/02 có thể chuẩn bị sau khi baseline/contract rõ; UX-03/04 chỉ tích hợp sau setup/lifecycle/auth foundation; UX-05 sau health contract; UX-06 sau revocation và permission fixes; UX-07 đóng vòng. Agent frontend không tự giải quyết migration/packaging bằng dữ liệu fake để bỏ dependency. Mỗi PR một work package hoặc một phần nhỏ review được, kèm trạng thái chưa hỗ trợ nếu contract còn thiếu. Không yêu cầu merge hoặc deploy trong gói planning này.

### Handoff prompt mẫu cho agent triển khai trải nghiệm

> Đọc `docs/handoff/experience-work-package.md`, roadmap tổng và full checkout AGENTS.md trước sửa. Thực hiện work package UX-[ID] cho ứng dụng Windows local mỗi owner, giữ art gốc và Telegram primary. Xác nhận contract backend cần dùng, không tạo state operational giả hoặc nhập secret qua dashboard. Trình bày file/luồng cụ thể, triển khai theo phạm vi đã giao, kiểm tra success/error/offline/restart/revocation tương ứng. Bàn giao diff, screenshots đã ẩn secret cùng viewport, test evidence và giới hạn còn lại; không tự mở SaaS, đổi database, bỏ auth hoặc tự xác nhận destructive action.

## 12. Acceptance criteria và phép đo

### Gate chức năng bắt buộc

- [ ] Fresh-install trên Windows 10/11 sạch dùng SQLite/runtime đóng gói, hoàn tất môi trường thật, account, bot, AI tùy chọn và mở dashboard không cần CLI/MySQL. Installation MySQL cũ được tiếp tục dùng, không tự convert. Kết quả cho dependency chưa cài được phải ghi blocker, không pass giả.
- [ ] One-click launch có phiên hợp lệ và auth giữ boundary local; direct URL chưa auth không có dữ liệu owner. Fallback mã GUI hoạt động khi không có bootstrap.
- [ ] Không dùng grant/code sau use/TTL; account khác không pair thành owner; logout không tự login lại; backend restart yêu cầu phiên mới.
- [ ] Secret native không có read endpoint/raw-secret HTTP path, không nằm trong DOM/dashboard/localStorage/query/log/screenshot; replace key lỗi giữ cấu hình hoạt động trước đó. Kiểm tra bằng fixture, không dùng secret thật trong artifacts.
- [ ] Chưa có app credential thì lưu/resume được và dashboard hạn chế hiển thị đúng; không tự cấp owner bot-only. QR cần API ID/hash và có OTP/2FA fallback; không có auto BotFather/API creation giả.
- [ ] Cloud chat+embedding hoạt động không cần Ollama; có consent nội dung/chi phí; local chat+embed chọn riêng; chat healthy không biến embedding unsupported thành RAG ready. Embedding profile change có preview/reindex và tôn trọng cloud rules từng nguồn.
- [ ] Hai trạng thái account và bot độc lập; cloud Off không block setup; cloud budget exhausted và provider bad key có recovery khác nhau.
- [ ] Gián đoạn ở mỗi bước rồi restart quay về bước hợp lệ; không lưu OTP/password, không tạo account/db/job trùng.
- [ ] Default BLOCK; bật knowledge không tự bật moderation/member ask; BLOCK khi job queued/running/retry/restart không bị worker cấp lại quyền.
- [ ] Preview nguồn/bulk counts/quyền/time scope đúng backend; page selection không biến thành tất cả; pending hết hạn không confirm được.
- [ ] Learning phase/counts thật; sync xong embedding fail retry đúng phần; không có data không render 0 như đã kiểm tra.
- [ ] SSE offline/stale/session expired hiện đúng banner và timestamp; mutation chưa biết kết quả không retry mù.
- [ ] 19 quyền và 6 AI mode vẫn truy cập được dưới advanced; route migration không làm mất các tính năng đã có.
- [ ] Art checklist và keyboard/responsive/contrast checks bên trên đạt, không dùng stored secret cho visual QA.
- [ ] Tài liệu/backend/copy nhất quán phạm vi group `/ask`, 7 ngày, learning semantics, retention và cloud data disclosure.

### Usability target để xác minh beta

Các con số sau là mục tiêu đề xuất, không phải metric đã đạt. Thử với ít nhất 5 người mới không tham gia phát triển, dùng account/bot riêng và môi trường beta đã đồng ý. Không thu credential hoặc nội dung chat vào nghiên cứu.

| Metric | Cách tính | Target đề xuất |
| --- | --- | --- |
| Hoàn tất onboarding không trợ giúp | Người có credential chuẩn bị hoàn tất account + bot pairing + câu trả lời đầu + dashboard / người bắt đầu, ghi lỗi dependency riêng | ≥4/5 trong 15 phút theo spec tổng; báo số tuyệt đối kèm tỷ lệ |
| Thiết lập thao tác chủ động | Từ launcher ready đến bot paired, loại thời gian chờ OTP/download nhưng báo elapsed tổng riêng | Median ≤10 phút |
| Mở dashboard sau setup | Click tới trang authenticated, loại cold-start worker nhưng báo cold-start riêng | 1 click; p95 ≤5 giây khi backend sẵn sàng |
| Biết nơi tương tác chính | Người chọn mở Telegram để hỏi trợ lý sau setup | 5/5 không nhầm dashboard thành chatbot |
| Sửa key/token hết hiệu lực | Người tự tìm connection đúng và replace thành công | ≥4/5 không cần terminal/support |
| Hiểu phạm vi quyền | Người diễn giải đúng BLOCK, knowledge và member `/ask` sau preview | 5/5 ở bài kiểm tra tác vụ có dữ liệu nhạy cảm |
| Thu hồi quyền | Backend chặn request mới ngay sau commit; UI phản ánh qua event/poll | 0 lần job cũ bật lại quyền; UI target ≤5 giây |
| Responsive/keyboard blockers | Blocker không hoàn tất được tác vụ ở ma trận viewport và keyboard | 0 blocker trong release gate |

Chỉ đo local opt-in hoặc qua quan sát beta; default không gửi telemetry lên server. Nếu có telemetry sau này, chỉ event/status/timing đã cho phép, không phone/usernames/Chat ID, prompts, message content, file path nhạy cảm hay credential. Không suy ra adoption bằng việc mở link bot; cần event thành công thực hoặc quan sát tác vụ.

## 13. Bộ UAT tối thiểu và bằng chứng bàn giao

1. Fresh machine không MySQL/Python/Ollama → setup SQLite → account OTP/2FA hoặc QR đã có app credential → bot đúng owner → cloud chat+embedding/consent → cấp một nguồn → học → hỏi bot có dẫn chứng phù hợp.
2. Fresh machine → skip AI → dashboard → keyword/search/task khả dụng; bật AI sau không setup lại account.
3. Ollama có daemon/chưa có daemon, không model/chat thiếu embedding; tải/hủy/retry model, giới hạn phần cứng. Đổi embedding profile preview/reindex và nguồn cấm cloud.
4. OTP sai/hết hạn/FloodWait, token bị thu hồi, wrong-owner pairing, pairing nonce hết hạn; sửa đúng bước.
5. Kill/restart app ở từng bước và job phase; mất mạng/provider tạm lỗi; browser/SSE offline và session expiry khi có draft.
6. Dashboard direct URL chưa login; one-click/expired/reused grant; GUI code countdown; logout và backend restart.
7. Nhiều trang nguồn, search không mất filter, bulk chọn trang và chọn mọi nguồn phân biệt; tên/ID dài và channel không lấy được nội dung.
8. BLOCK queued/running/retry/restart job; member ask đúng group; pending action hết hạn/quyền thay đổi/execute fail không xuất hiện completed giả.
9. Preview xóa dữ liệu/model/leave source; cancel không thực thi; thiếu count không confirm; nguồn owner creator/admin có guard đúng.
10. Keyboard-only, screen-reader smoke, zoom 200%, reduced-motion, scale125/150%, viewport320/375/768/1024/1440.
11. Installation MySQL hiện có mở đúng dữ liệu và không convert; setup chưa có app credentials lưu/resume và dashboard limited state, account verified vẫn bắt buộc trước owner pairing.

Agent QA nộp: OS/build/version/commit, setup preconditions, case pass/fail với bước tái hiện, screenshot đã redaction cho main screen-state, kết quả keyboard/contrast, log có correlation ID không secret và các failure chưa khắc phục. Chỉ gate thật đã chạy được mới ghi pass. Các kiểm tra provider/Telegram thật phải chạy trong tài khoản beta owner kiểm soát và không gửi/xóa tin thật ngoài kịch bản đã cho phép.

## 14. Quyết định kiến trúc đã khóa và đầu vào triển khai còn lại

| Quyết định | Default UX trong gói này | Ai chốt / trước milestone nào |
| --- | --- | --- |
| Native UI framework và packaging | Đã khóa PySide6 Qt Widgets launcher/setup/tray, runtime bundled, browser React dashboard; không QtWebEngine | Packaging/launcher thực hiện trước UX-03; distribution/font license gate |
| Database | Đã khóa SQLite cho cài mới; MySQL cũ advanced, không auto-conversion | Backend/packaging chứng minh cả hai trước beta |
| Local auth transport | Đã khóa named pipe SID, ticket256-bit/hash-only/TTL30s/one-use, fragment + same-origin redeem; GUI code fallback | Auth/security chứng minh trước UX-04 |
| Group corpus và private-bot corpus | Group trong chính group theo audit; private bot theo policy hiện hành đã test | Backend/policy, trước UX-06 và docs |
| Identity/profile | Đã khóa một active profile per Windows SID; không thêm multi-account switch | Backend/security chứng minh pairing và disconnected mode |
| Provider models/presets ngân sách | Backend cung cấp supported config; owner duyệt trước cloud | AI/backend, trước UX-03/05 |

Không đưa SaaS trở lại như một câu hỏi mở của v1. Những quyết định kỹ thuật này là đầu vào triển khai có owner và deadline; không cần giữ gói planning ở trạng thái chờ để viết phần UX còn lại.
