# Đợt cải tiến 2 — Luồng sử dụng, ranh giới riêng tư và AI

PR: https://github.com/NyanBUIDL/telegram-ai-personal-assistant/pull/3 (draft, không merge/deploy).
Gốc đợt này:`3228e543`. Chủ dự án đã cho phép sửa/commit/push nhánh feature và cập nhật draft PR.

## Phạm vi và quyết định sản phẩm

PM/BA chọn một đợt hữu hạn: làm cho người dùng thấy bước tiếp theo, hiểu dữ liệu nào còn cũ, không chọn vượt hợp đồng500 nguồn; ràng buộc cloud AI theo quyền nguồn; xử lý lỗi model và worker trung thực. UX giữ phong cách dashboard và cơ chế preview/confirm hiện tại. Architect giữ các service, route planner và credential store sẵn có. Security review tập trung vào classifier/owner-private/inputAI; AI và DevOps triển khai các lỗi có reproduction; reviewer độc lập kiểm tra diff và SDK thật với transport giả.

Không đổi dependency/model, provision hạ tầng, đăng nhập/gửi Telegram thật, gọi AI trả phí, tạo credential, đổi firewall/bảo mật OS, xóa dữ liệu người dùng, merge hay deploy.

## Ranh giới riêng tư được sửa

| Finding hoặc hardening | Bằng chứng trước sửa | Hành vi sau sửa / nghiệm thu |
|---|---|---|
| Classifier bỏ qua quyền nguồn (medium, owner-authenticated) | Endpoint history-ai-delete-filter nhận chat không có policy, trả200 và gọi mock cloud; RAG thông thường kiểm tra ALLOW. | API kiểm tra ALLOW+READ trước truy vấn nội dung. Runtime kiểm tra lại, giới hạn request một lần và dùng AiRouter.plan có thẩm quyền trước cloud call. BLOCK/no policy/no read, global off và route không choOpenAI bị từ chối. |
| Secret đã nhận diện đi vào cloud classifier (medium, owner-authenticated) | Token giả được contains_secret nhận ra vẫn xuất hiện nguyên trong responses.create input. | Từ chối tiêu chí chứa secret; redact text nguồn trước cắt 1000 ký tự, không sửa bản ghi gốc. Test token vượt ranh giới cắtđộdài. |
| Control bot trong group (preventive hardening) | Owner có thể gọi lệnh quản trị trong group hoặc kích hoạt callback group. | Chỉ private chat đúng owner ID; group/inline/inaccessible callbacks không được mở flow. GroupAI qua TelegramClient vẫn theo group_ai_ask riêng. |
| Dữ liệu Telegram là input không tin cậy (defense in depth) | Nguồn/câu hỏi ghép chuỗi cùng input. | JSON phân biệt question/untrusted_sources và chỉ dẫn không làm theo nguồn; redaction dùng chung. Đây không phải chứng minh chống mọi promptinjection. |

Route được kiểm tra sau lượt đọc local đã có quyền và trước cloud request; không nhân bản logic AiRouter trong API. Session/CSRF vẫn bắt buộc. Một owner hợp lệ được quyền đọc local không đồng nghĩa được dùng mọi provider.

### Trạng thái formal security scan

Skill Standard security-scan đã được đọc. Tool discovery không cung cấp start_codex_security_standard_scan cùng công cụ draft/artifact/completion bắt buộc; vì vậy không thể khởi tạo scan có danh tính chính thức. Không có scan ID giả, canonical report tự viết hay tuyên bố audit toàn repo hoàn tất. Tài liệu này là ghi chú review mã theo các boundary cụ thể và regression đã xác minh. Hành động bị chặn: bắt đầu formal Standard scan cho repository; lý do: thiếu dịch vụ/tool, không phải auto-review denial. Công việc mã/test/PR độc lập vẫn hoàn tất.

## AI và lỗi vận hành

