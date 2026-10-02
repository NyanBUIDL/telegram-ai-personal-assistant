# Windows local v1 — gói triển khai nền tảng, cài đặt, kết nối và truy cập dashboard

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` để thực hiện từng task; nếu người dùng yêu cầu điều phối nhiều agent, dùng `superpowers:subagent-driven-development`. Các checkbox là hạng mục bàn giao, chưa phải công việc đã thực hiện. Đọc kế hoạch tổng thể và các gói an toàn/runtime/UI trước khi sửa file chung.

**Goal:** Một người dùng mới cài ứng dụng Windows, kết nối tài khoản Telegram và bot riêng qua cửa sổ an toàn, rồi mở dashboard bằng một lần bấm mà không phải cài Python, Node hay MySQL.

**Architecture:** Một bản cài cho mỗi người dùng Windows, một owner Telegram cho mỗi profile; launcher/tray PySide6 và runtime cùng chạy dưới SID của người đó. SQLite là mặc định cho bản cài mới; phát hiện và giữ cấu hình MySQL hiện có, cung cấp MySQL như lựa chọn nâng cao, không tự chuyển đổi dữ liệu. React dashboard tiếp tục chạy qua loopback trong trình duyệt, giữ art style hiện tại; bí mật và đăng nhập Telegram nằm trong GUI native và Windows Credential Manager.

**Tech Stack:** Python >=3.12 như repository, PySide6, FastAPI/Uvicorn, React hiện có, SQLAlchemy/Alembic, SQLite + aiosqlite, MySQL hiện có, Telethon/aiogram, keyring/Credential Manager, Qdrant local. Đề xuất artifact Windows x64 bằng PyInstaller dạng onedir + Inno Setup per-user; khóa phiên bản build sau thử nghiệm artifact, không dựa vào dependency của máy phát triển.

**Spec:** [Thiết kế Windows public beta](../superpowers/specs/2026-10-02-windows-public-beta-design.md) là nguồn quyết định và contract có thẩm quyền. Yêu cầu người dùng: Windows cục bộ cho từng người, tài khoản Telegram riêng, cài và kết nối dễ, dashboard quản lý dễ, giữ art style. Bằng chứng nguồn: `E:\ChatGPT Project\Telegram\audit-snapshot` tại commit `7429fcffd61860e9502f066e0b6a921df72fe176` và [RELEASE_READINESS_REVIEW_VI.md](../../RELEASE_READINESS_REVIEW_VI.md). Snapshot không phải Git checkout; khi triển khai phải dùng checkout của repository thật và xác nhận lại HEAD.

## Global Constraints

- Windows local per-user v1 đã được người dùng chốt; không triển khai SaaS, tài khoản website, tenant, billing hoặc public Admin API trong gói này.
- Bản cài mới dùng SQLite embedded. MySQL hiện có giữ nguyên, MySQL nâng cao là opt-in. Không tự export/import, chuyển DB, đổi root password hay thay schema của DB không xác định.
- Runtime/launcher không chạy LocalSystem, không cần Administrator cho đường đi chuẩn; Credential Manager thuộc cùng SID. Không chạy elevated toàn ứng dụng để xử lý một prerequisite.
- API key, bot token, API hash, số điện thoại, Telegram OTP/2FA và QR đăng nhập chỉ qua GUI native/IPC được bảo vệ; không gửi qua bot, HTTP dashboard, URL, telemetry, log, export, AppSetting hoặc .env. Pairing code không phải OTP đăng nhập Telegram và là ngoại lệ có chủ đích của `/pair`.
- Không lưu OTP/2FA; chỉ giữ trạng thái phiên tạm trong RAM. Không tuyên bố Python có thể zeroize mọi bản sao của chuỗi; giảm thời gian giữ và không ghi ra đĩa.
- Giữ default deny, owner-only, quyền theo nguồn và xác nhận hành động của policy. Thiết lập thành công không đồng nghĩa ALLOW mọi nguồn.
- Giữ art style của dashboard; thêm wizard/trạng thái/tương tác phù hợp với thành phần và token hiện có. Việc dùng PySide6 không cho phép redesign toàn bộ giao diện.
- Config và dữ liệu nằm trong `%LOCALAPPDATA%\TelegramAIPersonalAssistant`; tài nguyên ứng dụng chỉ đọc từ package/install dir. Không phụ thuộc current working directory, `.venv`, `node_modules`, Python/Node hệ thống hoặc source checkout.
- Thay đổi schema phải có đường cài mới và đường nâng cấp. Không stamp head hay bỏ lỗi duplicate vô điều kiện để làm migration “xanh”.
- Code signing, quyền phân phối dependency/font/asset và license repository là cổng release cần xác minh; bản kế hoạch này không xác nhận đã có certificate hoặc quyền phân phối.
- Dùng PySide6 Qt Widgets và browser mặc định; không QtWebEngine. Pin runtime/Qt còn hỗ trợ Windows 10 mục tiêu sau clean VM validation và review support/licensing; không tự chọn latest Qt rồi hứa hỗ trợ.
- API ID/hash là prerequisite cả OTP và QR. Full-ready cần owner MTProto được xác minh và bot owner pairing; không thêm bot-only identity mode. Cho lưu/thoát/resume và xem dashboard incomplete với capability khóa thao tác chưa sẵn sàng.
- Embedding cloud có profile/capability/consent riêng và không ngầm cần Ollama; LOCAL ONLY cấm cloud chat/context/embedding. Đổi provider/model/version/dimension cần owner-approved store/reindex; không trộn corpus và không hidden fallback. SQLite mặc định ngoài cloud-synced folder.

## Review Focus

1. Hai request xác nhận/worker chạy đồng thời: chỉ một lần chuyển trạng thái/claim; SQLite không được dựa vào `FOR UPDATE SKIP LOCKED` của MySQL. Sở hữu PL-02.
2. Browser từ website khác và user Windows khác thử vào loopback/IPC: không được cấp session hoặc nhập secret. Sở hữu PL-04.
3. Cài trên Windows sạch không có Python/Node/MySQL/Ollama: wizard mở được, SQLite và cloud chat/embedding hoạt động với consent; local thiếu Ollama nói rõ hạn chế, không báo Ready giả. Sở hữu PL-03/05/06/07.
4. Upgrade config/DB cũ, schema dang dở hoặc dừng giữa migration: bảo toàn dữ liệu, chặn start khi schema không tương thích và đưa ra repair có bằng chứng. Sở hữu PL-01/03/07.
5. Crash khi session Telegram đang giải mã hoặc khi restore SQLite có WAL: không tự dùng file session cũ chưa xác minh, không ghép file DB và WAL của hai bản khác nhau. Sở hữu PL-05/08.

## 1. Quyết định và bằng chứng mã nguồn

| Khu vực | Hiện trạng được đọc | Thay đổi đích |
| --- | --- | --- |
| `cli.py:137`, `setup/wizard.py` | Thiếu database_password là chạy wizard MySQL; thiết lập còn ở terminal và root .env | Bootstrap native độc lập DB; SQLite không cần database_password |
| `paths.py:10`, `config.py:155,252` | project_root và cwd được dùng để đọc/ghi config, tìm dashboard | Tách app resources với user config/data; path tuyệt đối |
| `security.py` | SecretStore/keyring, session Fernet; `chmod(0o600)` được thử | Giữ Credential Manager, thêm kiểm tra backend Windows và DACL thật; không coi chmod là bảo vệ NTFS đầy đủ |
| `runtime.py:561`, `user_client.py:173` | Telethon start hỏi OTP/2FA terminal; bootstrap ghi owner rồi pairing | Auth service có state machine, GUI OTP/2FA và QR, owner từ `get_me()` |
| `admin_api/auth.py`, `app.py:453` | Mã 8 số theo chu kỳ 300s, cookie HttpOnly/SameSite strict, CSRF; auth session RAM | Recovery code one-use thật, launcher ticket một lần, không tạo password dashboard mới |
| `admin_api/app.py:131` | AdminContext cần database và owner_id trước khi dựng app | Setup broker/runtime phase độc lập; dashboard quản lý chỉ mở khi owner đã paired |
| `pyproject.toml` | Chỉ đóng wheel `src/tg_assistant`; aiosqlite đang ở dev | Package có migrations/dashboard/docs; aiosqlite là runtime dependency |
| `alembic/0001` | import models hiện tại + create_all, tạo cả bảng/column mà 0002/0003 sẽ tạo | Initial schema cố định lịch sử, cài mới chạy chuỗi Alembic thật |
| `db/base.py`, `user_client.py:608` | Engine đã có nhánh SQLite; upsert có sqlite_insert | Điểm khởi đầu khả thi cho embedded DB, chưa phải production support |
| `services/search.py` | Keyword dùng SQLAlchemy `contains`/LIKE; không có MATCH/AGAINST trong source đã đọc | Giữ hành vi keyword có kiểm thử dialect; không cần thêm FTS5 vào v1 để cài được |
| `models.py:108`, runtime/actions/tasks/admin | Index có mysql_prefix FULLTEXT; nhiều `with_for_update`, `skip_locked` | Schema dialect rõ ràng, CAS/claim an toàn cho SQLite |
| `cli.py:571` | Backup/restore phụ thuộc mysqldump/mysql, manifest schema=0001, vector chỉ metadata | Storage adapter và manifest revision thật; SQLite snapshot + restore có dừng worker |

**Vì sao SQLite mặc định:** v1 chỉ một owner chạy trên một máy. Source đã có engine/upsert SQLite và tìm kiếm không buộc FULLTEXT. Nó bỏ prerequisite server, root account, service và cổng DB khỏi luồng người mới. Đây là quyết định kiến trúc mới, không phải kết luận rằng 150 test hiện có chứng minh SQLite vận hành đầy đủ.

**Vì sao không chọn MySQL riêng do app quản lý:** có thể giữ dialect/locking gần code cũ nhưng phải phân phối binary và cập nhật bảo mật, kiểm tra license, tạo datadir/process riêng, xử lý VC/runtime prerequisites, port conflict, crash, upgrade server và cleanup. Không được chạy chung server của người dùng với root tự động. Phương án này tạo một subproject vận hành lớn hơn nhu cầu một owner; chỉ xem lại nếu benchmark SQLite sau PL-02 không đạt tải beta đã chọn.

**Giới hạn SQLite:** WAL cho phép đọc/ghi đồng thời nhưng một writer tại một thời điểm; chọn transaction ngắn, không giữ transaction qua Telegram/AI/network await, DB trên ổ local, không UNC/network share. Đây là cơ sở thiết kế theo [SQLite WAL](https://www.sqlite.org/wal.html). Autoincrement cần tên kiểu `INTEGER` ở SQLite, và transaction driver cần cấu hình/test tường minh theo [SQLAlchemy SQLite dialect](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html).

## 2. Biên file và hợp đồng bàn giao

Đường dẫn dưới đây là repository-relative để agent tìm đúng file khi checkout thực. Tên file **Create** là đề xuất chốt cho gói này; nếu HEAD đã có module tương đương, tái sử dụng sau khi ghi mapping vào báo cáo bàn giao.

| Module mới | Trách nhiệm | Consumer |
| --- | --- | --- |
| `src/tg_assistant/resources.py` | Đọc tài nguyên đóng gói, quản lý lifetime resource extraction | CLI, migrations, Admin API, launcher |
| `src/tg_assistant/db/storage.py` | Chọn backend/path, init SQLite, contract backup/restore | Setup, runtime, doctor, CLI |
| `src/tg_assistant/db/transitions.py` | Atomic state transition và job claim theo dialect | Runtime, actions, tasks, admin routes |
| `src/tg_assistant/db/migrations.py` | Programmatic Alembic runner, schema inventory/repair gate | Setup, installer upgrade |
| `src/tg_assistant/setup/state.py` | State machine onboarding và snapshot không secret | Native wizard, launcher, doctor |
| `src/tg_assistant/setup/connections.py` | Validate/test/save credential, provider health | IPC broker, native wizard |
| `src/tg_assistant/telegram/auth_service.py` | Auth account OTP/QR/2FA, timeout/cancel, owner identity | Setup service |
| `src/tg_assistant/windows/ipc.py` | Named pipe + SID/logon session + broker request validation | Launcher, setup/runtime broker |
| `src/tg_assistant/windows/launcher.py` | Entry point GUI/tray, state/start/stop/open/reconfigure | Installer shortcut |
| `src/tg_assistant/windows/wizard.py` | Native onboarding và secret controls, không nghiệp vụ policy | Launcher |
| `src/tg_assistant/admin_api/launch_auth.py` | One-use launch ticket store/redemption | Native broker, auth routes |
| `src/tg_assistant/services/storage_backup.py` | Snapshot/manifest/staging restore theo backend | CLI, Windows launcher/backup UI |
| `src/tg_assistant/contracts.py` | DTO canonical theo spec, mirror validated schema frontend | Tất cả backend/GUI/browser, không secret field |

### Hợp đồng Python chung

Các DTO công khai nằm trong `src/tg_assistant/contracts.py` và mirror tại `dashboard-prototype/src/contracts/generated.js` với JSDoc và schema validation. Chốt contract theo spec trước B00; dataclass nội bộ không tạo import vòng tới PySide6. Services là API canonical; hàm helper nội bộ sau đây không phải tên thay thế để frontend gọi. ID Telegram trong JSON là string decimal.

```python
# contracts.py
ConnectionState = Literal["checking", "ready", "degraded", "disconnected", "unknown"]
OnboardingStage = Literal["welcome", "storage_ready", "ai_configured",
    "telegram_verified", "bot_verified", "owner_paired", "source_selected",
    "first_answer", "ready"]
