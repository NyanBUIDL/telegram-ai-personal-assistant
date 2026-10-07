"""A private per-SID control directory and owned OS handle, across profiles."""

from __future__ import annotations

import ctypes
import os
import tempfile
from pathlib import Path
from uuid import UUID

import psutil

from ..paths import APP_NAME, current_user_sid
from ..services.maintenance import FileLock, MaintenanceBusy


class AlreadyRunning(RuntimeError):
    pass


def process_incarnation_exists(pid, started_at):
    if type(pid) is not int or pid <= 0 or type(started_at) not in {int, float}:
        return False
    if os.name == "nt":
        return _windows_process_incarnation_exists(pid, started_at)
    try:
        return abs(psutil.Process(pid).create_time() - started_at) < 0.001
    except psutil.NoSuchProcess:
        return False


def _windows_process_incarnation_exists(pid, started_at):
    from ctypes import wintypes

    if pid > 0xFFFFFFFF:
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    process = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
    if not process:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: this PID does not exist.
            return False
        _raise_process_inspection_error(pid, error)
    try:
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(
            process,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            _raise_process_inspection_error(pid, ctypes.get_last_error())
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        actual_started_at = ticks / 10_000_000 - 11_644_473_600
        if not abs(actual_started_at - started_at) < 0.001:
            return False
        # A process object and its creation time outlive execution while any
        # handle remains open. Inspect lifetime on the same incarnation handle;
        # exit code 259 alone is ambiguous with the STILL_ACTIVE sentinel.
        status = kernel.WaitForSingleObject(process, 0)
        if status == 0:  # WAIT_OBJECT_0: execution has completed.
            return False
        if status == 258:  # WAIT_TIMEOUT: the exact process is still running.
            return True
        _raise_process_inspection_error(pid, ctypes.get_last_error())
    finally:
        kernel.CloseHandle(process)


def _raise_process_inspection_error(pid, error):
    if error == 5:
        raise psutil.AccessDenied(pid)
    raise psutil.Error("process_inspection_unavailable")


def process_matches_owner(pid, started_at):
    if not process_incarnation_exists(pid, started_at):
        return False
    if os.name != "nt":
        return psutil.Process(pid).uids().real == os.getuid()
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    api.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    api.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    api.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    process = kernel.OpenProcess(0x1000, False, pid)
    if not process:
        return False
    token = wintypes.HANDLE()
    try:
        if not api.OpenProcessToken(process, 8, ctypes.byref(token)):
            return False
        size = wintypes.DWORD()
        api.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not size.value:
            return False
        buffer = ctypes.create_string_buffer(size.value)
        if not api.GetTokenInformation(token, 1, buffer, size.value, ctypes.byref(size)):
            return False
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        text = wintypes.LPWSTR()
        if not api.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            return False
        try:
            return text.value == current_user_sid()
        finally:
            kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        if token:
            kernel.CloseHandle(token)
        kernel.CloseHandle(process)


def native_control_directory() -> Path:
    if os.name != "nt":
        return Path(tempfile.gettempdir()) / f"{APP_NAME}-{os.getuid()}-desktop"
    from ctypes import wintypes

    # The native known folder, rather than a profile/data-root override, binds
    # all configured profiles and Terminal Services sessions to the same owner.
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole = ctypes.WinDLL("ole32")
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    identifier = ctypes.create_string_buffer(UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091").bytes_le)
    location = wintypes.LPWSTR()
    if shell.SHGetKnownFolderPath(identifier, 0, None, ctypes.byref(location)):
        raise OSError("native_data_root_unavailable")
    try:
        return Path(location.value) / APP_NAME / ".desktop-control"
    finally:
        ole.CoTaskMemFree(ctypes.cast(location, ctypes.c_void_p))


def secure_directory(directory: Path) -> None:
    """Protect only an app-owned directory; refuse a foreign NTFS owner."""
    _refuse_reparse(directory)
    directory.mkdir(parents=True, exist_ok=True)
    _secure_owned_path(directory, directory=True)


def _refuse_reparse(path: Path) -> None:
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or (os.name == "nt" and ancestor.is_junction()):
            raise OSError("storage_access_denied")


def secure_tree(root: Path) -> None:
    """Secure existing files too, including explicitly protected legacy ACLs."""
    # Applying an inheritable directory ACL changes child descriptors too. Reject
    # unsafe entries throughout the tree before that can affect an outside alias.
    _refuse_reparse(root)
    if not root.is_dir():
        raise OSError("storage_access_denied")
    _assert_owned_path(root)
    entries = [(root, True)]
    pending = [root]
    while pending:
        for path in pending.pop().iterdir():
            _refuse_reparse(path)
            if path.is_dir():
                pending.append(path)
                directory = True
            elif path.is_file() and path.stat().st_nlink == 1:
                directory = False
            else:
                raise OSError("storage_access_denied")
            _assert_owned_path(path)
            entries.append((path, directory))
    for path, directory in entries:
        _refuse_reparse(path)
        if not directory and path.stat().st_nlink != 1:
            raise OSError("storage_access_denied")
        _secure_owned_path(path, directory=directory)


def _assert_owned_path(path: Path) -> None:
    if os.name != "nt":
        if path.stat().st_uid != os.getuid():
            raise OSError("storage_access_denied")
        return
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32")
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    api.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    api.GetNamedSecurityInfoW.restype = wintypes.DWORD
    api.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    api.ConvertSidToStringSidW.restype = wintypes.BOOL
    owner, original = ctypes.c_void_p(), ctypes.c_void_p()
    if api.GetNamedSecurityInfoW(
        str(path), 1, 1, ctypes.byref(owner), None, None, None, ctypes.byref(original)
    ):
        raise OSError("storage_access_denied")
    sid_text = wintypes.LPWSTR()
    try:
        if not api.ConvertSidToStringSidW(owner, ctypes.byref(sid_text)):
            raise OSError("storage_access_denied")
        if sid_text.value != current_user_sid():
            raise OSError("storage_access_denied")
    finally:
        if sid_text:
            kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
        kernel.LocalFree(original)


def _secure_owned_path(path: Path, *, directory: bool) -> None:
    _assert_owned_path(path)
    if os.name != "nt":
        path.chmod(0o700 if directory else 0o600)
        return
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32")
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    api.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    api.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    api.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    api.SetNamedSecurityInfoW.restype = wintypes.DWORD
    descriptor = ctypes.c_void_p()
    inherit = "OICI" if directory else ""
    sddl = f"D:P(A;{inherit};FA;;;{current_user_sid()})(A;{inherit};FA;;;SY)"
    if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None
    ):
        raise OSError("storage_access_denied")
    try:
        present, defaulted, acl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if (
            not api.GetSecurityDescriptorDacl(
                descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)
            )
            or not present
            or not acl
        ):
            raise OSError("storage_access_denied")
        if api.SetNamedSecurityInfoW(str(path), 1, 4 | 0x80000000, None, None, acl, None):
            raise OSError("storage_access_denied")
    finally:
        kernel.LocalFree(descriptor)


class InstanceGuard:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or native_control_directory()
        self.lock = None

    def acquire(self):
        if self.lock:
            raise AlreadyRunning("runtime_already_running")
        secure_directory(self.directory)
        try:
            self.lock = FileLock(self.directory / "runtime.lock", exclusive=True)
        except MaintenanceBusy:
            raise AlreadyRunning("runtime_already_running") from None
        return self

    def close(self):
        if self.lock:
            self.lock.close()
            self.lock = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.close()
