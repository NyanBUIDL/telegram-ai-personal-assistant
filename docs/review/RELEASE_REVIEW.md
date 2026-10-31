# Đợt cải tiến dễ dùng — 31/10/2026

## Kết quả và phạm vi

Nhánh: `codex/ease-of-use-2026-10-31`, gốc `7429fcffd61860e9502f066e0b6a921df72fe176`.
Đây là đợt cải tiến đầu, ưu tiên cài mới và thao tác hằng ngày. Chủ dự án đã giao quyền tự đánh giá, phân vai và sửa cục bộ. Không push, mở PR, merge, deploy, kết nối tài khoản Telegram thật, đọc credential hay chạy migration trên database thật.

| Vai | Trách nhiệm / kết quả |
|---|---|
| PM / Product Owner | Chọn kết quả: cài được, thoát bước nhập được, nguồn bị BLOCK không tự bật lại, hỏi lại AI không lỗi, dashboard giữ thao tác đang sửa. |
| BA | Truy vết setup → pair → cấp quyền/học → search/ask → xác nhận → worker; mô tả tiêu chí nghiệm thu trong kế hoạch và PRODUCT_REVIEW.md. |
| UI/UX | Sửa điều hướng Telegram, lỗi có đường quay lại, bộ lọc có hiệu lực, giữ bản nháp, focus bàn phím trên mobile/tablet; giữ phong cách giao diện hiện có. |
| Architect | Giữ MySQL là nguồn chuẩn, index local độc lập provider chat, cache không phá transaction, kiểm tra lại ủy quyền khi xử lý job. |
| Developers | Thêm regression trước khi sửa; dùng dispatcher/services/SQLite thật với transport AI/Telegram giả. |
| DevOps | Sửa kiểm tra Python, lỗi native stderr PowerShell 5.1, migration cài mới, reindex; thêm CI offline và hướng dẫn khôi phục. |
| Reviewer độc lập | Rà diff, kiểm tra chất lượng test; tìm thêm trạng thái `/ask` và lỗi native stderr, đã tái hiện và sửa. |

## Thay đổi

- `start.ps1` kiểm tra Python trong môi trường hiện có, giữ nguyên môi trường lỗi, báo cách đổi tên/tạo lại; dừng khi tạo môi trường, pip hoặc lệnh chạy thất bại. Không tự nâng cấp pip. Probe import xử lý đúng stderr trên Windows PowerShell 5.1.
- Migration 0002/0003 tránh tạo trùng bảng/cột/index mà migration đầu đã tạo; vẫn hoàn tất schema cũ hoặc dở dang và giữ bản ghi hiện có. Không stamp hoặc bỏ qua migration bằng tay.
- `reindex` truyền đủ BudgetService theo cấu hình, sử dụng embedding local. Đổi provider trả lời chỉ cần restart khi dùng CLI; không bắt buộc reindex.
- Telegram: lệnh thoát các trạng thái nhập, `/cancel` không ID thoát flow, menu xóa trạng thái cũ; `/search` dùng chung xử lý ngày lỗi/chia tin dài; `/confirm` và `/cancel` trả lỗi dễ hiểu; `/today` dùng ngày Việt Nam; `/ask` không giữ trạng thái nhập model cũ. Nội dung chấp thuận/docs mô tả đúng câu trả lời trong group chỉ dùng nguồn của group đó.
- Cache AI: cập nhật row hết hạn, savepoint khi insert đồng thời, giữ transaction của caller; tách định dạng dẫn chứng/quota trong khóa; nhận biết xóa mềm/xóa thật/thay nguồn và thay đổi cửa sổ thời gian.
- Job học: BLOCK hủy công việc cũ; worker kiểm tra phiên ủy quyền trước sync và embedding. Một lần xác nhận học mới sau BLOCK vẫn hợp lệ. Khi timestamp cùng giây, từ chối bảo thủ và yêu cầu xác nhận lại.
- Dashboard: giữ từng field đang sửa khi refresh nhưng cập nhật quyền và field chưa sửa; lọc/sắp xếp/phân trang nhóm ít hoạt động; menu mobile/tablet có focus, Escape, khôi phục focus, ngăn thao tác nền và dọn trạng thái khi logout/resize.
- Thêm `.github/workflows/checks.yml` cho Python 3.12/3.13, lint/build/Sites và browser regression với API giả. CI chưa chạy trên GitHub vì chưa publish.

## Thử trên máy của bạn

1. Xem README → “Bắt đầu với một nguồn”. Nếu `.venv` tải về trỏ máy khác, đóng ứng dụng rồi đổi tên thành tên chưa tồn tại; `start.bat` tạo môi trường mới. Giữ dữ liệu/session/credential.
2. Để dùng dashboard từ mã nguồn mới: cài Node.js 22+, vào `dashboard-prototype`, chạy `npm ci` rồi `npm run build` trước khi khởi động. Build được tạo cục bộ; thay đổi nguồn frontend không tự cập nhật bản build cũ.
3. Chạy `.\start.bat`, hoàn thành wizard và `/pair` trong terminal/bot. Bắt đầu với một nguồn đã cho phép, đồng bộ/học rồi thử tìm từ khóa. Không cần AI cho keyword search.
4. Thử chuyển menu khi đang nhập; `/cancel`, `/help`, `/groups`, `/ask` phải hoạt động. Kiểm tra `/today` theo ngày Việt Nam.
5. Lấy mã dashboard: `.\.venv\Scripts\tg-assistant.exe dashboard-code`. Mã chỉ nhập vào dashboard local. Không gửi key/session cho người hỗ trợ.

