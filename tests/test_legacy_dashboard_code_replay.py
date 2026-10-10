"""Review follow-up: standalone advanced codes remain spent after restart."""
from __future__ import annotations

import json

import httpx
from test_admin_api import admin_client  # noqa: F401

from tg_assistant.admin_api import auth, create_admin_app


async def test_real_admin_app_restart_denies_consumed_code(admin_client, monkeypatch):  # noqa: F811
    client, secret = admin_client
    monkeypatch.setattr(auth.time, 'time', lambda: 1_900_000_001.0)
    code = auth.dashboard_login_code(secret)
    first = await client.post('/api/v1/auth/login', json={'code': code})
    assert first.status_code == 200
    assert (await client.post('/api/v1/auth/login', json={'code': code})).status_code == 401
    second_app = create_admin_app(client._transport.app.state.admin_context)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second_app), base_url='http://127.0.0.1:8765') as restarted:
        assert (await restarted.post('/api/v1/auth/login', json={'code': code})).status_code == 401
        monkeypatch.setattr(auth.time, 'time', lambda: 1_900_000_301.0)
        next_code = auth.dashboard_login_code(secret)
        assert next_code != code
        assert (await restarted.post('/api/v1/auth/login', json={'code': next_code})).status_code == 200
        assert (await restarted.post('/api/v1/auth/login', json={'code': next_code})).status_code == 401


async def test_real_admin_app_corrupt_replay_state_fails_closed(admin_client, monkeypatch):  # noqa: F811
    client, secret = admin_client
    monkeypatch.setattr(auth.time, 'time', lambda: 1_900_000_001.0)
    code = auth.dashboard_login_code(secret)
    assert (await client.post('/api/v1/auth/login', json={'code': code})).status_code == 200
    config = client._transport.app.state.admin_context.settings_getter().data_dir / 'config'
    replay = config / 'admin-login-code-consumed.json'
    assert replay.exists(), 'Standalone API must persist its consumed period'
    encoded = replay.read_text(encoding='utf-8')
    assert code not in encoded and secret not in encoded
    assert len(encoded.encode()) <= 4096
    payload = json.loads(encoded)
    assert payload['profile_id'] == 'default'
    assert len(payload['secret_fingerprint']) == 64
    replay.write_text('{corrupt', encoding='utf-8')
    monkeypatch.setattr(auth.time, 'time', lambda: 1_900_000_301.0)
    second_app = create_admin_app(client._transport.app.state.admin_context)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second_app), base_url='http://127.0.0.1:8765') as restarted:
        response = await restarted.post('/api/v1/auth/login', json={'code': auth.dashboard_login_code(secret)})
        assert response.status_code == 401
        assert '{corrupt' not in response.text
    assert replay.read_text(encoding='utf-8') == '{corrupt'


def test_persistent_replay_cross_process_atomic_consume(tmp_path):
    import os
    import subprocess
    import sys
    from concurrent.futures import ThreadPoolExecutor

    config = tmp_path / 'config'
    code = (
        "from pathlib import Path; import sys; "
        "from tg_assistant.admin_api.auth import AdminAuth, LegacyCodeReplayStore, dashboard_login_code; "
        "print('ready',flush=True); sys.stdin.readline(); "
        "a=AdminAuth('synthetic-replay-test',replay_store=LegacyCodeReplayStore(Path(sys.argv[1]),profile_id='race')); "
        "print(int(a.validate_login_code(dashboard_login_code('synthetic-replay-test',at=1900000001),at=1900000001)),flush=True)"
    )
    children = []
    try:
        for _ in range(6):
            child = subprocess.Popen([sys.executable, '-u', '-c', code, str(config)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            children.append(child)
        for child in children:
            assert child.stdout.readline().strip() == 'ready'
        def release(child):
            output, error = child.communicate('go\n', timeout=20)
            assert child.returncode == 0, error
            return int(output.strip())
        with ThreadPoolExecutor(max_workers=6) as pool:
            consumed = list(pool.map(release, children))
        assert sum(consumed) == 1
        assert len(list(config.glob('admin-login-code-consumed.json'))) == 1
        assert not list(config.glob('.admin-code-*'))
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)


def test_persistent_replay_lock_unavailable_denies_without_state(tmp_path):
    from tg_assistant.desktop.instance import secure_directory
    from tg_assistant.services.maintenance import FileLock
    config = tmp_path / 'config'
    secure_directory(config)
    replay = auth.LegacyCodeReplayStore(config, profile_id='locked')
    with FileLock(config / 'admin-login-code-consumed.lock', exclusive=True):
        assert not replay.consume('synthetic-only', 100)
    assert not (config / 'admin-login-code-consumed.json').exists()
    assert replay.consume('synthetic-only', 100)
    assert not replay.consume('synthetic-only', 100)
    assert not replay.consume('synthetic-only', 99)  # Clock rollback remains denied.
    assert replay.consume('synthetic-only', 101)


def test_persistent_replay_atomic_write_failure_denies(tmp_path, monkeypatch):
    replay = auth.LegacyCodeReplayStore(tmp_path / 'config', profile_id='failure')
    def unavailable(*args):
        raise OSError('synthetic-private-error')
    monkeypatch.setattr(auth.os, 'replace', unavailable)
    assert not replay.consume('synthetic-only', 100)
    assert not (tmp_path / 'config/admin-login-code-consumed.json').exists()
    assert not list((tmp_path / 'config').glob('.admin-code-*'))


def test_persistent_replay_foreign_binding_and_oversize_fail_closed(tmp_path):
    config = tmp_path / 'config'
    replay = auth.LegacyCodeReplayStore(config, profile_id='binding')
    assert replay.consume('synthetic-only', 100)
    state_path = config / 'admin-login-code-consumed.json'
    before = state_path.read_text(encoding='utf-8')
    assert not auth.LegacyCodeReplayStore(config, profile_id='foreign').consume('synthetic-only', 101)
    assert not replay.consume('changed-secret', 101)
    assert state_path.read_text(encoding='utf-8') == before
    state_path.write_text(' ' * 4097, encoding='utf-8')
    assert not replay.consume('synthetic-only', 101)


def _windows_sddl(path):
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32')
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    api.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    api.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p]
    descriptor, text = ctypes.c_void_p(), wintypes.LPWSTR()
    assert api.GetNamedSecurityInfoW(str(path), 1, 7, None, None, None, None, ctypes.byref(descriptor)) == 0
    try:
        assert api.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 7, ctypes.byref(text), None)
        return text.value
    finally:
        if text:
            kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
        kernel.LocalFree(descriptor)


