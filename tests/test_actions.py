from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tg_assistant.services.actions import PendingActionService


@pytest.mark.asyncio
async def test_pending_action_is_single_use(session) -> None:
    service = PendingActionService(ttl_seconds=300)
    action = await service.create(
        session,
        action_type="delete_message",
        requested_by=42,
        payload={"delete_any": False},
        chat_id=100,
        message_id=9,
        preview="message preview",
    )
    await session.flush()
    confirmed = await service.confirm(session, action.action_id, 42)
    assert confirmed.status == "confirmed"
    with pytest.raises(ValueError):
        await service.confirm(session, action.action_id, 42)


@pytest.mark.asyncio
async def test_pending_action_expiry_and_owner(session) -> None:
    service = PendingActionService(ttl_seconds=300)
    action = await service.create(
        session,
        action_type="send_message",
        requested_by=42,
        payload={"text": "hello"},
        chat_id=100,
    )
    await session.flush()
    with pytest.raises(PermissionError):
        await service.confirm(session, action.action_id, 7)

    action.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(TimeoutError):
        await service.confirm(session, action.action_id, 42)
    assert action.status == "expired"


@pytest.mark.asyncio
async def test_action_validation_blocks_wildcard_and_secret(session) -> None:
    service = PendingActionService()
    with pytest.raises(ValueError):
        await service.create(
            session,
            action_type="delete_message",
            requested_by=42,
            payload={"delete_any": True},
            chat_id=100,
            message_id=1,
        )
    with pytest.raises(ValueError):
        await service.create(
            session,
            action_type="send_message",
            requested_by=42,
            payload={"text": "token=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"},
            chat_id=100,
        )


@pytest.mark.asyncio
async def test_action_preview_and_reason_are_redacted(session) -> None:
    service = PendingActionService()
    action = await service.create(
        session,
        action_type="delete_message",
        requested_by=42,
        payload={"delete_any": False},
        chat_id=100,
        message_id=1,
        preview="token=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
        reason="otp=123456",
    )
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in (action.preview or "")
    assert action.reason == "[REDACTED]"


@pytest.mark.asyncio
async def test_guided_moderation_actions_require_a_specific_chat(session) -> None:
    service = PendingActionService()
    for action_type, payload in (
        ("setup_moderation", {"mode": "review_first"}),
        ("set_admin_only_auto_moderation", {"enabled": True}),
        ("set_link_spam_auto_moderation", {"enabled": True}),
        ("set_group_ai_ask", {"enabled": True}),
        ("sync_chat_history", {"limit": 1000}),
        ("enable_group_learning", {"limit": 1000, "embedding": True}),
    ):
        with pytest.raises(ValueError):
            await service.create(
                session,
                action_type=action_type,
                requested_by=42,
                payload=payload,
            )
        action = await service.create(
            session,
            action_type=action_type,
            requested_by=42,
            payload=payload,
            chat_id=-1001,
            preview="safe preview",
        )
        assert action.chat_id == -1001


@pytest.mark.asyncio
async def test_bulk_learning_action_accepts_many_groups_without_single_chat_id(session) -> None:
    service = PendingActionService()
    many_chat_ids = [-(index + 1) for index in range(150)]
    action = await service.create(
        session,
        action_type="enable_group_learning_bulk",
        requested_by=42,
        payload={"chat_ids": many_chat_ids, "limit": 1000},
        preview="Học từ 150 group",
    )

    assert action.chat_id is None
    assert action.payload["chat_ids"] == many_chat_ids

    with pytest.raises(ValueError):
        await service.create(
            session,
            action_type="enable_group_learning_bulk",
            requested_by=42,
            payload={"chat_ids": []},
        )
    with pytest.raises(ValueError):
        await service.create(
            session,
            action_type="enable_group_learning_bulk",
            requested_by=42,
            payload={"chat_ids": list(range(501))},
        )
