# Sơ đồ hoạt động chi tiết

Tài liệu này mô tả luồng chạy thực tế của dự án `Telegram AI Personal Assistant`,
dựa trên các entry point trong `cli.py`, bộ điều phối `runtime.py`, hai giao diện
Telegram và các service hiện có.

## Ký hiệu

- SQLite là nguồn dữ liệu chuẩn và mỗi database context tự `commit`; khi có lỗi sẽ
  `rollback`.
- Qdrant local chỉ là chỉ mục vector dẫn về bản ghi SQLite.
- Đường đi qua `PendingAction` có nghĩa là owner phải xem trước và xác nhận trước
  khi worker thực thi.
- Chat mặc định là `BLOCK`. Một thao tác trên chat chỉ được tiếp tục khi chat ở
  `ALLOW`, quyền con được bật và quyền Telegram thực tế còn hợp lệ.

## 1. Toàn cảnh hoạt động của hệ thống

```mermaid
flowchart TB
    owner["Owner"] -->|"Lệnh và nút bấm"| control["Control Bot<br/>Aiogram, owner-only"]
    member["Thành viên group"] -->|"@username /ask câu hỏi"| tg["Telegram"]
    tg --> user["Telegram User Client<br/>Telethon / MTProto"]
    control --> policy["Policy Engine<br/>default deny, allowlist, quyền, rate limit"]
    user --> policy

    subgraph runtime["Application Runtime"]
        control
        user
        policy
        services["Services<br/>search, task, memory, digest, action"]
        rag["RAG Service<br/>keyword + semantic retrieval"]
        scheduler["Scheduler<br/>reminder, refresh knowledge, learning jobs"]
        action_worker["Pending-action worker"]
    end

    policy --> services
    services --> mysql[("SQLite<br/>nguồn dữ liệu chuẩn")]
    services --> rag
    rag --> mysql
    rag --> qdrant[("Qdrant local<br/>vector + reference ID")]
    rag --> ai["OpenAI hoặc OpenRouter<br/>Responses + Embeddings"]
    services --> coin["CoinGecko API"]

    scheduler --> mysql
    scheduler --> qdrant
    scheduler --> ai
    action_worker --> mysql
    action_worker -->|"Kiểm tra quyền lần cuối"| policy
    action_worker -->|"Gửi, sửa, xóa, ghim, sync"| user
    user -->|"Sự kiện mới, sửa, xóa"| tg
    control -->|"Phản hồi riêng cho owner"| tg
    user -->|"Reply trong group"| tg
```

## 2. Khởi động, ghép nối và vòng đời tiến trình