def test_windows_replay_hardlink_refusal_leaves_outside_alias_acl_untouched(tmp_path):
    import os

    import pytest
    if os.name != 'nt':
        pytest.skip('Actual Windows NTFS inheritance/hardlink test')
    config = tmp_path / 'config'
    config.mkdir()
    outside = tmp_path / 'disposable-outside-file'
    outside.write_text('{}', encoding='utf-8')
    alias = config / 'admin-login-code-consumed.json'
    os.link(outside, alias)
    assert alias.stat().st_nlink == 2
    before_outside, before_config = _windows_sddl(outside), _windows_sddl(config)
    accepted = auth.LegacyCodeReplayStore(config, profile_id='hardlink').consume('synthetic-only', 100)
    assert _windows_sddl(outside) == before_outside, 'Denied hardlink must not alter its outside alias ACL'
    assert _windows_sddl(config) == before_config, 'Unsafe tree must be preflighted before inheritance mutation'
    assert not accepted
    assert outside.read_text(encoding='utf-8') == '{}'
    assert not (config / 'admin-login-code-consumed.lock').exists()


def test_windows_replay_foreign_created_descendant_acl_untouched(tmp_path):
    import ctypes
    import os
    from ctypes import wintypes

    import pytest

    from tg_assistant.paths import current_user_sid
    if os.name != 'nt':
        pytest.skip('Actual Windows foreign-owner new-file fixture')
    config = tmp_path / 'config'
    config.mkdir()
    foreign = config / 'disposable-foreign-note'
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    class Attributes(ctypes.Structure):
        _fields_ = [('length', wintypes.DWORD), ('descriptor', ctypes.c_void_p), ('inherit', wintypes.BOOL)]
    descriptor = ctypes.c_void_p()
    # Only a newly created disposable file; never SetOwner on an existing object.
    assert api.ConvertStringSecurityDescriptorToSecurityDescriptorW(f'O:BAG:BAD:(A;;GA;;;{current_user_sid()})', 1, ctypes.byref(descriptor), None)
    try:
        attributes = Attributes(ctypes.sizeof(Attributes), descriptor, False)
        handle = kernel.CreateFileW(str(foreign), 0x40000000, 7, ctypes.byref(attributes), 1, 0, None)
        if handle == ctypes.c_void_p(-1).value:
            error = ctypes.get_last_error()
            assert error in {5, 1307, 1314}
            pytest.skip('Token cannot create Administrators-owned disposable file; no privileges enabled')
        kernel.CloseHandle(handle)
    finally:
        kernel.LocalFree(descriptor)
    before_foreign, before_config = _windows_sddl(foreign), _windows_sddl(config)
    assert 'O:BA' in before_foreign
    accepted = auth.LegacyCodeReplayStore(config, profile_id='foreign-child').consume('synthetic-only', 100)
    assert _windows_sddl(foreign) == before_foreign, 'Rejected foreign child must retain its original descriptor'
    assert _windows_sddl(config) == before_config, 'No inherited ACL mutation before full-tree validation'
    assert not accepted
    assert not (config / 'admin-login-code-consumed.json').exists()
