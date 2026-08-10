from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tg_assistant.db.models import TelegramChat, TelegramMessage
from tg_assistant.policy import PolicyEngine
from tg_assistant.services.daily_digest import (
    DigestItem,
    deduplicate_digest_items,
    digest_citation_priority,
    digest_source_list,
    load_daily_digest_dataset,
    prefer_primary_citations,
    referenced_item_indexes,
    select_digest_context,
)


def _item(chat_id: int, message_id: int, text: str) -> DigestItem:
    return DigestItem(
        chat_id=chat_id,
        message_id=message_id,
        title=f"Nguồn {chat_id}",
        username=None,
        chat_type="group",
        text=text,
        sent_at=datetime.now(UTC),
    )


def test_daily_digest_removes_cross_source_duplicates_and_selects_fairly() -> None:
    items = [
        _item(-1, 1, "Bitcoin tăng mạnh sau thông tin tích cực từ thị trường."),
        _item(
            -2,
            2,
            "Bitcoin tăng mạnh sau thông tin tích cực từ thị trường! https://example.com",
        ),
        _item(-2, 3, "Ethereum công bố bản nâng cấp mạng lưới quan trọng trong hôm nay."),
        _item(-3, 4, "ok"),
    ]

    unique, unsuitable, duplicates, stats = deduplicate_digest_items(items)
    selected = select_digest_context(unique, stats, max_context_chars=10_000)

    assert len(unique) == 2
    assert unsuitable == 1
    assert duplicates == 1
    assert {item.chat_id for item in selected} == {-1, -2}
    assert stats[-2].duplicate_count == 1


def test_daily_digest_prioritizes_coin68_direct_publisher() -> None:
    now = datetime.now(UTC)
    repost = DigestItem(-1, 1, "Repost", None, "channel", "Other market news", now)
    coin68 = DigestItem(
        -2,
        2,
        "Coin68",
        "coin68",
        "channel",
        "Direct publisher article https://coin68.com/article",
        now - timedelta(minutes=10),
    )

    unique, _unsuitable, _duplicates, stats = deduplicate_digest_items([repost, coin68])
    selected = select_digest_context(unique, stats, max_context_chars=10_000)

    assert digest_citation_priority(coin68) == "primary_publisher"
    assert selected[0].chat_id == coin68.chat_id


def test_daily_digest_replaces_matching_repost_citation_with_coin68() -> None:
    now = datetime.now(UTC)
    coin68 = DigestItem(
        -2,
        2,
        "Coin68",
        "coin68",
        "channel",
        "Nga truy na Pavel Durov Telegram sau cao buoc khung bo https://coin68.com/article",
        now,
    )
    repost = DigestItem(
        -1,
        1,
        "GFI",
        None,
        "channel",
        "Nga dua Pavel Durov Telegram vao danh sach truy na quoc te",
        now,
    )

    answer = prefer_primary_citations("Tin moi [S2]", [coin68, repost])

    assert answer == "Tin moi [S1]"


@pytest.mark.asyncio
async def test_daily_digest_reports_joined_authorized_and_active_sources(session) -> None:
    chats = [
        TelegramChat(chat_id=-1, title="A", chat_type="group"),
        TelegramChat(chat_id=-2, title="B", chat_type="channel"),
        TelegramChat(chat_id=-3, title="C", chat_type="supergroup"),
    ]
    session.add_all(chats)
    await session.flush()
    policy = PolicyEngine()
    await policy.apply_template(session, -1, "knowledge")
    await policy.apply_template(session, -2, "knowledge")
    now = datetime.now(UTC)
    session.add_all(
        [
            TelegramMessage(
                chat_id=-1,
                message_id=1,
                text="Thông tin thị trường có nội dung phù hợp để tổng hợp hôm nay.",
                sent_at=now,
            ),
            TelegramMessage(
                chat_id=-2,
                message_id=2,
                text="Một dự án vừa công bố sản phẩm mới quan trọng cho cộng đồng.",
                sent_at=now,
            ),
            TelegramMessage(
                chat_id=-3,
                message_id=3,
                text="Tin này không được đọc vì nguồn chưa cấp quyền tổng hợp.",
                sent_at=now,
            ),
        ]
    )
    await session.flush()

    dataset = await load_daily_digest_dataset(
        session,
        since=now - timedelta(hours=1),
        until=now + timedelta(hours=1),
    )

    assert dataset.joined_source_count == 3
    assert dataset.authorized_source_count == 2
    assert dataset.missing_source_count == 1
    assert dataset.raw_message_count == 2
    assert dataset.active_source_count == 2
    assert "DANH SÁCH NGUỒN ĐÃ TỔNG HỢP (2)" in digest_source_list(dataset)


def test_daily_digest_uses_only_citations_referenced_by_ai() -> None:
    assert referenced_item_indexes(
        "Tin thứ ba [S3], rồi tin đầu [S1] và lặp lại [S3].",
        item_count=4,
    ) == [2, 0]
    assert referenced_item_indexes("Không có mã nguồn.", item_count=20) == list(range(10))