```mermaid
flowchart TD
    a0(["Người dùng chạy tg-assistant start hoặc run"]) --> ready["Kiểm tra cấu hình và thư mục runtime"]
    ready --> db_secret{"Profile đã có SQLite?"}
    db_secret -->|"Chưa"| setup["Tạo SQLite rỗng trong profile<br/>chạy migration dưới khóa ghi"]
    db_secret -->|"Rồi"| tg_secret
    setup --> tg_secret{"Đủ API ID, API hash,<br/>phone và bot token?"}
    tg_secret -->|"Chưa"| prompt["Nhập secret ẩn trong terminal"]
    tg_secret -->|"Đủ"| ai_secret
    prompt --> ai_secret{"Provider AI đang bật<br/>nhưng thiếu API key?"}
    ai_secret -->|"Có"| prompt_ai["Chọn provider và nhập key ẩn"]
    ai_secret -->|"Không"| coin_secret
    prompt_ai --> coin_secret{"Thiếu CoinGecko key?"}
    coin_secret -->|"Có"| prompt_coin["Nhập CoinGecko key ẩn"]
    coin_secret -->|"Không"| session_check
    prompt_coin --> session_check{"Có session mã hóa<br/>và owner đã paired?"}

    session_check -->|"Chưa"| bootstrap["Bootstrap Telethon<br/>OTP hoặc 2FA chỉ nhập tại terminal"]
    bootstrap --> verify_bot["Xác minh bot token và lấy username"]
    verify_bot --> discover_first["Đọc metadata dialog<br/>không đọc nội dung chat bị BLOCK"]
    discover_first --> pair_code["Sinh mã pairing một lần<br/>TTL 5 phút"]
    pair_code --> pair_wait{"Owner gửi /pair đúng mã<br/>từ đúng Telegram user?"}
    pair_wait -->|"Sai hoặc hết hạn"| pair_fail(["Dừng bootstrap với lỗi"])
    pair_wait -->|"Đúng"| paired["Đánh dấu is_owner_paired = true"]
    paired --> launch
    session_check -->|"Đủ"| launch{"Chạy foreground hay background?"}

    launch -->|"start"| instance_bg["Kiểm tra PID cũ<br/>giữ instance lock<br/>tạo worker nền"]
    launch -->|"run"| instance_fg["Giữ instance lock<br/>chạy trong terminal"]
    instance_bg --> app_init
    instance_fg --> app_init["Khởi tạo Database, Policy, AI,<br/>CoinGecko, Qdrant và Scheduler"]

    app_init --> auth["Telethon authenticate"]
    auth --> paired_check{"Account tồn tại<br/>và đã paired?"}
    paired_check -->|"Không"| runtime_fail(["Từ chối khởi động"])
    paired_check -->|"Có"| discover["Cập nhật metadata dialog"]
    discover --> recover["Đưa learning job bị gián đoạn<br/>về trạng thái queued"]
    recover --> register["Đăng ký handler Telethon và Aiogram"]
    register --> start_scheduler["Khởi động scheduler"]

    start_scheduler --> parallel{{"Chạy song song"}}
    parallel --> bot_poll["Control Bot polling"]
    parallel --> mtproto["Telegram User Client<br/>run_until_disconnected"]
    parallel --> pending_loop["Pending-action worker<br/>poll mỗi 2 giây"]
    parallel --> reminders["Reminder job<br/>mỗi 30 giây"]
    parallel --> refresh["Knowledge refresh<br/>mỗi 5 phút"]
    parallel --> learning["Learning job worker<br/>mặc định mỗi 2 giây"]

    bot_poll --> stop_wait
    mtproto --> stop_wait
    pending_loop --> stop_wait
    reminders --> stop_wait
    refresh --> stop_wait
    learning --> stop_wait{"SIGINT, SIGTERM,<br/>stop.request hoặc lỗi dừng?"}
    stop_wait -->|"Chưa"| parallel
    stop_wait -->|"Có"| shutdown["Cancel task và dừng scheduler"]
    shutdown --> close["Đóng bot, CoinGecko, Qdrant và database<br/>mã hóa lại Telethon session"]
    close --> cleanup["Xóa PID và started_at"]
    cleanup --> done(["Dừng an toàn"])
```

## 3. Cổng kiểm soát Policy Engine

Luồng này được dùng cho các thao tác đọc/ghi trên chat. Với truy vấn đọc nhiều
nguồn, hệ thống kiểm tra owner và rate limit một lần rồi lọc toàn bộ Chat ID được
phép; không trừ rate limit riêng cho từng nguồn.

```mermaid
flowchart TD
    p0(["Nhận yêu cầu có PolicyContext"]) --> owner_check{"actor_id = owner_id?"}
    owner_check -->|"Không"| deny_owner(["Từ chối: not_owner"])
    owner_check -->|"Có"| safe_check{"Dữ liệu đầu vào an toàn?"}
    safe_check -->|"Không"| deny_unsafe(["Từ chối: unsafe"])
    safe_check -->|"Có"| scoped{"Có chat_id và permission?"}
    scoped -->|"Không"| allow_global(["Cho phép thao tác không gắn chat"])
    scoped -->|"Có"| allowlist{"Chat đang ALLOW?"}
    allowlist -->|"Không"| deny_chat(["Từ chối: chat_blocked"])
    allowlist -->|"Có"| permission{"Quyền con đang bật?"}
    permission -->|"Không"| deny_permission(["Từ chối: permission_disabled"])
    permission -->|"Có"| rights{"Quyền này cần quyền Telegram thực tế?"}
    rights -->|"Có nhưng thiếu"| deny_right(["Từ chối: telegram_right_missing"])
    rights -->|"Không hoặc đã đủ"| rate{"Vượt sliding-window rate limit?"}
    rate -->|"Có"| deny_rate(["Từ chối: rate_limited"])
    rate -->|"Không"| destructive{"Là quyền phá hủy?"}
    destructive -->|"Không"| allowed(["Cho phép"])
    destructive -->|"Có"| confirmed{"Đã xác nhận?"}
    confirmed -->|"Không"| need_confirm(["Từ chối tạm thời:<br/>confirmation_required"])
    confirmed -->|"Có"| allowed
```

