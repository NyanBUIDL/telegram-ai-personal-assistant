from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from telethon.tl import types

from tg_assistant.db.models import (
    AuditLog,
    SyncState,
    TelegramChat,
    TelegramMessage,
    TelegramMessageVersion,
)
from tg_assistant.policy import PolicyEngine
from tg_assistant.telegram.user_client import (
    UserClientAdapter,
    classify_dialog_entity,
    dialog_title,
    extract_group_ai_question,
    is_implicitly_trusted_message,
    message_contains_external_link,
)


def test_group_ai_question_requires_exact_user_client_mention() -> None:
    assert (
        extract_group_ai_question(
            "@your_assistant_username /ask dự án nào có cập nhật mới?",
            "your_assistant_username",
        )
        == "dự án nào có cập nhật mới?"
    )
    assert (
        extract_group_ai_question(
            "@your_assistant_username /ask\nTóm tắt tin nổi bật",
            "@your_assistant_username",
        )
        == "Tóm tắt tin nổi bật"
    )
    assert extract_group_ai_question("@nguoikhac /ask câu hỏi", "your_assistant_username") is None
    assert extract_group_ai_question("/ask câu hỏi", "your_assistant_username") is None


def fake_message(message_id: int, text: str, date: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        chat_id=100,
        id=message_id,
        sender_id=42,
        message=text,
        date=date,
        edit_date=None,
        reply_to=None,
        out=False,
        media=None,
    )


def test_deleted_user_without_first_name_is_private() -> None:
    entity = types.User(id=5388593787, deleted=True)
    dialog = SimpleNamespace(name=None)

    chat_type = classify_dialog_entity(entity)

    assert chat_type == "private"
    assert dialog_title(dialog, entity, chat_type) == "Tài khoản đã xóa"


def test_auto_moderation_implicit_trust_rules() -> None:
    base = {
        "sender_id": 22,
        "out": False,
        "action": None,
        "post_author": None,
    }
    assert not is_implicitly_trusted_message(
        SimpleNamespace(**base),
        owner_id=11,
        chat_id=-1001,
    )
    for override in (
        {"sender_id": 11},
        {"sender_id": None},
        {"sender_id": -1001},
        {"out": True},
        {"action": object()},
        {"post_author": "Anonymous Admin"},
    ):
        assert is_implicitly_trusted_message(
            SimpleNamespace(**{**base, **override}),
            owner_id=11,
            chat_id=-1001,
        )


def test_external_link_detection_avoids_plain_mentions_and_email() -> None:
    for text in (
        "https://example.com/ad",
        "www.example.com",
        "t.me/another_group",
        "telegram.me/joinchat/test",
        "example.com/path",
    ):
        assert message_contains_external_link(
            SimpleNamespace(message=text, entities=[], media=None)
        )
    hidden_link = SimpleNamespace(
        message="bấm vào đây",
        entities=[types.MessageEntityTextUrl(offset=0, length=11, url="https://example.com")],
        media=None,
    )
    assert message_contains_external_link(hidden_link)
    for text in (
        "Nội dung bình thường không có link",
        "Trao đổi với @Group_68Trading",
        "Email support@example.com",
    ):
        assert not message_contains_external_link(
            SimpleNamespace(message=text, entities=[], media=None)
        )


@pytest.mark.asyncio
async def test_upsert_preserves_edit_version(session) -> None:
    session.add(TelegramChat(chat_id=100, title="Test", chat_type="group"))
    await session.flush()
    adapter = object.__new__(UserClientAdapter)
    now = datetime.now(UTC)
    await adapter._upsert_message(session, fake_message(1, "v1", now))
    await session.flush()
    await adapter._upsert_message(session, fake_message(1, "v2", now))
    await session.flush()
    row = await session.scalar(
        select(TelegramMessage).where(
            TelegramMessage.chat_id == 100, TelegramMessage.message_id == 1
        )
    )
    versions = await session.scalar(select(func.count(TelegramMessageVersion.id)))
    assert row is not None and row.text == "v2"
    assert versions == 1


class FakeClient:
    def __init__(self, messages: list[SimpleNamespace]) -> None:
        self.messages = messages
        self.last_reverse: bool | None = None

    def iter_messages(
        self,
        *_args,
        min_id: int,
        reverse: bool,
        limit: int,
        **_kwargs,
    ):
        self.last_reverse = reverse

        async def iterate():
            eligible = [message for message in self.messages if message.id > min_id]
            eligible.sort(key=lambda message: message.id, reverse=not reverse)
            for message in eligible[:limit]:
                yield message

        return iterate()