# ConnectionStatus(service, state, checked_at, code, message, next_action, capabilities)
# PublicProfile(profile_id, owner_id|None, storage_backend, setup_stage, version)
# EmbeddingProfile(provider, endpoint_id|None, model, embedding_version, dimension, store_id, cloud_consent)
# OperationResult(operation_id, state, progress|None, code, message, next_action)
# Không credential/code/token trong DTO công khai; stale health phải unknown.

# setup/state.py
class OnboardingCoordinator:
    async def status(self) -> OnboardingStatus: ...
    async def resume(self) -> OnboardingStatus: ...
    async def complete_stage(self, stage: OnboardingStage,
                             verified_evidence_id: str) -> OnboardingStatus: ...

# db/storage.py
StorageBackend = Literal["sqlite", "mysql"]
def resolve_storage(settings: Settings, store: SecretStore) -> StorageConfig: ...
# StorageConfig nội bộ: backend, async_url, sync_url, sqlite_path; repr không lộ URL mật khẩu.
class StorageService:
    async def open(self, profile: PublicProfile) -> Database: ...
    def migrate(self) -> MigrationReport: ...
    def backup(self, destination: Path) -> BackupManifest: ...
    def restore(self, source: Path, maintenance_lease: MaintenanceLease) -> RestoreReport: ...

