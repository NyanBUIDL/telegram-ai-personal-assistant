from __future__ import annotations

import getpass
import secrets
import string
import subprocess
import sys
from pathlib import Path

import typer

from ..config import get_settings
from ..paths import project_root
from ..security import SecretStore
from .mysql import (
    detect_mysql,
    inspect_provisioning,
    provision_database,
    verify_app_connection,
    verify_migrated_database,
)


def _password(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits + "-_!@#%"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _save_database_config(
    path: Path, *, host: str, port: int, database: str, app_user: str
) -> None:
    updates = {
        "TG_ASSISTANT_DATABASE_HOST": host,
        "TG_ASSISTANT_DATABASE_PORT": str(port),
        "TG_ASSISTANT_DATABASE_NAME": database,
        "TG_ASSISTANT_DATABASE_USER": app_user,
    }
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    kept = [line for line in existing if not any(line.startswith(f"{key}=") for key in updates)]
    content = "\n".join([*kept, *(f"{key}={value}" for key, value in updates.items())])
    path.write_text(content.strip() + "\n", encoding="utf-8")


def run_setup() -> None:
    settings, store = get_settings(), SecretStore()
    typer.echo("Telegram AI Personal Assistant\n")
    typer.echo("[1/6] Kiểm tra môi trường")
    detection = detect_mysql(settings.database_host, settings.database_port)
    typer.echo(f"MySQL CLI: {'có' if detection.cli_found else 'không'}")
    typer.echo(f"Phiên bản: {detection.version or 'không xác định'}")
    typer.echo(f"Tiến trình mysqld: {'có' if detection.process_found else 'không'}")
    typer.echo(
        f"Cổng {settings.database_port}: {'đang mở' if detection.port_open else 'không kết nối được'}"
    )
    if detection.services:
        typer.echo(
            "Service: " + ", ".join(f"{name} ({status})" for name, status in detection.services)
        )
    if not detection.port_open:
        typer.echo(
            "Chưa có MySQL hoạt động. Cài MySQL Community Server 8.0/LTS từ mysql.com hoặc cấu hình kết nối rồi chạy lại."
        )
        raise typer.Exit(2)
    typer.echo("[2/6] Thiết lập MySQL")
    admin_user = typer.prompt("Tài khoản quản trị", default="root")
    admin_password = getpass.getpass("Mật khẩu quản trị (không lưu): ")
    database = typer.prompt("Database", default=settings.database_name)
    app_user = typer.prompt("User ứng dụng", default=settings.database_user)
    try:
        state = inspect_provisioning(
            host=settings.database_host,
            port=settings.database_port,
            admin_user=admin_user,
            admin_password=admin_password,
            database=database,
            app_user=app_user,
        )
        if state.database_exists and not typer.confirm(
            f"Database {database} đã tồn tại ({state.table_count} bảng; "
            f"Alembic: {'có' if state.has_alembic_version else 'chưa có'}). "
            "Xác nhận đây là database ứng dụng; không xóa/ghi đè và chỉ nâng cấp schema?",
            default=False,
        ):
            raise typer.Abort()
        if state.app_user_exists:
            if not state.database_exists:
                raise typer.BadParameter(
                    f"User {app_user}@127.0.0.1 đã tồn tại nhưng database chưa có. "
                    "Hãy chọn tên user ứng dụng khác."
                )
            app_password = getpass.getpass(
                "Mật khẩu hiện có của user ứng dụng (không đổi, không lưu root): "
            )
            version = verify_app_connection(
                host=settings.database_host,
                port=settings.database_port,
                database=database,
                app_user=app_user,
                app_password=app_password,
            )
        else:
            if not typer.confirm(
                "Tạo database/user riêng và chỉ cấp quyền trong database này?",
                default=True,
            ):
                raise typer.Abort()
            app_password = _password()
            version = provision_database(
                host=settings.database_host,
                port=settings.database_port,
                admin_user=admin_user,
                admin_password=admin_password,
                database=database,
                app_user=app_user,
                app_password=app_password,
                allow_existing_database=state.database_exists,
            )
    finally:
        admin_password = ""  # noqa: S105
    store.set("database_password", app_password)
    app_password = ""  # noqa: S105
    _save_database_config(
        project_root() / ".env",
        host=settings.database_host,
        port=settings.database_port,
        database=database,
        app_user=app_user,
    )
    get_settings.cache_clear()
    typer.echo(f"MySQL {version}: đã tạo database/user.")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    verify_migrated_database(
        host=settings.database_host,
        port=settings.database_port,
        database=database,
        app_user=app_user,
        app_password=store.get("database_password") or "",
    )
    typer.echo("Đã kiểm tra bảng, unique index, tiếng Việt và transaction rollback.")
    typer.echo(
        "[3/6] Telegram, [4/6] Bot, [5/6] nhà cung cấp AI và [6/6] pairing "
        "sẽ được hỏi an toàn khi chạy lần đầu."
    )
    typer.echo("Sau thiết lập, chạy `tg-assistant doctor` để kiểm tra trạng thái.")
