"""Native bot acquisition must follow a completed worker handoff."""

import sys
from concurrent.futures import Future
from types import ModuleType, SimpleNamespace

import pytest

from tg_assistant.desktop import setup_context, setup_ui, telegram_context


def completed(value):
    future = Future()
    future.set_result(value)
    return future


@pytest.mark.parametrize("drain_failure", [False, True])
def test_bot_handoff_owns_cleanup_before_worker_resume(qt_application, monkeypatch, drain_failure):
    events = []

    class Runtime:
        handoff_active = False

        def quiesce(self):
            self.handoff_active = True
            events.append("quiesce")
            return completed(True)

        def resume_after_handoff(self):
            assert "release" in events
            self.handoff_active = False
            events.append("resume")
            return completed(None)

    runtime = Runtime()
    selected = SimpleNamespace(refresh=lambda: events.append("account_refresh"), verification=lambda: object())

    class Context:
        engine, fence = object(), object()

        def install_telegram(self, value):
            assert runtime.handoff_active and events == ["quiesce"]
            assert value is selected
            events.append("account")

        def install_bot(self, value):
            events.append("bot")

        def advance_verified(self):
            events.append("verify")

        def detach_telegram(self):
            events.append("drain")
            if drain_failure:
                raise ValueError("bot_shutdown_pending")

        def close(self):
            events.append("release")

    context = Context()
    monkeypatch.setattr(setup_context, "open_setup_context", lambda *_args, **_kwargs: context)
    monkeypatch.setattr(telegram_context, "open_telegram_context", lambda *_args, **_kwargs: selected)
    bot_module = ModuleType("tg_assistant.desktop.bot_context")
    bot_module.open_bot_context = lambda *_args, **_kwargs: SimpleNamespace()
    monkeypatch.setitem(sys.modules, bot_module.__name__, bot_module)
    dialog_module = ModuleType("tg_assistant.desktop.dialogs.bot")

    class Dialog:
        def __init__(self, *_args, **_kwargs):
            assert runtime.handoff_active

        def exec(self):
            events.append("dialog")

    dialog_module.NativeBotDialog = Dialog
    monkeypatch.setitem(sys.modules, dialog_module.__name__, dialog_module)
    control = setup_ui.NativeSetupController.for_settings(object(), runtime_controller=runtime)
    try:
        # The real controller executor creates the selected setup context.
        context.provider = SimpleNamespace(drain=lambda: None)
        context.coordinator = object()
        control.executor.submit(control.coordinator_getter).result(5)
        assert "open_bot_dialog" in control.dialog_handlers, "Bot native handler is missing"
        if drain_failure:
            with pytest.raises(ValueError, match="bot_shutdown_pending"):
                control.dialog_handlers["open_bot_dialog"](None)
            assert events == ["quiesce", "account", "account_refresh", "bot", "dialog", "verify", "drain"]
            assert runtime._native_handoff_context is context and runtime.handoff_active
        else:
            control.dialog_handlers["open_bot_dialog"](None)
            assert events == ["quiesce", "account", "account_refresh", "bot", "dialog", "verify", "drain", "release", "resume"]
            assert runtime._native_handoff_context is None and not runtime.handoff_active
    finally:
        # Do not invent recovery success for a retained drain in this contract
        # test; root owns the temporary fake context, not a live process.
        control.executor.shutdown(wait=True)
