from __future__ import annotations

import re
import shutil
import socket
import subprocess
from dataclasses import dataclass
from os import environ
from pathlib import Path

import pymysql

IDENTIFIER = re.compile(r"^[A-Za-z0-9_]{1,64}$")
CORE_TABLES = {
    "telegram_accounts",
    "telegram_chats",
    "telegram_messages",
    "telegram_chat_policies",
    "telegram_chat_permissions",
    "pending_actions",
    "tasks",
    "ai_memories",
    "audit_logs",
    "background_jobs",
}


@dataclass(slots=True)
class MySQLDetection:
    cli_found: bool
    version: str | None
    services: list[tuple[str, str]]
    port_open: bool
    process_found: bool


@dataclass(slots=True)
class MySQLProvisioningState:
    version: str
    database_exists: bool
    app_user_exists: bool
    table_count: int = 0
    has_alembic_version: bool = False


class ExistingAppUserError(RuntimeError):
    pass


def find_mysql_tool(name: str) -> str | None:
    executable_name = f"{name}.exe" if not name.lower().endswith(".exe") else name
    direct = shutil.which(executable_name) or shutil.which(name)
    if direct:
        return direct
    roots = {
        Path(environ.get("PROGRAMFILES", r"C:\Program Files")),
        Path(environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")),
    }
    patterns = (
        f"MySQL/MySQL Server */bin/{executable_name}",
        f"MariaDB */bin/{executable_name}",
    )
    for root in roots:
        for pattern in patterns:
            matches = sorted(root.glob(pattern), reverse=True)
            if matches:
                return str(matches[0])
    return None


def detect_mysql(host: str = "127.0.0.1", port: int = 3306) -> MySQLDetection:
    executable = find_mysql_tool("mysql")
    version = None
    if executable:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=5, check=False
        )
        version = result.stdout.strip() or result.stderr.strip()
    services: list[tuple[str, str]] = []
    process_found = False
    powershell = shutil.which("powershell")
    if powershell:
        script = "Get-Service | Where-Object {$_.Name -match 'mysql|maria'} | ForEach-Object {\"$($_.Name)|$($_.Status)\"}"
        result = subprocess.run(
            [powershell, "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        services = [tuple(line.split("|", 1)) for line in result.stdout.splitlines() if "|" in line]
        process_result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-Command",
                "if (Get-Process -Name mysqld -ErrorAction SilentlyContinue) { 'yes' }",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        process_found = process_result.stdout.strip() == "yes"
    try:
        with socket.create_connection((host, port), timeout=1):
            port_open = True
    except OSError:
        port_open = False
    return MySQLDetection(bool(executable), version, services, port_open, process_found)


def _identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ValueError("Tên database/user chỉ được chứa chữ, số và dấu gạch dưới")
    return value


def inspect_provisioning(
    *,
    host: str,
    port: int,
    admin_user: str,
    admin_password: str,
    database: str,
    app_user: str,
) -> MySQLProvisioningState:
    database, app_user = _identifier(database), _identifier(app_user)
    connection = pymysql.connect(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        charset="utf8mb4",
        connect_timeout=10,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION()")
            version = str(cursor.fetchone()[0])
            cursor.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name=%s", (database,)
            )
            database_exists = cursor.fetchone() is not None
            cursor.execute(
                "SELECT 1 FROM mysql.user WHERE user=%s AND host='127.0.0.1'", (app_user,)
            )
            app_user_exists = cursor.fetchone() is not None
            table_count = 0
            has_alembic_version = False
            if database_exists:
                cursor.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema=%s",
                    (database,),
                )
                table_names = {str(row[0]) for row in cursor.fetchall()}
                table_count = len(table_names)
                has_alembic_version = "alembic_version" in table_names
        return MySQLProvisioningState(
            version,
            database_exists,
            app_user_exists,
            table_count,
            has_alembic_version,
        )
    finally:
        connection.close()


def verify_app_connection(
    *, host: str, port: int, database: str, app_user: str, app_password: str
) -> str:
    connection = pymysql.connect(
        host=host,
        port=port,
        user=app_user,
        password=app_password,
        database=database,
        charset="utf8mb4",
        connect_timeout=10,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION()")
            return str(cursor.fetchone()[0])
    finally:
        connection.close()


def verify_migrated_database(
    *, host: str, port: int, database: str, app_user: str, app_password: str
) -> None:
    connection = pymysql.connect(
        host=host,
        port=port,
        user=app_user,
        password=app_password,
        database=database,
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=10,
    )
    probe_key = "__tg_assistant_migration_probe__"
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema=%s",
                (database,),
            )
            table_names = {str(row[0]) for row in cursor.fetchall()}
            missing = CORE_TABLES - table_names
            if missing:
                raise RuntimeError(f"Migration thiếu bảng: {', '.join(sorted(missing))}")
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.statistics "
                "WHERE table_schema=%s AND table_name='telegram_messages' "
                "AND index_name='uq_telegram_messages_chat_message'",
                (database,),
            )
            if int(cursor.fetchone()[0]) < 2:
                raise RuntimeError("Thiếu unique index chat_id + message_id.")
            cursor.execute(
                "INSERT INTO app_settings (`key`, `value`, description) "
                "VALUES (%s, JSON_OBJECT('text', %s), %s)",
                (probe_key, "Tiếng Việt: kiểm tra", "transaction rollback probe"),
            )
            cursor.execute(
                "SELECT JSON_UNQUOTE(JSON_EXTRACT(`value`, '$.text')) "
                "FROM app_settings WHERE `key`=%s",
                (probe_key,),
            )
            if cursor.fetchone()[0] != "Tiếng Việt: kiểm tra":
                raise RuntimeError("Kiểm tra utf8mb4 tiếng Việt thất bại.")
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM app_settings WHERE `key`=%s", (probe_key,))
            if cursor.fetchone() is not None:
                raise RuntimeError("Kiểm tra transaction rollback thất bại.")
    finally:
        connection.rollback()
        connection.close()


def provision_database(
    *,
    host: str,
    port: int,
    admin_user: str,
    admin_password: str,
    database: str,
    app_user: str,
    app_password: str,
    allow_existing_database: bool = False,
) -> str:
    database, app_user = _identifier(database), _identifier(app_user)
    connection = pymysql.connect(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=10,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION()")
            version = str(cursor.fetchone()[0])
            cursor.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name=%s", (database,)
            )
            if cursor.fetchone() is not None and not allow_existing_database:
                raise FileExistsError(
                    f"Database {database} đã tồn tại; cần xác nhận rõ trước khi dùng."
                )
            cursor.execute(
                "SELECT 1 FROM mysql.user WHERE user=%s AND host='127.0.0.1'", (app_user,)
            )
            if cursor.fetchone() is not None:
                raise ExistingAppUserError(
                    f"User {app_user}@127.0.0.1 đã tồn tại; ứng dụng không tự đổi mật khẩu."
                )
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
            cursor.execute(
                f"CREATE USER `{app_user}`@'127.0.0.1' IDENTIFIED BY %s", (app_password,)
            )
            privileges = "SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX, REFERENCES, CREATE TEMPORARY TABLES"
            cursor.execute(f"GRANT {privileges} ON `{database}`.* TO `{app_user}`@'127.0.0.1'")
        connection.commit()
        return version
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
