from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

APP_NAME = "TelegramAIPersonalAssistant"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def user_data_root() -> Path:
    configured = os.getenv("TG_ASSISTANT_DATA_DIR")
    if configured:
        return resolve_data_path(Path(configured))
    return default_user_data_root()


def default_user_data_root() -> Path:
    local = os.getenv("LOCALAPPDATA")
    return ((Path(local) if local else Path.home() / ".local" / "share") / APP_NAME).resolve()


def resolve_data_path(path: Path) -> Path:
    """Relative overrides belong to the per-user app root, never the launch cwd."""
    path = path.expanduser()
    if not path.is_absolute():
        local = os.getenv("LOCALAPPDATA")
        base = (Path(local) if local else Path.home() / ".local" / "share") / APP_NAME
        path = base / path
    return path.resolve()


def resource_path(*parts: str) -> Path:
    """Resolve installed/bundled read-only assets separately from profile data."""
    relative = Path(*parts)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise ValueError("Resource path must remain inside the asset directory")
    root = Path(getattr(sys, "_MEIPASS", project_root()))
    return root / relative


def current_user_sid() -> str:
    """Read the process token's Windows SID, without consulting credential stores."""
    if os.name != "nt":
        return f"uid:{os.getuid()}"
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    security.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    security.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    security.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    token = wintypes.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise OSError("Cannot establish profile SID ownership")
    try:
        size = wintypes.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not size.value:
            raise OSError("Cannot establish profile SID ownership")
        buffer = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise OSError("Cannot establish profile SID ownership")
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        sid_string = wintypes.LPWSTR()
        if not security.ConvertSidToStringSidW(sid, ctypes.byref(sid_string)):
            raise OSError("Cannot establish profile SID ownership")
        try:
            return sid_string.value
        finally:
            kernel.LocalFree(ctypes.cast(sid_string, ctypes.c_void_p))
    finally:
        kernel.CloseHandle(token)


@contextmanager
def _ownership_lock(root: Path):
    """Serialize profile claims across processes, including an abandoned claim."""
    name = hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()
    if os.name != "nt":
        import fcntl

        with (Path(tempfile.gettempdir()) / f"tg-assistant-storage-{name}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    mutex = kernel.CreateMutexW(None, False, f"Global\\TelegramAIPersonalAssistant.Storage.{name}")
    if not mutex:
        raise ValueError("Cannot establish app data ownership lock")
    acquired = False
    try:
        # An abandoned mutex grants ownership, but the marker still needs validation.
        acquired = kernel.WaitForSingleObject(mutex, 5000) in {0, 0x80}
        if not acquired:
            raise ValueError("App data ownership claim is busy")
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(mutex)
        kernel.CloseHandle(mutex)


def ensure_runtime_dirs(
    root: Path | None = None, *, profile_id: str | None = None
) -> dict[str, Path]:
    if root is None:
        # Import lazily: Settings itself resolves paths without creating folders.
        from .config import get_settings

        settings = get_settings()
        root = settings.data_dir
        profile_id = profile_id or settings.profile_id
    else:
        root = resolve_data_path(root)
    profile_id = profile_id or "default"
    marker = root / ".tg-assistant-data"
    expected = {"version": 1, "app": APP_NAME, "profile_id": profile_id, "sid": current_user_sid()}
    with _ownership_lock(root):
        claim = not marker.exists()
        if not claim:
            try:
                content = marker.read_text(encoding="utf-8")
                # The old app's empty marker can only be adopted in this user's
                # standard per-user root; an arbitrary empty marker proves nothing.
                if not content and root != default_user_data_root():
                    raise ValueError("Legacy app data ownership requires the current per-user root")
                if content:
                    value = json.loads(content)
                    if value != expected or type(value.get("version")) is not int:
                        raise ValueError("App data ownership does not match this profile")
                claim = not content
            except (OSError, json.JSONDecodeError):
                raise ValueError("App data ownership marker is invalid") from None
        elif root.exists() and any(root.iterdir()):
            raise ValueError("Cannot establish ownership of a nonempty app data directory")
        root.mkdir(parents=True, exist_ok=True)
        if claim:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=root, delete=False
                ) as file:
                    temporary = Path(file.name)
                    json.dump(expected, file)
                    file.flush()
                    os.fsync(file.fileno())
                temporary.replace(marker)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        paths = {
            "data": root,
            "config": root / "config",
            "db": root / "db",
            "logs": root / "logs",
            "qdrant": root / "qdrant",
            "downloads": root / "downloads",
            "backups": root / "backups",
            "sessions": root / "sessions",
        }
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)
        return paths
