# Quyền riêng tư

Ứng dụng chạy cục bộ và chỉ lập chỉ mục nội dung của chat đã được allow. Chat bị block chỉ
được đọc metadata tối thiểu (tên, ID, loại, username, quyền tài khoản) để chủ sở hữu chọn.

MySQL lưu nội dung và nguồn; Qdrant chỉ lưu embedding, ID tham chiếu và metadata lọc.
OpenAI là tùy chọn. Khi bật, ứng dụng chỉ gửi top-k đoạn liên quan đã kiểm tra lại quyền,
redact secret, dùng Responses API với `store=False`, và không gửi toàn bộ lịch sử.

Khi chủ sở hữu bật `group_ai_ask` cho một group, nội dung AI tổng hợp từ kho dự án
đã học có thể được đăng vào group đó. Câu hỏi tin tức/cập nhật sẽ kèm mã nguồn, link
nguồn và phần dẫn chứng; câu hỏi kiến thức thông thường sẽ ẩn chúng. Quyền này mặc
định tắt, áp dụng riêng từng group, cần chủ sở hữu xác nhận và có thể thu hồi trong
flow quản lý group.

Truy vấn lịch sử `@username đã nói gì` chỉ đọc tin của tài khoản được mention trong
chính group đã bật `group_ai_ask`; các tin truy xuất được đồng bộ vào MySQL theo cùng
chính sách dữ liệu của group đó.

Khi thu hồi chat, quyền có hiệu lực ngay. Trước khi xóa dữ liệu/memory cũ, ứng dụng cần
chính sách rõ ràng của chủ sở hữu. `purge` xóa dữ liệu local và credential nhưng cố ý
không tự drop database MySQL để tránh mất dữ liệu ngoài ý muốn.
