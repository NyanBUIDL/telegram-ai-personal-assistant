# UAT protocol — chứng minh người ngoài dùng được

**Chưa chạy.** Q02 là QA artifact; Q03 là người dùng thật và soak. Không dùng 150 test audit làm bằng chứng installer/onboarding đã thành công. Protocol này cho người tiếp quản tổ chức nghiệm thu, không tự cấp quyền truy cập account hoặc gọi API trả phí.

## Mẫu thử và điều kiện

- 5 người mới chưa biết code dự án, tự cài trên Windows standard user. Có consent tham gia; mỗi người dùng Telegram/bot/API của mình, không chia sẻ key/session/OTP qua chat cho agent.
- Luồng chính cloud chat + embedding đã supported, không cài Ollama. Một luồng local Ollama được QA riêng và đo thời gian tải/setup riêng. CoinGecko optional; không yêu cầu tài khoản/provider không dùng.
- Credentials api_id/hash, bot token, provider key đã chuẩn bị cho chỉ tiêu 15 phút; đồng thời ghi thời gian end-to-end từ chưa có credentials, số bước ngoài app và điểm vướng. Không giấu phần BotFather/my.telegram.org bằng cách gọi tổng onboarding dưới 15 phút.
- Dùng nguồn Telegram thử nhỏ được chủ sở hữu cho phép học; đặt hạn mức request/cost, gửi/xóa và nguồn cụ thể. Consent cloud theo nguồn; một nguồn LOCAL ONLY để test cấm gửi cloud.
- Máy QA sạch không có Python/Node/MySQL/Ollama; OS/build/x64/runtime/package versions và installer SHA-256 ghi rõ. Windows 10 support chỉ claim sau lock Qt/runtime phù hợp và artifact pass; Windows 11 kiểm riêng.

## Kịch bản người thử mới

1. Từ file installer, cài bằng quyền standard user và mở shortcut; không mở terminal. Nhìn thấy dữ liệu nằm đúng profile và app không báo Ready khi chưa kết nối.
2. Chọn cloud AI; mở trang cấp key từ app nếu cần; nhập native dialog, validate và lưu. Test một key sai, mất mạng và retry; key không xuất hiện browser/log/screenshots.
3. Nhập Telegram API ID/hash, QR hoặc OTP; OTP/2FA thực hiện native. QR hết hạn có regenerate, cancel/save/resume rõ ràng. Thử QR ít nhất một lần và OTP/2FA trong QA riêng.
4. Mở hướng dẫn BotFather, nhập token native, thấy bot name/username thật; bấm link vào bot và Start bằng đúng account. Sai owner/replay/expired link phải reject trong QA.
5. Bấm “Mở dashboard”; vào quản trị trong tối đa 2 hành động từ launcher đang mở, không copy code mặc định. Mở trực tiếp URL trần thấy hướng dẫn quay về app, không tự tạo admin session.
6. Tìm một nguồn, preview quyền/consent, xác nhận học; thấy progress và coverage có mẫu số đúng. Hỏi câu trả lời từ nguồn với citation đúng; ghi thời gian câu trả lời đầu.
7. BLOCK nguồn khi job đang chạy: không tự allow lại, không index/deliver sau fence. Đổi embedding profile thấy reindex/consent rõ; LOCAL ONLY không gửi cloud qua shared context.
8. Đăng xuất dashboard và mở lại từ app; đóng app, chạy lại và resume. Tìm connection health, jobs/retry, quyền, budget, backup/restore. Tester tự tìm ít nhất 3 tác vụ quản lý không cần chỉ đường.

## Thước đo và kết quả

| Phép đo | Target / cách ghi |
|---|---|
| Task success | >=4/5 người hoàn thành kết nối và first AI answer không trợ giúp trực tiếp |
| Onboarding chuẩn bị credentials | <=15 phút cho ít nhất 4/5; timer từ mở wizard tới first verified answer |
| End-to-end chưa chuẩn bị | Ghi riêng thời gian tạo credentials/bot, wait/rate limit và setup trong app |
| Dashboard entry | <=2 hành động từ launcher mở; không cần nhập secret hoặc terminal |
| Native credentials | Không lộ canary secret qua logs/browser/IPC status hoặc screenshot |
| Error recovery | Có next action đúng; không mất stage đã verified, không Ready giả |
| Management findability | Ghi success, thời gian và số lần lạc cho 3 tác vụ được giao |
| Art/accessibility | Review ảnh artifact/baseline + keyboard/DPI/mobile matrix |

Một người cần hướng dẫn để xong vẫn được hỗ trợ, nhưng kết quả lần đó tính assisted và không tính success unassisted. Ghi failure và sửa rồi test lại; không chỉ loại người gặp khó khỏi mẫu. Kết quả nêu rõ số participant, environment, task, thời gian, assistance, issue IDs và evidence.

## Soak 72 giờ

Chạy release candidate liên tục 72h với test workload đã ghi: periodic synthetic job/sync, truy vấn AI trong hạn mức đã duyệt, queue retry/disconnect, restart, Windows sleep/wake và dashboard login/logout. Không chỉ để app idle 72h. Ghi baseline/end RSS, DB/vector counts, queue lag, stuck leases, reconnect và redacted logs; checkpoint ở 0/24/48/72h. Mỗi ngày ít nhất một roundtrip sync → index → query với expected source ID và một revoked-source negative case.

Pass khi không mất dữ liệu, không quyền bị hồi phục ngầm, không stuck lease không recover, không rò local-only/credential; memory growth hoặc duplicate/unexpected external side effects phải được điều tra và có disposition review. Timeout external side effect giữ uncertain, không retry gây gửi lặp. Ngưỡng tài nguyên cụ thể được khóa sau benchmark baseline B00/Q02 và workload, không invent số RSS từ audit.

## Các tình huống artifact QA riêng

Q02 kiểm empty/legacy SQLite và MySQL, upgrade/restore interrupted, disk full, path tiếng Việt/space, port occupied, keyring failure, corrupted session/config, two SID isolation, malicious Origin/Host/IPC, simultaneous ticket/code redemption, archive path traversal/oversize/checksum, uninstall-retain/reinstall. Không destruct real user data; dùng fixture và profile test.

## Evidence và beta-ready

Lưu report anonymized tại evidence/task-Q02.md và evidence/task-Q03.md; người review không phải implementer tự sign-off. Recording nếu có phải consent và che secret từ đầu, không phát tán raw account/messages. Các bước chưa chạy ghi Pending, không ghi Pass giả. R02 chỉ báo beta-ready sau toàn bộ G0–G6 Verified, kèm commit/installer hash/support matrix/known limitations. Public publish là hành động riêng.
