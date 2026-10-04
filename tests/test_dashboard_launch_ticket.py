import base64
import importlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from tg_assistant.admin_api import auth
from tg_assistant.desktop.worker import RuntimeGateway
from tg_assistant.paths import current_user_sid

NOW = datetime.now(UTC)
ORIGIN = 'http://127.0.0.1:8765'
PROFILE = 'default'


def service():
    assert hasattr(auth, 'DashboardTicketService'), 'Native dashboard ticket service is missing'
    return auth.DashboardTicketService(profile_id=PROFILE, windows_sid=current_user_sid(), origin=ORIGIN)


def test_wrong_sid_denied():
    issuer = service()
    with pytest.raises(PermissionError):
        issuer.issue(PROFILE, 'S-1-5-21-foreign', NOW)
    with pytest.raises(PermissionError):
        issuer.issue('other', current_user_sid(), NOW)


def test_ticket_once_30s_hash_only_and_setup_authority():
    issuer = service()
    ticket = issuer.issue(PROFILE, current_user_sid(), NOW)
    assert len(base64.urlsafe_b64decode(ticket.raw_ticket + '=')) == 32
    assert ticket.expires_at == NOW + timedelta(seconds=30)
    assert ticket.raw_ticket not in repr(vars(issuer))
    session = issuer.redeem(ticket.raw_ticket, ORIGIN, NOW)
    assert session.owner_id is None and session.authority == 'setup_only'
    assert session.profile_id == PROFILE
    with pytest.raises(PermissionError):
        issuer.redeem(ticket.raw_ticket, ORIGIN, NOW)
    expired = issuer.issue(PROFILE, current_user_sid(), NOW)
    with pytest.raises(PermissionError):
        issuer.redeem(expired.raw_ticket, ORIGIN, NOW + timedelta(seconds=30))


def test_atomic_replay_race():
    issuer = service()
    ticket = issuer.issue(PROFILE, current_user_sid(), NOW)
    barrier = threading.Barrier(12)
    def redeem(_):
        barrier.wait()
        try:
            return issuer.redeem(ticket.raw_ticket, ORIGIN, NOW)
        except PermissionError:
            return None
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(redeem, range(12)))
    assert len([result for result in results if result]) == 1


def test_ticket_audience_and_origin_bound():
    issuer = service()
    ticket = issuer.issue(PROFILE, current_user_sid(), NOW)
    for origin in ('http://localhost:8765', ORIGIN + '/evil', 'https://evil.invalid', None):
        with pytest.raises(PermissionError):
            issuer.redeem(ticket.raw_ticket, origin, NOW)
    issuer.redeem(ticket.raw_ticket, ORIGIN, NOW)


def test_legacy_code_one_use():
    legacy = auth.AdminAuth('synthetic')
    code = auth.dashboard_login_code('synthetic', at=1000)
    assert legacy.validate_login_code(code, at=1000)
    assert not legacy.validate_login_code(code, at=1000)


@pytest.mark.parametrize('headers', [
    {'Host': 'localhost:8765', 'Origin': ORIGIN},
    {'Origin': 'http://evil.invalid'}, {},
    {'Origin': ORIGIN, 'Host': '127.0.0.1:9999'},
])
async def test_bad_origin_host_denied(headers):
    gateway = RuntimeGateway(8765, uuid4().hex, profile_id=PROFILE)
    ticket = gateway.tickets.issue(PROFILE, current_user_sid(), NOW)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url=ORIGIN) as client:
        result = await client.post('/api/v1/auth/launch/redeem', json={'ticket': ticket.raw_ticket}, headers=headers)
        assert result.status_code == 403