Khi owner chuyển một chat sang `BLOCK`, hệ thống tắt ngay toàn bộ quyền con, xử lý
memory theo lựa chọn `keep`, `archive` hoặc `delete`, đồng thời hủy các pending
action khác của chat đó.

## 4. Control Bot và các nhóm chức năng

```mermaid
flowchart TD
    c0(["Control Bot nhận update"]) --> owner{"from_user là owner?"}
    owner -->|"Không"| silent(["Bỏ qua im lặng"])
    owner -->|"Có"| input{"Loại yêu cầu?"}

    input -->|"Help hoặc menu"| menu["Mở menu Groups hoặc AI"]
    input -->|"Đọc dữ liệu"| read_ops["Thông tin group, permissions,<br/>search, memory, task, project, audit"]
    input -->|"AI"| ai_ops{"Nhánh AI"}
    input -->|"Thay đổi có kiểm soát"| mutation["Tạo preview + PendingAction"]
    input -->|"Task thường"| task_ops["Tạo, sửa, hoàn tất hoặc liệt kê task"]
    input -->|"Reminder"| remind["Parse thời gian tiếng Việt<br/>lưu Reminder scheduled"]
    input -->|"Văn bản tự nhiên"| natural{"Có chứa secret?"}

    ai_ops -->|"Giá coin"| price["Gọi CoinGecko<br/>không cần AI hoặc RAG"]
    ai_ops -->|"Tìm Telegram"| keyword["Search SQLite local<br/>mặc định 7 ngày"]
    ai_ops -->|"Hỏi AI"| rag["RAG trên chat ALLOW + search_messages"]
    ai_ops -->|"Digest"| digest["Lọc, khử trùng, phân bổ nguồn<br/>AI phân loại + dẫn chứng"]
    ai_ops -->|"Học nguồn"| learn_select["Chọn tối đa 500 group/channel<br/>review toàn bộ lựa chọn"]
    ai_ops -->|"Bật hoặc tắt AI"| ai_setting["Cập nhật AppSetting ai_enabled"]

    natural -->|"Có"| reject_secret(["Không lưu và không gửi ra API"])
    natural -->|"Không, là câu hỏi giá"| price
    natural -->|"Không, AI sẵn sàng"| rag
    natural -->|"Không, AI tắt"| keyword

    learn_select --> mutation
    mutation --> pending["PendingAction status = pending<br/>TTL mặc định 5 phút"]
    pending --> confirm{"Owner confirm hay cancel?"}
    confirm -->|"Cancel"| cancelled(["status = cancelled"])
    confirm -->|"Quá hạn"| expired(["status = expired"])
    confirm -->|"Confirm"| confirmed["status = confirmed"]
    confirmed --> worker["Worker khóa row bằng skip_locked<br/>kiểm tra owner, policy và quyền thực tế"]
    worker --> outcome{"Thực thi thành công?"}
    outcome -->|"Có"| executed["status = executed<br/>ghi AuditLog success"]
    outcome -->|"Không"| failed["status = failed<br/>ghi AuditLog failed"]

    menu --> response["Trả kết quả cho owner"]
    read_ops --> response
    task_ops --> response
    remind --> response
    price --> response
    keyword --> response
    rag --> response
    digest --> response
    ai_setting --> response
    executed --> response
    failed --> response
```

Các thay đổi đi qua `PendingAction` gồm gửi/sửa/xóa/ghim tin, thay allowlist hoặc
quyền, áp dụng template, thiết lập moderation, bật/tắt group AI, đồng bộ lịch sử,
học nguồn, tạo/quên memory và xóa task. Task thủ công thông thường và reminder
được ghi trực tiếp bởi owner.

## 5. Thành viên trong group hỏi AI

