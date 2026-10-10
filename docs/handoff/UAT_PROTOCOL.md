# UAT protocol — owner tự trải nghiệm trên Windows 11

**Chưa chạy.** Theo [amendment trực tiếp của owner ngày 10/10/2026](../superpowers/specs/2026-10-10-owner-preview-amendment.md), bản preview trước mắt dùng trên máy Windows 11 hiện tại với một owner/profile, do owner tự trải nghiệm; không chờ 5 người mới hoặc clean VM. Q02 là QA artifact trên máy này; Q03 ghi phiên owner thực tế và evidence soak riêng. Test tự động không chứng minh installer/onboarding đã thành công. Protocol không tự cấp quyền dùng credentials/account hoặc gọi API trả phí.

## Mẫu thử và điều kiện

- Một owner tự cài/mở và trải nghiệm trên máy Windows 11 x64 hiện tại. Ghi SID/quyền standard user hay elevated thực tế; không gọi phiên này là nghiệm thu 5 người mới. Owner chỉ nhập key/session/OTP/2FA qua native dialog trong trial được cấp phạm vi riêng, không chia sẻ qua chat cho agent.
- Luồng chính cloud chat + embedding đã supported, không cài Ollama. Một luồng local Ollama được QA riêng và đo thời gian tải/setup riêng. CoinGecko optional; không yêu cầu tài khoản/provider không dùng.
- Nếu owner đã chuẩn bị credentials api_id/hash, bot token, provider key trong trial được cấp phạm vi riêng, ghi thời gian onboarding để đối chiếu mốc tham chiếu 15 phút; đồng thời ghi thời gian end-to-end từ chưa có credentials, số bước ngoài app và điểm vướng. Không giấu phần BotFather/my.telegram.org bằng cách gọi tổng onboarding dưới 15 phút.
- Dùng nguồn Telegram thử nhỏ được chủ sở hữu cho phép học; đặt hạn mức request/cost, gửi/xóa và nguồn cụ thể. Consent cloud theo nguồn; một nguồn LOCAL ONLY để test cấm gửi cloud.
- Ghi OS/build/x64, runtime/package versions, installer SHA-256 và các runtime đã có sẵn trên máy. Máy hiện tại không chứng minh clean install trên máy không có Python/Node/MySQL/Ollama. Windows 10, clean VM và novice usability độc lập chưa được kiểm chứng; không claim coverage từ phiên owner.

## Kịch bản owner tự trải nghiệm

1. Từ file installer, cài bằng quyền standard user và mở shortcut; không mở terminal. Nhìn thấy dữ liệu nằm đúng profile và app không báo Ready khi chưa kết nối.
2. Chọn cloud AI; mở trang cấp key từ app nếu cần; nhập native dialog, validate và lưu. Test một key sai, mất mạng và retry; key không xuất hiện browser/log/screenshots.
3. Nhập Telegram API ID/hash, QR hoặc OTP; OTP/2FA thực hiện native. QR hết hạn có regenerate, cancel/save/resume rõ ràng. Thử QR ít nhất một lần và OTP/2FA trong QA riêng.
4. Mở hướng dẫn BotFather, nhập token native, thấy bot name/username thật; bấm link vào bot và Start bằng đúng account. Sai owner/replay/expired link phải reject trong QA.
5. Bấm “Mở dashboard”; vào quản trị trong tối đa 2 hành động từ launcher đang mở, không copy code mặc định. Mở trực tiếp URL trần thấy hướng dẫn quay về app, không tự tạo admin session.
6. Tìm một nguồn, preview quyền/consent, xác nhận học; thấy progress và coverage có mẫu số đúng. Hỏi câu trả lời từ nguồn với citation đúng; ghi thời gian câu trả lời đầu.
7. BLOCK nguồn khi job đang chạy: không tự allow lại, không index/deliver sau fence. Đổi embedding profile thấy reindex/consent rõ; LOCAL ONLY không gửi cloud qua shared context.
8. Đăng xuất dashboard và mở lại từ app; đóng app, chạy lại và resume. Tìm connection health, jobs/retry, quyền, budget, backup/restore. Owner tự tìm ít nhất 3 tác vụ quản lý; ghi trợ giúp nếu có. Backup/restore dùng dữ liệu/profile thử riêng, không phá profile hiện hữu.

