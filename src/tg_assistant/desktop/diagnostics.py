"""Read-only bundle checks; never load settings, credentials or a user profile."""

import ctypes
import importlib
import json
import sys
from importlib.resources import files
from pathlib import Path

from ..paths import resource_path


def run_diagnostics(destination: str) -> int:
    checks = {}
    errors = {}
    frozen = bool(getattr(sys, "frozen", False))
    for name in (
            "aiogram", "aiosqlite", "alembic", "apscheduler", "cryptography",
            "fastapi", "httpx", "keyring.backends.Windows", "openai", "psutil",
            "qdrant_client", "sqlalchemy.dialects.sqlite.aiosqlite", "structlog",
            "telethon", "uvicorn", "qrcode", "win32crypt", "win32pipe",
            "tg_assistant.desktop.worker",
        ):
        try:
            importlib.import_module(name)
            checks["import:" + name] = True
        except Exception as error:
            checks["import:" + name] = False
            errors["import:" + name] = type(error).__name__
    try:
        required = (
            "README.md", "alembic/env.py", "dashboard-prototype/dist/client/index.html",
            "dashboard-prototype/public/auth-bootstrap.js",
            "dashboard-prototype/public/fonts/PeterObscure.ttf",
            "dashboard-prototype/public/fonts/DarleySans-Regular.otf",
        )
        checks["assets"] = all(resource_path(name).is_file() for name in required)
        from alembic.script import ScriptDirectory

        checks["migrations"] = bool(ScriptDirectory(str(resource_path("alembic"))).get_current_head())
        json.loads(files("tg_assistant.desktop").joinpath("design_tokens.json").read_text())
        checks["design_resources"] = True
    except Exception:
        checks["assets"] = False
    try:
        from PySide6.QtCore import QLibraryInfo
        from PySide6.QtWidgets import QApplication

        plugin = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)) / "platforms/qwindows.dll"
        ctypes.WinDLL(str(plugin))
        application = QApplication.instance() or QApplication(["bundle-diagnostics"])
        checks["qt_platform"] = plugin.is_file() and (not frozen or application.platformName() == "windows")
        checks["native_runtime"] = not frozen or all(
            list(Path(sys._MEIPASS).rglob(name))
            for name in ("python312.dll", "Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "VCRUNTIME140.dll")
        )
        qt_version = QLibraryInfo.version().toString()
    except Exception as error:
        checks["qt_platform"] = False
        errors["qt_platform"] = type(error).__name__
        qt_version = None
    report = {
        "schema_version": 1, "status": "ok" if checks and all(checks.values()) else "failed",
        "frozen": frozen, "python": ".".join(map(str, sys.version_info[:3])),
        "qt": qt_version, "checks": checks, "errors": errors,
    }
    try:
        # Exclusive creation prevents an operator typo from overwriting existing data.
        with Path(destination).open("x", encoding="utf-8") as output:
            json.dump(report, output, indent=2, sort_keys=True)
            output.write("\n")
    except OSError:
        return 2
    return 0 if report["status"] == "ok" else 1
