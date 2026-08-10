from __future__ import annotations

import pytest
from sqlalchemy import select

from tg_assistant.db.models import ModerationRule, PermissionName
from tg_assistant.policy import (
    DecisionReason,
    PolicyContext,
    PolicyEngine,
    SlidingWindowLimiter,
)


@pytest.mark.asyncio
async def test_default_deny_and_owner_only(session) -> None:
    engine = PolicyEngine()
    outsider = await engine.evaluate(
        session, PolicyContext(2, 1, 100, PermissionName.READ_MESSAGES)
    )
    assert outsider.reason is DecisionReason.NOT_OWNER

    blocked = await engine.evaluate(session, PolicyContext(1, 1, 100, PermissionName.READ_MESSAGES))
    assert blocked.reason is DecisionReason.CHAT_BLOCKED


@pytest.mark.asyncio
async def test_permission_matrix_and_telegram_right(session) -> None:
    engine = PolicyEngine()
    await engine.set_allowed(session, 100, True)
    await engine.set_permission(session, 100, PermissionName.SEND_MESSAGES, True)
    await session.flush()

    missing = await engine.evaluate(session, PolicyContext(1, 1, 100, PermissionName.SEND_MESSAGES))
    assert missing.reason is DecisionReason.TELEGRAM_RIGHT_MISSING

    allowed = await engine.evaluate(
        session,
        PolicyContext(1, 1, 100, PermissionName.SEND_MESSAGES, frozenset({"send_messages"})),
    )
    assert allowed.allowed


@pytest.mark.asyncio
async def test_destructive_action_needs_confirmation(session) -> None:
    engine = PolicyEngine()
    await engine.set_allowed(session, 100, True)
    await engine.set_permission(session, 100, PermissionName.DELETE_ANY_MESSAGES, True)
    await session.flush()

    decision = await engine.evaluate(
        session,
        PolicyContext(
            1,
            1,
            100,
            PermissionName.DELETE_ANY_MESSAGES,
            frozenset({"delete_messages"}),
        ),
    )
    assert decision.reason is DecisionReason.CONFIRMATION_REQUIRED
    assert decision.requires_confirmation

    confirmed = await engine.evaluate(
        session,
        PolicyContext(
            1,
            1,
            100,
            PermissionName.DELETE_ANY_MESSAGES,
            frozenset({"delete_messages"}),
            confirmed=True,
        ),
    )
    assert confirmed.allowed


@pytest.mark.asyncio
async def test_block_revokes_permissions_immediately(session) -> None:
    engine = PolicyEngine()
    await engine.apply_template(session, 100, "knowledge")
    await engine.set_allowed(session, 100, False)
    await session.flush()
    decision = await engine.evaluate(
        session, PolicyContext(1, 1, 100, PermissionName.SEARCH_MESSAGES)
    )
    assert decision.reason is DecisionReason.CHAT_BLOCKED


@pytest.mark.asyncio
async def test_moderation_bundle_is_review_first_and_searchable(session) -> None:
    engine = PolicyEngine()
    await engine.apply_moderation_bundle(session, 100)
    await session.flush()

    for permission in (
        PermissionName.READ_MESSAGES,
        PermissionName.MONITOR_NEW_MESSAGES,
        PermissionName.MODERATE_MESSAGES,
        PermissionName.DELETE_ANY_MESSAGES,
        PermissionName.SYNC_HISTORY,
        PermissionName.SEARCH_MESSAGES,
    ):
        decision = await engine.evaluate(
            session,
            PolicyContext(
                1,
                1,
                100,
                permission,
                frozenset({"delete_messages"}),
            ),
        )
        if permission in {
            PermissionName.MODERATE_MESSAGES,
            PermissionName.DELETE_ANY_MESSAGES,
        }:
            assert decision.requires_confirmation
        else:
            assert decision.allowed

    automatic = await engine.evaluate(
        session,
        PolicyContext(1, 1, 100, PermissionName.AUTO_MODERATION),
    )
    assert automatic.reason is DecisionReason.PERMISSION_DISABLED


