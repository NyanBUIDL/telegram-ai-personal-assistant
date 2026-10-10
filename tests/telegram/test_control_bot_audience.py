"""Owner authentication must also bind the management receiving audience."""

from datetime import UTC, datetime

import pytest
from aiogram.types import CallbackQuery, Chat, InaccessibleMessage, Message, User

from tg_assistant.policy import PolicyEngine
from tg_assistant.telegram.control_bot import ControlBot


def incoming(chat_id=17, chat_type="private"):
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type=chat_type),
        from_user=User(id=17, is_bot=False, first_name="Owner"),
        text="/help",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["message", "callback"])
@pytest.mark.parametrize(
    "origin",
    ["private", "group", "supergroup", "channel", "foreign", "missing", "inline", "inaccessible"],
)
async def test_management_requires_accessible_private_owner_receiver(kind, origin):
    bot = ControlBot(
        "123456:synthetic-test-token",
        owner_id=17,
        database=None,
        policy=PolicyEngine(),
        admission=lambda: True,
    )
    msg = incoming()
    if origin in {"group", "supergroup", "channel"}:
        msg = incoming(-50, origin)
    elif origin == "foreign":
        msg = incoming(18)
    elif origin in {"missing", "inline"}:
        msg = None
    elif origin == "inaccessible":
        msg = InaccessibleMessage(message_id=1, date=0, chat=Chat(id=17, type="private"))
    try:
        if kind == "callback":
            if isinstance(msg, Message):
                msg = msg.model_copy(
                    update={"from_user": User(id=123456, is_bot=True, first_name="Bot")}
                )
            update = CallbackQuery(
                id="q",
                chat_instance="c",
                from_user=incoming().from_user,
                message=msg,
                inline_message_id="inline" if origin == "inline" else None,
                data="ai:ask",
            )
            actual = bot._owner_callback(update)
        else:
            actual = bot._owner(msg)
        assert actual is (origin == "private")
    finally:
        await bot.close()
