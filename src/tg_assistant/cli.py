from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import typer
from sqlalchemy import select

from . import __version__
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
from .services.maintenance import profile_maintenance, profile_writer
from .services.storage import StorageService
from .setup.mysql import detect_mysql, find_mysql_tool
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


def _runtime_files() -> tuple[Path, Path, Path]:
    root = ensure_runtime_dirs()["data"]
    return root / "assistant.pid", root / "assistant.lock", root / "stop.request"


def _started_file() -> Path:
    return ensure_runtime_dirs()["data"] / "started_at"


def _process_exists(pid: int) -> bool:
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

    def __enter__(self) -> InstanceLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+")
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise RuntimeError("Ứng dụng đã chạy ở instance khác") from exc
        return self

    def __exit__(self, *_: object) -> None:
        if self.handle:
            try:
                if os.name == "nt":
                    import msvcrt

                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.handle, fcntl.LOCK_UN)
            finally:
                self.handle.close()


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
    """Thiết lập nếu cần rồi chạy nền, không cần Administrator."""
    pid_file, lock_file, stop_file = _runtime_files()
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
        except ValueError:
            pid = 0
        if pid and _process_exists(pid):
            typer.echo(f"Đang chạy (PID {pid}).")
            settings = get_settings()
            if settings.admin_api_enabled:
                typer.echo(
                    f"Dashboard local: http://{settings.admin_api_host}:"
                    f"{settings.admin_api_port} "
                    "(lấy mã bằng: .\\.venv\\Scripts\\tg-assistant.exe dashboard-code)"
                )
            return
        pid_file.unlink(missing_ok=True)
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
    pid_file, _, stop_file = _runtime_files()
    if not pid_file.exists():
        typer.echo("Không chạy.")
        return
    pid = int(pid_file.read_text().strip())
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
    stop()
    start()


