"""Windows local named pipe: explicit SID ACL, impersonated peer, bounded DTO.

The Windows account is the trust boundary. Same-SID programs have equivalent
native authority; this does not claim to contain malware running as that user.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import threading
import time
from ctypes import wintypes
from datetime import UTC, datetime

from ..contracts import NativeCommand
from ..paths import current_user_sid

MAX_MESSAGE = 8192


class NativeIdentityFailure(RuntimeError):
    """Fatal: retire this server rather than reuse an impersonated thread."""


def pipe_name(profile_id, run_id):
    digest = hashlib.sha256(f'{current_user_sid()}:{profile_id}:{run_id}'.encode()).hexdigest()
    return '\\\\.\\pipe\\TelegramAIPersonalAssistant-' + digest


def _apis():
    if os.name != 'nt':
        raise OSError('windows_ipc_required')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    kernel.CreateNamedPipeW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
    kernel.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel.WriteFile.argtypes = kernel.ReadFile.argtypes
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.GetNamedPipeServerProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.SetNamedPipeHandleState.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p]
    kernel.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel.GetCurrentThread.restype = wintypes.HANDLE
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    advapi.ImpersonateNamedPipeClient.argtypes = [wintypes.HANDLE]
    advapi.OpenThreadToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    return kernel, advapi


def _peer_sid(handle, kernel, advapi):
    if not advapi.ImpersonateNamedPipeClient(handle):
        raise PermissionError('native_peer_denied')
    token = wintypes.HANDLE()
    sid_text = wintypes.LPWSTR()
    try:
        if not advapi.OpenThreadToken(kernel.GetCurrentThread(), 8, True, ctypes.byref(token)):
            raise PermissionError('native_peer_denied')
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not 0 < size.value <= 65536:
            raise PermissionError('native_peer_denied')
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size.value, ctypes.byref(size)):
            raise PermissionError('native_peer_denied')
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise PermissionError('native_peer_denied')
        return sid_text.value
    finally:
        if sid_text:
            kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
        if token:
            kernel.CloseHandle(token)
        if not advapi.RevertToSelf():
            # Never service another connection under an un-reverted identity.
            raise NativeIdentityFailure('native_identity_failed')


def _read(handle, kernel, stop=None):
    deadline = time.monotonic() + 2
    buffer, size = ctypes.create_string_buffer(MAX_MESSAGE + 1), wintypes.DWORD()
    while time.monotonic() < deadline and not (stop and stop.is_set()):
        if kernel.ReadFile(handle, buffer, MAX_MESSAGE + 1, ctypes.byref(size), None):
            if not 0 < size.value <= MAX_MESSAGE:
                raise PermissionError('native_payload_denied')
            return buffer.raw[:size.value]
        if ctypes.get_last_error() not in {232, 535, 536}:
            raise OSError('native_pipe_unavailable')
        time.sleep(0.01)
    raise OSError('native_pipe_timeout')


def _write(handle, kernel, data):
    size = wintypes.DWORD()
    if len(data) > MAX_MESSAGE or not kernel.WriteFile(handle, data, len(data), ctypes.byref(size), None) or size.value != len(data):
        raise OSError('native_pipe_unavailable')


class NativePipeServer:
    def __init__(self, profile_id, run_id, tickets, *, command_handler=None, before_ticket=None):
        self.profile_id, self.run_id, self.tickets = profile_id, run_id, tickets
        self.sid = current_user_sid()
        self.command_handler = command_handler
        self.before_ticket = before_ticket
        self.sddl = f'D:P(A;;GA;;;{self.sid})'
        self.stop = threading.Event()
        self.failed = threading.Event()
        self.thread = None
        self.handle = None

    def __enter__(self):
        kernel, advapi = _apis()
        class SecurityAttributes(ctypes.Structure):
            _fields_ = [('length', wintypes.DWORD), ('descriptor', ctypes.c_void_p), ('inherit', wintypes.BOOL)]
        descriptor = ctypes.c_void_p()
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(self.sddl, 1, ctypes.byref(descriptor), None):
            raise OSError('native_acl_failed')
        try:
            attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
            # Duplex + FIRST_PIPE_INSTANCE; message + NOWAIT + REJECT_REMOTE.
            self.handle = kernel.CreateNamedPipeW(pipe_name(self.profile_id, self.run_id), 0x80003, 0xF, 1, MAX_MESSAGE, MAX_MESSAGE, 0, ctypes.byref(attributes))
            if self.handle == ctypes.c_void_p(-1).value:
                self.handle = None
                raise OSError('native_pipe_unavailable')
        finally:
            kernel.LocalFree(descriptor)
        self.thread = threading.Thread(target=self._serve, daemon=True, name='native-dashboard-pipe')
        self.thread.start()
        return self

    def dispatch(self, data, peer_sid):
        if peer_sid != self.sid:
            raise PermissionError('native_peer_denied')
        if len(data) > MAX_MESSAGE:
            raise PermissionError('native_payload_denied')
        try:
            command = NativeCommand.model_validate_json(data)
            command.model_dump_json()
        except ValueError:
            raise PermissionError('native_command_denied') from None
        if command.profile_id != self.profile_id:
            raise PermissionError('native_profile_denied')
        if command.name.value == 'issue_dashboard_ticket':
            if command.payload_nonsecret:
                raise PermissionError('native_payload_denied')
            if self.before_ticket is not None:
                admitted = False
                try:
                    admitted = self.before_ticket() is None
                except Exception:
                    admitted = False
                if not admitted:
                    raise PermissionError('native_ticket_unavailable') from None
            ticket = self.tickets.issue(self.profile_id, peer_sid, datetime.now(UTC))
            return {'raw_ticket': ticket.raw_ticket, 'expires_at': ticket.expires_at.isoformat()}
        if self.command_handler is None:
            raise PermissionError('native_command_unavailable')
        return self.command_handler(command)

    def _serve(self):
        kernel, advapi = _apis()
        try:
            while not self.stop.is_set():
                connected = kernel.ConnectNamedPipe(self.handle, None)
                error = ctypes.get_last_error()
                if not connected and error not in {535, 536}:
                    self.stop.wait(0.01)
                    continue
                try:
                    data = _read(self.handle, kernel, self.stop)
                    peer_sid = _peer_sid(self.handle, kernel, advapi)
                    result = self.dispatch(data, peer_sid)
                    _write(self.handle, kernel, json.dumps(result).encode())
                    _read(self.handle, kernel, self.stop)  # bounded acknowledgement before disconnect
                except NativeIdentityFailure:
                    self.tickets.shutdown()
                    self.failed.set()
                    self.stop.set()
                    break
                except (OSError, ValueError, PermissionError):
                    try:
                        _write(self.handle, kernel, b'{"code":"native_denied"}')
                        _read(self.handle, kernel, self.stop)
                    except OSError:
                        pass
                finally:
                    kernel.DisconnectNamedPipe(self.handle)
        finally:
            self.tickets.shutdown()
            if not self.stop.is_set():
                self.failed.set()
                self.stop.set()
            kernel.CloseHandle(self.handle)

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=3)
            if self.thread.is_alive():
                raise OSError('native_pipe_shutdown_failed')
            self.thread = None
        self.handle = None

    def __exit__(self, *_):
        self.close()


def native_request(profile_id, run_id, command, *, server_pid):
    kernel, _ = _apis()
    name = pipe_name(profile_id, run_id)
    data = json.dumps(command, allow_nan=False).encode()
    if len(data) > MAX_MESSAGE:
        raise PermissionError('native_payload_denied')
    kernel.WaitNamedPipeW(name, 500)
    # SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION prevents server delegation.
    handle = kernel.CreateFileW(name, 0xC0000000, 0, None, 3, 0x110000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError('native_pipe_unavailable')
    try:
        actual = wintypes.DWORD()
        if not kernel.GetNamedPipeServerProcessId(handle, ctypes.byref(actual)) or actual.value != server_pid:
            raise PermissionError('native_server_denied')
        mode = wintypes.DWORD(3)  # message read + NOWAIT for a bounded client wait
        if not kernel.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None):
            raise OSError('native_pipe_unavailable')
        _write(handle, kernel, data)
        response = json.loads(_read(handle, kernel))
        _write(handle, kernel, b'{}')
        if not isinstance(response, dict) or 'code' in response:
            raise PermissionError('native_command_denied')
        return response
    finally:
        kernel.CloseHandle(handle)