# db/migrations.py
def inspect_schema(storage: StorageConfig) -> SchemaInventory: ...
def upgrade_schema(storage: StorageConfig, *, approved_repair: str | None = None) -> MigrationReport: ...
# Inventory: backend/revision/fingerprint/classification;
# MigrationReport: previous_revision/current_revision, changed, code, next_action.

# db/transitions.py
class JobRepository:
    async def claim(self, job_type: str, worker_id: str, now: datetime,
                    lease_seconds: int) -> JobLease | None: ...
    async def complete(self, lease: JobLease, result: OperationResult) -> bool: ...
async def transition_action(session: AsyncSession, *, action_id: str, owner_id: int,
                            expected_status: str, target_status: str,
                            now: datetime) -> bool: ...
# JobLease: id, claim_token, payload, expires_at; rowcount==1 là thắng CAS.

# setup/connections.py (chỉ GUI/IPC gọi, không expose credential DTO qua HTTP)
class CredentialConnectionService:
    async def validate_and_save(self, provider: str, secret_input: SecretInput,
                                options: ConnectionOptions) -> ConnectionStatus: ...
# provider: telegram_api|telegram_bot|openai|openrouter|coingecko;
# SecretInput chỉ native process RAM, repr=False; không dashboard DTO.

# telegram/auth_service.py
async def begin_auth(mode: Literal["otp", "qr"]) -> TelegramAuthStep: ...
async def submit_code(flow_id: str, code: str) -> TelegramAuthStep: ...
async def submit_2fa(flow_id: str, password: str) -> TelegramAuthStep: ...
async def cancel_auth(flow_id: str) -> None: ...
# AuthStep: flow_id, state(code_required|qr_required|password_required|authenticated|
# expired|cancelled|error), expires_at/error_code/masked_owner;
# QR payload chỉ đi tới native widget qua IPC, tuyệt đối không vào snapshot browser.

# admin_api/launch_auth.py
class DashboardTicketService:
    def issue(self, profile_id: str, windows_sid: str, now: datetime) -> LaunchTicket: ...
    def redeem(self, ticket: str, origin: str, now: datetime) -> AdminSession: ...
# instance/origin/audience binding trong service state, không tin client để đổi owner.
# Ticket entropy >=256 bit, TTL 30s, atomic one-use; stored digest thay raw ticket.
```

`ConnectionStatus` và `TelegramAuthStep` chỉ trả mã lỗi ổn định: `invalid_credentials`, `network_unavailable`, `timeout`, `rate_limited`, `keyring_unavailable`, `storage_unavailable`, `cancelled`, `unsupported_schema`. Không trả nguyên exception có HTTP URL bot token hay request body. GUI dịch mã lỗi sang lời giải thích và nút thử lại. ConnectionStatus tách telegram_account/control_bot/chat_ai/embeddings/storage/runtime, checked_at/next_action/capability thật; key tồn tại không chứng minh Online.

Helper types do module sở hữu định nghĩa: `OnboardingStatus` gồm PublicProfile, ConnectionStatus[], stage evidence IDs, required next action và disabled capabilities; `SecretInput` là container repr=False chỉ trong RAM; `ConnectionOptions` không secret gồm model/provider/consent/test action; `MaintenanceLease` gồm profile_id, generation, holder, expires_at và state writers_fenced. Lease được lifecycle coordinator cấp sau stop/fence, restore không nhận bool giả thay proof này. Session setup có owner_id nullable và authority/capability setup-only; session quản lý cần owner_verified+paired evidence, không cấp int owner=0 giả. Chốt cùng root DTO/schema review trước code.

### Hợp đồng IPC và HTTP

- Pipe ví dụ `\\.\pipe\TelegramAIPersonalAssistant.<SID-hash>`. `protocol_version=1`, mỗi message `request_id`, `operation`, `payload`; length limit 64 KiB, timeout 30s cho RPC thường, tác vụ dài trả flow_id rồi poll status. Không log payload; request parser reject unknown fields/operation.
- NativeCommand canonical không secret: `open_connection_dialog`, `open_telegram_login`, `open_bot_dialog`, `issue_dashboard_ticket`, `runtime_start`, `runtime_stop`; gồm name/request_id/profile_id/payload_nonsecret. Internal **native-to-broker** RPC mới được mang secret ephemeral: `credential_validate_save`, `telegram_auth_code`, `telegram_auth_2fa`; không dùng public NativeCommand DTO hoặc bridge HTTP cho payload này. Root MySQL chỉ RPC native opt-in, không lưu root. Broker vẫn verify SID/logon session cho mọi RPC.
- `issue_dashboard_ticket` trả `{origin, instance_id, ticket, expires_at}` qua IPC. Browser URL canonical `http://127.0.0.1:<port>/#launch=<ticket>`; không ticket trong query string. GUI không in URL có fragment vào console/log.
- `POST /api/v1/auth/launch/redeem` body `{ticket}` từ trang cùng origin; instance/profile binding lưu server-side, không tin client để đổi owner. Chỉ chấp nhận Origin đúng numeric loopback + port đang phục vụ, không CORS wildcard, kiểm tra client loopback/Host/Fetch Metadata khi có. Không chấp nhận missing Origin cho đường browser redemption. Success cùng shape với `/auth/login`, cookie `tg_admin_session` HttpOnly/SameSite Strict và CSRF hiện có. Không cấp ticket qua HTTP.
- Dashboard đọc fragment, gọi `history.replaceState` xóa fragment ngay trước network await, rồi redeem; giữ ticket trong biến RAM và xóa sau lần dùng. Không đưa ticket/session/CSRF vào localStorage/sessionStorage, state persist, error report hay service worker.
- `GET /healthz` vẫn chỉ trạng thái công khai tối thiểu. `GET /api/v1/setup/status` public sanitize/read-only chỉ stage tổng quát không profile/account/key/provider; SID-issued setup session mới xem OnboardingStatus chi tiết/capability. `GET /api/v1/connections` trả ConnectionStatus sanitize dưới session; `GET /api/v1/operations/{id}` trả OperationResult với owner/profile scope. Browser chỉ hướng dẫn/mở native dialog qua command bridge đã auth + CSRF; không cho bridge mang secret.
- Trước pairing browser được xem dashboard incomplete/read-only dưới setup capability; không cố dựng `AdminContext(owner_id=0)` để dùng API quản trị. Sau pairing runtime dựng AdminContext với owner thật; setup-only capability không được quyền gửi/xóa/sync. Owner được lấy từ MTProto verified evidence, không từ người đầu tiên redeem ticket.
- Giữ CLI `dashboard-code` làm recovery cục bộ và thêm GUI “Hiện mã dự phòng”. TTL 300s, rate limit và **one-use thật**. Thay deterministic mã chu kỳ replayable bằng issued random code digest lưu ở broker auth state; cấp lại thu hồi code cũ, CLI lấy qua SID-authenticated IPC. Không gửi bot/log. Đăng xuất browser thu hồi session; shortcut “Mở dashboard” là hành động chủ động đăng nhập lại của Windows user, không auto-login khi chỉ mở URL trần.

## 3. Các task triển khai và nghiệm thu

### PL-01 — Config per-user và package resources

**Files Modify:** `paths.py`, `config.py`, `cli.py` (cwd, autostart, docs hint), `setup/wizard.py`, `runtime.py` env writers, `admin_api/app.py` docs/static resolver, `pyproject.toml`. **Create:** `resources.py`, `contracts.py` và frontend schema mirror theo spec, `tests/test_installed_resources.py`, `tests/test_user_config_paths.py`.

