"""Disposable actual Windows worker/Qt browser fixture; private stdout tickets only."""

from __future__ import annotations

import json
import os
import queue
import shutil
import socket
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from windows_fixture_owner import prepare_windows_fixture_owner  # noqa: E402

from tg_assistant.config import Settings, save_settings


def emit(value):
    print(json.dumps(value), flush=True)


WORKER_CODES = {"storage_access_denied", "native_worker_failed", "native_worker_early_exit"}


def unexpected_worker_exit(runtime, *, stopping):
    return (
        runtime.process is not None and runtime.process.poll() is not None
        and not stopping and not runtime.handoff_active
        and runtime.snapshot.phase != "stopped"
    )


def worker_failure_code(path):
    try:
        if path.stat().st_size > 1024:
            return "native_worker_early_exit"
        code = json.loads(path.read_text(encoding="utf-8")).get("code")
        return code if code in WORKER_CODES else "native_worker_early_exit"
    except (OSError, ValueError, TypeError, AttributeError):
        return "native_worker_early_exit"


def main():
    if os.name != "nt":
        raise SystemExit(77)
    try:
        owner_matches_before = prepare_windows_fixture_owner()
    except (OSError, RuntimeError):
        emit({"type": "failure", "code": "windows_fixture_owner_unavailable"})
        raise SystemExit(2) from None
    if sys.argv[1] == "--worker":
        from tg_assistant.desktop.worker import serve_worker

        diagnostic = Path(sys.argv[3]).parent / "worker-diagnostic.json"
        outcome = {
            "code": "native_worker_running",
            "owner_matches_before": owner_matches_before,
            "owner_matches_after": True,
        }
        diagnostic.write_text(json.dumps(outcome), encoding="utf-8")
        try:
            serve_worker(
                Settings(_env_file=None, data_dir=Path(sys.argv[2])),
                instance_directory=Path(sys.argv[3]),
            )
        except Exception as error:
            outcome["code"] = (
                "storage_access_denied"
                if isinstance(error, OSError) and error.args == ("storage_access_denied",)
                else "native_worker_failed"
            )
            diagnostic.write_text(json.dumps(outcome), encoding="utf-8")
            raise SystemExit(2) from None
        outcome["code"] = "native_worker_stopped"
        diagnostic.write_text(json.dumps(outcome), encoding="utf-8")
        return
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer
    from PySide6.QtWidgets import QApplication

    from tg_assistant.desktop.app import LauncherWindow
    from tg_assistant.desktop.dialogs.provider import ProviderDialog
    from tg_assistant.desktop.dialogs.telegram import TelegramDialog
    from tg_assistant.desktop.runtime_controller import RuntimeController
    from tg_assistant.desktop.setup_ui import NativeSetupController

    for name in list(os.environ):
        if name.startswith("TG_ASSISTANT_"):
            del os.environ[name]
    root = Path(sys.argv[3]).resolve() / ("fixture-" + uuid4().hex)
    root.mkdir(parents=True)
    (root / "parent-diagnostic.json").write_text(json.dumps({
        "owner_matches_before": owner_matches_before, "owner_matches_after": True
    }), encoding="utf-8")
    bundle = root / "bundle"
    shutil.copytree(Path(sys.argv[1]), bundle)
    raw = (bundle / "index.html").read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    newline = {"lf": b"\n", "crlf": b"\r\n", "cr": b"\r"}[sys.argv[2]]
    (bundle / "index.html").write_bytes(raw.replace(b"\n", newline))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings = Settings(
        _env_file=None, data_dir=root / "profile", dashboard_dist_path=bundle, admin_api_port=port
    )
    save_settings(settings)
    runtime = RuntimeController(
        settings,
        worker_command=[
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            str(settings.data_dir),
            str(root / "sid-control"),
        ],
    )
    # The actual native factories use only this fixture's guard/store. Never
    # touch the human's Credential Manager while exercising Telegram entry.
    from tg_assistant.desktop import instance
    from tg_assistant.services import telegram_publication

    instance.native_control_directory = lambda: root / "sid-control"
    telegram_publication.native_control_directory = instance.native_control_directory

    class Store:
        def get(self, _name):
            return None

    original_factory = NativeSetupController.for_settings
    NativeSetupController.for_settings = classmethod(
        lambda _cls, selected, **kwargs: original_factory(
            selected,
            **kwargs,
            context_options={"secret_store_factory": Store},
        )
    )
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = LauncherWindow(runtime)
    window.hide_on_close = True
    window.show()
    commands = queue.Queue()

    def reader():
        for line in sys.stdin:
            commands.put(line.strip())
        commands.put("stop")

    threading.Thread(target=reader, daemon=True).start()
    began = time.monotonic()
    announced = False
    failed = False
    ticket_future = None
    seen = []
    setup_controllers = []
    stopping = False
    stop_began = None
    telegram_seen = False
    telegram_resumed = False
    timer = QTimer()

    def tick():
        nonlocal announced, failed, ticket_future, stopping, stop_began, telegram_seen, telegram_resumed
        for widget in app.topLevelWidgets():
            if isinstance(widget, ProviderDialog) and widget.isVisible():
                seen.append(id(widget))
                if window.setup_dialog is not None:
                    setup_controllers.append(window.setup_dialog.controller)
                valid = (
                    widget.secret_input.text() == ""
                    and widget.windowTitle() == "Kết nối AI • Telegram AI"
                )
                widget.reject()
                window.finish_setup()
                emit(
                    {
                        "type": "dialog",
                        "count": len(seen),
                        "empty": valid,
                        "launcher_visible": window.isVisible(),
                    }
                )
            elif isinstance(widget, TelegramDialog) and widget.isVisible():
                telegram_seen = True
                if window.setup_dialog is not None:
                    setup_controllers.append(window.setup_dialog.controller)
                empty = all(
                    field.text() == ""
                    for field in (
                        widget.api_id,
                        widget.api_hash,
                        widget.phone,
                        widget.otp,
                        widget.password,
                    )
                )
                stopped = runtime.process.poll() is not None and not runtime.state_file.exists()
                retained = runtime.handoff_active and not widget._owns_runner
                widget.reject()
                emit(
                    {
                        "type": "telegram",
                        "empty": empty,
                        "worker_stopped": stopped,
                        "retained_context": retained,
                    }
                )
        if (
            telegram_seen
            and not telegram_resumed
            and not runtime.handoff_active
            and (runtime.snapshot.phase == "ready")
        ):
            telegram_resumed = True
            window.finish_setup()
            emit({"type": "telegram_resumed", "ready": True})
        if not announced and runtime.snapshot.phase == "ready":
            window.close()
            announced = True
            emit(
                {
                    "type": "ready",
                    "origin": runtime.snapshot.url,
                    "hidden": not window.isVisible(),
                    "profile_id": settings.profile_id,
                }
            )
        early_exit = unexpected_worker_exit(runtime, stopping=stopping)
        if not failed and (early_exit or (not announced and time.monotonic() - began > 25)):
            failed = True
            code = worker_failure_code(root / "worker-diagnostic.json") if early_exit else "readiness_timeout"
            emit({"type": "failure", "code": code})
            commands.put("stop")
        if ticket_future is not None and ticket_future.done():
            try:
                emit({"type": "ticket", "url": ticket_future.result()})
            except Exception:
                emit({"type": "failure", "code": "ticket_failed"})
            ticket_future = None
        while not commands.empty():
            command = commands.get_nowait()
            if command == "issue" and announced and not stopping:
                ticket_future = runtime.open_dashboard()
            elif command == "status":
                emit({"type": "status", "count": len(seen), "launcher_visible": window.isVisible()})
            elif command == "stop" and not stopping:
                stopping = True
                stop_began = time.monotonic()
                window.finish_setup()
                window.finish_native_dialogs()
                runtime.stop()
        if stopping:
            # Original controller checks exact SID/profile/run before writing stop.request.
            runtime.stop()
            if runtime.process is not None and runtime.process.poll() is not None:
                released = False
                try:
                    with instance.InstanceGuard(root / "sid-control"):
                        released = True
                except (OSError, RuntimeError):
                    pass
                clean = runtime.process.returncode == 0 and not runtime.state_file.exists() and released
                emit({"type": "stopped", "clean": clean, "owned_handle_released": released})
                app.exit(0 if clean and not failed else 2)
            elif time.monotonic() - stop_began > 20:
                emit({"type": "failure", "code": "owned_stop_timeout"})
                app.exit(2)

    timer.timeout.connect(tick)
    timer.start(25)
    try:
        exit_code = app.exec()
    finally:
        timer.stop()
        window.finish_setup()
        window.finish_native_dialogs()
        window.hide_on_close = False
        window.close()
        runtime.stop()
        runtime.close()
        runtime.executor.shutdown(wait=True, cancel_futures=True)
        for controller in setup_controllers:
            controller.executor.shutdown(wait=True, cancel_futures=False)
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
