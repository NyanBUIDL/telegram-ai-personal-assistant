# Kiến trúc

## Luồng dữ liệu

1. Telethon liệt kê metadata chat nhưng không lấy nội dung chat bị block.
2. Policy Engine cho phép sync/listener chỉ khi chat được allow và đúng quyền.
3. Upsert tin nhắn theo `(chat_id, message_id)`; `sync_states` và
   `knowledge_checkpoint:<chat_id>` giữ checkpoint từng nguồn.
4. Tất cả group/channel dùng chung một kho: MySQL là dữ liệu gốc và collection
   `telegram_messages` của Qdrant là chỉ mục vector hợp nhất. Học tiếp chỉ upsert tin mới.
5. Keyword search truy vấn MySQL. Semantic search truy vấn corpus Qdrant bằng danh sách
   Chat ID đã được phép và ID bản ghi MySQL nằm trong cửa sổ trượt 7×24 giờ tính từ lúc
   hỏi. Policy và rate limit được kiểm tra một lần cho mỗi yêu cầu đọc, không tính riêng
   từng nguồn.
6. RAG gửi ngữ cảnh tối thiểu cho Responses API và trả nguồn `chat_id/message_id`.
7. Hành động ghi ra Telegram đi qua pending action và Policy Engine lần cuối.

## Hỏi AI từ group

1. Owner bật `group_ai_ask` cho một group `ALLOW` trong control bot và xác nhận
   pending action.
2. Telegram user client chỉ nhận cú pháp chính xác `@<username> /ask <câu hỏi>`.
3. Runtime kiểm tra lại allowlist và quyền `group_ai_ask`, chặn secret, giới hạn
   1.500 ký tự và rate-limit riêng theo thành viên/group.
4. Corpus đầu vào chỉ gồm nguồn `auto_knowledge` đang `ALLOW`; RAG tiếp tục lọc
   `search_messages` và cửa sổ trượt 7×24 giờ.
5. Câu trả lời được gửi bằng Telegram Client, reply vào tin gọi và dùng định dạng
   HTML an toàn của Telegram.

Trước bước 4, bộ định tuyến nhận diện câu hỏi về hội thoại hiện tại. Với ý định
`nhóm này/chủ đề đang thảo luận/tóm tắt hội thoại`, runtime bỏ qua corpus hợp nhất,
đọc tối đa 100 tin mới nhất trong 7 ngày từ đúng `chat_id` đang gọi rồi tóm tắt.

Nếu câu hỏi có `@username` và ý định `đã nói/đã nhắn/lịch sử/chủ đề`, Telegram
Client phân giải entity thành `sender_id`, gọi Telegram history với đồng thời
`chat_id` và `from_user`, upsert tối đa 100 kết quả vào MySQL, rồi truy vấn lại bằng
cặp `(chat_id, sender_id)`. Luồng này chạy trước định tuyến group/corpus nên không
thể trộn tin của sender khác hoặc group khác.

## Danh mục quản lý group

Entry point `/groups` và nút `Mở quản lý nhóm` mở một hub ba nhánh:

- `ai`: group `ALLOW` có `group_ai_ask` đang bật.
- `permissions`: group `ALLOW` có ít nhất một quyền khác đang bật nhưng chưa bật
  `group_ai_ask`.
- `all`: toàn bộ group/supergroup như danh sách truyền thống.

Mỗi nhánh dùng cùng xếp hạng tương tác, tối đa 10 group mỗi trang. Tìm kiếm và CSV
vẫn chạy trên toàn bộ danh sách.

## Tính nhất quán

MySQL là source of truth. Mỗi context manager database tự commit hoặc rollback. Unique
constraint chống sync trùng. Worker hành động dùng row lock/`skip_locked`; PID lock chặn
hai instance trên cùng máy.

## Schema

Migration `0001` tạo các nhóm bảng Telegram, policy, task, AI và hệ thống được mô tả trong
đặc tả. ID Telegram dùng BIGINT, nội dung dùng TEXT/LONGTEXT tương thích, metadata dùng
JSON, timestamp lưu UTC. Migration production chỉ chạy `upgrade`; downgrade là thao tác
phát triển có chủ đích.