**Depends:** Không. **Produces:** `config_path() -> Path`, `app_resource(relative: str)` context manager; `get_settings()` đọc absolute config path; package dashboard/migrations/docs path tồn tại trong bản đã cài.

- [ ] Viết test gọi từ ba cwd khác nhau; config giữ cùng giá trị, `.env` ở cwd khác không được tự đọc, không ghi file vào install dir. Thêm test username/path tiếng Việt và có khoảng trắng.
- [ ] Tách `install_root`/`resources` chỉ đọc và `user_data_root` có thể ghi; `config.env` trong LocalAppData, atomic write qua temp + replace dưới lock. Dùng allowlist non-secret setting; không cho `save_settings_env` ghi arbitrary key hoặc newline injection.
- [ ] Luồng import `.env` legacy là explicit, preview key không secret; old path được chọn bằng file picker/CLI flag, không scan credential hoặc arbitrary profile. Sao lưu config cũ, giữ backend MySQL, kiểm tra Credential Manager hiện tại bằng bool trạng thái, không xuất nội dung secret. Nếu không đủ dấu hiệu legacy, hỏi lựa chọn native; không tự tạo SQLite đè trải nghiệm đang dùng.
- [ ] Canonical migration source vẫn `alembic/`; build đưa vào `tg_assistant/resources/migrations`, dashboard build vào `resources/dashboard`, docs allowlist vào `resources/docs`. Dùng importlib.resources và giữ extraction lifetime cho Alembic/static serving. Không duy trì hai bản migration sửa bằng tay.
- [ ] Chạy Alembic programmatically với script_location tuyệt đối; không `python -m alembic` phụ thuộc cwd/alembic.ini ở root.
- [ ] Test wheel installed trong môi trường không có checkout: docs endpoint đọc đúng, deep URL dashboard trả index, assets MIME đúng, migration resource tồn tại; path traversal trả 404.
- [ ] Verify: `python -m pytest tests/test_user_config_paths.py tests/test_installed_resources.py`; build wheel + install smoke riêng. Lưu manifest file trong artifact làm bằng chứng, không chỉ `build succeeded`.
- [ ] Chốt contracts.py theo spec: enum/DTO canonical, schema mirror frontend generated/validated, test unknown/stale health và field allowlist không secret. Commit riêng; bàn giao API path/contracts cho agent native/storage/frontend.

**Pass quan sát:** Đổi cwd/shortcut target không đổi cấu hình; installed artifact chạy doctor/docs/dashboard không tìm `.venv`/repo. Config không có key thật.

### PL-02 — SQLite production adapter và concurrency

**Files Modify:** `db/base.py`, `db/models.py`, `config.py`, `runtime.py` make_database + claim sites, `services/actions.py`, `services/tasks.py`, `admin_api/app.py` các write route có lock, `telegram/user_client.py`, `pyproject.toml`. **Create:** `db/storage.py`, `db/transitions.py`, `tests/test_sqlite_storage.py`, `tests/test_storage_concurrency.py`, `tests/test_storage_search_parity.py`.

**Depends:** PL-01 path contract. **Produces:** StorageConfig + Database hỗ trợ hai dialect, atomic transition/claim. **Coordination:** Agent an toàn/policy sở hữu quy tắc revoke/BLOCK và pending-action execution; thống nhất CAS trước khi cùng sửa runtime/actions. Backend storage không được tự đổi quyền trong job.

- [ ] Viết test file-backed SQLite (không chỉ `:memory:`): FK, Telegram BigInteger IDs âm/lớn, autoincrement id, unique chat_id/message_id, JSON roundtrip, UTC datetime/naive compatibility, upsert edit/delete, reopen sau commit.
- [ ] Thêm `database_backend=sqlite|mysql` và `sqlite_path` non-secret. Chọn SQLite chỉ khi install mới; resolve MySQL yêu cầu secret, resolve SQLite không gọi prompt database_password. URL SQLite chuẩn SQLAlchemy không tự nối chuỗi path Windows sai; reject UNC/network path cho đường standard.
- [ ] Đưa aiosqlite từ dev vào runtime; không đổi driver MySQL ngoài phạm vi. SQLite connection thiết lập `foreign_keys=ON`, `busy_timeout=5000`, WAL được xác nhận; mặc định durability `synchronous=FULL`, không âm thầm đổi NORMAL để benchmark đẹp. Ghi SQLite library version vào doctor/artifact manifest.
- [ ] Dùng BigInteger `.with_variant(Integer, "sqlite")` cho **PK autoincrement**, giữ Telegram IDs BigInteger; migration cũng tương ứng. MySQL FULLTEXT index chỉ tạo cho MySQL, SQLite không giả định có FTS; không đổi ranking/filter semantics trong task cài đặt.
- [ ] Thay select-lock-then-update cho SQLite bằng UPDATE có predicate trạng thái/version + rowcount; claim token riêng từng lần claim. Update completion/pause/cancel chỉ khi vẫn giữ claim và policy version còn hiệu lực. CAS không cho stale worker ghi lại status mới hơn. MySQL có thể giữ row lock trong adapter nhưng cùng contract hành vi.
- [ ] Audit các sites `runtime.py:1148,1722,1883,2280`, actions confirm/cancel, task updates, admin job transitions. Transaction DB phải kết thúc trước Telegram/AI await. Retry SQLITE_BUSY chỉ quanh DB transaction ngắn với bounded backoff, không retry toàn network action.
- [ ] Viết race tests: hai connections claim một job -> đúng một token; confirm/confirm và confirm/cancel -> một winner; pause/BLOCK trước/sau claim -> worker không bật lại quyền; reminder bị hai poll -> không phát hai lần do claim. Restart xử lý claim stale theo policy gói runtime.
- [ ] Không tuyên bố exactly-once Telegram send từ DB transaction: crash sau remote side effect nhưng trước commit phải sang trạng thái uncertain/reconcile, không tự gửi lại. Gói an toàn sở hữu lựa chọn idempotency/reconciliation.
- [ ] Keyword parity fixture tiếng Việt, emoji, '%'/'_', sender/date/scope/filter/delete giữa MySQL và SQLite; ghi rõ collation case/accent differences, không hứa kết quả y hệt mọi collation. Giữ authorization filter trước trả kết quả. `%`/`_` phải có test và quyết định literal/wildcard nhất quán với contract hiện có.
- [ ] Verify: `python -m pytest tests/test_sqlite_storage.py tests/test_storage_concurrency.py tests/test_storage_search_parity.py tests/test_sync.py tests/test_actions.py tests/test_tasks_time.py`; chạy integration MySQL riêng, sau đó full suite.
- [ ] Benchmark beta tham chiếu: file SQLite 100.000 messages, admin read đồng thời batch sync 200 messages/commit và một learning job; trên máy tham chiếu ghi rõ CPU/RAM/disk, không deadlock/SQLITE_BUSY chưa xử lý trong 30 phút. P95 local API read <2s là ngưỡng đề xuất nội bộ, không bảo đảm mọi PC. Không đủ -> tối ưu batch/index trước khi cân nhắc lại MySQL managed.
- [ ] Commit/báo cáo cả dialect, bảo đảm tests không dùng `Base.metadata.create_all` để thay migration acceptance.

**Pass quan sát:** Cài mới không cần MySQL; thao tác đồng thời không chạy job/hành động hai lần do race; update/reopen không mất record, BLOCK vẫn là thẩm quyền cuối.

### PL-03 — Migration cố định, cài mới và upgrade có kiểm chứng