- Ollama tải model phải nhận terminal success. HTTP 200 chứa error, JSON/progress không hợp lệ, stream rỗng/cắtngang hoặc cancel không thành “completed”. Lỗi được redact và giới hạn độ dài.
- Answer engine ghi usage nhận được và từ chối output trống, failed/incomplete/cancelled; cấu hình fallback hiện hữu có thể thử provider kế tiếp. Không tự mở routecloud mới. Budget vẫn theo transaction caller; không có kế toán API độc lập với rollback toàn bộ request.
- RAG không tự gán ba nguồn đầu khi model không trích dẫn. Outputtrống/citation-only/mã nguồn không có trong context không vào cache. Chế độ cần citation nhưng không có mã hợp lệ báo lỗi để thử lại; cache version loại các entry tạo theo quy tắc cũ. Kiểm tra mã không chứng minh mọi nội dung model là sự thật hay mọi link model tạo ra là đáng tin.
- Worker giám sát coretask và báo lỗi khi task trả về/dừng ngoài dự kiến. Lỗi lúc khởi động đi qua cleanup; các resource closer đều được thử. Schedulerjob được hủy/drain khi DB/client còn mở.
- Pause dở dang sau restart trở thành paused; không tự chạy lại cho đến khi owner resume.
- Review độc lập dùng aiogram SDK thật với polling giả phát hiện cancel outer poller còn để task con chạy. Bản sửa dùng public stop/startup/middleware để dừng và drain update trước đóng session; không truy cập privateSDK fields trong production.
- Review tái hiện stop đồng thời core failure làm mất exception; supervision ưu tiên lỗi thực và không bỏ qua lỗi do stop.

## Luồng dùng đã cải thiện

- `/start` chỉ một welcome có nút chọn nguồn/quản lý/AI; `/help` giữ reference. FSM cũ được xóa và prompt chỉ rõ `/cancel`.
- Reminder hiểu giờ tối, weekday số và tuần lịch; từ chối chuỗi không hiểu. Ngày không giờ mặc định08:00, ack hiển thị Asia/Ho_Chi_Minh và DB vẫnUTC. Xem USER_GUIDE.md để biết grammar/default; đây không phải parser ngôn ngữ tự nhiên tổng quát.
- Dashboard giữ snapshot khi refresh lỗi và gắn nhãn cũ+lần tải thành công cuối+Retry. Response cũ không ghi đè scope/reload mới; unmount an toàn. Khi đổi group, form cũ bị ẩn đến khi dữ liệu có đúng Chat ID; test tái hiện và chặn PUT nhầm sang group mới trong lúc tải hoặc lỗi.
- Lô học theo filter tối đa 500 nguồn được ghi rõ là500 đầu, có số đã chọn/tổng/còn lại; chọn thủ công không vượt500, preview không gửi payload 501.
- Health dựa trên status+checked_at. Không có phép đo làUNKNOWN, dữ liệu quá 5 phút làSTALE; latency không che failed. Clock kiểm tra lại mỗi 30 giây, cấu hình provider không tự được coi ONLINE.

## Xác minh và giới hạn

Kết quả tích hợp cuối và CI được ghi riêng trong CYCLE2_VALIDATION.md. Các lỗi mới có RED→GREEN trước khi sửa. Python dùng SQLite/HTTP/Telegram/AI giả; browser thật chạy UI với API giả. Kiểm tra SDK aiogram không gọi Telegram.

CI đầu tiên của`3228e543`: dashboard vàPython 3.13 pass; Python 3.12 fail 1 test do ORM onupdate tự cập nhật thời gian khi gán timestamp không đổi. Fixture softdelete nay dùng SQL UPDATE rõ updated_at để kiểm chứng việc max-timestamp không đổi một cách xác định, giữ nguyên ý nghĩa regression.

Chưa chạy Telegram thật/OTP, MySQL concurrency, model thật, cài trên tài khoản sạch hoặc Task Scheduler. Readiness handshake vẫn chưa có: PID xác nhận tiến trình đã được tạo, không chứng minh toàn bộ thành phần ready. Model/giá cấu hình hiện hữu chưa được xác minh với provider. Redaction chỉ là patternmatching; budget khi toàntransactionrollback và cachefingerprint cùnggiây/count-neutral vẫn cần vòng thiết kế riêng.

Backlog còn lại: handshake/readiness; outcome notification của ordinarypendingactions; hoàn thiện CSVformula control; nguồn HealthCheck runtime thực; freshnessUI cho các view ngoài batch; corpus revision và kế toán cloud độc lập transaction. Không tuyên bố đã giải quyết mọi cải tiến hay sẵn sàngproduction từ testoffline.
