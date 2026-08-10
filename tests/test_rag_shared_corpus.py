from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tg_assistant.ai.rag import (
    RagService,
    rag_time_window,
    referenced_evidence_indexes,
    renumber_source_references,
    telegram_message_url,
    without_source_references,
)
from tg_assistant.db.models import TelegramChat, TelegramMessage
from tg_assistant.policy import PolicyEngine, SlidingWindowLimiter


class FakeAi:
    available = True
    provider = "ollama"
    model = "qwen3:8b"

    async def embed(self, session, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    async def answer(self, session, question: str, context: list[str]) -> str:
        assert question == "dữ liệu semantic"
        assert any("nguồn cuối cùng" in source for source in context)
        assert any("[S1]" in source for source in context)
        assert any("Thời gian đăng:" in source for source in context)
        return "Đã trả lời từ kho hợp nhất."


def test_visible_citations_are_renumbered_after_filtering() -> None:
    answer = "Nội dung từ nguồn thứ chín [S9] và nguồn thứ ba [S3]."
    assert (
        renumber_source_references(answer, [8, 2])
        == "Nội dung từ nguồn thứ chín [S1] và nguồn thứ ba [S2]."
    )


class FakeVectors:
    def __init__(self, expected_chat_ids: list[int]) -> None:
        self.expected_chat_ids = expected_chat_ids

    def search(
        self,
        vector: list[float],
        *,
        allowed_chat_ids: list[int],
        allowed_reference_ids: list[int] | None,
        limit: int,
    ) -> list[tuple[int, float, dict]]:
        assert allowed_chat_ids == self.expected_chat_ids
        assert allowed_reference_ids is not None
        assert len(allowed_reference_ids) == 1
        return [
            (
                1,
                0.99,
                {
                    "chat_id": self.expected_chat_ids[-1],
                    "message_id": 1,
                },
            )
        ]


@pytest.mark.asyncio
async def test_rag_queries_more_than_100_sources_as_one_corpus(session) -> None:
    chat_ids = [-(index + 1) for index in range(150)]
    for chat_id in chat_ids:
        session.add(TelegramChat(chat_id=chat_id, title=str(chat_id), chat_type="group"))
    await session.flush()

    policy = PolicyEngine(SlidingWindowLimiter(limit=1, seconds=60))
    for chat_id in chat_ids:
        await policy.apply_template(session, chat_id, "knowledge")
    session.add(
        TelegramMessage(
            chat_id=chat_ids[-1],
            message_id=1,
            text="nguồn cuối cùng",
            sent_at=datetime.now(UTC),
        )
    )
    await session.flush()

    rag = RagService(policy, FakeAi(), FakeVectors(chat_ids))
    answer = await rag.answer(
        session,
        "dữ liệu semantic",
        actor_id=1,
        owner_id=1,
        chat_ids=chat_ids,
    )

    assert answer.startswith("Đã trả lời từ kho hợp nhất.")
    assert "DẪN CHỨNG" in answer
    assert f"Tên group/channel: {chat_ids[-1]}" in answer
    assert "đăng lúc" in answer
    assert "https://t.me/" not in answer


def test_rag_time_window_is_exactly_seven_days_from_question_time() -> None:
    asked_at = datetime(2026, 7, 25, 15, 30, tzinfo=UTC)

    start, end = rag_time_window(asked_at)

    assert end == asked_at
    assert start == asked_at - timedelta(days=7)


def test_group_answer_removes_source_codes_and_evidence_appendix() -> None:
    answer = (
        "Dự án có cập nhật mới [S1].\n\n"
        "## DẪN CHỨNG\n"
        "• [S1] Group dự án — đăng lúc 10:00"
    )

    assert without_source_references(answer) == "Dự án có cập nhật mới."


def test_telegram_message_url_for_public_and_private_sources() -> None:
    public = TelegramChat(
        chat_id=-1001315055119,
        title="Coin68",
        username="Group_68Trading",
        chat_type="supergroup",
    )
    private = TelegramChat(
        chat_id=-1001315055119,
        title="Private",
        chat_type="supergroup",
    )
    basic_group = TelegramChat(
        chat_id=-5388593787,
        title="Basic",
        chat_type="group",
    )

    assert telegram_message_url(public, 123) == "https://t.me/Group_68Trading/123"
    assert telegram_message_url(private, 456) == "https://t.me/c/1315055119/456"
    assert telegram_message_url(basic_group, 789) is None


def test_rag_citations_only_include_sources_used_by_answer() -> None:
    assert referenced_evidence_indexes("Kết luận [S2] và [S1].", item_count=4) == [1, 0]
    assert referenced_evidence_indexes("Không có mã nguồn.", item_count=5) == [0, 1, 2]


class ScopedAi:
    available = True
    provider = "ollama"
    model = "qwen3:8b"

    async def embed(self, session, text: str) -> list[float]:
        assert text == "semantic scope"
        return [1.0, 0.0, 0.0]

    async def answer(self, session, question: str, context: list[str]) -> str:
        assert question == "semantic scope"
        assert context
        assert all("Ngoài phạm vi" not in item for item in context)
        return "Câu trả lời đúng phạm vi [S1]."


class ScopedVectors:
    def search(
        self,
        vector: list[float],
        *,
        allowed_chat_ids: list[int],
        allowed_reference_ids: list[int] | None,
        limit: int,
    ) -> list[tuple[int, float, dict]]:
        assert allowed_chat_ids == [100]
        if allowed_reference_ids == []:
            return []
        assert allowed_reference_ids and len(allowed_reference_ids) == 1
        return [(1, 0.99, {"chat_id": 100, "message_id": 1})]


@pytest.mark.asyncio
async def test_rag_in_filter_scopes_keyword_vector_route_and_citations(session) -> None:
    now = datetime.now(UTC)
    session.add_all(
        [
            TelegramChat(chat_id=100, title="Trong phạm vi", chat_type="channel"),
            TelegramChat(chat_id=200, title="Ngoài phạm vi", chat_type="channel"),
            TelegramMessage(
                chat_id=100,
                message_id=1,
                text="semantic scope trong nguồn được chọn",
                sent_at=now,
            ),
            TelegramMessage(
                chat_id=200,
                message_id=1,
                text="semantic scope ngoài nguồn được chọn",
                sent_at=now,
            ),
        ]
    )
    policy = PolicyEngine(SlidingWindowLimiter(limit=10, seconds=60))
    await policy.apply_template(session, 100, "knowledge")
    await policy.apply_template(session, 200, "knowledge")
    await session.flush()

    answer = await RagService(policy, ScopedAi(), ScopedVectors()).answer(
        session,
        "semantic scope in:100",
        actor_id=1,
        owner_id=1,
        chat_ids=[100, 200],
    )

    assert "Trong phạm vi" in answer
    assert "Ngoài phạm vi" not in answer


@pytest.mark.asyncio
async def test_rag_in_filter_never_falls_back_to_another_source(session) -> None:
    session.add_all(
        [
            TelegramChat(chat_id=100, title="Không có dữ liệu", chat_type="channel"),
            TelegramChat(chat_id=200, title="Có dữ liệu", chat_type="channel"),
            TelegramMessage(
                chat_id=200,
                message_id=1,
                text="semantic scope chỉ tồn tại ngoài phạm vi",
                sent_at=datetime.now(UTC),
            ),
        ]
    )
    policy = PolicyEngine(SlidingWindowLimiter(limit=10, seconds=60))
    await policy.apply_template(session, 100, "knowledge")
    await policy.apply_template(session, 200, "knowledge")
    await session.flush()

    answer = await RagService(policy, ScopedAi(), ScopedVectors()).answer(
        session,
        "semantic scope in:100",
        actor_id=1,
        owner_id=1,
        chat_ids=[100, 200],
    )

    assert answer.startswith("Không tìm thấy đủ thông tin")
    assert "DẪN CHỨNG" not in answer