**Files Modify:** `alembic/versions/0001_initial_schema.py`, `0003_ai_routing_operations.py` (SQLite autoincrement nếu cần), `0004_local_first_ai.py` (dialect/batch operations nếu cần), `alembic/env.py`, `setup/wizard.py`. **Create:** `db/migrations.py`, migration compatibility revision kế tiếp sau HEAD thực, `tests/integration/test_migration_matrix.py`, schema fixtures chỉ dữ liệu giả.

**Depends:** PL-01 + PL-02 schema choice. **Produces:** Inventory/upgrade/repair APIs và matrices cho SQLite/MySQL.

- [ ] Viết failing integration test `upgrade head` từ database trống qua Alembic thật, chứng minh lỗi 0001 -> 0002 hiện tại trên SQLite; thêm MySQL trống. Không đặt fixture bằng models hiện tại.
- [ ] Dựng 0001 bằng `op.create_table/create_index` cố định baseline lịch sử trước additions 0002-0005. Lấy schema từ lịch sử repository/migration chain đã kiểm tra; không đoán rồi cắt current metadata. Initial migration không import `tg_assistant.db.models/Base`. Có comment lịch sử, data types/default/FK/index explicit.
- [ ] Vì code chưa stable public nhưng có người đang dùng, việc sửa 0001 phải có ma trận legacy. 0002 knowledge_sources và 0003 additions/runtime_metrics không tạo lại cấu trúc đã thuộc baseline; inspect/repair phân biệt DB canonical theo revision với DB từng nhận create_all metadata hiện tại.
- [ ] `inspect_schema` classification: empty; canonical_supported_revision; legacy_complete_metadata; partial_or_unknown; future_revision. Chỉ chạy tự động empty/canonical. Legacy complete phải kiểm tra fingerprint columns/types/null/default/FK/index và có repair plan explicit; schema khác/future chặn start, không DROP, không stamp head mặc định.
- [ ] Lưu ý migration 0006 đơn thuần không thể sửa database bị fail ngay tại 0002. Repair gate phải chạy **trước** upgrade trong runner hoặc dùng reviewed historical compatibility path; snapshot backup trước mọi repair. Không dùng “if table exists -> skip” cho mọi bảng vì có thể bỏ cấu trúc sai.
- [ ] Bổ sung dialect migrations: SQLite batch ALTER khi cần, FK bật theo connection, integer autoincrement đúng; MySQL utf8mb4 và unique/index vẫn đúng. Revision mới chỉ cho thay đổi schema thực, không đổi revision của DB đã dùng mù quáng.
- [ ] Test nâng cấp fixture revision 0001/0002/0003/0004/0005 canonical, legacy stamped 0001 nhưng chứa current tables, partial tạo bảng rồi crash, không alembic_version nhưng có app tables, DB unrelated. Xác nhận row counts/checksums/policy sau upgrade; unknown phải nguyên vẹn.
- [ ] Inject failure giữa upgrade và thử lại; MySQL DDL không giả định rollback toàn migration. Diagnostic ghi revision/table check đã hoàn thành, không DSN password; phục hồi từ backup trên fixture. Không auto downgrade khi binary rollback.
- [ ] Verify: `python -m pytest tests/integration/test_migration_matrix.py -m integration`; thực hiện trên SQLite và MySQL thực trong CI, quyền database test riêng. Fixture không chứa account/message thật.
- [ ] Commit + bảng revision/fingerprint/repair steps cho QA và installer owner.

**Pass quan sát:** Empty -> head thành công trên hai dialect; dữ liệu legacy đã hỗ trợ được giữ; schema chưa biết chặn với hướng dẫn, không tự “sửa” phá dữ liệu.

### PL-04 — Windows trust boundary và mở dashboard một lần bấm

**Files Modify:** `security.py`, `admin_api/auth.py`, `admin_api/app.py` auth routes/headers/static, `cli.py` dashboard/open command, `dashboard-prototype/src/api.js`, `dashboard-prototype/src/App.jsx` auth boot. **Create:** `windows/ipc.py`, `admin_api/launch_auth.py`, `tests/test_launch_auth.py`, `tests/windows/test_ipc_security.py`, frontend launch-auth tests.

**Depends:** PL-01; launcher consumes implementation PL-06. **Produces:** IPC v1, ticket redemption, recovery code path.

- [ ] Viết auth unit tests with injected clock: ticket hết hạn 30s, consumed lại, wrong instance/origin/purpose -> reject; hai threads/coroutines redeem đồng thời -> một session; restart invalidate tất cả ticket/session RAM.
- [ ] Pipe security descriptor explicit: current SID và logon SID thích hợp; không Everyone/Anonymous/Authenticated Users rộng, reject remote clients. Xác minh client token SID/logon session qua impersonation, không tin SID request body. Default pipe descriptor có thể cho Everyone/Anonymous đọc theo [Microsoft named pipe security](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights), nên bắt buộc thiết lập DACL.
- [ ] Đặt first-instance semantics cho server và chống fake broker bằng challenge HMAC với admin_dashboard_secret trong Credential Manager của current SID + instance binding; không truyền secret này trên pipe. Verify broker identity before secret RPC. Nếu user khác giữ pipe name trước, fail closed có diagnostic, không tự chuyển sang pipe rộng quyền.
- [ ] Khóa ACL config/data/session/endpoint files cho current SID và SYSTEM theo mục đích, kiểm tra write path ownership; quản trị viên máy và malware chạy cùng SID thuộc trust boundary của OS, không tuyên bố app chống được mọi local malware. Không đọc Credential Manager dưới tài khoản elevated khác.
- [ ] Launcher yêu cầu broker status, khởi động runtime nếu cần, chờ health/schema ready với timeout 30s; nếu đang setup mở native wizard hoặc dashboard incomplete read-only theo lựa chọn. Paired/first-answer capability kiểm tra trước management writes; runtime lỗi giữ diagnostic và nút thử lại, không browser trắng. Port mặc định 8765, conflict chọn loopback port khả dụng và publish endpoint qua authenticated pipe; không kill process chiếm cổng.
- [ ] Issue one-use ticket chỉ từ broker auth Windows. Đổi ticket thành cookie bằng same-origin POST; API giữ HttpOnly/SameSite/CSRF, logout/revoke/TTL. Strict Origin cho redemption và login fallback; rate-limit error không reveal validity thông qua chi tiết nhạy cảm.
- [ ] Full dashboard/static có CSP phù hợp asset self, frame-ancestors none, no-referrer, no-store cho auth/setup HTML và không external analytics/font/script trên trang launch. API CSP hiện tại chỉ áp `/api/` nên chưa đủ để bảo vệ fragment bootstrap. Không service worker cache auth page. Kiểm tra CSP không phá asset/UI hiện tại.
- [ ] Browser clear fragment trước request, không persist credential. Route mở URL trần chưa có cookie hiện login với hướng dẫn “Mở từ ứng dụng Windows” và mã dự phòng. Hết session đưa về login; không âm thầm auto-login từ browser.
- [ ] UI recovery native “Hiện mã dự phòng” và CLI `dashboard-code` dùng issued random code local 300s, digest-only, consume atomic one-use; replay không chấp nhận. Cấp code qua IPC; reissue/restart/logout revoke theo policy. Thêm simultaneous redeem/replay tests cho code fallback, không chỉ launch ticket. Show expiry chính xác, không gửi bot/log. Copy chỉ nếu user chọn, xóa clipboard có điều kiện sau TTL không ghi đè clipboard mới.
- [ ] Test Windows hai SID/logon session: user B không connect/read pipe/profile hoặc lấy code của A; website evil Origin/Host/CSRF/form POST/fetch không login/configure; plain localhost browser không được implicit owner access; popup/frame bị chặn. Test Unicode path/port conflict/runtime restart/logout.
- [ ] Verify backend unit + Windows integration + browser smoke; giữ video/screenshot không có ticket/code/key/PII. Commit auth riêng, review bởi agent security trước installer.

