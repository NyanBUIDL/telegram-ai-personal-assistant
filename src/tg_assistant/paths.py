from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "TelegramAIPersonalAssistant"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def user_data_root() -> Path:
    configured = os.getenv("TG_ASSISTANT_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    local = os.getenv("LOCALAPPDATA")
    return (Path(local) if local else Path.home() / ".local" / "share") / APP_NAME


def ensure_runtime_dirs() -> dict[str, Path]:
    root = user_data_root()
    paths = {
        "data": root,
        "logs": root / "logs",
        "qdrant": root / "qdrant",
        "downloads": root / "downloads",
        "backups": root / "backups",
        "sessions": root / "sessions",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    (root / ".tg-assistant-data").touch(exist_ok=True)
    return paths