## Thước đo và kết quả

| Phép đo | Target / cách ghi |
|---|---|
| Task success | Ghi từng kịch bản 1–8: completed/assisted/failed/Pending, issue và evidence; không suy thành novice usability |
| Onboarding chuẩn bị credentials | Ghi thời gian thực từ mở wizard tới first verified answer của owner; mốc 15 phút là tham chiếu, không có chỉ tiêu 4/5 |
| End-to-end chưa chuẩn bị | Ghi riêng thời gian tạo credentials/bot, wait/rate limit và setup trong app |
| Dashboard entry | <=2 hành động từ launcher mở; không cần nhập secret hoặc terminal |
| Native credentials | Không lộ canary secret qua logs/browser/IPC status hoặc screenshot |
| Error recovery | Có next action đúng; không mất stage đã verified, không Ready giả |
| Management findability | Ghi success, thời gian và số lần lạc cho 3 tác vụ được giao |
| Art/accessibility | Review ảnh artifact/baseline + keyboard/DPI/mobile matrix |

Owner được hỗ trợ khi cần; kết quả lần đó ghi assisted. Ghi failure, sửa rồi test lại và giữ kết quả trước. Report ghi một participant, environment, artifact/hash, task, thời gian, assistance, issue IDs và evidence. Việc owner hài lòng không tự biến các bước chưa chạy thành Pass hoặc toàn bộ Q03 thành Verified.

## Soak 72 giờ

Đây là evidence dài hạn riêng. Có thể cung cấp candidate owner preview đã review trước khi đủ 72 giờ, nhưng phải công bố soak Pending, số giờ/workload thực và các giới hạn. Một phiên owner ngắn hoặc test tự động không thay soak; Q03 còn thiếu evidence này không được nghiệm thu toàn bộ.

Chạy release candidate liên tục 72h với test workload đã ghi: periodic synthetic job/sync, truy vấn AI trong hạn mức đã duyệt, queue retry/disconnect, restart, Windows sleep/wake và dashboard login/logout. Không chỉ để app idle 72h. Ghi baseline/end RSS, DB/vector counts, queue lag, stuck leases, reconnect và redacted logs; checkpoint ở 0/24/48/72h. Mỗi ngày ít nhất một roundtrip sync → index → query với expected source ID và một revoked-source negative case.

Pass khi không mất dữ liệu, không quyền bị hồi phục ngầm, không stuck lease không recover, không rò local-only/credential; memory growth hoặc duplicate/unexpected external side effects phải được điều tra và có disposition review. Timeout external side effect giữ uncertain, không retry gây gửi lặp. Ngưỡng tài nguyên cụ thể được khóa sau benchmark baseline B00/Q02 và workload, không invent số RSS từ audit.

## Các tình huống artifact QA riêng

Q02 kiểm empty/legacy SQLite theo [amendment SQLite-only](../superpowers/specs/2026-10-06-sqlite-only-amendment.md), upgrade/restore interrupted, disk full, path tiếng Việt/space, port occupied, keyring failure, corrupted session/config, two SID isolation, malicious Origin/Host/IPC, simultaneous ticket/code redemption, archive path traversal/oversize/checksum, uninstall-retain/reinstall. MySQL hiện hữu không được kết nối, migrate/reset/xóa. Không destruct real user data; dùng fixture và profile test. Negative/security QA cần evidence riêng; phiên owner không thay toàn bộ matrix.

## Evidence và beta-ready

Lưu report anonymized tại evidence/task-Q02.md và evidence/task-Q03.md; tách kết quả owner trial và checkpoint soak, người review không phải implementer tự sign-off. Recording nếu có phải consent và che secret từ đầu, không phát tán raw account/messages. Các bước chưa chạy ghi Pending; coverage ngoài preview có thể ghi Deferred theo phạm vi, không ghi Pass. Preview riêng không đổi 26-task DAG, task/gate status hay `beta_ready`/`published`. R02 chỉ báo beta-ready sau toàn bộ G0–G6 Verified, kèm commit/installer hash/support matrix/known limitations. Public publish là hành động riêng cần authorization cuối.