**Pass quan sát:** Shortcut Windows -> dashboard đã đăng nhập khi owner paired; URL trần -> yêu cầu local login; wrong user/website không tạo session. Recovery không cần password mới.

### PL-05 — Native onboarding, Telegram và API connections

**Files Modify:** `runtime.py` prompt/bootstrap extraction + readiness, `telegram/user_client.py`, `telegram/pairing.py`, `setup/wizard.py`, `security.py`, `cli.py` readiness/doctor; backend Admin API chỉ nhận status. **Create:** `setup/state.py`, `setup/connections.py`, `telegram/auth_service.py`, `windows/wizard.py`, `tests/test_setup_state.py`, `tests/test_telegram_auth_flow.py`, `tests/test_connection_configuration.py`.

**Depends:** PL-01/02/03/04. **Produces:** Native setup state/connection/auth contract, owner-paired Ready.

- [ ] Tách nghiệp vụ khỏi `getpass/typer/print`, giữ CLI adapter tương thích. Native broker có thể dựng setup state khi chưa có DB/password/owner; không tạo Management AdminContext giả.
- [ ] Native wizard theo OnboardingStage canonical: welcome + SQLite/storage_ready; chọn cloud/local/off và ai_configured; Telegram API ID/hash + OTP/QR/2FA -> telegram_verified; token/getMe -> bot_verified; pairing -> owner_paired; chọn/xác nhận một nguồn -> source_selected; câu trả lời thử -> first_answer; review -> ready. MySQL existing nâng cao giữ backend. Không buộc CoinGecko/tất cả provider keys; save/exit/resume ở mọi bước, auth ephemeral bắt đầu lại. Nếu chọn AI off/keyword-only, ghi evidence disabled/limited riêng và UI nói rõ chưa có câu trả lời AI/semantic full, không giả first_answer đã chạy.
- [ ] Telegram API ID/hash và bot token có hướng dẫn official và nút mở trang tạo app/BotFather, người dùng tự thao tác tạo tài nguyên. Không hứa app tự tạo Telegram bot/API credentials. Secret native fields masked, reveal giữ nhấn tùy chọn, không serialize QSettings/preferences; widget clear sau submit/cancel.
- [ ] OTP state: begin -> code_required -> authenticated hoặc password_required -> authenticated. QR vẫn cần API ID/hash; native render payload, refresh khi expired, cancel/disconnect an toàn, 2FA native nếu Telegram yêu cầu, fallback OTP rõ. Có giới hạn concurrent flow=1, flow bound current installation/SID; resend có cooldown theo Telegram error, không retry vô hạn. Không đọc Telegram OTP inbox tự động.
- [ ] Telethon `get_me()` là nguồn owner ID; không nhận arbitrary owner_id từ browser/form. Switching account không ghi owner mới lên corpus cũ: yêu cầu backup/review/reset profile explicit trong gói lifecycle; v1 một account/profile, không multi-account routing.
- [ ] Bot get_me validation chỉ chứng minh token hợp lệ; pairing deep link start nonce một lần cần sender_id bằng account vừa xác minh, TTL 300s; fallback `/pair CODE` tương đương. Start payload trong giới hạn Telegram, không token auth account; người dùng bấm Start, app không tự impersonate gửi. Replay/expired/wrong sender -> reject. Restart giữa login/pairing cho resume rõ ràng, không coi session.enc tồn tại là paired.
- [ ] Credential test trước commit; invalid/network error giữ key tốt cũ, replace/delete cần explicit action. Credential Manager backend thiếu hoặc không Windows -> fail closed, không fallback plaintext keyring. Provider error log chỉ mã an toàn; redaction hỗ trợ tên api_key và secret giá trị bất kỳ, không chỉ prefix regex known.
- [ ] OpenAI/OpenRouter user chọn một provider, test kết nối dùng synthetic input không gửi Telegram history; chỉ kiểm tra models/auth không bill nếu có thể, gọi generation có thể tính phí phải có hành động “Thử trả lời” với mô tả chi phí. CoinGecko optional và không chặn core Ready; provider off hợp lệ, UI nói tính năng AI off.
- [ ] Thay cấu hình Ollama-only embedding hiện tại bằng EmbeddingProfile explicit. Cloud standard path: selected chat provider + embedding provider/model đã probe capability; owner thấy nguồn dữ liệu gửi cloud, chi phí và consent trước học. Không bắt cài Ollama để dùng cloud. OpenRouter không hỗ trợ embedding đã chọn -> truthful error/keyword-only hoặc chọn OpenAI embedding explicit, không hidden fallback. Local path dùng Ollama chat/embed, guide cài/tải dung lượng/network/cancel, không tải model lớn chưa chọn.
- [ ] Bàn giao EmbeddingProfile cho gói AI/vector: store_id fingerprint(provider, approved endpoint, model, embedding_version, dimension); đổi profile cần preview/reindex owner-approved, không mix corpus hoặc âm thầm chọn lại store. LOCAL ONLY nguồn cấm cloud chat/context/embedding kể cả global consent; fallback cần cả global consent, policy nguồn và budget. Readiness chat/embedding riêng, checked_at thật, capabilities thật, unknown/stale không Online. Không redefine corpus policy một mình trong task GUI.
- [ ] Profile session NTFS ACL current SID; giải mã file runtime trong directory riêng được bảo vệ, cleanup session và journal/WAL/SHM liên quan sau graceful stop. Crash recovery phát hiện plaintext residue, xác minh/khôi phục theo policy rồi dọn; không để plaintext trong TEMP chung. Encryption at rest giữ key trong Credential Manager; chốt rotate/logout/session revoked tests.
- [ ] Tests mock Telegram/provider: OTP wrong/expired, 2FA wrong, QR expired/cancel, QR thiếu API ID/hash, FloodWait, no network, revoked account, valid bot but wrong pairing actor; cancel giữa lưu credentials; restart ở mọi stage; keyring failure không tạo .env/raw log. Cloud setup không Ollama, unsupported embeddings, consent absent/withdrawn, LOCAL ONLY + global consent, profile switch pending reindex đều có test. Canary secret arbitrary không theo prefix vẫn vắng log/config/HTTP snapshot/screenshots.
- [ ] Verify các test mới + `tests/test_api_key_setup.py`, `tests/test_security_memory.py`, `tests/test_search_budget_pairing.py`; UAT live chỉ bằng account/key do tester cung cấp cho app native, tuyệt đối không thu qua chat agent. Commit flow riêng.

**Pass quan sát:** Người mới hoàn thành bằng GUI, không terminal; OTP/2FA không đi qua bot/browser; owner paired đúng; sửa API key không phải restart toàn wizard, không mất config tốt vì lỗi mạng.

### PL-06 — Launcher/tray PySide6 và vòng đời local

**Files Modify:** `cli.py` start/stop/restart/status/autostart worker interface, `runtime.py` startup/shutdown health, `pyproject.toml` entry points. **Create:** `windows/launcher.py`, `windows/runtime_control.py`, launcher assets theo art style, `tests/test_runtime_control.py`, `tests/windows/test_launcher_smoke.py`.

**Depends:** PL-04/05 contracts. **Produces:** `tg-assistant-desktop` entry point, GUI/tray, shortcut/autostart.

