from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tg_assistant.db.models import TelegramChat, TelegramMessage
from tg_assistant.policy import PolicyEngine, SlidingWindowLimiter
from tg_assistant.runtime import (
    Application,
    knowledge_checkpoint_key,
    question_requests_news,
    question_targets_current_group,
    sender_history_username,
)
from tg_assistant.services.coingecko import CoinPrice


class FakeDatabase:
    def __init__(self, session) -> None:
        self.value = session

    @asynccontextmanager
    async def session(self):
        yield self.value


class FakeTelegramClient:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, str, int]] = []

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str,
        reply_to: int,
    ) -> None:
        self.sent.append((chat_id, text, parse_mode, reply_to))


class FakeUser:
    username = "your_assistant_username"
    owner_id = 1

    def __init__(self) -> None:
        self.client = FakeTelegramClient()


class FakeSenderUser(FakeUser):
    def __init__(self) -> None:
        super().__init__()
        self.sync_requests: list[tuple[int, str, int]] = []

    async def sync_sender_history(
        self,
        session,
        *,
        chat_id: int,
        username: str,
        limit: int,
    ) -> tuple[int, int]:
        self.sync_requests.append((chat_id, username, limit))
        return 77, 2


class FakeAi:
    available = True
    max_input_tokens = 12_000

    async def answer(
        self,
        session,
        question: str,
        context: list[str],
        *,
        include_source_refs: bool,
    ) -> str:
        assert question == "chủ đề đang thảo luận là gì?"
        assert include_source_refs is False
        assert any("nâng cấp sản phẩm" in item for item in context)
        assert all("nguồn bên ngoài" not in item for item in context)
        return "Nhóm đang thảo luận về nâng cấp sản phẩm [S1]."


class FakeSenderAi:
    available = True
    max_input_tokens = 12_000

    async def answer(
        self,
        session,
        question: str,
        context: list[str],
        *,
        include_source_refs: bool,
    ) -> str:
        assert question == "@a_member đã nói về chủ đề gì"
        assert include_source_refs is False
        assert all("Tài khoản đang truy cứu: @a_member" in item for item in context)
        assert any("chủ đề alpha" in item for item in context)
        assert any("chủ đề beta" in item for item in context)
        assert all("người khác nói" not in item for item in context)
        return "@a_member chủ yếu nói về alpha và beta."


class FakeRag:
    async def answer(
        self,
        session,
        question: str,
        *,
        actor_id: int,
        owner_id: int,
        chat_ids: list[int],
        include_citations: bool,
    ) -> str:
        assert question == "cập nhật dự án"
        assert actor_id == owner_id == 1
        assert chat_ids == [100]
        assert include_citations is True
        return "Câu trả lời từ nguồn đã học [S1].\n\nDẪN CHỨNG\n• [S1] Nguồn dự án"


class FakeCoinGecko:
    available = True

    async def get_price(self, query: str) -> CoinPrice:
        assert query == "BTC"
        return CoinPrice(
            coin_id="bitcoin",
            name="Bitcoin",
            symbol="BTC",
            usd=Decimal("68000"),
            vnd=Decimal("1770000000"),
            usd_market_cap=None,
            usd_24h_volume=None,
            usd_24h_change=Decimal("1.5"),
            last_updated_at=datetime.now(UTC),
        )


@pytest.mark.parametrize(
    "question",
    [
        "Tin tức mới nhất về Bitcoin",
        "Cập nhật dự án trong 24h",
        "Thị trường hôm nay có gì mới?",
        "Latest news",
    ],
)
def test_news_questions_require_sources(question: str) -> None:
    assert question_requests_news(question)


@pytest.mark.parametrize(
    "question",
    [
        "Tokenomics của dự án là gì?",
        "Giải thích cơ chế staking",
        "So sánh hai mô hình đồng thuận",
    ],
)
def test_general_knowledge_questions_do_not_require_sources(question: str) -> None:
    assert not question_requests_news(question)


