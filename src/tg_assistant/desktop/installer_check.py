"""Installer-only inspection; never initialize a profile, Qt or credentials."""

import os
import stat

from .instance import (
    AlreadyRunning,
    InstanceGuard,
    _assert_owned_path,
    native_control_directory,
)

UNSAFE, UNSUPPORTED, BUSY = 20, 21, 22


def _safe_tree(root):
    for path in (root, *root.parents):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OSError("unsafe_install_profile")
    pending = [root] if root.exists() else []
    while pending:
        path = pending.pop()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OSError("unsafe_install_profile")
        _assert_owned_path(path)
        if stat.S_ISDIR(info.st_mode):
            pending.extend(path.iterdir())
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError("unsafe_install_profile")


def main(*, prepare=False):
    """Return fixed exit codes only. A successful check does not claim fencing."""
    try:
        directory = native_control_directory()
        root = directory.parent
        _safe_tree(root)
        _safe_tree(root.parent / "Programs/TelegramAIPersonalAssistant")
        configured = os.environ.get("TG_ASSISTANT_DATA_DIR")
        if configured and os.path.normcase(os.path.abspath(configured)) != os.path.normcase(str(root)):
            return UNSUPPORTED
        if os.environ.get("TG_ASSISTANT_STORAGE_BACKEND", "sqlite") != "sqlite":
            return UNSUPPORTED
        path_overrides = ("qdrant_path", "ollama_qdrant_path", "dashboard_dist_path")
        if any(os.environ.get("TG_ASSISTANT_" + name.upper()) for name in path_overrides):
            return UNSUPPORTED
        config = root / "config/settings.json"
        if config.exists():
            if config.stat().st_size > 1024 * 1024:
                return UNSUPPORTED
            # This reads non-secret JSON only; no Settings instance or .env load.
            from ..config import _read_config

            settings = _read_config(config)
            if any(settings.get(name) is not None for name in path_overrides):
                return UNSUPPORTED
            data_dir = settings.get("data_dir")
            if data_dir is not None and (
                not isinstance(data_dir, str)
                or os.path.normcase(os.path.abspath(data_dir)) != os.path.normcase(str(root))
            ):
                return UNSUPPORTED
        if prepare:
            with InstanceGuard(directory):
                # Runtime's FileLock creates the empty file; protect its owner too.
                from .instance import _secure_owned_path

                _secure_owned_path(directory / "runtime.lock", directory=False)
        return 0
    except AlreadyRunning:
        return BUSY
    except ValueError:
        return UNSUPPORTED
    except (OSError, RuntimeError, TypeError):
        return UNSAFE
