from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import typer
from sqlalchemy import select

from .admin_api import dashboard_login_code, ensure_dashboard_secret
from .admin_api.auth import login_code_expires_at
from .config import get_settings
from .db.models import (
    PermissionName,
    TelegramAccount,
    TelegramChatPermission,
    TelegramChatPolicy,
    TelegramMessage,
)
from .desktop.instance import AlreadyRunning, InstanceGuard
from .desktop.runtime_controller import RuntimeController
from .paths import ensure_runtime_dirs, project_root
from .policy import PolicyEngine
from .runtime import (
    bootstrap,
    configure_ai_provider,
    configure_coingecko_key,
    make_database,
    make_local_embedding_engine,
    make_user_client,
    prompt_secrets,
    run_application,
)
from .security import SecretStore
from .services.maintenance import FileLock, MaintenanceBusy, profile_writer
from .services.storage import StorageService
from .setup.mysql import detect_mysql
from .setup.wizard import run_setup


def _configure_console_utf8() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


_configure_console_utf8()

app = typer.Typer(help="Telegram AI Personal Assistant", no_args_is_help=True)
NATIVE_START_TIMEOUT = 20.0
NATIVE_STOP_TIMEOUT = 20.0


def _runtime_files() -> tuple[Path, Path, Path]:
    root = ensure_runtime_dirs()["data"]
    return root / "assistant.pid", root / "assistant.lock", root / "stop.request"


def _command_runtime_files() -> tuple[Path, Path, Path]:
    try:
        return _runtime_files()
    except (OSError, ValueError):
        typer.echo(
            "Không truy cập được profile; runtime_configuration_invalid. Kiểm tra cấu hình và quyền sở hữu."
        )
        raise typer.Exit(code=1) from None


def _started_file() -> Path:
    return ensure_runtime_dirs()["data"] / "started_at"


def _legacy_pid(path: Path) -> int:
    try:
        with path.open(encoding="ascii") as stream:
            content = stream.read(65)
        if len(content) > 64:
            return 0
        pid = int(content.strip())
        return pid if 0 < pid <= 0xFFFFFFFF else 0
    except (OSError, ValueError):
        return 0