## Xác minh

Kết quả cuối được ghi vào `VALIDATION.md` sau lượt kiểm tra tích hợp. Các regression đã được quan sát RED trước bản sửa và GREEN sau đó. Những test chỉ giám sát hành vi đã đúng từ trước được giữ rõ là guard, không tính là lỗi đã sửa.

- Python: SQLite, mock Telegram/AI và vector store local trong thư mục test tạm; không gọi model hoặc MySQL thật.
- Frontend: `npm run lint`, `npm run build`, `npm run test:sites`, `npm run test:regression`. Bộ regression tự mở/dừng Vite loopback và chặn API bằng fixture giả; không chạy bộ UAT gắn dữ liệu thật.
- Viewport browser: 1440×900, 1024×768, 390×844; browser local là Chrome.
- Môi trường hạn chế chặn socketpair nội bộ của asyncio và Chrome loopback. Các test được chạy với escalation đã được duyệt, giữ transport giả. Không có action bị auto-review từ chối.

## Các giới hạn và backlog ưu tiên

1. **P1 — Giám sát worker con và trạng thái khởi động:** runtime tạo các task rồi chỉ chờ stopping; task chết chưa được giám sát. CLI cũng có thể báo đã tạo tiến trình trước khi các thành phần sẵn sàng. Cần readiness handshake và lỗi có hành động phục hồi, kiểm thử task chết/API bind lỗi.
2. **P1 — Khôi phục pause:** job `pause_requested` còn sau restart chưa được chuyển về trạng thái có thể tiếp tục, nhưng vẫn được tính backlog; cần kiểm thử restart trong từng pha pause.
3. **P1 — Kết quả pending action:** cần thông báo hoàn tất/lỗi trong Telegram theo action ID sau commit, không chỉ audit. Hiện nhiều thao tác chỉ báo chờ.
4. **P1 — Reminder:** parser có thể hiểu sai buổi tối và chấp nhận chuỗi vô nghĩa; cần parser nghiêm ngặt và xem trước ngày/giờ local.
5. **P1 — Model download:** Ollama stream lỗi HTTP 200 hoặc mất terminal success có thể bị coi hoàn tất; cần fail rõ và cho retry.
6. **P2 — Refresh dashboard:** dữ liệu cũ vẫn hiện khi refresh lỗi; cần nhãn stale, thời điểm thành công cuối và Retry. Selection “tất cả đã lọc” phải phân trang hơn 500 nguồn hoặc ghi rõ giới hạn. Health phải dùng phép đo thật; document request cũ không được mở lại reader đã đóng.
7. **P2 — Cache fingerprint:** sửa nội dung trong cùng giây có thể không đổi MAX(updated_at); cần corpus revision rõ ràng khi mọi mutation, test thay nội dung cùng timestamp. Cửa sổ thời gian có thể giữ nguyên count nếu số tin đi ra và đi vào bằng nhau trong khi max ID/update không đổi; nên dùng revision hoặc kiểm tra lại từng nguồn cache để loại góc này. MySQL concurrency/row lock mới được kiểm chứng bằng SQLite và review SQL, chưa chạy MySQL thật.
8. **P2 — Search/media và trạng thái học:** media filter đang gom mọi has_media; inventory có thể ghi đè trạng thái paused. Cần phân loại đúng loại file và giữ trạng thái job.
9. **P2 — Repository hygiene:** checkout gốc theo dõi `.venv`, `node_modules`, `.env`, cache, output và dist dù đã ignore. Không đọc nội dung bí mật/capture cá nhân. Đợt này không xóa dữ liệu hoặc rewrite history; nên làm commit dọn tracking riêng, có hướng dẫn sao lưu config trước checkout và luôn build dashboard từ source.
10. **Kiến trúc migration dài hạn:** 0001 vẫn dùng metadata hiện hành thay vì snapshot bất biến. Các sửa idempotent giải quyết va chạm hiện tại; nên đóng băng schema nền ở đợt migration riêng và kiểm thử trên MySQL thử nghiệm.

Phần chưa chạy: tài khoản Telegram thật, OTP/2FA/pair thật, database MySQL thử nghiệm, API/model thật, chất lượng câu trả lời, model availability/pricing, clean-account install, Windows Task Scheduler và CI GitHub. Không có kết luận “sẵn sàng production” từ kết quả offline.

Xem thêm: `PRODUCT_REVIEW.md`, `FILE_COVERAGE.md`, kế hoạch và thiết kế trong `docs/superpowers/`.