class FakeSenderHistoryClient:
    def __init__(self, messages: list[SimpleNamespace]) -> None:
        self.messages = messages
        self.requested_username: str | None = None
        self.requested_chat_id: int | None = None

    async def get_entity(self, username: str):
        self.requested_username = username
        return types.User(id=77)

    def iter_messages(
        self,
        chat_id: int,
        *,
        from_user,
        reverse: bool,
        limit: int,
    ):
        self.requested_chat_id = chat_id
        assert from_user.id == 77
        assert reverse is False

        async def iterate():
            matching = [message for message in self.messages if message.sender_id == 77]
            matching.sort(key=lambda message: message.id, reverse=True)
            for message in matching[:limit]:
                yield message

        return iterate()


@pytest.mark.asyncio
async def test_sender_history_resolves_username_and_syncs_only_that_user_in_chat(session) -> None:
    session.add(TelegramChat(chat_id=100, title="Test", chat_type="supergroup"))
    await PolicyEngine().apply_template(session, 100, "knowledge")
    await session.flush()
    now = datetime.now(UTC)
    target_one = fake_message(1, "target one", now - timedelta(minutes=2))
    target_one.sender_id = 77
    other = fake_message(2, "other sender", now - timedelta(minutes=1))
    other.sender_id = 88
    target_two = fake_message(3, "target two", now)
    target_two.sender_id = 77
    client = FakeSenderHistoryClient([target_one, other, target_two])
    adapter = object.__new__(UserClientAdapter)
    adapter.client = client

    sender_id, synced = await adapter.sync_sender_history(
        session,
        chat_id=100,
        username="@a_member",
        limit=100,
    )
    await session.flush()
    rows = list(
        (
            await session.scalars(
                select(TelegramMessage).where(TelegramMessage.chat_id == 100)
            )
        ).all()
    )

    assert sender_id == 77
    assert synced == 2
    assert client.requested_username == "@a_member"
    assert client.requested_chat_id == 100
    assert {(row.message_id, row.sender_id) for row in rows} == {(1, 77), (3, 77)}


class FakeModerationClient:
    def __init__(self, *, is_admin: bool | None) -> None:
        self.is_admin = is_admin
        self.deleted: list[tuple[int, list[int]]] = []

    async def get_permissions(self, _chat_id: int, _sender_id: int) -> SimpleNamespace:
        if self.is_admin is None:
            raise RuntimeError("role lookup unavailable")
        return SimpleNamespace(is_admin=self.is_admin, is_creator=False)

    async def get_entity(self, _chat_id: int) -> SimpleNamespace:
        return SimpleNamespace(
            creator=False,
            broadcast=False,
            first_name=None,
            admin_rights=SimpleNamespace(
                post_messages=False,
                edit_messages=False,
                delete_messages=True,
                pin_messages=False,
            ),
            default_banned_rights=None,
        )

    async def delete_messages(self, chat_id: int, message_ids: list[int]) -> None:
        self.deleted.append((chat_id, message_ids))


@pytest.mark.asyncio
async def test_auto_moderation_deletes_only_non_admin_links(session) -> None:
    session.add(TelegramChat(chat_id=100, title="Test", chat_type="supergroup"))
    policy = PolicyEngine()
    await policy.set_link_spam_auto_moderation(session, 100, enabled=True)
    await session.flush()
    adapter = object.__new__(UserClientAdapter)
    adapter.policy = policy
    adapter.owner_id = 1
    adapter._trusted_admin_cache = {}
    non_admin_client = FakeModerationClient(is_admin=False)
    adapter.client = non_admin_client
    now = datetime.now(UTC)
    normal_message = fake_message(1, "member post without link", now)

    await adapter._upsert_message(session, normal_message)
    await session.flush()
    deleted = await adapter._auto_moderate_new_message(
        session,
        chat_id=100,
        message=normal_message,
    )
    assert not deleted
    assert non_admin_client.deleted == []

    member_link = fake_message(
        2,
        "quảng cáo https://example.com/join",
        now + timedelta(seconds=1),
    )
    await adapter._upsert_message(session, member_link)
    await session.flush()
    deleted = await adapter._auto_moderate_new_message(
        session,
        chat_id=100,
        message=member_link,
    )
    await session.flush()

    stored = await session.scalar(
        select(TelegramMessage).where(
            TelegramMessage.chat_id == 100,
            TelegramMessage.message_id == 2,
        )
    )
    audit = await session.scalar(
        select(AuditLog).where(
            AuditLog.action == "auto_delete_non_admin_link",
            AuditLog.target_id == "100/2",
        )
    )
    assert deleted
    assert non_admin_client.deleted == [(100, [2])]
    assert stored is not None and stored.is_deleted
    assert audit is not None and audit.outcome == "success"

    admin_client = FakeModerationClient(is_admin=True)
    adapter.client = admin_client
    admin_message = fake_message(
        3,
        "admin link https://example.com",
        now + timedelta(seconds=2),
    )
    await adapter._upsert_message(session, admin_message)
    await session.flush()
    deleted = await adapter._auto_moderate_new_message(
        session,
        chat_id=100,
        message=admin_message,
    )

    assert not deleted
    assert admin_client.deleted == []

    unavailable_client = FakeModerationClient(is_admin=None)
    adapter.client = unavailable_client
    unknown_message = fake_message(
        4,
        "unknown role https://example.com",
        now + timedelta(seconds=3),
    )
    unknown_message.sender_id = 43
    await adapter._upsert_message(session, unknown_message)
    await session.flush()
    deleted = await adapter._auto_moderate_new_message(
        session,
        chat_id=100,
        message=unknown_message,
    )
    await session.flush()
    failed_safe = await session.scalar(
        select(AuditLog).where(
            AuditLog.action == "auto_moderation_role_check",
            AuditLog.target_id == "100/4",
        )
    )

    assert not deleted
    assert unavailable_client.deleted == []
    assert failed_safe is not None and failed_safe.outcome == "failed_safe_kept"