```mermaid
flowchart TD
    g0(["Telethon nhận NewMessage"]) --> parse{"Khớp chính xác<br/>@username /ask câu hỏi?"}
    parse -->|"Không"| listener["Tiếp tục luồng listener thông thường"]
    parse -->|"Có"| authorized{"Group/supergroup đang ALLOW<br/>và bật group_ai_ask?"}
    authorized -->|"Không"| silent(["Bỏ qua im lặng"])
    authorized -->|"Có"| valid{"Câu hỏi hợp lệ?"}
    valid -->|"Rỗng"| usage["Trả cú pháp sử dụng"]
    valid -->|"Dài hơn 1.500 ký tự"| too_long["Yêu cầu rút gọn"]
    valid -->|"Chứa secret"| secret["Không gửi tới AI"]
    valid -->|"Quá 3 câu/phút<br/>theo member + group"| limited["Thông báo rate limit"]
    valid -->|"Hợp lệ"| route{"Nhận diện ý định"}

    route -->|"Hỏi giá coin"| coin_ready{"CoinGecko đã cấu hình?"}
    coin_ready -->|"Không"| coin_config["Hướng dẫn owner cấu hình key"]
    coin_ready -->|"Có"| coin_api["Lấy giá và định dạng kết quả"]

    route -->|"AI đang tắt hoặc unavailable"| ai_off["Thông báo AI chưa sẵn sàng"]
    route -->|"Hỏi lịch sử @username"| sender["Phân giải username thành Telegram User"]
    sender --> sender_sync["Lấy tối đa 100 tin mới nhất<br/>của đúng sender trong đúng group"]
    sender_sync --> sender_mysql["Upsert vào SQLite"]
    sender_mysql --> sender_context["Query lại bằng chat_id + sender_id<br/>loại tin gọi lệnh"]
    sender_context --> answer_rows["Redact, giới hạn context<br/>gọi Responses API"]

    route -->|"Hỏi hội thoại group hiện tại"| local_group["Đọc tối đa 100 tin đã đồng bộ<br/>trong 7 ngày của đúng group"]
    local_group --> answer_rows

    route -->|"Hỏi kiến thức hoặc dự án"| learned{"Liệt kê nguồn ALLOW<br/>đã bật auto_knowledge"}
    learned -->|"Không có nguồn"| no_source["Thông báo chưa có nguồn đã học"]
    learned -->|"Có"| rag_auth["Policy lọc tiếp quyền search_messages<br/>một lần cho toàn request"]
    rag_auth --> keyword["Keyword retrieval từ SQLite<br/>cửa sổ trượt 7 ngày"]
    rag_auth --> semantic["Embedding câu hỏi + semantic retrieval<br/>Qdrant, giới hạn ID SQLite gần đây"]
    keyword --> merge["Gộp theo chat_id + message_id<br/>xếp hạng và lấy top 10"]
    semantic --> merge
    merge --> context["Tạo context đã redact<br/>kèm mã nguồn S#"]
    context --> ai["Kiểm tra token, ngân sách và rate limit<br/>gọi Responses API"]
    ai --> citations{"Câu hỏi tin tức,<br/>cập nhật hoặc diễn biến?"}
    citations -->|"Có"| with_source["Giữ mã S# và nối DẪN CHỨNG"]
    citations -->|"Không"| without_source["Loại mã và phụ lục nguồn"]

    usage --> deliver
    too_long --> deliver
    secret --> deliver
    limited --> deliver
    coin_config --> deliver
    coin_api --> deliver
    ai_off --> deliver
    no_source --> deliver
    answer_rows --> deliver
    with_source --> deliver
    without_source --> deliver["Chuyển Markdown sang Telegram HTML an toàn<br/>chia nhỏ nếu cần"]
    deliver --> reply["Telegram User Client reply_to<br/>đúng message gọi lệnh"]
    reply --> done(["Ghi log kết quả gửi"])
```

## 6. Học dữ liệu group/channel và duy trì knowledge corpus