def _process_exists(pid: int) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == "nt":
        tasklist = shutil.which("tasklist")
        if not tasklist:
            return False
        result = subprocess.run(
            [tasklist, "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in result.stdout and "No tasks" not in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path, self.handle = path, None
        self.guard = None

    def __enter__(self) -> InstanceLock:
        try:
            self.guard = InstanceGuard().acquire()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = FileLock(self.path, exclusive=True)
        except (AlreadyRunning, MaintenanceBusy):
            self.__exit__()
            raise RuntimeError("Ứng dụng đã chạy ở instance khác") from None
        except BaseException:
            self.__exit__()
            raise
        return self

    def __exit__(self, *_: object) -> None:
        if self.handle:
            try:
                self.handle.close()
            finally:
                self.handle = None
                if self.guard:
                    self.guard.close()
                    self.guard = None
        elif self.guard:
            self.guard.close()
            self.guard = None


def _native_controller() -> RuntimeController:
    return RuntimeController(get_settings())


def _echo_native_state(state) -> None:
    typer.echo(f"Trạng thái: {state.phase}; {state.code}.")
    if state.phase == "ready":
        typer.echo(f"Runtime đã xác nhận (PID {state.pid}).")
        typer.echo(f"Dashboard local: {state.url}")


def _native_start() -> None:
    runtime = _native_controller()
    try:
        started = runtime.start()
        deadline = time.monotonic() + NATIVE_START_TIMEOUT
        state = runtime.snapshot
        while time.monotonic() < deadline:
            if started.done():
                started.result()
            state = runtime.refresh().result(timeout=max(0.01, min(2, deadline - time.monotonic())))
            if state.phase == "ready":
                _echo_native_state(state)
                return
            if state.phase == "error" or (state.phase == "stopped" and runtime.process):
                _echo_native_state(state)
                raise typer.Exit(code=1)
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        typer.echo(
            f"Đang khởi động; chưa xác nhận readiness. Chạy status để kiểm tra. ({state.code})"
        )
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except TimeoutError:
        typer.echo("Đang khởi động; chưa xác nhận readiness. Chạy status để kiểm tra.")
        raise typer.Exit(code=1) from None
    except (OSError, ValueError, RuntimeError):
        typer.echo("Không thể khởi động; runtime_start_failed. Kiểm tra cấu hình hoặc bảo trì.")
        raise typer.Exit(code=1) from None
    finally:
        runtime.close()


def _native_status() -> None:
    runtime = _native_controller()
    try:
        _echo_native_state(runtime.refresh().result(timeout=2))
    except (OSError, ValueError, RuntimeError, TimeoutError):
        typer.echo("Trạng thái: unknown; runtime_readiness_unavailable.")
        raise typer.Exit(code=1) from None
    finally:
        runtime.close()


def _native_stop() -> None:
    runtime = _native_controller()
    try:
        state = runtime.refresh().result(timeout=2)
        if state.phase != "ready":
            _echo_native_state(state)
            typer.echo("Chưa xác nhận runtime; không gửi yêu cầu dừng. Chạy status để kiểm tra.")
            raise typer.Exit(code=1)
        runtime.stop()
        deadline = time.monotonic() + NATIVE_STOP_TIMEOUT
        while time.monotonic() < deadline:
            state = runtime.refresh().result(timeout=2)
            if state.phase == "stopped":
                typer.echo("Đã dừng an toàn.")
                return
            if state.phase == "error":
                break
            time.sleep(0.05)
        _echo_native_state(state)
        typer.echo("Chưa xác nhận đã dừng; không ép tắt. Chạy status để kiểm tra.")
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except (OSError, ValueError, RuntimeError, TimeoutError):
        typer.echo("Chưa xác nhận đã dừng; runtime_stop_unconfirmed. Không ép tắt.")
        raise typer.Exit(code=1) from None
    finally:
        runtime.close()


async def _is_paired() -> bool:
    database = make_database(get_settings(), SecretStore())
    try:
        async with database.session() as session:
            account = await session.scalar(select(TelegramAccount).limit(1))
            return bool(account and account.is_owner_paired)
    finally:
        await database.close()


def _ensure_ready() -> None:
    store, paths = SecretStore(), ensure_runtime_dirs()
    prompted_secrets = False
    settings = get_settings()
    if settings.storage_backend == "mysql":
        if not store.get("database_password"):
            run_setup()
        settings = get_settings()
    _prepare_sqlite_storage(settings, store)
    required = ("telegram_api_id", "telegram_api_hash", "telegram_phone", "telegram_bot_token")
    if any(not store.get(key) for key in required):
        prompt_secrets(store)
        prompted_secrets = True
    settings = get_settings()
    if settings.ai_secret_name and not store.get(settings.ai_secret_name):
        typer.echo(
            "Thiết lập nhà cung cấp AI (key được nhập ẩn và lưu trong Credential Manager)..."
        )
        configure_ai_provider(store, prompt_key=True)
    if not prompted_secrets and not store.get("coingecko_api_key"):
        typer.echo(
            "Thiết lập CoinGecko để tra giá (key được nhập ẩn và lưu trong Credential Manager)..."
        )
        configure_coingecko_key(store, prompt_key=True)
    encrypted = paths["sessions"] / "account.session.enc"
    paired = asyncio.run(_is_paired())
    if not encrypted.exists() or not paired:
        typer.echo("Thiết lập Telegram lần đầu (OTP/2FA chỉ nhập tại terminal)...")
        asyncio.run(bootstrap())


def _prepare_sqlite_storage(settings, store) -> None:
    from .contracts import PublicProfile

    service = StorageService(settings, store)
    database = service.open(
        PublicProfile(
            profile_id=settings.profile_id,
            owner_id=None,
            storage_backend=settings.storage_backend,
            setup_stage="welcome",
            version=1,
        )
    )
    try:
        service.migrate()
    finally:
        asyncio.run(database.close())


@app.command()
def start() -> None:
    """Mở runtime native trên Windows; giữ đường chạy nền legacy trên hệ khác."""
    pid_file, lock_file, stop_file = _command_runtime_files()
    if pid_file.exists():
        pid = _legacy_pid(pid_file)
        if pid and _process_exists(pid):
            typer.echo(
                f"Đã có tiến trình legacy (PID {pid}); chưa kiểm tra readiness. Không mở thêm."
            )
            return
        pid_file.unlink(missing_ok=True)
    if os.name == "nt":
        _native_start()
        return
    with InstanceLock(lock_file):
        _ensure_ready()
    stop_file.unlink(missing_ok=True)
    log_path = ensure_runtime_dirs()["logs"] / "background.log"
    flags = 0
    if os.name == "nt":
        flags = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )  # type: ignore[attr-defined]
    with log_path.open("a", encoding="utf-8") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "tg_assistant.cli", "worker"],
            cwd=project_root(),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=output,
            creationflags=flags,
            close_fds=True,
        )
    for _ in range(20):
        time.sleep(0.1)
        if pid_file.exists():
            break
        if process.poll() is not None:
            raise typer.Exit(f"Không thể chạy nền; xem {log_path}")
    typer.echo(f"Đã chạy nền (PID {process.pid}). Có thể đóng terminal.")
    settings = get_settings()
    if settings.admin_api_enabled:
        typer.echo(
            f"Dashboard local: http://{settings.admin_api_host}:"
            f"{settings.admin_api_port} "
            "(lấy mã bằng: .\\.venv\\Scripts\\tg-assistant.exe dashboard-code)"
        )


