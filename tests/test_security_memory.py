from __future__ import annotations

import pytest

from tg_assistant.security import contains_secret, redact
from tg_assistant.services.memory import MemoryService


def test_secret_redaction() -> None:
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    value = {"token": token, "message": f"bot token={token}"}
    redacted = redact(value)
    assert redacted["token"] == "[REDACTED]"
    assert token not in redacted["message"]
    assert contains_secret(token)


def test_coingecko_key_is_detected_and_redacted() -> None:
    key = "CG-exampleSecretKey123456789012345"

    assert contains_secret(key)
    assert key not in str(redact(f"coingecko={key}"))


@pytest.mark.asyncio
async def test_memory_scope_and_confirmation(session) -> None:
    service = MemoryService()
    with pytest.raises(PermissionError):
        await service.create(
            session,
            content="Quy ước viết báo cáo ngắn gọn",
            memory_type="semantic",
            scope_type="private",
            scope_id=None,
            confirmed=False,
        )
    memory = await service.create(
        session,
        content="Quy ước viết báo cáo ngắn gọn",
        memory_type="semantic",
        scope_type="private",
        scope_id=None,
        confirmed=True,
    )
    assert memory.status == "active"


@pytest.mark.asyncio
async def test_memory_rejects_secret(session) -> None:
    with pytest.raises(ValueError):
        await MemoryService().create(
            session,
            content="token=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
            memory_type="semantic",
            scope_type="private",
            scope_id=None,
            confirmed=True,
        )