```mermaid
flowchart TD
    k0(["Owner mở Học từ group/channel"]) --> select["Chọn từng nguồn, cả trang, tìm kiếm<br/>hoặc chọn tất cả; tối đa 500 nguồn"]
    select --> review["Xem lại danh sách và phạm vi dữ liệu"]
    review --> pending["Tạo enable_group_learning_bulk<br/>PendingAction"]
    pending --> confirm{"Owner xác nhận trong TTL?"}
    confirm -->|"Không"| stop(["Hủy hoặc hết hạn, không tạo job"])
    confirm -->|"Có"| action_worker["Pending-action worker nhận action confirmed"]
    action_worker --> validate["Lọc Chat ID hợp lệ<br/>group, supergroup hoặc channel"]
    validate --> enqueue["Mỗi nguồn tạo một BackgroundJob learn_group<br/>status = queued, max_attempts = 3"]
    enqueue --> inventory["KnowledgeSource status = queued"]

    inventory --> poll["Learning worker lấy job cũ nhất<br/>FOR UPDATE SKIP LOCKED"]
    poll --> stale{"Có job running quá 15 phút?"}
    stale -->|"Có"| requeue_stale["Đưa lại queued và xóa lock"]
    stale -->|"Không"| lock
    requeue_stale --> lock["Chuyển một job queued sang running<br/>tăng attempts và đặt locked_at"]

    lock --> phase1["Pha 1: SQLite là nguồn chuẩn"]
    phase1 --> policy["Bật ALLOW + template knowledge"]
    policy --> sync["Telethon sync tối đa 1.000 tin<br/>chỉ ID mới hơn SyncState checkpoint"]
    sync --> upsert["Upsert theo chat_id + message_id<br/>lưu version nếu nội dung đã sửa"]
    upsert --> counts["Đếm message và message có text"]
    counts --> commit["Commit transaction SQLite"]

    commit --> phase2["Pha 2: tạo dữ liệu dẫn xuất"]
    phase2 --> checkpoint["Đọc knowledge_checkpoint của nguồn"]
    checkpoint --> rows["Lấy text chưa xóa, ID lớn hơn checkpoint<br/>bỏ nội dung giống secret"]
    rows --> batch["Chia batch theo giới hạn item và token"]
    batch --> budget{"AI, embedding và ngân sách sẵn sàng?"}
    budget -->|"Không hoặc lỗi"| job_error
    budget -->|"Có"| embed["Embeddings API<br/>rate limit có chờ"]
    embed --> qdrant["Upsert batch vào collection telegram_messages<br/>kèm chat_id, message_id"]
    qdrant --> update_cp["Cập nhật checkpoint đến ID mới nhất"]
    update_cp --> complete["Job = completed<br/>KnowledgeSource = learned hoặc no_content"]

    job_error["Lưu lỗi và giải phóng lock"] --> attempts{"Đã đủ 3 lần thử?"}
    attempts -->|"Chưa"| retry["Job = queued<br/>run_after lùi 1 đến 5 phút"]
    retry --> poll
    attempts -->|"Rồi"| failed(["Job = failed<br/>giữ nguyên dữ liệu SQLite đã commit"])
    complete --> more{"Còn job queued?"}
    more -->|"Có"| poll
    more -->|"Không"| idle(["Chờ chu kỳ tiếp theo"])

    subgraph continuous["Refresh liên tục mỗi 5 phút"]
        r0["Kiểm tra AI, vector store và knowledge lock"] --> backlog{"Còn learning backlog?"}
        backlog -->|"Có"| defer["Hoãn refresh"]
        backlog -->|"Không"| learned_sources["Lấy nguồn ALLOW + auto_knowledge"]
        learned_sources --> recent["Mỗi nguồn lấy tối đa 100 row<br/>sau knowledge checkpoint"]
        recent --> r_embed["Embedding + upsert Qdrant"]
        r_embed --> r_cp["Cập nhật checkpoint"]
    end

    idle --> r0
```

Nếu tiến trình bị dừng khi job đang chạy, job được trả về `queued`. Khi khởi động
lại, runtime cũng phục hồi toàn bộ learning job dở dang trước khi nhận traffic.

## 7. Listener Telegram, đồng bộ và AUTO moderation