@app.command(hidden=True)
def worker() -> None:
    pid_file, lock_file, stop_file = _runtime_files()
    with InstanceLock(lock_file):
        pid_file.write_text(str(os.getpid()), encoding="ascii")
        _started_file().write_text(datetime.now(UTC).isoformat(), encoding="ascii")
        stop_file.unlink(missing_ok=True)

        async def main() -> None:
            task = asyncio.create_task(run_application())
            try:
                while not stop_file.exists() and not task.done():
                    await asyncio.sleep(1)
                if stop_file.exists():
                    # run_application receives cancellation and performs its finalizers.
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            finally:
                stop_file.unlink(missing_ok=True)

        try:
            asyncio.run(main())
        finally:
            pid_file.unlink(missing_ok=True)
            _started_file().unlink(missing_ok=True)


@app.command()
def run() -> None:
    """Chạy foreground và hiển thị log."""
    pid_file, lock_file, stop_file = _runtime_files()
    with InstanceLock(lock_file):
        _ensure_ready()
        stop_file.unlink(missing_ok=True)
        pid_file.write_text(str(os.getpid()), encoding="ascii")
        _started_file().write_text(datetime.now(UTC).isoformat(), encoding="ascii")
        try:
            asyncio.run(run_application())
        finally:
            pid_file.unlink(missing_ok=True)
            _started_file().unlink(missing_ok=True)


@app.command()
def stop() -> None:
    pid_file, _, stop_file = _command_runtime_files()
    pid = _legacy_pid(pid_file)
    if os.name == "nt" and not (pid and _process_exists(pid)):
        _native_stop()
        return
    if not pid_file.exists():
        typer.echo("Không chạy.")
        return
    if not _process_exists(pid):
        pid_file.unlink(missing_ok=True)
        typer.echo("Không chạy (đã dọn PID cũ).")
        return
    stop_file.write_text(datetime.now(UTC).isoformat(), encoding="ascii")
    for _ in range(100):
        time.sleep(0.1)
        if not _process_exists(pid):
            typer.echo("Đã dừng an toàn.")
            return
    typer.echo("Tiến trình chưa dừng sau 10 giây; không ép tắt để tránh hỏng session.")


@app.command()
def restart() -> None:
    if os.name == "nt":
        pid_file, _, _ = _command_runtime_files()
        pid = _legacy_pid(pid_file)
        if not (pid and _process_exists(pid)):
            runtime = _native_controller()
            try:
                state = runtime.refresh().result(timeout=2)
            except (OSError, ValueError, RuntimeError, TimeoutError):
                typer.echo("Chưa xác nhận runtime; runtime_readiness_unavailable.")
                raise typer.Exit(code=1) from None
            finally:
                runtime.close()
            if state.phase == "ready":
                _native_stop()
            else:
                # Missing/stale readiness is safe to start only if no SID worker owns the guard.
                # Release this probe before launch; the child acquires the same singleton guard.
                try:
                    with InstanceGuard():
                        pass
                except (AlreadyRunning, OSError, ValueError):
                    typer.echo("Chưa xác nhận runtime; runtime_restart_unconfirmed. Không mở thêm.")
                    raise typer.Exit(code=1) from None
            start()
            return
    stop()
    start()


