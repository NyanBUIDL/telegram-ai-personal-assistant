# Kiểm tra trước khi đẩy PR

Áp dụng cho người phát triển và các agent; đây không phải luồng cài đặt của người dùng.
Chỉ Coordinator stage, commit và push. CI xanh phải thuộc đúng commit được đối chiếu.

## Chọn đủ phạm vi kiểm tra

| Thay đổi | Kiểm tra trước push |
| --- | --- |
| Python runtime, database, backup hoặc vector | Test hành vi mới và regression liên quan; toàn bộ Python khi chốt đợt thay đổi; gate MySQL bắt buộc đủ tám module nếu ảnh hưởng storage, quyền, budget hoặc index. |
| Dashboard, auth hoặc native launcher | Build và lint frontend; bộ visual hiện có ở 360/390/1280/1440; luồng auth trình duyệt thật; kiểm tra Qt/native liên quan. Không chỉ chạy bộ test mới. |
| Chỉ sửa test | Tái hiện lỗi cũ, kiểm tra bộ test bị ảnh hưởng và các assertion vẫn giữ nguyên ý nghĩa; xác nhận production source không đổi. Không tăng timeout để che lỗi chưa rõ nguyên nhân. |
| Contracts, design tokens hoặc assets | Kiểm tra generated drift; đối chiếu font/reference hashes và kiểm tra art. Không tự sửa generated mirror hoặc đổi font. |
| Metadata audit hoặc tài liệu | Kiểm tra JSON/link/checklist và audit phù hợp. Mọi fixture được miễn phải có đúng path, kind, hash và bằng chứng; không miễn theo cả thư mục test. |

## Chốt kết quả theo thứ tự

- [ ] Đã đọc và giải thích từng failure/error/skip; lỗi môi trường được ghi riêng.
- [ ] Test UI kiểm tra màn hình hiện tại; các ca bảo mật, lỗi JS/console và bàn phím vẫn được giữ.
- [ ] Các dịch vụ/SDK chạy bằng môi trường đã khai báo, đúng cwd; fixture là dữ liệu tổng hợp riêng.
- [ ] Có review độc lập và mọi finding Critical/Important đã được xử lý.
- [ ] Stage đúng danh sách tệp đã review; `git diff --cached --check` đạt.
- [ ] Chạy audit trên nội dung stage, chờ hoàn tất và **đọc exit code cùng danh sách finding**. Không push khi còn mục chưa phân loại.
- [ ] Commit và push nhánh riêng; không merge/release trong bước này.
- [ ] Đối chiếu CI bằng **full SHA**, kiểm tra đủ các job bắt buộc và artifact upload. Không dùng CI của commit cũ làm bằng chứng cho commit mới.
- [ ] Cập nhật checklist/evidence đúng mức; human/VM/UAT, installer và quyền phân phối chỉ hoàn tất khi có bằng chứng tương ứng.

Các lệnh CI chính nằm trong [.github/workflows/ci.yml](../../.github/workflows/ci.yml).
Runner [scripts/ci_checks.py](../../scripts/ci_checks.py) từ chối gate MySQL nếu thiếu
module, bỏ qua ca bắt buộc hoặc dùng mục tiêu không hợp lệ. Audit tại
[scripts/check-release-content.py](../../scripts/check-release-content.py) chỉ phân loại
fixture theo hash đã review; kết quả audit không cấp quyền phát hành.
