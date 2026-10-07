"""Token prerequisite for disposable Windows test/tool resources; no pytest import."""

from __future__ import annotations

import os


def prepare_windows_fixture_owner() -> bool | None:
    """Create test objects for this user even in an elevated Windows runner.

    An elevated runner's default object owner can be Administrators. Normalize
    this process token's default owner to its existing user SID, without changing
    privileges, identities or any existing object's owner/security descriptor.
    Production's foreign-owner refusal remains unchanged.
    """
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
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
    security.SetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    security.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    token = wintypes.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x88, ctypes.byref(token)):
        raise RuntimeError("windows_fixture_owner_unavailable")
    try:

        def information(kind):
            size = wintypes.DWORD()
            security.GetTokenInformation(token, kind, None, 0, ctypes.byref(size))
            if not 0 < size.value <= 65536:
                raise RuntimeError("windows_fixture_owner_unavailable")
            buffer = ctypes.create_string_buffer(size.value)
            if not security.GetTokenInformation(token, kind, buffer, size, ctypes.byref(size)):
                raise RuntimeError("windows_fixture_owner_unavailable")
            return buffer

        user = information(1)  # TOKEN_USER starts with SID_AND_ATTRIBUTES.Sid.
        owner = information(4)  # TOKEN_OWNER starts with its Owner pointer.
        user_sid = ctypes.cast(user, ctypes.POINTER(ctypes.c_void_p))[0]
        owner_sid = ctypes.cast(owner, ctypes.POINTER(ctypes.c_void_p))[0]
        matched_before = bool(security.EqualSid(user_sid, owner_sid))
        if not matched_before:
            desired = ctypes.c_void_p(user_sid)
            if not security.SetTokenInformation(
                token, 4, ctypes.byref(desired), ctypes.sizeof(desired)
            ):
                raise RuntimeError("windows_fixture_owner_unavailable")
            owner = information(4)
            owner_sid = ctypes.cast(owner, ctypes.POINTER(ctypes.c_void_p))[0]
            if not security.EqualSid(user_sid, owner_sid):
                raise RuntimeError("windows_fixture_owner_unavailable")
        return matched_before
    finally:
        kernel.CloseHandle(token)
