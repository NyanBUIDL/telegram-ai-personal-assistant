"""Actual browser/session and same-SID pipe boundaries for native dialog requests."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from tg_assistant.contracts import NativeCommand
from tg_assistant.desktop.ipc import NativePipeServer, native_request
from tg_assistant.desktop.worker import RuntimeGateway
from tg_assistant.paths import current_user_sid
from tg_assistant.services.maintenance import MaintenanceService


@pytest.fixture
def gateway(tmp_path):
    fence = MaintenanceService(tmp_path / "config", profile_id="native_relay")
    context = SimpleNamespace(fence=fence, coordinator=None)
    value = RuntimeGateway(18765, uuid4().hex, profile_id="native_relay", setup_context=context)
    try:
        yield value
    finally:
        if getattr(value, "native_relay", None) is not None:
            value.native_relay.close()
        value.tickets.shutdown()
        fence.close()


def poll_command(gateway, names=None):
    return NativeCommand(
        name="open_connection_dialog", request_id=uuid4().hex,
        profile_id=gateway.tickets.profile_id,
        payload_nonsecret={"delivery": "claim", "available": names or []},
    )


async def login(client, gateway):
    ticket = gateway.tickets.issue(
        gateway.tickets.profile_id, current_user_sid(), datetime.now(UTC)
    )
    response = await client.post(
        "/api/v1/auth/launch/redeem", json={"ticket": ticket.raw_ticket},
        headers={"Origin": gateway.origin},
    )
    assert response.status_code == 200
    return {"Origin": gateway.origin, "X-CSRF-Token": response.json()["csrf_token"]}


@pytest.mark.asyncio
async def test_actual_session_submission_delivers_once_through_sid_pipe(gateway):
    assert callable(getattr(gateway, "native_claim", None)), "actual native claim is missing"
    run_id = uuid4().hex
    with NativePipeServer(
        gateway.tickets.profile_id, run_id, gateway.tickets,
        command_handler=gateway.native_claim,
    ):
        def claim(names):
            return native_request(
                gateway.tickets.profile_id, run_id,
                poll_command(gateway, names).model_dump(mode="json"), server_pid=os.getpid(),
            )

        assert claim(["open_connection_dialog"]) == {"command": None}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway), base_url=gateway.origin
        ) as client:
            headers = await login(client, gateway)
            availability = await client.get("/api/v1/native/dialogs")
            assert availability.status_code == 200
            assert availability.json() == {
                "profile_id": "native_relay", "commands": ["open_connection_dialog"]
            }
            command = NativeCommand(
                name="open_connection_dialog", request_id=uuid4().hex,
                profile_id="native_relay", payload_nonsecret={},
            )
            response = await client.post(
                "/api/v1/native/commands", json=command.model_dump(mode="json"), headers=headers
            )
            assert response.status_code == 200
            assert response.json()["state"] == "queued"
            delivered = NativeCommand.model_validate(claim(["open_connection_dialog"])["command"])
            assert delivered == command
            assert claim(["open_connection_dialog"]) == {"command": None}
            replay = await client.post(
                "/api/v1/native/commands", json=command.model_dump(mode="json"), headers=headers
            )
            assert replay.json()["code"] == "native_request_replayed"


@pytest.mark.asyncio
async def test_logout_withdraws_actual_pending_session_request(gateway):
    assert callable(getattr(gateway, "native_claim", None)), "actual native claim is missing"
    gateway.native_claim(poll_command(gateway, ["open_connection_dialog"]))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway), base_url=gateway.origin
    ) as client:
        headers = await login(client, gateway)
        body = dict(name="open_connection_dialog", request_id=uuid4().hex,
                    profile_id="native_relay", payload_nonsecret={})
        response = await client.post("/api/v1/native/commands", json=body, headers=headers)
        assert response.json()["state"] == "queued"
        assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 200
        assert gateway.native_claim(poll_command(gateway, ["open_connection_dialog"])) == {
            "command": None
        }
        assert (await client.get("/api/v1/native/dialogs")).status_code == 401


@pytest.mark.asyncio
async def test_browser_cannot_claim_or_bypass_native_session_boundary(gateway):
    assert callable(getattr(gateway, "native_claim", None)), "actual native claim is missing"
    gateway.native_claim(poll_command(gateway, ["open_connection_dialog"]))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway), base_url=gateway.origin
    ) as client:
        assert (await client.get("/api/v1/native/dialogs")).status_code == 401
        headers = await login(client, gateway)
        command = poll_command(gateway, ["open_connection_dialog"])
        response = await client.post(
            "/api/v1/native/commands", json=command.model_dump(mode="json"), headers=headers
        )
        assert response.json()["code"] == "native_command_denied"
        command = command.model_copy(update={"payload_nonsecret": {}})
        for denied in (
            {"Origin": gateway.origin},
            {**headers, "Origin": "https://foreign.invalid"},
            {**headers, "Host": "foreign.invalid"},
        ):
            response = await client.post(
                "/api/v1/native/commands", json=command.model_dump(mode="json"), headers=denied
            )
            assert response.status_code == 403
        assert gateway.native_claim(poll_command(gateway, ["open_connection_dialog"])) == {
            "command": None
        }


@pytest.mark.asyncio
async def test_no_native_context_is_truthfully_unavailable():
    gateway = RuntimeGateway(18765, uuid4().hex, profile_id="native_relay")
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway), base_url=gateway.origin
        ) as client:
            headers = await login(client, gateway)
            assert (await client.get("/api/v1/native/dialogs")).json()["commands"] == []
            body = dict(name="open_connection_dialog", request_id=uuid4().hex,
                        profile_id="native_relay", payload_nonsecret={})
            response = await client.post("/api/v1/native/commands", json=body, headers=headers)
            assert response.status_code == 409
            assert response.json() == {"code": "native_dialog_unavailable"}
    finally:
        gateway.tickets.shutdown()


@pytest.mark.asyncio
async def test_owner_transition_invalidates_queued_actual_session(gateway):
    gateway.native_claim(poll_command(gateway, ["open_connection_dialog"]))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway), base_url=gateway.origin
    ) as client:
        headers = await login(client, gateway)
        body = dict(name="open_connection_dialog", request_id=uuid4().hex,
                    profile_id="native_relay", payload_nonsecret={})
        response = await client.post("/api/v1/native/commands", json=body, headers=headers)
        assert response.json()["state"] == "queued"
        gateway.tickets.set_verified_owner(1234567)
        assert gateway.native_claim(poll_command(gateway, ["open_connection_dialog"])) == {
            "command": None
        }
        assert (await client.get("/api/v1/native/dialogs")).status_code == 401


@pytest.mark.parametrize("payload", [
    {}, {"delivery": "claim", "available": ["issue_dashboard_ticket"]},
    {"delivery": "claim", "available": ["open_connection_dialog"] * 2},
    {"delivery": "claim", "available": [], "extra": True},
])
def test_native_poll_is_strict_and_actual_pipe_refuses_foreign_sid(gateway, payload):
    assert callable(getattr(gateway, "native_claim", None)), "actual native claim is missing"
    command = poll_command(gateway).model_copy(update={"payload_nonsecret": payload})
    with pytest.raises(PermissionError, match="native_command_denied"):
        gateway.native_claim(command)
    pipe = NativePipeServer("native_relay", uuid4().hex, gateway.tickets,
                            command_handler=gateway.native_claim)
    with pytest.raises(PermissionError, match="native_peer_denied"):
        pipe.dispatch(json.dumps(command.model_dump(mode="json")).encode(), "S-1-5-21-foreign")


def test_launcher_controller_has_background_native_claim():
    from tg_assistant.desktop.runtime_controller import RuntimeController

    assert callable(getattr(RuntimeController, "claim_native_dialogs", None)), (
        "launcher cannot retrieve native dialog requests"
    )


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows worker and named pipe")
def test_hidden_launcher_opens_real_provider_modal_from_browser(qt_application, tmp_path, monkeypatch):
    import time

    from desktop.test_launcher import controller, ready, stop
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest

    from tg_assistant.config import Settings
    from tg_assistant.desktop.app import LauncherWindow
    from tg_assistant.desktop.dialogs.provider import ProviderDialog

    for name in list(os.environ):
        if name.startswith("TG_ASSISTANT_"):
            monkeypatch.delenv(name)
    settings = Settings(_env_file=None, data_dir=tmp_path / "profile")
    runtime = controller(settings, tmp_path)
    window = LauncherWindow(runtime)
    seen = []
    observer = QTimer()

    def inspect():
        for widget in qt_application.topLevelWidgets():
            if isinstance(widget, ProviderDialog) and widget.isVisible():
                seen.append((widget.secret_input.text(), widget.windowTitle()))
                widget.reject()
                if window.setup_dialog is not None:
                    window.setup_dialog.reject()

    observer.timeout.connect(inspect)
    observer.start(25)
    try:
        window.hide_on_close = True
        window.show()
        QTest.qWait(50)
        state = ready(runtime)
        window.close()
        assert not window.isVisible()
        with httpx.Client(base_url=state.url, trust_env=False, timeout=3) as client:
            launch = runtime.open_dashboard().result(timeout=5)
            token = launch.split("#launch_ticket=", 1)[1]
            response = client.post("/api/v1/auth/launch/redeem", json={"ticket": token},
                                   headers={"Origin": state.url})
            assert response.status_code == 200
            headers = {"Origin": state.url, "X-CSRF-Token": response.json()["csrf_token"]}
            limit = time.monotonic() + 12
            while time.monotonic() < limit:
                QTest.qWait(25)
                availability = client.get("/api/v1/native/dialogs")
                if availability.status_code == 200 and availability.json()["commands"]:
                    break
            assert availability.json()["commands"] == ["open_connection_dialog", "open_telegram_login"], (
                "hidden launcher did not advertise an actual provider handler"
            )
            command = dict(name="open_connection_dialog", request_id=uuid4().hex,
                           profile_id=settings.profile_id, payload_nonsecret={})
            queued = client.post("/api/v1/native/commands", json=command, headers=headers)
            assert queued.status_code == 200 and queued.json()["state"] == "queued"
            limit = time.monotonic() + 12
            while not seen and time.monotonic() < limit:
                QTest.qWait(25)
            assert seen == [("", "Kết nối AI • Telegram AI")], (
                "request did not open the actual native dialog once"
            )
            assert window.isVisible()
    finally:
        observer.stop()
        window.finish_setup()
        if hasattr(window, "finish_native_dialogs"):
            window.finish_native_dialogs()
        window.hide_on_close = False
        window.close()
        stop(runtime)