@pytest.mark.asyncio
async def test_link_spam_auto_moderation_can_be_enabled_and_disabled(session) -> None:
    engine = PolicyEngine()
    await engine.set_link_spam_auto_moderation(session, 100, enabled=True)
    await session.flush()

    automatic = await engine.evaluate(
        session,
        PolicyContext(1, 1, 100, PermissionName.AUTO_MODERATION),
    )
    rule = await session.scalar(
        select(ModerationRule).where(
            ModerationRule.chat_id == 100,
            ModerationRule.name == "non_admin_external_link_auto_delete",
        )
    )
    assert automatic.allowed
    assert rule is not None and rule.enabled and rule.mode == "auto_delete"
    assert rule.rule_json["scope"] == "new_messages_only"

    assert rule.rule_json["delete_when"] == "contains_external_link_and_sender_is_not_admin"

    await engine.set_link_spam_auto_moderation(session, 100, enabled=False)
    await session.flush()
    automatic = await engine.evaluate(
        session,
        PolicyContext(1, 1, 100, PermissionName.AUTO_MODERATION),
    )
    assert automatic.reason is DecisionReason.PERMISSION_DISABLED
    assert rule.enabled is False


@pytest.mark.asyncio
async def test_group_ai_ask_requires_allow_and_survives_permission_templates(session) -> None:
    engine = PolicyEngine()
    with pytest.raises(PermissionError):
        await engine.set_group_ai_ask(session, 100, enabled=True)

    await engine.set_allowed(session, 100, True)
    await engine.set_group_ai_ask(session, 100, enabled=True)
    await engine.apply_template(session, 100, "knowledge")
    await session.flush()

    enabled = await engine.evaluate(
        session,
        PolicyContext(1, 1, 100, PermissionName.GROUP_AI_ASK),
    )
    assert enabled.allowed

    await engine.set_group_ai_ask(session, 100, enabled=False)
    await session.flush()
    disabled = await engine.evaluate(
        session,
        PolicyContext(1, 1, 100, PermissionName.GROUP_AI_ASK),
    )
    assert disabled.reason is DecisionReason.PERMISSION_DISABLED


@pytest.mark.asyncio
async def test_source_ai_efficiency_is_saved_only_for_allowed_sources(session) -> None:
    engine = PolicyEngine()
    with pytest.raises(PermissionError):
        await engine.set_ai_efficiency(session, 100, preset="saving")

    await engine.set_allowed(session, 100, True)
    policy = await engine.set_ai_efficiency(
        session,
        100,
        preset="quality",
        filtering_level="strict",
        rag_top_k=5,
        rag_max_context_tokens=4_000,
    )

    assert policy.ai_efficiency_preset == "quality"
    assert policy.filtering_level == "strict"
    assert policy.rag_top_k == 5
    assert policy.rag_max_context_tokens == 4_000


@pytest.mark.asyncio
async def test_rate_limit(session) -> None:
    engine = PolicyEngine(SlidingWindowLimiter(limit=1, seconds=60))
    await engine.set_allowed(session, 100, True)
    await engine.set_permission(session, 100, PermissionName.READ_MESSAGES, True)
    await session.flush()
    first = await engine.evaluate(session, PolicyContext(1, 1, 100, PermissionName.READ_MESSAGES))
    second = await engine.evaluate(session, PolicyContext(1, 1, 100, PermissionName.READ_MESSAGES))
    assert first.allowed
    assert second.reason is DecisionReason.RATE_LIMITED


@pytest.mark.asyncio
async def test_bulk_read_authorization_consumes_one_rate_limit_for_many_sources(session) -> None:
    engine = PolicyEngine(SlidingWindowLimiter(limit=1, seconds=60))
    chat_ids = [-(index + 1) for index in range(150)]
    for chat_id in chat_ids:
        await engine.apply_template(session, chat_id, "knowledge")
    await session.flush()

    allowed = await engine.filter_allowed_chat_ids(
        session,
        actor_id=1,
        owner_id=1,
        chat_ids=chat_ids,
        permission=PermissionName.SEARCH_MESSAGES,
    )
    rate_limited = await engine.filter_allowed_chat_ids(
        session,
        actor_id=1,
        owner_id=1,
        chat_ids=chat_ids,
        permission=PermissionName.SEARCH_MESSAGES,
    )

    assert allowed == chat_ids
    assert rate_limited == []
