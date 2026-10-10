from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from tg_assistant.ai.budget import BudgetService, estimate_cost
from tg_assistant.db.models import AiUsage, TelegramChat, TelegramMessage
from tg_assistant.policy import PolicyEngine
from tg_assistant.services.search import SearchService, parse_search_query
from tg_assistant.telegram.pairing import PairingCode


@pytest.mark.asyncio
async def test_message_dedup_and_permission_filtered_search(session) -> None:
    session.add(TelegramChat(chat_id=100, title="Allowed", chat_type="group"))
    session.add(TelegramChat(chat_id=200, title="Blocked", chat_type="group"))
    await session.flush()
    now = datetime.now(UTC)
    session.add_all(
        [
            TelegramMessage(
                chat_id=100,
                message_id=1,
                text="deadline dự án",
                sent_at=now - timedelta(minutes=1),
            ),
            TelegramMessage(
                chat_id=100,
                message_id=2,
                text="deadline token=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
                sent_at=now,
            ),
            TelegramMessage(chat_id=200, message_id=1, text="deadline bí mật", sent_at=now),
        ]
    )
    policy = PolicyEngine()
    await policy.apply_template(session, 100, "knowledge")
    await session.flush()
    results = await SearchService(policy).keyword(
        session, "deadline", owner_id=1, actor_id=1, chat_ids=[100, 200]
    )
    assert {(item.chat_id, item.message_id) for item in results} == {(100, 1), (100, 2)}
    assert [item.message_id for item in results] == [2, 1]
    assert all("ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in item.text for item in results)
    blocked_results = await SearchService(policy).keyword(
        session,
        "deadline in:200",
        owner_id=1,
        actor_id=1,
        chat_ids=[100],
        preauthorized=True,
    )
    assert blocked_results == []

    session.add(TelegramMessage(chat_id=100, message_id=1, text="duplicate", sent_at=now))
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_keyword_search_defaults_to_recent_window_but_allows_explicit_dates(session) -> None:
    session.add(TelegramChat(chat_id=300, title="Recent", chat_type="group"))
    await session.flush()
    now = datetime.now(UTC)
    session.add_all(
        [
            TelegramMessage(
                chat_id=300,
                message_id=1,
                text="market signal",
                sent_at=now - timedelta(days=8),
            ),
            TelegramMessage(
                chat_id=300,
                message_id=2,
                text="market signal",
                sent_at=now - timedelta(days=1),
            ),
        ]
    )
    policy = PolicyEngine()
    await policy.apply_template(session, 300, "knowledge")
    await session.flush()

    service = SearchService(policy)
    recent = await service.keyword(
        session,
        "market signal",
        owner_id=1,
        actor_id=1,
        chat_ids=[300],
        default_after=now - timedelta(days=7),
    )
    historical = await service.keyword(
        session,
        "market signal after:2026-01-01 before:2027-01-01",
        owner_id=1,
        actor_id=1,
        chat_ids=[300],
        default_after=now - timedelta(days=7),
    )

    assert [item.message_id for item in recent] == [2]
    assert {item.message_id for item in historical} == {1, 2}


@pytest.mark.asyncio
async def test_ai_budget_limit(session) -> None:
    service = BudgetService(daily_limit=1.0, monthly_limit=10.0)
    session.add(
        AiUsage(
            occurred_at=datetime.now(UTC),
            model="test",
            operation="answer",
            input_tokens=1,
            output_tokens=1,
            estimated_cost_usd=1.0,
            success=True,
        )
    )
    await session.flush()
    with pytest.raises(RuntimeError):
        await service.ensure_available(session)


@pytest.mark.asyncio
async def test_projected_ai_request_cannot_cross_budget(session) -> None:
    service = BudgetService(daily_limit=0.001, monthly_limit=0.001)
    with pytest.raises(RuntimeError):
        await service.ensure_request_within_budget(
            session,
            model="gpt-5.6-sol",
            input_tokens=1000,
            output_tokens=1000,
        )


def test_official_default_model_cost_estimate() -> None:
    # Official 2026-10-03 snapshot: >272k whole-request tier, $4/$18 per million.
    # https://developers.openai.com/api/docs/models/gpt-5.6-terra
    assert estimate_cost("gpt-5.6-terra", 1_000_000, 1_000_000) == 22.0
    assert estimate_cost("gpt-5.6-terra", 1000, 1000) == 0.014
    assert estimate_cost("text-embedding-3-small", 1_000_000, 0) == 0.02
    with pytest.raises(ValueError):
        estimate_cost("unknown-model", 1, 1)


def test_pairing_code_owner_expiry_and_single_use() -> None:
    code = PairingCode.create(42)
    assert not code.consume(code.value, 7)
    assert code.consume(code.value.lower(), 42)
    assert not code.consume(code.value, 42)


def test_search_filter_parser() -> None:
    parsed = parse_search_query(
        "deadline in:-100 from:42 after:2026-07-01 before:2026-07-31 has:link type:task"
    )
    assert parsed.text == "deadline"
    assert parsed.chat_id == -100
    assert parsed.sender_id == 42
    assert parsed.has == "link"
    assert parsed.content_type == "task"
    assert parsed.before is not None and parsed.before.day == 1