@app.command()
def status() -> None:
    pid_file, _, _ = _command_runtime_files()
    pid = _legacy_pid(pid_file)
    if os.name == "nt" and not (pid and _process_exists(pid)):
        _native_status()
        return
    if not pid_file.exists():
        typer.echo("Trạng thái: stopped")
        return
    uptime = "-"
    try:
        started = datetime.fromisoformat(_started_file().read_text(encoding="ascii"))
        uptime = str(datetime.now(UTC) - started).split(".")[0]
    except (OSError, ValueError):
        pass
    typer.echo(
        f"Trạng thái: {'legacy_process' if pid and _process_exists(pid) else 'stale'}; "
        "readiness chưa kiểm tra; "
        f"PID: {pid or '-'}; uptime: {uptime}"
    )


@app.command("dashboard-code")
def dashboard_code() -> None:
    """Hiển thị mã đăng nhập dashboard local có hiệu lực trong 5 phút."""
    settings, store = get_settings(), SecretStore()
    if not settings.admin_api_enabled:
        typer.echo("Admin API đang tắt trong cấu hình.")
        raise typer.Exit(code=1)
    secret = ensure_dashboard_secret(store)
    expires = login_code_expires_at().astimezone()
    typer.echo(f"Dashboard: http://{settings.admin_api_host}:{settings.admin_api_port}")
    typer.echo(f"Mã đăng nhập: {dashboard_login_code(secret)}")
    typer.echo(f"Hết hạn lúc: {expires.strftime('%H:%M:%S %d/%m/%Y')}")