@app.command()
def status() -> None:
    pid_file, _, _ = _runtime_files()
    if not pid_file.exists():
        typer.echo("Trạng thái: stopped")
        return
    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        pid = 0
    uptime = "-"
    try:
        started = datetime.fromisoformat(_started_file().read_text(encoding="ascii"))
        uptime = str(datetime.now(UTC) - started).split(".")[0]
    except (OSError, ValueError):
        pass
    typer.echo(
        f"Trạng thái: {'running' if pid and _process_exists(pid) else 'stale'}; "
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


@app.command()
def backup(output: Path | None = None) -> None:
    settings, store, paths = get_settings(), SecretStore(), ensure_runtime_dirs()
    dump = find_mysql_tool("mysqldump")
    if not dump:
        raise typer.BadParameter("Không tìm thấy mysqldump")
    output = output or paths["backups"] / f"backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    if output.exists():
        raise typer.BadParameter(f"Không ghi đè backup đã tồn tại: {output}")
    with tempfile.NamedTemporaryFile(
        prefix="tg-assistant-backup-", suffix=".sql", dir=paths["backups"], delete=False
    ) as temp_file:
        sql_path = Path(temp_file.name)
    env = os.environ.copy()
    env["MYSQL_PWD"] = store.get("database_password") or ""
    try:
        with sql_path.open("wb") as stream:
            subprocess.run(
                [
                    dump,
                    "--host",
                    settings.database_host,
                    "--port",
                    str(settings.database_port),
                    "--user",
                    settings.database_user,
                    "--single-transaction",
                    "--routines",
                    settings.database_name,
                ],
                stdout=stream,
                env=env,
                check=True,
            )
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(sql_path, "database.sql")
            archive.writestr(
                "manifest.txt",
                f"version={__version__}\nschema=0001\ncreated_at={datetime.now(UTC).isoformat()}\n",
            )
            safe_config = {
                "TG_ASSISTANT_TIMEZONE": settings.timezone,
                "TG_ASSISTANT_LOG_LEVEL": settings.log_level,
                "TG_ASSISTANT_ADMIN_API_ENABLED": settings.admin_api_enabled,
                "TG_ASSISTANT_ADMIN_API_HOST": settings.admin_api_host,
                "TG_ASSISTANT_ADMIN_API_PORT": settings.admin_api_port,
                "TG_ASSISTANT_ADMIN_SESSION_MINUTES": settings.admin_session_minutes,
                "TG_ASSISTANT_DATABASE_HOST": settings.database_host,
                "TG_ASSISTANT_DATABASE_PORT": settings.database_port,
                "TG_ASSISTANT_DATABASE_NAME": settings.database_name,
                "TG_ASSISTANT_DATABASE_USER": settings.database_user,
                "TG_ASSISTANT_MEDIA_DOWNLOAD_ENABLED": settings.media_download_enabled,
                "TG_ASSISTANT_MEDIA_MAX_SIZE_MB": settings.media_max_size_mb,
                "TG_ASSISTANT_DAILY_AI_BUDGET_USD": settings.daily_ai_budget_usd,
                "TG_ASSISTANT_MONTHLY_AI_BUDGET_USD": settings.monthly_ai_budget_usd,
                "TG_ASSISTANT_MAX_AI_REQUESTS_PER_MINUTE": (settings.max_ai_requests_per_minute),
                "TG_ASSISTANT_LEARNING_JOB_INTERVAL_SECONDS": (
                    settings.learning_job_interval_seconds
                ),
                "TG_ASSISTANT_AI_PROVIDER": settings.ai_provider,
                "TG_ASSISTANT_OPENAI_BASE_URL": settings.openai_base_url,
                "TG_ASSISTANT_OPENAI_PRIMARY_MODEL": settings.openai_primary_model,
                "TG_ASSISTANT_OPENAI_FAST_MODEL": settings.openai_fast_model,
                "TG_ASSISTANT_OPENAI_DEEP_MODEL": settings.openai_deep_model,
                "TG_ASSISTANT_OPENAI_EMBEDDING_MODEL": settings.openai_embedding_model,
                "TG_ASSISTANT_OPENROUTER_BASE_URL": settings.openrouter_base_url,
                "TG_ASSISTANT_OPENROUTER_PRIMARY_MODEL": settings.openrouter_primary_model,
                "TG_ASSISTANT_OPENROUTER_EMBEDDING_MODEL": (settings.openrouter_embedding_model),
                "TG_ASSISTANT_OLLAMA_BASE_URL": settings.ollama_base_url,
                "TG_ASSISTANT_OLLAMA_PRIMARY_MODEL": settings.ollama_primary_model,
                "TG_ASSISTANT_OLLAMA_EMBEDDING_MODEL": settings.ollama_embedding_model,
                "TG_ASSISTANT_OLLAMA_VECTOR_SIZE": settings.ollama_vector_size,
            }
            archive.writestr(
                "config.env",
                "\n".join(f"{key}={value}" for key, value in safe_config.items()) + "\n",
            )
            qdrant_path = settings.resolved_semantic_vector_path
            metadata = {
                "configured": str(qdrant_path),
                "present": qdrant_path.exists(),
                "file_count": (
                    sum(1 for item in qdrant_path.rglob("*") if item.is_file())
                    if qdrant_path.exists()
                    else 0
                ),
            }
            archive.writestr(
                "qdrant_metadata.json",
                json.dumps(metadata, ensure_ascii=False, indent=2),
            )
        typer.echo(f"Backup không chứa secret: {output}")
    finally:
        env["MYSQL_PWD"] = ""
        sql_path.unlink(missing_ok=True)


@app.command()
@profile_maintenance(lambda: get_settings())
def restore(archive: Path) -> None:
    if not archive.is_file() or not zipfile.is_zipfile(archive):
        raise typer.BadParameter("Backup không hợp lệ")
    with zipfile.ZipFile(archive) as source:
        if "database.sql" not in source.namelist() or "manifest.txt" not in source.namelist():
            raise typer.BadParameter("Thiếu manifest/database.sql")
        manifest = source.read("manifest.txt").decode("utf-8")
        if "schema=0001" not in manifest:
            raise typer.BadParameter("Phiên bản schema không tương thích")
        if source.getinfo("database.sql").file_size > 5 * 1024 * 1024 * 1024:
            raise typer.BadParameter("Database backup vượt giới hạn an toàn 5 GiB.")
    if not typer.confirm(
        "Restore sẽ thay đổi database. Ứng dụng sẽ tự tạo backup hiện trạng trước. Tiếp tục?",
        default=False,
    ):
        raise typer.Abort()
    settings, store, paths = get_settings(), SecretStore(), ensure_runtime_dirs()
    mysql = find_mysql_tool("mysql")
    if not mysql:
        raise typer.BadParameter("Không tìm thấy mysql CLI")
    pre_restore = paths["backups"] / (f"pre-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip")
    backup(pre_restore)
    with tempfile.NamedTemporaryFile(
        prefix="tg-assistant-restore-", suffix=".sql", dir=paths["backups"], delete=False
    ) as temp_file:
        temp = Path(temp_file.name)
    env = os.environ.copy()
    env["MYSQL_PWD"] = store.get("database_password") or ""
    try:
        with zipfile.ZipFile(archive) as source:
            with source.open("database.sql") as source_sql, temp.open("wb") as target_sql:
                shutil.copyfileobj(source_sql, target_sql, length=1024 * 1024)
        with temp.open("rb") as stream:
            subprocess.run(
                [
                    mysql,
                    "--host",
                    settings.database_host,
                    "--port",
                    str(settings.database_port),
                    "--user",
                    settings.database_user,
                    settings.database_name,
                ],
                stdin=stream,
                env=env,
                check=True,
            )
        typer.echo("Restore hoàn tất.")
    finally:
        env["MYSQL_PWD"] = ""
        temp.unlink(missing_ok=True)


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