- [ ] Launcher initial screen: trạng thái, Mở dashboard, Thiết lập/Kết nối, Start/Stop, Diagnostic, Backup. UI phản ánh starting/running/stopped/setup/error; không hiện PID như thông tin chính của người mới.
- [ ] Dùng Qt worker thread/signals cho IPC/process I/O, không network/asyncio blocking trên GUI thread. Broker/runtime tách process để đóng cửa sổ không dừng trợ lý; tray cho Exit launcher và Stop assistant khác nhau, có nhãn rõ. Startup lỗi có diagnostic sanitzed và nút xem log.
- [ ] PyInstaller onedir khác chạy `sys.executable -m tg_assistant.cli`: define frozen command modes/dispatch rõ; launcher worker dùng executable đã cài + `--worker`, không shell string/`cmd /c start.bat`. Ghi PID cùng executable/instance ID, không tin PID tái sử dụng; single instance per SID, port per runtime.
- [ ] Stop/restart timeout và shutdown cuối cùng close Telethon/encrypt session/Qdrant/DB; không kill tùy ý process vì PID file cũ. Nếu force stop cần thông báo trạng thái có thể interrupted và restart recovery rõ.
- [ ] Autostart opt-in theo user logon, current SID, never highest privileges/SYSTEM. Tên task/Run entry bao gồm installation/SID hợp lệ để hai user trên cùng máy không đè nhau; path quoting spaces/Unicode test. UAT revoke autostart không ảnh hưởng user khác.
- [ ] Diagnostic native lấy snapshot an toàn; export log phải redact/canary scan, không dump process env/credential. Browser status không có nút nhập secret; nút Kết nối dẫn người dùng mở Windows app qua hướng dẫn nếu native không đang mở, không tùy ý expose remote protocol handler trong v1.
- [ ] Test double click nhiều lần không tạo 2 runtime/2 Qdrant lock owners; Windows sleep/wake/logoff, expired browser session, port occupied, MySQL offline existing install, no Ollama, startup corrupt config. Không báo running chỉ vì pid_file tồn tại.
- [ ] Verify unit + Windows VM GUI/tray smoke; bộ smoke ghi kết quả observable từng trạng thái. Commit launcher riêng.

**Pass quan sát:** Start menu shortcut mở app; lần đầu wizard, lần sau dashboard; tray/status/start/stop dùng được không console và không admin.

### PL-07 — Artifact, installer, upgrade và release build

**Files Modify:** `pyproject.toml`, frontend build config hiện có, `.gitignore`, CI backend/frontend/build files. **Create:** `packaging/windows/assistant.spec`, `packaging/windows/installer.iss`, `scripts/build_windows_release.ps1`, Python/frontend dependency lock phù hợp build tool đã chọn, `tests/windows/test_installer_lifecycle.py`, `docs/windows-install.md`.

**Depends:** PL-01..06 và PL-08 upgrade/restore contract; security/runtime blockers ở các gói khác phải pass trước release beta.

- [ ] Lock release dependencies bao gồm PySide6/PyInstaller/aiosqlite/pywin32 keyring backend, frontend lockfile; build runner sạch có Python build-time và Node build-time. End user không cần chúng. Pin Python runtime/Qt version phù hợp Windows 10 mục tiêu sau clean VM validation và official platform-support review; không mặc định latest Qt hỗ trợ mọi Windows. Ghi support matrix/x64/license/notices PySide6/Qt, không ngầm hứa ARM/Linux/macOS.
- [ ] Build frontend một lần trong CI, đưa dist/client + docs + migrations vào wheel/frozen artifact; inspect Qt plugins và keyring backend bundled. Loại test data/.env/.venv/cache/node_modules/logs/exports/secret khỏi installer; artifact scanner chỉ dữ liệu synthetic.
- [ ] Per-user install vào LocalAppData Programs, shortcut/tray icon theo art style, không chạm Credential Manager của user khác, không mở firewall/Public listener. App data ở thư mục riêng không bị wipe khi thay binary.
- [ ] Installer standard path SQLite không download/cài MySQL. MySQL advanced chỉ detect/verify connection và hướng dẫn prerequisites rõ; không tự đổi service/root DB. Nếu binary muốn hỗ trợ MySQL backup CLI thì đóng gói tool có license hoặc cảnh báo rõ chức năng advanced cần tool; không để requirement này lọt vào SQLite core.
- [ ] Upgrade: đọc install/profile version, dừng runtime có phối hợp, snapshot config/DB, install binaries vào versioned staging, verify manifest, run migration trước worker. Incompatible/failure -> giữ backup/previous binary và ngừng worker, không chạy binary cũ với schema mới nếu chưa được chứng minh tương thích. Recovery qua PL-08, không auto downgrade schema.
- [ ] Kill installer ở từng phase, mất mạng, disk full, file AV lock, thiếu quyền profile; reinstall phải resume an toàn, không xóa data hay credentials. Uninstall remove binary/shortcut/autostart; mặc định giữ dữ liệu. “Xóa dữ liệu và kết nối” là luồng riêng explicit, dừng worker, scope user profile, không DROP shared MySQL database không được xác nhận.
- [ ] Authenticode sign launcher/worker/installer khi release stable, timestamp/check signature; certificate ở secret CI/release operator, không source/docs. Private beta unsigned phải ghi rõ trạng thái và hướng dẫn xác minh hash; không hứa hết SmartScreen chỉ vì ký. Review license của project/Qt/installer/DB libs/fonts/assets trước public distribution.
- [ ] Publish release version/hash/build manifest/backend schema revision/resource inventory/changelog/support matrix. CI build/test ở đúng SHA release; artifact smoke phải chạy bản **download/cài**, không dev checkout.
- [ ] Clean Windows standard-user VM không Python/Node/MySQL/Ollama: install -> launcher -> SQLite migration -> native connect -> pair -> dashboard; upgrade fixture MySQL cũ không đổi backend; hai Windows users độc lập; uninstall giữ data -> reinstall có data; purge explicit đúng profile.
- [ ] Verify frontend production build/backend suite/migration matrix/installed asset smoke/security/installer lifecycle. Commit build definition; signing/publish là bước riêng theo quyền người dùng, không tự push/tag/release trong nhiệm vụ lập kế hoạch.

**Pass quan sát:** QA nhận một installer có manifest/hash và tự chạy được từ Windows sạch; dữ liệu còn sau nâng cấp/reinstall; không cần tài khoản admin/root theo đường standard.

### PL-08 — Backup/restore theo backend, dependency cho upgrade

**Files Modify:** `cli.py` backup/restore, `services/operations.py` storage info, `config.py` safe backup fields. **Create:** `services/storage_backup.py`, `tests/test_storage_backup.py`, `tests/integration/test_restore_matrix.py`.

**Depends:** PL-01/02/03. **Produces:** `StorageService.backup(destination: Path) -> BackupManifest`, `StorageService.restore(source: Path, maintenance_lease: MaintenanceLease) -> RestoreReport`; native launcher/CLI gọi cùng service, implementation snapshot/staging nằm `services/storage_backup.py`. **Coordination:** Gói recovery/vector sở hữu rebuild Qdrant và retrieval acceptance; storage backup không tuyên bố vector đã được restore nếu chỉ có metadata.