async def test_logout_requires_user_reopen_and_setup_cannot_manage(caplog):
    gateway = RuntimeGateway(8765, uuid4().hex, profile_id=PROFILE)
    ticket = gateway.tickets.issue(PROFILE, current_user_sid(), datetime.now(UTC))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url=ORIGIN) as client:
        result = await client.post('/api/v1/auth/launch/redeem', json={'ticket': ticket.raw_ticket}, headers={'Origin': ORIGIN})
        assert result.status_code == 200
        assert result.json()['owner_id'] is None
        cookie = result.headers['set-cookie']
        assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie
        assert ticket.raw_ticket not in result.text and ticket.raw_ticket not in caplog.text
        assert (await client.get('/api/v1/overview')).status_code in (401, 403, 404)
        assert (await client.post('/api/v1/auth/login', json={'code': '12345678'}, headers={'Origin': ORIGIN})).status_code == 403
        assert (await client.post('/api/v1/auth/logout', headers={'Origin': ORIGIN})).status_code == 403
        csrf = result.json()['csrf_token']
        assert (await client.post('/api/v1/auth/logout', headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf})).status_code == 200
        assert (await client.get('/api/v1/auth/session')).status_code == 401
        assert (await client.post('/api/v1/auth/launch/redeem', json={'ticket': ticket.raw_ticket}, headers={'Origin': ORIGIN})).status_code == 401
        assert (await client.post('/api/v1/auth/launch/issue', headers={'Origin': ORIGIN})).status_code in (403, 404)


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows pipe/ACL test')
def test_windows_pipe_peer_acl_profile_allowlist_and_clean_close():
    from pathlib import Path
    path = Path(__file__).parents[1] / 'src/tg_assistant/desktop/ipc.py'
    assert path.exists(), 'SID authenticated Windows pipe is missing'
    ipc = importlib.import_module('tg_assistant.desktop.ipc')
    issuer = service()
    run_id = uuid4().hex
    command = {'name': 'issue_dashboard_ticket', 'request_id': uuid4().hex, 'profile_id': PROFILE, 'payload_nonsecret': {}}
    with ipc.NativePipeServer(PROFILE, run_id, issuer) as server:
        raw = ipc.native_request(PROFILE, run_id, command, server_pid=os.getpid())
        assert len(raw['raw_ticket']) == 43
        assert 'D:P' in server.sddl and current_user_sid() in server.sddl
        assert 'WD' not in server.sddl and 'AU' not in server.sddl
        for bad in ({**command, 'profile_id': 'other'}, {**command, 'name': 'execute'}, {**command, 'payload_nonsecret': {'path': 'cmd.exe'}}):
            with pytest.raises(PermissionError):
                ipc.native_request(PROFILE, run_id, bad, server_pid=os.getpid())
        with pytest.raises(PermissionError):
            ipc.native_request(PROFILE, run_id, command, server_pid=os.getpid() + 1)
    with pytest.raises((OSError, PermissionError)):
        ipc.native_request(PROFILE, run_id, command, server_pid=os.getpid())


def test_authority_change_revokes_old_tickets_and_sessions():
    issuer = service()
    old = issuer.issue(PROFILE, current_user_sid(), NOW)
    session = issuer.redeem(old.raw_ticket, ORIGIN, NOW)
    pending = issuer.issue(PROFILE, current_user_sid(), NOW)
    issuer.set_verified_owner(9007199254740993)
    assert issuer.get_session(session.token) is None
    with pytest.raises(PermissionError):
        issuer.redeem(pending.raw_ticket, ORIGIN, NOW)
    ticket = issuer.issue(PROFILE, current_user_sid(), NOW)
    management = issuer.redeem(ticket.raw_ticket, ORIGIN, NOW)
    assert management.owner_id == 9007199254740993 and management.authority == 'management'
    issuer.set_verified_owner(None)
    assert issuer.get_session(management.token) is None