@pytest.mark.parametrize(
    "question",
    [
        "Chủ đề đang thảo luận là gì?",
        "Nhóm này đang nói gì?",
        "Tóm tắt hội thoại trong group này",
        "Mọi người đang bàn gì?",
    ],
)
def test_current_group_questions_are_routed_locally(question: str) -> None:
    assert question_targets_current_group(question)


def test_project_wide_question_is_not_routed_to_current_group() -> None:
    assert not question_targets_current_group("Cập nhật mới nhất từ tất cả dự án")


def test_sender_history_question_extracts_mentioned_username() -> None:
    assert sender_history_username("@a_member đã nói về chủ đề gì") == "a_member"
    assert sender_history_username("Lịch sử nhắn của @a_member") == "a_member"
    assert sender_history_username("@a_member tokenomics là gì?") is None


def test_ollama_uses_independent_knowledge_checkpoint() -> None:
    assert knowledge_checkpoint_key("openai", 123) == "knowledge_checkpoint:123"
    assert knowledge_checkpoint_key("openrouter", 123) == "knowledge_checkpoint:123"
    assert (
        knowledge_checkpoint_key("ollama", 123)
        == "knowledge_checkpoint:ollama:123"
    )


@pytest.mark.asyncio
async def test_group_ai_answer_uses_invoking_group_and_replies_in_place(session) -> None:
    session.add_all(
        [
            TelegramChat(chat_id=100, title="Consumer", chat_type="group"),
            TelegramChat(chat_id=200, title="Learned", chat_type="channel"),
            TelegramChat(chat_id=300, title="Allowed but not learned", chat_type="group"),
        ]
    )
    policy = PolicyEngine()
    await policy.set_allowed(session, 100, True)
    await policy.set_group_ai_ask(session, 100, enabled=True)
    await policy.apply_template(session, 200, "knowledge")
    await policy.apply_template(session, 300, "read_only")
    await session.flush()

    app = object.__new__(Application)
    app.database = FakeDatabase(session)
    app.user = FakeUser()
    app.ai = FakeAi()
    app.rag = FakeRag()
    app._group_ai_limiter = SlidingWindowLimiter(limit=3, seconds=60)

    await app._handle_group_ai_ask(100, 42, 99, "cập nhật dự án")

    assert len(app.user.client.sent) == 2
    assert "Đã nhận câu hỏi" in app.user.client.sent[0][1]
    chat_id, text, parse_mode, reply_to = app.user.client.sent[1]
    assert chat_id == 100
    assert "Câu trả lời từ nguồn đã học" in text
    assert "DẪN CHỨNG" in text
    assert parse_mode == "html"
    assert reply_to == 99


@pytest.mark.asyncio
async def test_group_price_question_uses_coingecko_without_rag(session) -> None:
    session.add(TelegramChat(chat_id=100, title="Consumer", chat_type="group"))
    policy = PolicyEngine()
    await policy.set_allowed(session, 100, True)
    await policy.set_group_ai_ask(session, 100, enabled=True)
    await session.flush()

    app = object.__new__(Application)
    app.database = FakeDatabase(session)
    app.user = FakeUser()
    app.ai = FakeAi()
    app.rag = FakeRag()
    app.coingecko = FakeCoinGecko()
    app._group_ai_limiter = SlidingWindowLimiter(limit=3, seconds=60)

    await app._handle_group_ai_ask(100, 42, 99, "giá BTC bao nhiêu?")

    assert len(app.user.client.sent) == 1
    _chat_id, text, _parse_mode, _reply_to = app.user.client.sent[0]
    assert "GIÁ COINGECKO" in text
    assert "Bitcoin (BTC)" in text