@app.command()
def logs(lines: int = typer.Option(100, min=1, max=5000)) -> None:
    path = ensure_runtime_dirs()["logs"] / "background.log"
    if not path.exists():
        typer.echo("Chưa có log.")
        return
    typer.echo("\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]))


@app.command()
def doctor() -> None:
    settings, store, paths = get_settings(), SecretStore(), ensure_runtime_dirs()
    typer.echo(
        f"Python: {sys.version.split()[0]} {'OK' if sys.version_info >= (3, 12) else 'FAIL'}"
    )
    if settings.storage_backend == "mysql":
        detection = detect_mysql(settings.database_host, settings.database_port)
        typer.echo(f"MySQL version: {detection.version or 'không xác định'}")
        typer.echo(
            "MySQL services: "
            + (
                ", ".join(f"{name}={state}" for name, state in detection.services)
                if detection.services
                else "không phát hiện"
            )
        )
        typer.echo(
            f"MySQL port: {'OK' if detection.port_open else 'FAIL'}; "
            f"process: {'OK' if detection.process_found else 'MISSING'}; "
            f"CLI: {'OK' if detection.cli_found else 'không có'}"
        )
    required = (
        "telegram_api_id",
        "telegram_api_hash",
        "telegram_phone",
        "telegram_bot_token",
    )
    if settings.storage_backend == "mysql":
        required = ("database_password", *required)
    for key in required:
        typer.echo(f"Credential {key}: {'OK' if store.get(key) else 'MISSING'}")
    typer.echo(
        f"Encrypted session: {'OK' if (paths['sessions'] / 'account.session.enc').exists() else 'MISSING'}"
    )
    if settings.storage_backend == "sqlite" or store.get("database_password"):

        async def ping() -> bool:
            db = make_database(settings, store)
            try:
                return await db.ping()
            finally:
                await db.close()

        try:
            typer.echo(
                f"Database ({settings.storage_backend}): {'OK' if asyncio.run(ping()) else 'FAIL'}"
            )
        except Exception:
            typer.echo(f"Database ({settings.storage_backend}): FAIL (storage_unavailable)")
    selected_secret = settings.ai_secret_name
    ai_ready = settings.ai_provider == "ollama" or bool(
        selected_secret and store.get(selected_secret)
    )
    typer.echo(f"AI provider: {settings.ai_provider}")
    typer.echo(
        "AI credential: "
        f"{'không cần (Ollama local)' if settings.ai_provider == 'ollama' else ('configured' if ai_ready else 'off/missing')}"
    )
    typer.echo(
        f"OpenAI key: {'configured' if store.get('openai_api_key') else 'missing'}; "
        f"OpenRouter key: {'configured' if store.get('openrouter_api_key') else 'missing'}"
    )
    typer.echo(f"CoinGecko key: {'configured' if store.get('coingecko_api_key') else 'missing'}")
    if settings.ai_provider == "ollama":
        try:
            tags_url = f"{settings.ollama_base_url.removesuffix('/v1')}/api/tags"
            response = httpx.get(tags_url, timeout=5)
            response.raise_for_status()
            installed = {
                str(item.get("name"))
                for item in response.json().get("models", [])
                if item.get("name")
            }
            chat_ready = settings.ollama_primary_model in installed
            embedding_ready = settings.ollama_embedding_model in installed
            typer.echo(
                f"Ollama API: OK; chat model: {'OK' if chat_ready else 'MISSING'}; "
                f"embedding model: {'OK' if embedding_ready else 'MISSING'}"
            )
        except Exception as exc:
            typer.echo(f"Ollama API: FAIL ({exc})")
    active_qdrant = settings.resolved_semantic_vector_path
    typer.echo(
        f"Qdrant active: {active_qdrant} ({'OK' if active_qdrant.exists() else 'chưa khởi tạo'})"
    )
    if settings.admin_api_enabled:
        health_url = f"http://{settings.admin_api_host}:{settings.admin_api_port}/healthz"
        try:
            response = httpx.get(health_url, timeout=2)
            response.raise_for_status()
            typer.echo(f"Admin API: OK ({health_url})")
        except Exception as exc:
            typer.echo(f"Admin API: FAIL ({exc})")
    else:
        typer.echo("Admin API: disabled")
    status()


@app.command()
def reconfigure() -> None:
    prompt_secrets(SecretStore())
    typer.echo("Đã lưu secret trong Windows Credential Manager. Chạy restart để áp dụng.")


@app.command("coingecko-key")
def coingecko_key() -> None:
    ready = configure_coingecko_key(SecretStore(), prompt_key=True)
    typer.echo(
        "CoinGecko credential: "
        f"{'đã cấu hình' if ready else 'chưa cấu hình'}. "
        "Chạy tg-assistant restart để áp dụng."
    )


@app.command("ai-provider")
def ai_provider(
    provider: str | None = typer.Argument(
        None,
        help="openai|openrouter|ollama|off; bỏ trống để chọn tương tác",
    ),
) -> None:
    settings = configure_ai_provider(
        SecretStore(),
        provider=provider,
        prompt_key=True,
    )
    secret_name = settings.ai_secret_name
    ready = settings.ai_provider == "ollama" or bool(secret_name and SecretStore().get(secret_name))
    typer.echo(
        f"Đã chọn {settings.ai_provider}; "
        f"credential: {'không cần (local)' if settings.ai_provider == 'ollama' else ('đã có' if ready else 'chưa có/tắt')}. "
        "Chạy tg-assistant stop, tg-assistant reindex, rồi tg-assistant start để áp dụng "
        "khi đổi giữa cloud và Ollama."
    )


@app.command()
@profile_writer(lambda: get_settings())
def sync(limit: int = typer.Option(1000, min=1, max=100000)) -> None:
    async def execute() -> None:
        settings, store, paths, policy = (
            get_settings(),
            SecretStore(),
            ensure_runtime_dirs(),
            PolicyEngine(),
        )
        db, user = make_database(settings, store), make_user_client(settings, store, policy, paths)
        try:
            me = await user.authenticate(store.get("telegram_phone") or "")
            async with db.session() as session:
                await user.discover_dialogs(session)
                policies = (
                    await session.scalars(
                        select(TelegramChatPolicy).where(TelegramChatPolicy.allowed.is_(True))
                    )
                ).all()
            total = 0
            for row in policies:
                try:
                    async with db.session() as session:
                        total += await user.sync_history(
                            session,
                            chat_id=row.chat_id,
                            actor_id=int(me.id),
                            owner_id=int(me.id),
                            limit=limit,
                        )
                except PermissionError:
                    pass
            typer.echo(f"Đã đồng bộ {total} tin mới, không đọc chat bị block.")
        finally:
            await user.close()
            await db.close()

    asyncio.run(execute())


@app.command()
@profile_writer(lambda: get_settings())
def reindex() -> None:
    async def execute() -> None:
        from types import SimpleNamespace

        from .ai.vector import LocalVectorStore
        from .runtime import Application, make_budget

        settings, store = get_settings(), SecretStore()
        db = make_database(settings, store)
        ai = make_local_embedding_engine(settings, store, make_budget(settings))
        vectors = None
        try:
            if not ai.available or not settings.enable_embeddings or settings.ai_provider == "off":
                typer.echo("Embedding disabled or selected provider unavailable.")
                return
            vectors = LocalVectorStore(
                settings.resolved_semantic_vector_path,
                vector_size=settings.embedding_profile.dimension,
                profile=settings.embedding_profile,
            )
            runtime = Application.__new__(Application)
            runtime.settings, runtime.database, runtime.policy = settings, db, PolicyEngine()
            runtime.embedding_ai, runtime.rag = ai, SimpleNamespace(vectors=vectors)
            async with db.session() as session:
                allowed = (
                    select(TelegramChatPermission.chat_id)
                    .join(
                        TelegramChatPolicy,
                        TelegramChatPolicy.chat_id == TelegramChatPermission.chat_id,
                    )
                    .where(
                        TelegramChatPolicy.allowed.is_(True),
                        TelegramChatPermission.permission == PermissionName.SEARCH_MESSAGES.value,
                        TelegramChatPermission.enabled.is_(True),
                    )
                )
                rows = list(
                    (
                        await session.scalars(
                            select(TelegramMessage).where(
                                TelegramMessage.chat_id.in_(allowed),
                                TelegramMessage.is_deleted.is_(False),
                                TelegramMessage.text.is_not(None),
                            )
                        )
                    ).all()
                )
                result = await runtime._index_knowledge_rows(
                    session, rows, permission=PermissionName.SEARCH_MESSAGES
                )
                typer.echo(f"Indexed {result.indexed} messages; reused {result.reused}.")
        finally:
            if vectors is not None:
                vectors.close()
            await ai.close()
            await db.close()

    asyncio.run(execute())


def _portable_storage() -> StorageService:
    """Open the explicit profile without launching writers or prompting secrets."""
    settings = get_settings()
    storage = StorageService(settings, SecretStore())
    from .contracts import PublicProfile

    database = storage.open(PublicProfile(
        profile_id=settings.profile_id,
        owner_id=None,
        storage_backend=settings.storage_backend,
        setup_stage="storage_ready",
        version=1,
    ))
    try:
        asyncio.run(database.close())
    except BaseException:
        storage.fence.close()
        raise
    return storage


@app.command()
def backup(output: Path | None = None) -> None:
    """Create a portable SQLite/MySQL snapshot without external SQL tools."""
    storage = None
    try:
        storage = _portable_storage()
        output = output or storage.settings.data_dir / "backups" / (
            f"backup-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}.zip"
        )
        manifest = storage.backup(output)
        typer.echo(
            f"Đã tạo bản sao lưu {manifest.backend}; schema {manifest.schema_revision}. "
            "Thông tin đăng nhập và phiên Telegram không nằm trong bản sao lưu."
        )
    except MaintenanceBusy:
        typer.echo("Đang bảo trì; maintenance_in_progress. Thử lại sau khi bảo trì kết thúc.")
        raise typer.Exit(code=1) from None
    except Exception:
        typer.echo("Không thể tạo bản sao lưu; backup_failed. Kiểm tra quyền lưu và tệp đích.")
        raise typer.Exit(code=1) from None
    finally:
        if storage is not None and storage.fence is not None:
            storage.fence.close()


@app.command()
def restore(archive: Path) -> None:
    """Confirm first, then restore only while owning the actual writer fence."""
    from .services.backup import BackupError

    if not typer.confirm(
        "Khôi phục sẽ thay đổi dữ liệu và tạo bản sao lưu hiện trạng trước. "
        "Hãy dừng ứng dụng trước khi tiếp tục. Tiếp tục?", default=False,
    ):
        raise typer.Abort()
    storage = None
    lease = None
    try:
        storage = _portable_storage()
        lease = storage.fence.acquire("backup_restore", lease_seconds=1800, timeout=0)
        try:
            report = storage.restore(archive, lease)
        except BackupError as error:
            if error.original_preserved:
                storage.fence.release(lease)
                lease = None
            raise
        storage.fence.release(lease)
        lease = None
        typer.echo(
            f"Đã khôi phục dữ liệu {report.backend}; schema {report.schema_revision}. "
            "Ứng dụng cần khôi phục chỉ mục AI trước khi báo sẵn sàng. "
            "Bản sao lưu trước khôi phục đã được giữ lại."
        )
    except MaintenanceBusy:
        typer.echo(
            "Chưa thể khôi phục; maintenance_in_progress. "
            "Dừng ứng dụng và các tác vụ ghi, rồi kiểm tra lại trạng thái bảo trì."
        )
        raise typer.Exit(code=1) from None
    except Exception:
        typer.echo(
            "Không thể khôi phục; restore_failed. "
            "Kiểm tra bản sao lưu và trạng thái bảo trì trước khi thử lại."
        )
        raise typer.Exit(code=1) from None
    finally:
        # Failure never clears a persistent fence without verified preservation.
        if storage is not None and storage.fence is not None:
            storage.fence.close()


@app.command()
def autostart(action: str = typer.Argument(..., help="enable|disable|status")) -> None:
    """Quản lý tùy chọn tự chạy khi đăng nhập Windows."""
    if os.name != "nt":
        raise typer.BadParameter("Autostart Task Scheduler chỉ hỗ trợ Windows.")
    schtasks = shutil.which("schtasks")
    cmd = shutil.which("cmd")
    if not schtasks or not cmd:
        raise typer.BadParameter("Không tìm thấy schtasks.exe/cmd.exe.")
    task_name = "TelegramAIPersonalAssistant"
    if action == "status":
        result = subprocess.run(
            [schtasks, "/Query", "/TN", task_name],
            capture_output=True,
            text=True,
            check=False,
        )
        typer.echo("enabled" if result.returncode == 0 else "disabled")
        return
    if action == "enable":
        if not typer.confirm(
            "Bật tự khởi động Telegram AI Personal Assistant khi đăng nhập Windows?",
            default=False,
        ):
            raise typer.Abort()
        start_script = project_root() / "start.bat"
        task_command = f'"{cmd}" /c ""{start_script}""'
        subprocess.run(
            [
                schtasks,
                "/Create",
                "/F",
                "/SC",
                "ONLOGON",
                "/TN",
                task_name,
                "/TR",
                task_command,
            ],
            check=True,
        )
        typer.echo("Đã bật autostart.")
        return
    if action == "disable":
        if not typer.confirm("Tắt autostart?", default=True):
            raise typer.Abort()
        subprocess.run([schtasks, "/Delete", "/F", "/TN", task_name], check=True)
        typer.echo("Đã tắt autostart.")
        return
    raise typer.BadParameter("action phải là enable|disable|status")


@app.command()
def logout() -> None:
    if typer.prompt("Gõ LOGOUT để thu hồi Telegram session") != "LOGOUT":
        raise typer.Abort()

    async def execute() -> None:
        settings, store, paths, policy = (
            get_settings(),
            SecretStore(),
            ensure_runtime_dirs(),
            PolicyEngine(),
        )
        user = make_user_client(settings, store, policy, paths)
        try:
            await user.client.connect()
            await user.client.log_out()
        finally:
            await user.close()

    asyncio.run(execute())
    paths = ensure_runtime_dirs()
    (paths["sessions"] / "account.session.enc").unlink(missing_ok=True)
    SecretStore().delete("session_encryption_key")
    typer.echo("Đã logout và xóa session cục bộ.")


@app.command()
def uninstall() -> None:
    typer.echo(
        "Mã nguồn không bị xóa tự động. Dùng 'tg-assistant purge' nếu muốn xóa dữ liệu/credential, sau đó xóa .venv thủ công."
    )


@app.command()
def purge() -> None:
    if (
        typer.prompt("Gõ PURGE-ALL-DATA để xóa dữ liệu cục bộ và mọi credential")
        != "PURGE-ALL-DATA"
    ):
        raise typer.Abort()
    stop()
    store = SecretStore()
    for key in (
        "database_password",
        "telegram_api_id",
        "telegram_api_hash",
        "telegram_phone",
        "telegram_bot_token",
        "openai_api_key",
        "openrouter_api_key",
        "coingecko_api_key",
        "session_encryption_key",
    ):
        store.delete(key)
    root = ensure_runtime_dirs()["data"].resolve()
    if root == Path.home().resolve() or len(root.parts) < 3:
        raise RuntimeError("Từ chối xóa đường dẫn quá rộng")
    if not (root / ".tg-assistant-data").is_file():
        raise RuntimeError("Từ chối purge: thiếu marker thư mục dữ liệu ứng dụng.")
    if typer.prompt(f"Gõ chính xác đường dẫn sau để xác nhận lần cuối:\n{root}") != str(root):
        raise typer.Abort()
    shutil.rmtree(root)
    typer.echo(
        f"Đã xóa dữ liệu cục bộ {root} và credential; database MySQL không bị drop tự động để tránh mất dữ liệu ngoài ý muốn."
    )


if __name__ == "__main__":
    app()