async def test_malformed_redeem_and_command_do_not_echo_ticket(caplog):
    gateway = RuntimeGateway(8765, uuid4().hex, profile_id=PROFILE)
    marker = 'synthetic-private-marker'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url=ORIGIN, headers={'Origin': ORIGIN}) as client:
        for payload in ({'ticket': marker, 'extra': marker}, {'ticket': {'secret': marker}}, {'ticket': marker * 1024}):
            result = await client.post('/api/v1/auth/launch/redeem', json=payload)
            assert result.status_code == 401 and marker not in result.text
        ticket = gateway.tickets.issue(PROFILE, current_user_sid(), datetime.now(UTC))
        result = await client.post('/api/v1/auth/launch/redeem', json={'ticket': ticket.raw_ticket})
        csrf = result.json()['csrf_token']
        command = {'name': 'issue_dashboard_ticket', 'request_id': 'test', 'profile_id': PROFILE, 'payload_nonsecret': {}}
        assert (await client.post('/api/v1/native/commands', json=command)).status_code == 403
        assert (await client.post('/api/v1/native/commands', json=command, headers={'X-CSRF-Token': csrf})).status_code == 403
        command['name'] = 'open_bot_dialog'
        command['payload_nonsecret'] = {'token': marker}
        result = await client.post('/api/v1/native/commands', json=command, headers={'X-CSRF-Token': csrf})
        assert result.status_code == 403 and marker not in result.text and marker not in caplog.text


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows anonymous token ACL refusal')
def test_real_pipe_dacl_refuses_anonymous_token_and_duplicate_server():
    import ctypes

    from tg_assistant.desktop import ipc
    issuer = service()
    run_id = uuid4().hex
    with ipc.NativePipeServer(PROFILE, run_id, issuer) as server:
        with pytest.raises(OSError):
            with ipc.NativePipeServer(PROFILE, run_id, issuer):
                pass
        kernel, advapi = ipc._apis()
        advapi.ImpersonateAnonymousToken.argtypes = [ctypes.c_void_p]
        name = ipc.pipe_name(PROFILE, run_id)
        assert advapi.ImpersonateAnonymousToken(kernel.GetCurrentThread())
        try:
            handle = kernel.CreateFileW(name, 0xC0000000, 0, None, 3, 0x110000, None)
            assert handle == ctypes.c_void_p(-1).value
            assert ctypes.get_last_error() == 5  # real OS ACCESS_DENIED
        finally:
            assert advapi.RevertToSelf()
        assert server.thread.is_alive()
        with pytest.raises(PermissionError):
            server.dispatch(b'{}', 'S-1-5-7')


@pytest.mark.skipif(os.name != 'nt', reason='Windows impersonation failure fail-closed')
def test_revert_failure_withdraws_native_authority_and_stops_pipe(monkeypatch):
    from tg_assistant.desktop import ipc
    assert hasattr(ipc, 'NativeIdentityFailure'), 'RevertToSelf failure needs a fatal boundary'
    issuer = service()
    run_id = uuid4().hex
    original = ipc._peer_sid
    def failed_revert(*args):
        original(*args)  # exercise actual impersonation and safe revert first
        raise ipc.NativeIdentityFailure('native_identity_failed')
    monkeypatch.setattr(ipc, '_peer_sid', failed_revert)
    command = {'name': 'issue_dashboard_ticket', 'request_id': uuid4().hex, 'profile_id': PROFILE, 'payload_nonsecret': {}}
    with ipc.NativePipeServer(PROFILE, run_id, issuer) as server:
        with pytest.raises((OSError, PermissionError)):
            ipc.native_request(PROFILE, run_id, command, server_pid=os.getpid())
        assert server.failed.wait(1)
        with pytest.raises(PermissionError):
            issuer.issue(PROFILE, current_user_sid(), NOW)
        assert not server.thread.is_alive()


