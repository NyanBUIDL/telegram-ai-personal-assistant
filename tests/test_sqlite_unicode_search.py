from __future__ import annotations

import asyncio
import itertools
import unicodedata
from datetime import UTC, datetime, timedelta

from tg_assistant.db.base import Base, Database
from tg_assistant.db.models import TelegramChat, TelegramMessage
from tg_assistant.policy import PolicyEngine
from tg_assistant.services.search import SearchService
from tg_assistant.telegram.control_bot import group_search_query

OWNER = 1
CHAT = -1001
OTHER_CHAT = -1002
BASE = datetime(2026, 1, 1, tzinfo=UTC)


_db_counter = itertools.count(1)


def run_search(tmp_path, rows, query, *, limit=20, chat_ids=(CHAT,)):
    async def go():
        # Fresh disposable database per call: seeding is not idempotent.
        db = Database(f"sqlite+aiosqlite:///{tmp_path / f'u{next(_db_counter)}.db'}")
        try:
            async with db.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with db.session() as session:
                for chat in (CHAT, OTHER_CHAT):
                    session.add(TelegramChat(chat_id=chat, title=f"c{chat}", chat_type="supergroup"))
                await session.flush()
                for index, (chat, text) in enumerate(rows, start=1):
                    session.add(
                        TelegramMessage(
                            chat_id=chat,
                            message_id=index,
                            text=text,
                            sent_at=BASE + timedelta(minutes=index),
                        )
                    )
            async with db.session() as session:
                found = await SearchService(PolicyEngine()).keyword(
                    session,
                    query,
                    owner_id=OWNER,
                    actor_id=OWNER,
                    chat_ids=list(chat_ids),
                    limit=limit,
                    preauthorized=True,
                )
                return [item.message_id for item in found]
        finally:
            await db.close()

    return asyncio.run(go())


def test_uppercase_and_nfd_match_without_stripping_accents(tmp_path):
    nfd = unicodedata.normalize("NFD", "Hợp đồng tháng này")
    rows = [
        (CHAT, nfd),  # 1: stored NFD
        (CHAT, "HỢP ĐỒNG THÁNG"),  # 2: stored uppercase
        (CHAT, "hop dong thang"),  # 3: accent-stripped text must not match
        (CHAT, "dong"),  # 4: d != đ
    ]
    assert sorted(run_search(tmp_path, rows, "hợp đồng")) == [1, 2]
    assert sorted(run_search(tmp_path, rows, unicodedata.normalize("NFD", "HỢP ĐỒNG"))) == [1, 2]
    assert run_search(tmp_path, rows, "hop dong") == [3]
    assert sorted(run_search(tmp_path, rows, "đồng")) == [1, 2]
    assert sorted(run_search(tmp_path, rows, "dong")) == [3, 4]


def test_percent_and_underscore_are_literal(tmp_path):
    rows = [
        (CHAT, "giảm 50% hôm nay"),
        (CHAT, "giảm 505 hôm nay"),
        (CHAT, "file_a.txt"),
        (CHAT, "fileXa.txt"),
    ]
    assert run_search(tmp_path, rows, "50%") == [1]
    assert run_search(tmp_path, rows, "file_a") == [3]


def test_type_and_has_filters_with_unicode_and_scope(tmp_path):
    rows = [
        (CHAT, "QUYẾT ĐỊNH chốt phương án"),  # 1
        (CHAT, "Thống nhất lịch họp"),  # 2
        (CHAT, "CẦN gửi báo cáo"),  # 3
        (CHAT, "Deadline thứ sáu"),  # 4
        (CHAT, "xem HTTPS://example.test/x"),  # 5
        (OTHER_CHAT, "quyết định ở nhóm khác"),  # 6: outside scope
        (CHAT, "quyet dinh khong dau"),  # 7: stripped, no match
    ]
    assert run_search(tmp_path, rows, "type:decision") == [2, 1]  # sent_at desc
    assert run_search(tmp_path, rows, "type:task") == [4, 3]
    assert run_search(tmp_path, rows, "has:link") == [5]
    assert run_search(tmp_path, rows, "type:decision quyết") == [1]
    assert run_search(tmp_path, rows, "type:decision", limit=1) == [2]
    assert run_search(tmp_path, rows, "type:decision", chat_ids=(OTHER_CHAT,)) == [6]


def test_control_bot_group_search_query_folds(tmp_path):
    async def go():
        db = Database(f"sqlite+aiosqlite:///{tmp_path / 'g.db'}")
        try:
            async with db.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with db.session() as session:
                session.add_all(
                    [
                        TelegramChat(chat_id=-1, title="Nhóm ĐẦU TƯ", chat_type="supergroup"),
                        TelegramChat(chat_id=-2, title="Nhom dau tu", chat_type="supergroup"),
                        TelegramChat(chat_id=-3, title="a_b", chat_type="supergroup"),
                        TelegramChat(chat_id=-4, title="axb", chat_type="supergroup"),
                    ]
                )
            async with db.session() as session:
                async def titles(text):
                    rows = (await session.scalars(group_search_query(text))).all()
                    return sorted(row.title for row in rows)

                assert await titles("đầu tư") == ["Nhóm ĐẦU TƯ"]
                assert await titles("a_b") == ["a_b"]
        finally:
            await db.close()

    asyncio.run(go())
