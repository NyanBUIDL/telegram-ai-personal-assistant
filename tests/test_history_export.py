from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tg_assistant.db.models import PermissionName, TelegramChat, TelegramMessage
from tg_assistant.policy import PolicyEngine
from tg_assistant.services.history_export import history_csv_row, parse_search_terms
from tg_assistant.telegram.user_client import UserClientAdapter


def test_history_export_marks_promotion_and_requested_terms() -> None:
    chat = TelegramChat(
        chat_id=-100123,
        title="Source",
        chat_type="channel",
        username="source_channel",
    )
    message = TelegramMessage(
        chat_id=chat.chat_id,
        message_id=42,
        text="Airdrop SOL: đăng ký tại https://example.com",
        sent_at=datetime(2026, 1, 1, tzinfo=UTC),
        has_media=False,
    )

    row, is_promotion, is_match = history_csv_row(
        chat, message, parse_search_terms("sol, bitcoin")
    )

    assert is_promotion and is_match
    assert row[8] == "yes"
    assert "link" in row[9]
    assert row[10] == "yes"
    assert row[11] == "sol"
    assert row[12] == "https://t.me/source_channel/42"


class FakeHistoryClient:
    def __init__(self, messages: list[SimpleNamespace]) -> None:
        self.messages = messages
        self.max_ids: list[int] = []

    def iter_messages(self, _chat_id: int, *, max_id: int, limit: int):
        self.max_ids.append(max_id)

        async def iterate():
            eligible = [
                item for item in self.messages if max_id == 0 or item.id < max_id
            ]
            for item in sorted(eligible, key=lambda item: item.id, reverse=True)[:limit]:
                yield item

        return iterate()


def fake_message(message_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        chat_id=-100123,
        id=message_id,
        sender_id=10,
        message=f"post {message_id}",
        date=datetime(2026, 1, message_id, tzinfo=UTC),
        edit_date=None,
        reply_to=None,
        out=False,
        media=None,
    )


@pytest.mark.asyncio
async def test_backfill_page_moves_exclusive_cursor_toward_old_posts(session) -> None:
    policy = PolicyEngine()
    await policy.set_allowed(session, -100123, True)
    await policy.set_permission(session, -100123, PermissionName.SYNC_HISTORY, True)
    adapter = object.__new__(UserClientAdapter)
    adapter.policy = policy
    adapter.client = FakeHistoryClient([fake_message(1), fake_message(2), fake_message(3)])

    first = await adapter.backfill_history_page(
        session,
        chat_id=-100123,
        actor_id=1,
        owner_id=1,
        before_message_id=0,
        limit=2,
    )
    second = await adapter.backfill_history_page(
        session,
        chat_id=-100123,
        actor_id=1,
        owner_id=1,
        before_message_id=first.next_before_message_id,
        limit=2,
    )

    assert (first.synced_count, first.next_before_message_id, first.completed) == (2, 2, False)
    assert (second.synced_count, second.next_before_message_id, second.completed) == (1, 1, True)
    assert adapter.client.max_ids == [0, 2]