def wait_for_browser_open(opened, timeout_ms):
    from PySide6.QtCore import QEventLoop, QTimer

    # QTest.qWait retains the GIL while waiting, starving the Python IPC
    # worker. exec() releases it, as the launcher's actual event loop does.
    wait_loop = QEventLoop()
    completion = QTimer(wait_loop)
    completion.timeout.connect(lambda: wait_loop.quit() if opened else None)
    completion.start(10)
    timeout = QTimer(wait_loop)
    timeout.setSingleShot(True)
    timeout.timeout.connect(wait_loop.quit)
    timeout.start(timeout_ms)
    if not opened:
        wait_loop.exec()
    completion.stop()
    timeout.stop()


def test_actual_qt_dashboard_button_opens_native_ticket_url(qt_application, tmp_path, monkeypatch):
    import socket
    import time

    from desktop.test_launcher import controller, ready, stop
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QSignalSpy, QTest

    from tg_assistant.config import Settings
    from tg_assistant.desktop import app as desktop
    for name in list(os.environ):
        if name.startswith('TG_ASSISTANT_'):
            monkeypatch.delenv(name)
    selected = Settings(_env_file=None, data_dir=tmp_path / 'profile')
    opened = []
    monkeypatch.setattr(desktop.QDesktopServices, 'openUrl', lambda url: opened.append(url.toString()) or True)
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen()
        selected = selected.model_copy(update={'admin_api_port': occupied.getsockname()[1]})
        runtime = controller(selected, tmp_path)
        window = desktop.LauncherWindow(runtime)
        try:
            window.show()
            QTest.qWait(50)
            state = ready(runtime)
            deadline = time.monotonic() + 5
            while not window.dashboard_button.isEnabled() and time.monotonic() < deadline:
                QTest.qWait(50)
            assert window.dashboard_button.isEnabled()
            clicked = QSignalSpy(window.dashboard_button.clicked)
            QTest.mouseClick(window.dashboard_button, Qt.LeftButton)
            assert clicked.count() == 1
            wait_for_browser_open(opened, 5000)
            assert len(opened) == 1
            launch = opened[0]
            assert launch.startswith(state.url + '#launch_ticket=')
            assert state.port != selected.admin_api_port
            raw = launch.split('#launch_ticket=')[1]
            response = httpx.post(state.url + '/api/v1/auth/launch/redeem', json={'ticket': raw}, headers={'Origin': state.url}, trust_env=False)
            assert response.status_code == 200 and response.json()['authority'] == 'setup_only'
            assert httpx.post(state.url + '/api/v1/auth/launch/redeem', json={'ticket': raw}, headers={'Origin': state.url}, trust_env=False).status_code == 401
        finally:
            window.close()
            stop(runtime)


def test_actual_tray_dashboard_opens_when_launcher_is_hidden(qt_application, tmp_path, monkeypatch):
    from desktop.test_launcher import controller, ready, stop

    from tg_assistant.config import Settings
    from tg_assistant.desktop import app as desktop
    from tg_assistant.desktop.tray import TrayController
    for name in list(os.environ):
        if name.startswith('TG_ASSISTANT_'):
            monkeypatch.delenv(name)
    selected = Settings(_env_file=None, data_dir=tmp_path / 'profile')
    opened = []
    monkeypatch.setattr(desktop.QDesktopServices, 'openUrl', lambda url: opened.append(url.toString()) or True)
    runtime = controller(selected, tmp_path)
    window = desktop.LauncherWindow(runtime)
    tray = TrayController(window, runtime)
    try:
        window.show()
        from PySide6.QtTest import QTest
        QTest.qWait(50)
        state = ready(runtime)
        window.hide_on_close = True
        window.close()
        assert not window.isVisible() and not window.timer.isActive()
        action = next(action for action in tray.menu.actions() if action.text() == 'Mở dashboard')
        action.trigger()
        wait_for_browser_open(opened, 3000)
        assert len(opened) == 1, 'Tray launch must complete even with hidden launcher timer stopped'
        assert opened[0].startswith(state.url + '#launch_ticket=')
    finally:
        tray.close()
        window.hide_on_close = False
        window.close()
        stop(runtime)
