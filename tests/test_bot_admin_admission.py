"""Standalone legacy login cannot bypass current private bot admission."""

# ruff: noqa: F811 - imported disposable ASGI/SQLite fixture

import pytest
from test_admin_api import admin_client  # noqa: F401

from tg_assistant.admin_api import dashboard_login_code


@pytest.mark.parametrize("proof", [None, False, 1, "true"])
async def test_standalone_valid_login_code_does_not_create_unpaired_management(admin_client, proof):
    client, secret = admin_client
    context = client._transport.app.state.admin_context
    if hasattr(context, "management_admission"):
        context.management_admission = None if proof is None else lambda: proof
    response = await client.post(
        "/api/v1/auth/login", json={"code": dashboard_login_code(secret)},
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert response.status_code == 403, "Legacy dashboard secret cannot establish bot pairing"
    assert "owner_pairing_required" in response.text
    assert not response.cookies


async def test_standalone_existing_session_withdraws_after_bot_revocation(admin_client):
    client, secret = admin_client
    context = client._transport.app.state.admin_context
    assert hasattr(context, "management_admission"), "Standalone owning admission is missing"
    context.management_admission = lambda: True
    response = await client.post(
        "/api/v1/auth/login", json={"code": dashboard_login_code(secret)},
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert response.status_code == 200
    assert (await client.get("/api/v1/auth/session")).status_code == 200
    context.management_admission = lambda: False
    assert (await client.get("/api/v1/auth/session")).status_code == 403
    assert (await client.get("/api/v1/overview")).status_code == 403
    context.management_admission = lambda: True
    assert (await client.get("/api/v1/auth/session")).status_code == 401


async def test_legacy_login_rechecks_after_request_body_revocation(admin_client):
    client, secret = admin_client
    context = client._transport.app.state.admin_context
    admitted = [True]
    context.management_admission = lambda: admitted[0]

    async def body():
        yield b'{"code":"'
        admitted[0] = False
        yield dashboard_login_code(secret).encode() + b'"}'

    response = await client.post(
        "/api/v1/auth/login", content=body(),
        headers={"Origin": "http://127.0.0.1:8765", "Content-Type": "application/json"},
    )
    assert response.status_code == 403
    assert not response.cookies


async def test_standalone_admin_withdrawal_rolls_back_already_flushed_write(admin_client):
    from sqlalchemy import select

    from tg_assistant.db.models import AppSetting

    client, _secret = admin_client
    context = client._transport.app.state.admin_context
    admitted = [True]
    context.management_admission = lambda: admitted[0]
    with pytest.raises(PermissionError, match="owner_pairing_required"):
        async with context.database.session() as session:
            session.add(AppSetting(key="o04_uncommitted", value=True))
            await session.flush()
            admitted[0] = False
    async with context.database.engine.connect() as connection:
        assert await connection.scalar(
            select(AppSetting.key).where(AppSetting.key == "o04_uncommitted")
        ) is None