@pytest.mark.asyncio
async def test_checkpoint_resume_does_not_duplicate(session) -> None:
    session.add(TelegramChat(chat_id=100, title="Test", chat_type="group"))
    policy = PolicyEngine()
    await policy.apply_template(session, 100, "knowledge")
    await session.flush()
    now = datetime.now(UTC)
    adapter = object.__new__(UserClientAdapter)
    adapter.policy = policy
    adapter.client = FakeClient(
        [fake_message(1, "one", now), fake_message(2, "two", now + timedelta(seconds=1))]
    )
    assert await adapter.sync_history(session, chat_id=100, actor_id=1, owner_id=1, limit=100) == 2
    await session.flush()
    assert await adapter.sync_history(session, chat_id=100, actor_id=1, owner_id=1, limit=100) == 0
    checkpoint = await session.scalar(select(SyncState).where(SyncState.chat_id == 100))
    count = await session.scalar(select(func.count(TelegramMessage.id)))
    assert checkpoint is not None and checkpoint.last_message_id == 2
    assert count == 2
    assert adapter.client.last_reverse is False


@pytest.mark.asyncio
async def test_checkpoint_accepts_mysql_naive_datetime(session) -> None:
    session.add(TelegramChat(chat_id=100, title="Test", chat_type="group"))
    policy = PolicyEngine()
    await policy.apply_template(session, 100, "knowledge")
    now = datetime.now(UTC)
    session.add(
        SyncState(
            chat_id=100,
            last_message_id=1,
            last_message_date=now.replace(tzinfo=None),
            state="idle",
        )
    )
    await session.flush()
    adapter = object.__new__(UserClientAdapter)
    adapter.policy = policy
    adapter.client = FakeClient([fake_message(2, "two", now + timedelta(seconds=1))])

    synced = await adapter.sync_history(
        session,
        chat_id=100,
        actor_id=1,
        owner_id=1,
        limit=100,
    )
    await session.flush()

    checkpoint = await session.scalar(select(SyncState).where(SyncState.chat_id == 100))
    assert synced == 1
    assert checkpoint is not None and checkpoint.last_message_id == 2


@pytest.mark.asyncio
async def test_initial_sync_fetches_newest_messages_not_oldest(session) -> None:
    session.add(TelegramChat(chat_id=100, title="Test", chat_type="group"))
    policy = PolicyEngine()
    await policy.apply_template(session, 100, "knowledge")
    await session.flush()
    now = datetime.now(UTC)
    adapter = object.__new__(UserClientAdapter)
    adapter.policy = policy
    adapter.client = FakeClient(
        [
            fake_message(index, f"message {index}", now + timedelta(seconds=index))
            for index in range(1, 7)
        ]
    )

    synced = await adapter.sync_history(
        session,
        chat_id=100,
        actor_id=1,
        owner_id=1,
        limit=3,
    )
    await session.flush()

    message_ids = set(
        (
            await session.scalars(
                select(TelegramMessage.message_id).where(TelegramMessage.chat_id == 100)
            )
        ).all()
    )
    checkpoint = await session.scalar(select(SyncState).where(SyncState.chat_id == 100))
    assert synced == 3
    assert message_ids == {4, 5, 6}
    assert checkpoint is not None and checkpoint.last_message_id == 6
    assert adapter.client.last_reverse is False

