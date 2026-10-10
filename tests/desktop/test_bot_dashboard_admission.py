"""Revoked owning bot proof must withdraw sessions and native ticket authority."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from tg_assistant.desktop.ipc import NativePipeServer
from tg_assistant.desktop.worker import RuntimeGateway
from tg_assistant.paths import current_user_sid


@pytest.mark.parametrize("proof", [None, False, 1, "true"])
def test_gateway_withdraws_management_without_exact_current_private_admission(proof):
    gateway = RuntimeGateway(18765, uuid4().hex, profile_id="bot-gate")
    admitted = [True]
    gateway.runtime_owner = SimpleNamespace(
        _closed=False, stopping=SimpleNamespace(is_set=lambda: False),
        management_admitted=lambda: admitted[0],
    )
    admin = SimpleNamespace(state=SimpleNamespace(admin_context=SimpleNamespace(owner_id=123)))
    gateway.admin = admin
    issued = gateway.tickets.issue("bot-gate", current_user_sid(), datetime.now(UTC))
    session = gateway.tickets.redeem(issued.raw_ticket, gateway.origin, datetime.now(UTC))
    assert session.authority == "management"
    admitted[0] = proof
    assert gateway.admin is None, "A paired SQL bool is not current Bot API admission"
    assert gateway.tickets.get_session(session.token) is None
    ticket = gateway.tickets.issue("bot-gate", current_user_sid(), datetime.now(UTC))
    setup = gateway.tickets.redeem(ticket.raw_ticket, gateway.origin, datetime.now(UTC))
    assert setup.authority == "setup_only" and setup.owner_id is None


def test_native_ticket_path_rechecks_bot_admission_without_http_request():
    gateway = RuntimeGateway(18765, uuid4().hex, profile_id="bot-gate")
    admission = [True]
    gateway.runtime_owner = SimpleNamespace(
        _closed=False, stopping=SimpleNamespace(is_set=lambda: False),
        management_admitted=lambda: admission[0],
    )
    gateway.admin = SimpleNamespace(state=SimpleNamespace(admin_context=SimpleNamespace(owner_id=123)))
    assert hasattr(gateway, "before_dashboard_ticket"), "Native ticket admission hook is missing"
    pipe = NativePipeServer(
        "bot-gate", uuid4().hex, gateway.tickets,
        before_ticket=gateway.before_dashboard_ticket,
    )
    command = json.dumps(dict(
        name="issue_dashboard_ticket", request_id=uuid4().hex,
        profile_id="bot-gate", payload_nonsecret={},
    )).encode()
    admission[0] = False
    result = pipe.dispatch(command, current_user_sid())
    session = gateway.tickets.redeem(result["raw_ticket"], gateway.origin, datetime.now(UTC))
    assert session.authority == "setup_only" and session.owner_id is None


def test_gateway_admission_failure_does_not_retain_private_exception():
    gateway = RuntimeGateway(18765, uuid4().hex)

    def fail():
        raise RuntimeError("synthetic-private-admission-marker")

    gateway.runtime_owner = SimpleNamespace(
        _closed=False, stopping=SimpleNamespace(is_set=lambda: False), management_admitted=fail,
    )
    gateway.admin = SimpleNamespace(state=SimpleNamespace(admin_context=SimpleNamespace(owner_id=123)))
    assert gateway.admin is None


async def test_ticket_redemption_rechecks_after_streamed_body_withdrawal():
    gateway = RuntimeGateway(18765, uuid4().hex, profile_id="bot-gate")
    admitted = [True]
    gateway.runtime_owner = SimpleNamespace(
        _closed=False, stopping=SimpleNamespace(is_set=lambda: False),
        management_admitted=lambda: admitted[0],
    )
    gateway.admin = SimpleNamespace(state=SimpleNamespace(admin_context=SimpleNamespace(owner_id=123)))
    ticket = gateway.tickets.issue("bot-gate", current_user_sid(), datetime.now(UTC))

    async def body():
        yield b'{"ticket":"'
        admitted[0] = False
        yield ticket.raw_ticket.encode() + b'"}'

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway), base_url=gateway.origin,
    ) as client:
        response = await client.post(
            "/api/v1/auth/launch/redeem", content=body(), headers={"Origin": gateway.origin},
        )
    assert response.status_code == 401
    assert not response.cookies
