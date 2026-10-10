"""Private bot withdrawal fences MTProto work; SDK doubles do not prove health."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from telethon import events
from telethon.tl import types

from tg_assistant.db.models import PermissionName, TelegramChat, TelegramMessage
from tg_assistant.policy import PolicyEngine
from tg_assistant.services.revocation import AuthorizationRevoked
from tg_assistant.telegram.user_client import UserClientAdapter

CHAT = -1000000000001


class SdkDouble:
    def __init__(self):
        self.calls = []
        self.handlers = []

    def on(self, event):
        def register(handler):
            self.handlers.append(handler)
            return handler

        return register

    async def send_message(self, *args, **kwargs):
        self.calls.append(("send", args, kwargs))
        return types.Message(id=9, peer_id=types.PeerChannel(1), date=datetime.now(UTC))

    async def delete_messages(self, *args, **kwargs):
        self.calls.append(("delete", args, kwargs))

    async def edit_message(self, *args, **kwargs):
        self.calls.append(("edit", args, kwargs))

    async def pin_message(self, *args, **kwargs):
        self.calls.append(("pin", args, kwargs))

    async def delete_dialog(self, *args, **kwargs):
        self.calls.append(("leave", args, kwargs))

    def iter_messages(self, *args, **kwargs):
        self.calls.append(("history", args, kwargs))

        async def iterate():
            if False:
                yield

        return iterate()


async def adapter_for(session, proof=None):
    session.add(TelegramChat(chat_id=CHAT, title="Local test", chat_type="supergroup"))
    policy = PolicyEngine()
    await policy.set_allowed(session, CHAT, True)
    for permission in (
        PermissionName.SEND_MESSAGES,
        PermissionName.DELETE_ANY_MESSAGES,
        PermissionName.EDIT_OWN_MESSAGES,
        PermissionName.PIN_MESSAGES,
        PermissionName.MONITOR_NEW_MESSAGES,
        PermissionName.SYNC_HISTORY,
    ):
        await policy.set_permission(session, CHAT, permission, True)
    session.add(
        TelegramMessage(
            chat_id=CHAT, message_id=1, text="old", sent_at=datetime.now(UTC), is_outgoing=True
        )
    )
    await session.flush()
    adapter = object.__new__(UserClientAdapter)
    adapter.client = SdkDouble()
    adapter.policy = policy
    adapter.owner_id = 1
    adapter.username = "local_owner"
    adapter.management_admission = proof
    return adapter


async def perform(adapter, session, action):
    common = dict(
        chat_id=CHAT,
        actor_id=1,
        owner_id=1,
        telegram_rights=frozenset(
            {"send_messages", "delete_messages", "edit_messages", "pin_messages"}
        ),
    )
    if action == "send":
        return await adapter.send_message(session, text="local", **common)
    if action == "delete":
        return await adapter.delete_message(
            session, message_id=1, confirmed=True, delete_any=True, **common
        )
    if action == "bulk":
        return await adapter.delete_messages_bulk(session, message_ids=[1], **common)
    if action == "edit":
        return await adapter.edit_message(session, message_id=1, text="local", **common)
    return await adapter.pin_message(session, message_id=1, **common)


@pytest.mark.parametrize("action", ["send", "delete", "bulk", "edit", "pin"])
async def test_bot_withdrawal_during_policy_await_prevents_sdk_call(session, action):
    admitted = [True]
    adapter = await adapter_for(session, lambda: admitted[0])
    original = adapter.policy.evaluate

    async def withdrawing_policy(*args):
        decision = await original(*args)
        admitted[0] = False
        return decision

    adapter.policy.evaluate = withdrawing_policy
    with pytest.raises(PermissionError, match="^owner_pairing_required$"):
        await perform(adapter, session, action)
    assert adapter.client.calls == []


@pytest.mark.parametrize("proof", [False, 1, "true", object()])
async def test_leave_requires_exact_private_true(session, proof):
    adapter = await adapter_for(session, lambda: proof)
    with pytest.raises(PermissionError, match="^owner_pairing_required$"):
        await adapter.leave_chat(CHAT)
    assert adapter.client.calls == []


async def test_private_callback_exception_is_sanitized(session):
    def fail():
        raise RuntimeError("synthetic-private-marker")

    adapter = await adapter_for(session, fail)
    with pytest.raises(PermissionError, match="^owner_pairing_required$") as error:
        await adapter.leave_chat(CHAT)
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True
    assert adapter.client.calls == []


@pytest.mark.parametrize("proof", [None, lambda: True])
async def test_uninstalled_compatibility_and_verified_callback_preserve_policy(session, proof):
    adapter = await adapter_for(session, proof)
    assert await perform(adapter, session, "send") == 9
    assert len(adapter.client.calls) == 1
    await adapter.policy.set_permission(session, CHAT, PermissionName.SEND_MESSAGES, False)
    await session.flush()
    with pytest.raises(PermissionError):
        await perform(adapter, session, "send")
    assert len(adapter.client.calls) == 1


async def test_authorization_await_rechecks_private_proof_and_retains_source_epoch(
    session, monkeypatch
):
    from tg_assistant.telegram import user_client

    admitted = [True]
    adapter = await adapter_for(session, lambda: admitted[0])
    original = user_client.require_authorization

    async def withdrawing_source(*args):
        result = await original(*args)
        admitted[0] = False
        return result

    monkeypatch.setattr(user_client, "require_authorization", withdrawing_source)
    with pytest.raises(PermissionError, match="^owner_pairing_required$"):
        await adapter._authorization_fence(session, CHAT, PermissionName.SEND_MESSAGES)
    monkeypatch.setattr(user_client, "require_authorization", original)
    admitted[0] = True
    with pytest.raises(AuthorizationRevoked):
        await adapter._authorization_fence(session, CHAT, PermissionName.SEND_MESSAGES, -1)


@pytest.mark.parametrize("withdraw_during_policy", [False, True])
async def test_native_new_message_withdrawal_prevents_storage_and_group_ask(
    session, withdraw_during_policy
):
    admitted = [withdraw_during_policy]
    adapter = await adapter_for(session, lambda: admitted[0])
    writes, asks = [], []

    @asynccontextmanager
    async def transaction():
        yield session

    database = SimpleNamespace(session=transaction)
    original = adapter.policy.evaluate

    async def withdrawing_policy(*args):
        decision = await original(*args)
        admitted[0] = False
        return decision

    adapter.policy.evaluate = withdrawing_policy

    async def write(*args):
        writes.append(args)

    async def ask(*args):
        asks.append(args)

    adapter._upsert_message = write
    adapter.register_handlers(database, group_ask_handler=ask)
    message = types.Message(
        id=2,
        peer_id=types.PeerChannel(1),
        from_id=types.PeerUser(2),
        date=datetime.now(UTC),
        message="@local_owner /ask question",
    )
    await adapter.client.handlers[0](events.NewMessage.Event(message))
    assert writes == []
    assert asks == []


async def test_history_query_rechecks_after_awaited_cursor_read(session, monkeypatch):
    admitted = [True]
    adapter = await adapter_for(session, lambda: admitted[0])
    original = session.scalar

    async def withdrawing_read(*args, **kwargs):
        result = await original(*args, **kwargs)
        admitted[0] = False
        return result

    monkeypatch.setattr(session, "scalar", withdrawing_read)
    with pytest.raises(PermissionError, match="^owner_pairing_required$"):
        await adapter.sync_history(session, chat_id=CHAT, actor_id=1, owner_id=1)
    assert adapter.client.calls == []


@pytest.mark.parametrize("handler_index", [1, 2])
async def test_native_edit_delete_handler_withdrawal_keeps_stored_message(session, handler_index):
    adapter = await adapter_for(session, lambda: False)

    @asynccontextmanager
    async def transaction():
        pytest.fail("Denied native event must not enter storage")
        yield session

    adapter.register_handlers(SimpleNamespace(session=transaction))
    message = types.Message(
        id=1,
        peer_id=types.PeerChannel(1),
        from_id=types.PeerUser(2),
        date=datetime.now(UTC),
        message="changed",
    )
    event = (
        events.MessageEdited.Event(message)
        if handler_index == 1
        else SimpleNamespace(chat_id=CHAT, deleted_ids=[1])
    )
    await adapter.client.handlers[handler_index](event)
    stored = await session.scalar(select(TelegramMessage))
    assert stored.text == "old" and stored.is_deleted is False
