"""Actual SQLite/Application/RAG/SDK delivery; disposable transports and pairing only."""
# ruff: noqa: F811
import asyncio
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, MessageEntity, Update, User
from services.test_first_value_observation import initialize, learned, observed  # noqa: F401
from sqlalchemy import select, update
from test_first_value_index import CHAT, OWNER

from tg_assistant.db.models import AppSetting, TelegramChatPolicy


class TelegramWire(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.before_return = None

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109
        assert isinstance(method, SendMessage)
        self.calls.append(method)
        if self.before_return:
            await self.before_return(len(self.calls))
        return Message(message_id=1000 + len(self.calls), date=datetime.now(UTC),
                       chat=Chat(id=int(method.chat_id), type="private"), text=method.text)


@pytest.fixture
async def prepared(observed):
    env, app = observed, observed.application
    app.database.fence = env.fence
    app.database.management_admission = app.management_admitted
    wire = TelegramWire()
    bot = Bot("456:synthetic-first-value-token", session=wire)
    app.bot_runtime.bot = bot
    app.bot_runtime.repository = SimpleNamespace(engine=env.engine, _load=lambda connection: ({
        "enrollment": {"generation": "a" * 32, "bot_id": "456"},
        "pairing": {"owner_id": str(OWNER), "bot_id": "456", "enrollment_generation": "a" * 32}}, True))
    app.bot_runtime.service.repository = app.bot_runtime.repository
    env.provider_calls = []
    env.output = "Kế hoạch ra mắt là thứ Sáu [S1]."
    env.provider_hook = None

    async def provider(request):
        payload = json.loads(request.content)
        env.provider_calls.append(request.url.path)
        if env.provider_hook:
            await env.provider_hook(request)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"object": "list", "model": payload["model"],
                "data": [{"object": "embedding", "index": 0, "embedding": [1., 0., 0.]}],
                "usage": {"prompt_tokens": 10, "total_tokens": 10}})
        assert request.url.path.endswith("/responses")
        return httpx.Response(200, json={"id": "resp_disposable", "object": "response",
            "created_at": 1., "model": payload["model"], "status": "completed",
            "output": [{"type": "message", "id": "msg_disposable", "role": "assistant",
                "status": "completed", "content": [{"type": "output_text", "text": env.output, "annotations": []}]}],
            "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
            "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30,
                      "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}})

    await app.ai.client._client.aclose()
    app.ai.client._client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    app.rag.router = app.ai_router
    app.rag.provider_fence = app._model_fence
    env.wire, env.sdk = wire, bot
    yield env
    await wire.close()


@pytest.fixture
async def delivery(prepared):
    env = prepared
    initialize(env)
    env.add_indexed(10)
    learned(env)
    await env.first_value.refresh_observation()
    assert env.first_value.current_binding is not None
    return env


def incoming(env, *, message_id=123, chat_id=OWNER, chat_type="private", sender=OWNER, sender_bot=False):
    return Message(message_id=message_id, date=datetime.now(UTC),
        chat=Chat(id=chat_id, type=chat_type),
        from_user=User(id=sender, is_bot=sender_bot, first_name="Disposable"),
        text=f"/ask in:{CHAT} kế hoạch",
        entities=[MessageEntity(type="bot_command", offset=0, length=4)]).as_(env.sdk)


async def test_actual_selected_delivery_issues_receipt(delivery):
    env = delivery
    assert callable(getattr(env.first_value, "answer_selected", None)), "selected delivery owner is missing"
    assert await env.first_value.answer_selected(incoming(env), f"in:{CHAT} kế hoạch")
    state = env.first_value.history()
    assert len(state.receipts) == 1, (state.attempts, env.provider_calls, env.wire.calls)
    assert state.attempts[0].phase == "completed"
    assert state.receipts[0].message_ids == (1001,)
    assert len(env.provider_calls) == 2
    assert len(env.wire.calls) == 1
    assert env.first_value.first_answer_verification() is not None
    assert await env.first_value.answer_selected(incoming(env), f"in:{CHAT} changed question")
    assert len(env.provider_calls) == 2 and len(env.wire.calls) == 1
    assert "https://t.me/c/" in env.wire.calls[0].text
    assert "A" in env.wire.calls[0].text


async def test_actual_control_bot_routes_before_ack(delivery):
    import inspect

    from tg_assistant.telegram.control_bot import ControlBot
    env, app = delivery, delivery.application
    assert "first_value_getter" in inspect.signature(ControlBot).parameters, "dynamic purpose routing is missing"
    control = ControlBot("", owner_id=OWNER, database=app.database, policy=app.policy,
        rag=app.rag, bot_instance=env.sdk, admission=app.management_admitted,
        first_value_getter=lambda: app.first_value)
    await control.dp.feed_update(env.sdk, Update(update_id=123, message=incoming(env)))
    assert len(env.first_value.history().receipts) == 1
    assert len(env.wire.calls) == 1
    await control.close()


@pytest.mark.parametrize("audience", [dict(chat_id=7), dict(chat_type="group"), dict(sender=7), dict(sender_bot=True)])
async def test_wrong_audience_has_zero_effects(delivery, audience):
    env = delivery
    assert await env.first_value.answer_selected(incoming(env, **audience), f"in:{CHAT} kế hoạch")
    assert not env.provider_calls and not env.wire.calls and not env.first_value.history().attempts


@pytest.mark.parametrize("question", ["x" * 2049, "api_key=sk-" + "a" * 48])
async def test_question_boundary_precedes_all_providers(delivery, question):
    env = delivery
    assert await env.first_value.answer_selected(incoming(env), f"in:{CHAT} {question}")
    assert not env.provider_calls and not env.wire.calls and not env.first_value.history().attempts


@pytest.mark.parametrize("change", ["selection", "epoch", "block_regrant", "model", "consent", "point", "pair", "restore", "deadline"])
async def test_change_after_provider_prevents_delivery(delivery, monkeypatch, change):
    env, app = delivery, delivery.application
    async def alter(request):
        if not request.url.path.endswith("/responses"):
            return
        if change == "selection":
            env.add_source(-1002)
            await env.first_value.select_source(-1002)
            await env.first_value.select_source(CHAT)
        elif change in {"epoch", "block_regrant"}:
            with env.engine.begin() as connection:
                connection.execute(update(TelegramChatPolicy).values(authorization_epoch=2))
        elif change == "model":
            app.ai.model = "changed-model"
        elif change == "consent":
            app.settings.cloud_consent = not app.settings.cloud_consent
        elif change == "point":
            app.rag.vectors.delete_reference_ids([1])
        elif change == "pair":
            app.bot_runtime.pairing_verification = lambda: None
        elif change == "restore":
            from tg_assistant.services.first_value import invalidate_first_value_after_restore
            with env.first_value._transaction() as connection:
                invalidate_first_value_after_restore(connection, profile_id=app.settings.profile_id, windows_sid=env.sid)
        else:
            import tg_assistant.services.first_value as module
            original = module.monotonic
            monkeypatch.setattr(module, "monotonic", lambda: original() + 301)
    env.provider_hook = alter
    assert await env.first_value.answer_selected(incoming(env), f"in:{CHAT} kế hoạch")
    assert not env.first_value.history().receipts and not env.wire.calls
    assert len(env.provider_calls) <= 2


@pytest.mark.parametrize("failure", ["first", "last", "duplicate", "wrong_chat", "lost_admission", "late_epoch"])
async def test_sdk_failures_never_receipt_or_replay(delivery, failure, monkeypatch):
    env, app = delivery, delivery.application
    env.output = "[S1] " + "😀" * 2100
    original = env.wire.make_request
    async def send(bot, method, timeout=None):  # noqa: ASYNC109 - SDK signature
        ordinal = len(env.wire.calls) + 1
        if failure in {"first", "last"} and ordinal == (1 if failure == "first" else 2):
            env.wire.calls.append(method)
            raise ConnectionError("disposable transport interrupted")
        returned = await original(bot, method, timeout)
        if failure == "duplicate":
            return returned.model_copy(update={"message_id": 1001})
        if failure == "wrong_chat":
            return returned.model_copy(update={"chat": Chat(id=9, type="private")})
        if failure == "lost_admission":
            app.admitted = False
        if failure == "late_epoch" and ordinal == 2:
            with env.engine.begin() as connection:
                connection.execute(update(TelegramChatPolicy).values(authorization_epoch=2))
        return returned
    monkeypatch.setattr(env.wire, "make_request", send)
    assert await env.first_value.answer_selected(incoming(env), f"in:{CHAT} kế hoạch")
    app.admitted = True
    state = env.first_value.history()
    assert not state.receipts
    assert state.attempts[0].submitted_ordinal >= 1
    if failure == "lost_admission":
        assert state.attempts[0].phase == "delivering"
        assert state.attempts[0].returned_message_ids == ()
        assert "Khởi động lại" in env.first_value.selection_status().answer_operation.next_action
    calls = len(env.wire.calls), len(env.provider_calls)
    assert await env.first_value.answer_selected(incoming(env), f"in:{CHAT} different")
    assert calls == (len(env.wire.calls), len(env.provider_calls))


async def test_cancel_and_close_retain_last_real_send(delivery):
    env = delivery
    entered, release = asyncio.Event(), asyncio.Event()
    async def held(_ordinal):
        entered.set()
        await release.wait()
    env.wire.before_return = held
    task = asyncio.create_task(env.first_value.answer_selected(incoming(env), f"in:{CHAT} kế hoạch"))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    close = asyncio.create_task(env.first_value.close())
    await asyncio.sleep(0)
    assert not task.done() and not close.done() and env.first_value._answer_tasks
    release.set()
    assert await task is True
    await close
    with env.engine.connect() as connection:
        from tg_assistant.services.first_value import _read_namespace
        state = _read_namespace(connection, namespace=env.first_value._namespace)
    assert not state.receipts and state.attempts[0].submitted_ordinal == 1
    assert not env.first_value._answer_tasks


@pytest.mark.parametrize("committed", [True, False])
async def test_ambiguous_commit_reconciles_exact_receipt_without_resend(delivery, monkeypatch, committed):
    env, owner = delivery, delivery.first_value
    transaction = owner._transaction
    raised = []
    @contextmanager
    def ambiguous():
        with transaction() as connection:
            yield connection
            found = connection.execute(select(AppSetting.key).where(AppSetting.key.like("fv1.%receipt.%"))).first()
            if found and not raised and not committed:
                raised.append(True)
                raise OSError("commit rolled back")
        if found and not raised:
            raised.append(True)
            raise OSError("commit acknowledgement lost")
    monkeypatch.setattr(owner, "_transaction", ambiguous)
    assert await owner.answer_selected(incoming(env), f"in:{CHAT} kế hoạch")
    assert raised and len(owner.history().receipts) == int(committed)
    assert (owner.first_answer_verification() is not None) is committed
    assert await owner.answer_selected(incoming(env), f"in:{CHAT} retry")
    assert len(env.wire.calls) == 1 and len(env.provider_calls) == 2


@pytest.mark.parametrize("output", ["**bold _overlap** italic_ [S1]", "😀" * 32000 + "[S1]", "bad\x00 [S1]"], ids=["overlap", "oversize", "control"])
async def test_hostile_output_is_balanced_or_refused_before_send(delivery, output):
    env = delivery
    env.output = output
    assert await env.first_value.answer_selected(incoming(env), f"in:{CHAT} kế hoạch")
    if "overlap" in output:
        from xml.etree import ElementTree
        assert env.wire.calls
        for method in env.wire.calls:
            ElementTree.fromstring("<root>" + method.text + "</root>")  # noqa: S314 - fixed disposable text
    else:
        assert not env.wire.calls and not env.first_value.history().receipts
        assert env.first_value.selection_status().answer_operation.code == "answer_nonqualifying"


@pytest.mark.parametrize("value", ["😀" * 4000, "<&> &amp; **bold**", "```\nfirst\n\nsecond\n```", "**" + "long " * 1800 + "**"], ids=["astral", "entities", "code", "long_bold"])
def test_purpose_chunks_are_balanced_and_utf16_bounded(value):
    from xml.etree import ElementTree

    from tg_assistant.services.first_value import _answer_html_chunks
    chunks = _answer_html_chunks(value)
    assert 1 <= len(chunks) <= 16
    for chunk in chunks:
        assert len(chunk.encode("utf-16-le")) // 2 <= 3900
        ElementTree.fromstring("<root>" + chunk + "</root>")  # noqa: S314 - fixed disposable text
    if "first" in value:
        assert "<pre>first\n\nsecond</pre>" in chunks[0]


async def test_receipt_restart_rechecks_actual_reader_and_replay(delivery):
    from tg_assistant.services.first_value import FirstValueService
    env, app = delivery, delivery.application
    assert await env.first_value.answer_selected(incoming(env), f"in:{CHAT} kế hoạch")
    original = env.first_value.history().receipts[0]
    await env.first_value.close()
    app.first_value = FirstValueService(runtime=app, coordinator=env.coordinator, windows_sid=env.sid)
    env.first_value = app.first_value
    env.first_value.initialize()
    assert env.first_value.first_answer_verification() is None
    await env.first_value.refresh_observation()
    assert env.first_value.first_answer_verification() is not None
    assert env.first_value.history().receipts == (original,)
    assert await env.first_value.answer_selected(incoming(env), f"in:{CHAT} kế hoạch")
    assert len(env.wire.calls) == 1 and len(env.provider_calls) == 2


async def test_ready_new_request_falls_through_but_tombstone_survives_reenrollment(delivery):
    from tg_assistant.contracts import OnboardingStage
    from tg_assistant.desktop.setup_context import SetupContext
    from tg_assistant.services.onboarding import StageVerification
    env, owner = delivery, delivery.first_value
    assert await owner.answer_selected(incoming(env), f"in:{CHAT} kế hoạch")
    for stage in list(OnboardingStage)[:6]:
        env.coordinator._verifiers[stage] = lambda: StageVerification(owner_id=OWNER, fingerprint="f" * 64)
    for stage in (OnboardingStage.WELCOME, OnboardingStage.STORAGE_READY):
        env.coordinator.complete_stage(stage, env.coordinator.verify_stage(stage))
    assert SetupContext(env.coordinator, env.engine, env.fence, None).advance_verified().profile.setup_stage == OnboardingStage.READY
    assert not await owner.answer_selected(incoming(env, message_id=124), f"in:{CHAT} new ordinary")
    assert await owner.answer_selected(incoming(env), "ordinary changed selection")
    owner._bot.repository._load = lambda connection: ({
        "enrollment": {"generation": "b" * 32, "bot_id": "456"},
        "pairing": {"owner_id": str(OWNER), "bot_id": "456", "enrollment_generation": "b" * 32}}, True)
    assert await owner.answer_selected(incoming(env), f"in:{CHAT} different enrollment")
    assert len(env.wire.calls) == 1 and len(env.provider_calls) == 2


async def test_provider_activation_drains_actual_held_sdk_before_reattaching(delivery):
    env, owner = delivery, delivery.first_value
    entered, release = asyncio.Event(), asyncio.Event()
    async def held(_):
        entered.set()
        await release.wait()
    env.wire.before_return = held
    answer = asyncio.create_task(owner.answer_selected(incoming(env), f"in:{CHAT} kế hoạch"))
    await asyncio.wait_for(entered.wait(), 5)
    previous = owner.reader
    activated = []
    async def activate():
        assert not owner._answer_tasks and previous._closed
        activated.append(True)
    activation = asyncio.create_task(owner.activate_providers(activate, needs_change=lambda: True))
    await asyncio.sleep(0)
    assert not activation.done() and owner._answer_tasks and not activated
    release.set()
    assert await answer is True
    await activation
    assert activated and owner.reader is not previous
    assert not owner.history().receipts and len(env.wire.calls) == 1


async def test_all_real_chunks_and_ids_are_required_for_completion(delivery):
    env = delivery
    env.output = "[S1] **" + "😀<&> " * 650 + "**\n```\nfirst\n\nsecond\n```"
    assert await env.first_value.answer_selected(incoming(env), f"in:{CHAT} kế hoạch")
    (receipt,) = env.first_value.history().receipts
    assert len(env.wire.calls) > 1
    assert receipt.message_ids == tuple(range(1001, 1001 + len(env.wire.calls)))
    assert all(len(item.text.encode("utf-16-le")) // 2 <= 3900 for item in env.wire.calls)
    assert env.first_value.first_answer_verification() is not None


async def test_actual_dispatch_consumes_withdrawal_during_last_sdk(delivery):
    from tg_assistant.telegram.control_bot import ControlBot
    env, app = delivery, delivery.application
    control = ControlBot("", owner_id=OWNER, database=app.database, policy=app.policy,
        rag=app.rag, bot_instance=env.sdk, admission=app.management_admitted,
        first_value_getter=lambda: app.first_value)
    async def withdraw(_):
        app.admitted = False
    env.wire.before_return = withdraw
    await control.dp.feed_update(env.sdk, Update(update_id=123, message=incoming(env)))
    app.admitted = True
    assert not app.first_value.history().receipts
    assert app.first_value.history().attempts[0].submitted_ordinal == 1
    assert app.first_value.history().attempts[0].returned_message_ids == ()
    await control.dp.feed_update(env.sdk, Update(update_id=123, message=incoming(env)))
    assert len(env.wire.calls) == 1
    await control.close()


async def test_final_physical_commit_withdrawal_preserves_returned_ids_without_receipt(delivery, monkeypatch):
    env, app, owner = delivery, delivery.application, delivery.first_value
    original = owner._receipt_ledgers
    def withdraw(connection, receipt, attempt):
        assert connection.engine is env.coordinator.engine
        assert connection.connection.driver_connection.in_transaction
        assert original(connection, receipt, attempt)
        app.admitted = False
        return True
    monkeypatch.setattr(owner, "_receipt_ledgers", withdraw)
    assert await owner.answer_selected(incoming(env), f"in:{CHAT} kế hoạch")
    app.admitted = True
    state = owner.history()
    assert not state.receipts and state.attempts[0].phase == "delivering"
    assert state.attempts[0].returned_message_ids == (1001,)
    assert state.attempts[0].submitted_ordinal == 1
    assert await owner.answer_selected(incoming(env), f"in:{CHAT} retry")
    assert len(env.wire.calls) == 1


@pytest.mark.parametrize("unavailable", ["pair", "identity", "learning", "binding"])
async def test_unavailable_selected_test_is_consumed_without_ack_or_provider(delivery, unavailable):
    from tg_assistant.db.models import BackgroundJob
    from tg_assistant.telegram.control_bot import ControlBot
    env, app, owner = delivery, delivery.application, delivery.first_value
    control = ControlBot("", owner_id=OWNER, database=app.database, policy=app.policy,
        rag=app.rag, bot_instance=env.sdk, admission=app.management_admitted,
        first_value_getter=lambda: app.first_value)
    if unavailable == "pair":
        app.bot_runtime.pairing_verification = lambda: None
    elif unavailable == "identity":
        app.bot_runtime.service.verified_identity = None
    elif unavailable == "learning":
        with env.engine.begin() as connection:
            connection.execute(update(BackgroundJob).values(status="paused"))
    else:
        owner.current_binding = None
    await control.dp.feed_update(env.sdk, Update(update_id=123, message=incoming(env)))
    assert not env.wire.calls and not env.provider_calls
    assert not owner.history().attempts
    await control.close()