- [ ] Manifest JSON có format_version, app_version, actual Alembic revision, backend, created_at, file hash/size, vector mode (`snapshot` hoặc `rebuild_required`); không hardcode schema=0001. Không có SecretStore/session key/plaintext or encrypted account session trong portable backup default; yêu cầu login Telegram lại trên máy/profile mới.
- [ ] SQLite backup dùng SQLite online backup API vào staging file + integrity/FK checks, không copy riêng `.db` đang WAL hoạt động. [SQLite backup API](https://www.sqlite.org/backup.html) tạo snapshot DB; package config/vector state cần coordinated cutoff riêng. Không giả định DB snapshot tự làm Qdrant nhất quán.
- [ ] MySQL giữ adapter dump phù hợp với schema revision và tools; không bỏ privileges/charset, không lộ password qua argv/log. Hỗ trợ restore backup legacy schema marker sai bằng schema inspection có kiểm chứng, không coi marker=0001 chứng minh schema thật.
- [ ] Restore: verify archive format/hash/size/path traversal/backend/revision trước thay đổi; cấp MaintenanceLease sau dừng worker/fence broker+API writers, tạo backup hiện trạng, stage DB riêng, check schema/integrity, đóng connections, checkpoint xử lý WAL và swap profile target dưới exclusive lock. Không ghép WAL/SHM cũ vào DB mới. Lease stale/missing -> reject không thay dữ liệu; unknown future/backend conversion -> reject với hướng dẫn, không auto convert.
- [ ] Nếu vector backup chỉ metadata, hiện “cần tạo lại chỉ mục”, không “khôi phục hoàn tất” toàn app; reset/reconcile reuse trạng thái theo gói vector trước reindex, không tin vector_status cũ khi vector store trống. Không cấp lại ALLOW nguồn bị revoked.
- [ ] Test SQLite backup giữa write/WAL -> restore rows chính xác; tampered/oversized/path traversal/future manifest -> no change; disk full giữa swap -> previous data recoverable; MySQL fixture restore và SQLite fixture restore; MySQL backup không tự restore vào SQLite. Kiểm chứng retrieval sau vector rebuild theo gói runtime.
- [ ] Verify: `python -m pytest tests/test_storage_backup.py tests/integration/test_restore_matrix.py`; diễn tập bằng artifact Windows và dữ liệu giả. Commit backup service riêng trước gate upgrade PL-07.

**Pass quan sát:** Backup portable không chứa secret; restore vào profile mới mở được SQLite và chỉ dẫn login/index đúng; installer dùng cùng contract để có đường phục hồi dữ liệu.

## 4. Thứ tự, ownership và checklist giao agent

Chuỗi phụ thuộc: `PL-01 -> PL-02 -> PL-03`; `PL-01 -> PL-04`; `PL-02/03/04 -> PL-05 -> PL-06`; `PL-01/02/03 -> PL-08`; tất cả -> `PL-07`. Không gộp mọi task vào một PR khó review. Có thể song song resources/storage và auth sau khi contract path được chốt; runtime/actions/admin_api/app.py là file shared, phải tránh hai agent sửa cùng đoạn.

| Owner công việc | Scope độc quyền chính | Inputs/outputs cần giao |
| --- | --- | --- |
| Storage/migrations | PL-02/03/08; db/storage/transitions/migrations | Backend resolution, CAS, manifest/upgrade contracts |
| Windows auth/native | PL-04/05/06; windows/*, auth_service, launch_auth | IPC v1, native setup status, launcher ticket |
| Packaging/release | PL-01/07; resources/build/installer | Resource contract, installed artifact + manifest |
| Dashboard/UI | Fragment boot/status/copy + giữ style | api.js/App.jsx integration, no native secret browser forms |
| Safety/runtime | BLOCK/cancel/policy/idempotency/vector | CAS semantics, recovery states, no optimistic Ready |

**Prompt bàn giao để dùng cho agent thực thi:**

> Đọc `docs/handoff/platform-work-package.md`, kế hoạch tổng thể và gói safety/UI. Thực hiện task PL-XX được giao trong checkout/worktree của repository thật; xác nhận HEAD và AGENTS.md trước sửa. Chỉ sửa file sở hữu của task; với runtime.py/actions.py/admin_api/app.py, thống nhất vùng thay đổi với điều phối viên. Windows per-user v1, SQLite mới/MySQL existing, không auto convert, PySide6 native secrets, Credential Manager, loopback browser dashboard, giữ art style. Viết regression/integration test có ý nghĩa trước khi đổi hành vi; chạy đúng artifact/dialect acceptance. Không lấy credential qua chat/log, không đăng nhập/gửi/xóa Telegram thật khi chưa có phạm vi UAT được người dùng giao. Không commit generated dependency/data. Báo cáo file sửa, contract thay đổi, test pass/fail/skipped và giới hạn còn lại; không gọi task complete khi clean install hoặc race test yêu cầu còn chưa chạy. Không public release/merge/deploy tự động từ prompt này.

**Checklist điều phối trước nhận bàn giao:**

- [ ] HEAD/worktree/AGENTS đã được xác nhận, secret access không nằm trong nhiệm vụ coding.
- [ ] Mọi quyết định storage/auth của từng agent khớp contract tài liệu; không sneaky public listener hay password dashboard mới.
- [ ] Native connection flow và browser auth có error/cancel/restart/offline paths, không chỉ happy path.
- [ ] Không race do chỉ dựa vào singleton/SQLite FOR UPDATE; BLOCK stale job kiểm chứng cùng gói safety.
- [ ] Package resources/absolute config và installed artifact smoke có bằng chứng.
- [ ] Migration fresh/upgrades/legacy unknown và SQLite WAL backup/restore đã chạy, không thay bằng create_all unit fixture.
- [ ] Giữ art style qua screenshot comparison; ảnh bằng dữ liệu synthetic, không secret/PII.
- [ ] CI ở release SHA, license/assets/code signing/support matrix được ghi; fresh-user beta UAT thực có kết quả tách khỏi audit cũ.

## 5. Kịch bản nghiệm thu người dùng cuối

1. Tester Windows standard user, máy không Python/Node/MySQL/Ollama: cài installer, shortcut mở wizard; một SQLite file profile được tạo và schema head đúng, không root prompt.
2. Tester tự nhập API ID/hash/account OTP hoặc QR/2FA tại GUI native và bot token; không có value trong browser network/config/logs. Pair bằng chính account, sai account bị reject.
3. Chọn cloud/local/off đầu setup. Cloud chat+embedding dùng credential đã probe capability và explicit consent, không cần Ollama; CoinGecko optional. LOCAL ONLY vẫn chặn cloud route. Local thiếu Ollama/model hoặc chọn off có save/resume/incomplete với nhãn đúng, không Online giả. Đổi embedding profile cần owner-approved store/reindex plan.
4. “Mở dashboard” tạo local cookie, fragment bị xóa; reload session hoạt động. Logout -> URL trần cần login; launcher mở lại được. Recovery code local vào được, expired/replay ticket bị chặn.
5. Cho học một nguồn qua workflow policy của gói runtime, xem trạng thái hoàn thành thật, hỏi với citations, BLOCK khi job queued/running; không có sync/retrieval sau revoke. Phần cuối là gate liên gói, không do installer chứng minh.
6. Stop/restart, reboot với autostart opt-in; owner/data giữ, secret không hiện console, hai user Windows không nhìn profile nhau.
7. Backup/restore profile test, schema/data checks + rebuild vectors/retrieval được xác minh. Upgrade installer giữ data/config/MySQL existing; failed migration dừng worker và hướng dẫn phục hồi.
8. Uninstall mặc định giữ data, reinstall nhận profile; purge explicit chỉ xóa profile/credentials đã xác nhận, không ảnh hưởng DB/app user khác.

Kết quả audit cũ 150 passed/42% coverage chỉ là baseline. PL-XX hoàn thành phải ghi bằng chứng mới cho đúng commit/artifact; tasks trong tài liệu này hiện chưa được triển khai.