@pytest.mark.asyncio
async def test_group_conversation_question_uses_only_invoking_group_messages(session) -> None:
    now = datetime.now(UTC)
    session.add_all(
        [
            TelegramChat(chat_id=100, title="Consumer", chat_type="group"),
            TelegramChat(chat_id=200, title="Learned", chat_type="channel"),
            TelegramMessage(
                chat_id=100,
                message_id=1,
                text="Chúng ta đang bàn kế hoạch nâng cấp sản phẩm",
                sent_at=now - timedelta(minutes=2),
            ),
            TelegramMessage(
                chat_id=100,
                message_id=2,
                text="Phiên bản mới cần hoàn thành trong tuần",
                sent_at=now - timedelta(minutes=1),
            ),
            TelegramMessage(
                chat_id=200,
                message_id=1,
                text="tin từ nguồn bên ngoài",
                sent_at=now,
            ),
        ]
    )
    policy = PolicyEngine()
    await policy.set_allowed(session, 100, True)
    await policy.set_group_ai_ask(session, 100, enabled=True)
    await policy.apply_template(session, 200, "knowledge")
    await session.flush()

    app = object.__new__(Application)
    app.database = FakeDatabase(session)
    app.user = FakeUser()
    app.ai = FakeAi()
    app.rag = FakeRag()
    app._group_ai_limiter = SlidingWindowLimiter(limit=3, seconds=60)

    await app._handle_group_ai_ask(100, 42, 99, "chủ đề đang thảo luận là gì?")

    assert len(app.user.client.sent) == 2
    assert "Đã nhận câu hỏi" in app.user.client.sent[0][1]
    _chat_id, text, _parse_mode, _reply_to = app.user.client.sent[1]
    assert "nâng cấp sản phẩm" in text
    assert "[S1]" not in text
    assert "DẪN CHỨNG" not in text


@pytest.mark.asyncio
async def test_sender_history_question_uses_only_that_account_in_invoking_group(session) -> None:
    now = datetime.now(UTC)
    session.add_all(
        [
            TelegramChat(chat_id=100, title="Consumer", chat_type="group"),
            TelegramMessage(
                chat_id=100,
                message_id=1,
                sender_id=77,
                text="Tôi đang nghiên cứu chủ đề alpha",
                sent_at=now - timedelta(minutes=3),
            ),
            TelegramMessage(
                chat_id=100,
                message_id=2,
                sender_id=88,
                text="người khác nói về gamma",
                sent_at=now - timedelta(minutes=2),
            ),
            TelegramMessage(
                chat_id=100,
                message_id=3,
                sender_id=77,
                text="Tiếp theo là chủ đề beta",
                sent_at=now - timedelta(minutes=1),
            ),
        ]
    )
    policy = PolicyEngine()
    await policy.set_allowed(session, 100, True)
    await policy.set_group_ai_ask(session, 100, enabled=True)
    await session.flush()

    app = object.__new__(Application)
    app.database = FakeDatabase(session)
    app.user = FakeSenderUser()
    app.ai = FakeSenderAi()
    app.rag = FakeRag()
    app._group_ai_limiter = SlidingWindowLimiter(limit=3, seconds=60)

    await app._handle_group_ai_ask(100, 42, 99, "@a_member đã nói về chủ đề gì")

    assert app.user.sync_requests == [(100, "a_member", 100)]
    assert len(app.user.client.sent) == 2
    assert "Đã nhận câu hỏi" in app.user.client.sent[0][1]
    _chat_id, text, _parse_mode, _reply_to = app.user.client.sent[1]
    assert "@a_member chủ yếu nói về alpha và beta." in text
    assert "người khác nói" not in text


@pytest.mark.asyncio
async def test_group_ai_answer_is_silent_when_group_not_authorized(session) -> None:
    session.add(TelegramChat(chat_id=100, title="Blocked", chat_type="group"))
    await session.flush()

    app = object.__new__(Application)
    app.database = FakeDatabase(session)
    app.user = FakeUser()
    app.ai = FakeAi()
    app.rag = FakeRag()
    app._group_ai_limiter = SlidingWindowLimiter(limit=3, seconds=60)

    await app._handle_group_ai_ask(100, 42, 99, "cập nhật dự án")

    assert app.user.client.sent == []