```mermaid
flowchart TD
    e0(["Telethon nhận event"]) --> event{"Loại event?"}

    event -->|"NewMessage"| monitor_new{"Chat ALLOW + monitor_new_messages?"}
    monitor_new -->|"Không"| ask_only{"Vẫn kiểm tra cú pháp group /ask"}
    monitor_new -->|"Có"| upsert_new["Upsert message vào SQLite"]
    upsert_new --> auto_rule{"Đã bật rule AUTO xóa link non-admin?"}
    auto_rule -->|"Không"| ask_only
    auto_rule -->|"Có"| has_link{"Tin có external link?"}
    has_link -->|"Không"| keep(["Giữ tin"])
    has_link -->|"Có"| auto_permission{"Policy cho phép auto_moderation?"}
    auto_permission -->|"Không"| keep
    auto_permission -->|"Có"| trusted{"Sender là owner, creator,<br/>admin hoặc anonymous admin?"}
    trusted -->|"Có"| keep
    trusted -->|"Không"| rights{"Tài khoản còn quyền delete_messages<br/>và delete_any_messages?"}
    trusted -->|"Không xác minh được"| fail_safe["Giữ tin và ghi AuditLog fail-safe"]
    rights -->|"Không"| denied["Giữ tin và ghi AuditLog denied"]
    rights -->|"Có"| delete["Xóa tin mới qua Telegram API"]
    delete --> deleted_local["Đánh dấu bản SQLite is_deleted = true<br/>ghi AuditLog success"]

    event -->|"MessageEdited"| monitor_edit{"Chat ALLOW + monitor_new_messages?"}
    monitor_edit -->|"Có"| version["Lưu TelegramMessageVersion cũ<br/>cập nhật text và edited_at"]
    monitor_edit -->|"Không"| ignored(["Bỏ qua"])

    event -->|"MessageDeleted"| monitor_delete{"Chat ALLOW + monitor_new_messages?"}
    monitor_delete -->|"Có"| mark_deleted["Đánh dấu các message ID local là deleted"]
    monitor_delete -->|"Không"| ignored

    ask_only --> ask{"Có cú pháp @username /ask?"}
    ask -->|"Có"| group_flow["Chuyển sang luồng hỏi AI trong group"]
    ask -->|"Không"| done(["Kết thúc xử lý event"])
    deleted_local --> done
    version --> done
    mark_deleted --> done
    keep --> done
    fail_safe --> done
    denied --> done
```

AUTO moderation chỉ áp dụng cho tin **mới** sau khi owner đã xác nhận bật standing
authorization. Luồng quét và xóa tin cũ trong Control Bot vẫn tạo preview và cần
xác nhận riêng cho từng tin.

## 8. Vòng đời PendingAction

```mermaid
stateDiagram-v2
    [*] --> Pending: Tạo action + preview + expires_at
    Pending --> Cancelled: Owner cancel
    Pending --> Expired: Confirm sau TTL
    Pending --> Confirmed: Đúng owner confirm trong TTL
    Confirmed --> Executed: Worker kiểm tra cuối và thành công
    Confirmed --> Failed: Sai owner, mất quyền hoặc lỗi thực thi
    Pending --> Cancelled: Chat bị BLOCK và action thuộc chat đó
    Cancelled --> [*]
    Expired --> [*]
    Executed --> [*]
    Failed --> [*]
```

Worker chỉ lấy tối đa 10 action `confirmed` theo thứ tự xác nhận và dùng row lock
`skip_locked`. Trước thao tác Telegram, worker đọc lại quyền thực tế; do đó một
quyền đã bị thu hồi sau lúc owner bấm xác nhận vẫn làm action thất bại an toàn.

## 9. Scheduler và worker nền

| Chu kỳ | Thành phần | Hoạt động chính |
|---|---|---|
| Liên tục | Control Bot | Poll update riêng của owner và điều phối lệnh/FSM |
| Liên tục | Telegram User Client | Nghe tin mới, sửa, xóa và lệnh hỏi AI trong group |
| 2 giây | Pending-action worker | Thực thi action đã xác nhận, cập nhật trạng thái và audit |
| 30 giây | Reminder dispatcher | Khóa tối đa 20 reminder đến hạn, gửi owner, đánh dấu sent/failed |
| Mặc định 2 giây | Learning worker | Lấy một job học, sync SQLite rồi embedding vào Qdrant |
| 5 phút | Knowledge refresh | Bổ sung tối đa 100 message mới mỗi nguồn đã học |

## 10. Điểm dừng an toàn quan trọng

1. Người lạ nhắn Control Bot không nhận được thông tin trạng thái hệ thống.
2. Chat `BLOCK` không được đọc nội dung, tìm kiếm, đồng bộ hoặc thực thi action.
3. Secret bị chặn trước khi lưu memory, tạo action gửi tin hoặc gọi AI; tìm kiếm
   keyword local không gửi nội dung ra dịch vụ bên ngoài.
4. AI bị tắt, hết ngân sách hoặc lỗi không làm dừng tìm kiếm SQLite, task, reminder
   hay moderation local.
5. SQLite được commit trước khi embedding; lỗi API không làm mất dữ liệu đã sync.
6. Role admin hoặc quyền Telegram không xác minh được thì AUTO moderation giữ tin.
7. Session Telethon dạng rõ chỉ tồn tại lúc chạy và được mã hóa lại khi đóng.
