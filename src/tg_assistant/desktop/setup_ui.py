"""Native setup reads persisted state and opens available native dialogs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ..contracts import OnboardingStatus
from .theme import ArtPanel

CONNECTIONS = {
    "storage": "Dữ liệu",
    "telegram_account": "Tài khoản Telegram",
    "control_bot": "Bot Telegram",
    "chat_ai": "AI trả lời",
    "embeddings": "Chỉ mục AI",
    "runtime": "Ứng dụng",
}
STATES = {
    "unknown": "Chưa kiểm tra",
    "checking": "Đang kiểm tra",
    "ready": "Đã kiểm tra",
    "degraded": "Có giới hạn",
    "disconnected": "Chưa kết nối",
}
STAGES = {
    "welcome": "Chào mừng",
    "storage_ready": "Dữ liệu",
    "ai_configured": "Kết nối AI",
    "telegram_verified": "Tài khoản Telegram",
    "bot_verified": "Bot Telegram",
    "owner_paired": "Ghép bot",
    "source_selected": "Chọn nguồn",
    "first_answer": "Câu hỏi đầu tiên",
    "ready": "Sẵn sàng",
}
DIALOGS = {
    "open_connection_dialog": "Kết nối AI",
    "open_telegram_login": "Đăng nhập Telegram",
    "open_bot_dialog": "Kết nối bot",
}


@dataclass(frozen=True)
class SetupOutcome:
    status: OnboardingStatus | None
    ai_skipped: bool = False


class _OperationDialog(QDialog):
    """Keep GUI events responsive while ownership work drains on its executor."""

    def __init__(self, future, message, parent):
        super().__init__(parent)
        self.future = future
        self.setWindowTitle("Thiết lập Telegram AI")
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        layout = QVBoxLayout(self)
        panel = ArtPanel()
        layout.addWidget(panel)
        body = QVBoxLayout(panel)
        label = QLabel(message)
        label.setWordWrap(True)
        body.addWidget(label)
        self.timer = QTimer(self)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def poll(self):
        if self.future.done():
            self.timer.stop()
            self.accept()

    def reject(self):
        # Cancelling a Future cannot stop a running SDK/thread operation. Never
        # release its fence or restart the worker while that work is in flight.
        if self.future.done():
            super().reject()


def _wait_operation(future, message, parent):
    if not future.done():
        dialog = _OperationDialog(future, message, parent)
        dialog.exec()
    return future.result()


class NativeSetupController:
    """Private native getter; browser claims cannot complete setup."""

    def __init__(
        self,
        coordinator_getter,
        *,
        dialog_handlers=None,
        begin_handler=None,
        close_handler=None,
        skip_handler=None,
        refresh_handler=None,
    ):
        self.coordinator_getter = coordinator_getter
        self.begin_handler = begin_handler
        self.skip_handler, self.refresh_handler = skip_handler, refresh_handler
        self.close_handler = close_handler or (lambda: None)
        self.closed = False
        self.last_future = None
        self.cleanup_future = None
        self.dialog_handlers = dict(dialog_handlers or {})
        if any(
            name not in DIALOGS or not callable(handler)
            for name, handler in self.dialog_handlers.items()
        ):
            raise ValueError("setup_dialog_invalid")
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="native-setup")

    @classmethod
    def for_settings(
        cls, settings, *, dialog_handlers=None, context_options=None, runtime_controller=None
    ):
        from .setup_context import open_setup_context

        contexts = []

        def context():
            if not contexts:
                contexts.append(open_setup_context(settings, **(context_options or {})))
            return contexts[0]

        def close():
            if contexts:
                contexts[0].close()
                contexts.clear()
                if runtime_controller is not None:
                    runtime_controller._native_handoff_context = None
                if runtime_controller is not None and runtime_controller.handoff_active:
                    # A previous native SDK drain may have timed out. Only a
                    # later successful close releases the fence and permits
                    # worker restart; a repeated failure retains the gate.
                    runtime_controller.resume_after_handoff().result(timeout=30)

        def coordinator():
            current = context()
            current.provider.drain()
            return current.coordinator

        def refresh():
            measured = context().refresh()
            if runtime_controller is not None:
                observed = runtime_controller.setup_status().result(timeout=15)
                if observed is not None:
                    return observed
            return measured

        def open_provider(parent):
            from .dialogs.provider import ProviderDialog

            # Setup refresh initialized selected storage on the background executor.
            if not contexts:
                raise ValueError("setup_unavailable")
            provider = contexts[0].provider
            dialog = ProviderDialog(
                provider.service,
                parent=parent,
                **provider.dialog_options("chat_ai"),
                selection_getter=provider.dialog_options,
                activation_notice=provider.activation_notice,
            )
            dialog.exec()

        def open_telegram(parent):
            from .dialogs.telegram import TelegramDialog
            from .telegram_context import open_telegram_context

            if not contexts or runtime_controller is None:
                raise ValueError("setup_unavailable")
            current = contexts[0]
            stopped = _wait_operation(
                runtime_controller.quiesce(),
                "Đang dừng an toàn trước khi đăng nhập Telegram…",
                parent,
            )
            if stopped is not True:
                raise ValueError("telegram_worker_handoff_unavailable")
            # Retain the actual fence-owning context across failed cleanup and
            # dialog/controller disposal. Only successful drain clears it.
            runtime_controller._native_handoff_context = current

            def acquire():
                selected = open_telegram_context(
                    settings,
                    engine=current.engine,
                    fence=current.fence,
                    **(
                        {"secret_store_factory": context_options["secret_store_factory"]}
                        if context_options and "secret_store_factory" in context_options
                        else {}
                    ),
                )
                current.install_telegram(selected)
                return selected

            try:
                selected = _wait_operation(
                    control.executor.submit(acquire),
                    "Đang mở kết nối Telegram…",
                    parent,
                )
                dialog = TelegramDialog(selected.service, parent=parent, runner=selected.runner)
                dialog.exec()
                # Only the private owning context may supply evidence. Public
                # LoginStatus/on_changed never grants authority or pairing.
                _wait_operation(
                    control.executor.submit(current.advance_verified),
                    "Đang kiểm tra bước Telegram đã xác minh…",
                    parent,
                )
            finally:
                _wait_operation(
                    control.executor.submit(current.detach_telegram),
                    "Đang đóng phiên đăng nhập an toàn…",
                    parent,
                )

                def release_setup():
                    # Startup migration needs an exclusive maintenance fence.
                    # No native metadata probe may race that acquisition.
                    current.close()
                    contexts.clear()
                    runtime_controller._native_handoff_context = None

                _wait_operation(
                    control.executor.submit(release_setup),
                    "Đang chuẩn bị khởi động lại an toàn…",
                    parent,
                )
                _wait_operation(
                    runtime_controller.resume_after_handoff(),
                    "Đang mở lại ứng dụng…",
                    parent,
                )

        handlers = dict(dialog_handlers or {})
        handlers.setdefault("open_connection_dialog", open_provider)
        if runtime_controller is not None:
            handlers.setdefault("open_telegram_login", open_telegram)

        control = cls(
            coordinator,
            dialog_handlers=handlers,
            begin_handler=lambda: context().begin(),
            close_handler=close,
            skip_handler=lambda: context().skip_ai(),
            refresh_handler=refresh,
        )
        return control

    def refresh(self):
        if self.closed:
            raise ValueError("setup_closed")
        self.last_future = self.executor.submit(self._resume)
        return self.last_future

    def begin(self):
        if self.closed or self.begin_handler is None:
            raise ValueError("setup_unavailable")
        self.last_future = self.executor.submit(self._resume, True)
        return self.last_future

    def skip_ai(self):
        if self.closed or self.skip_handler is None:
            raise ValueError("setup_unavailable")
        self.last_future = self.executor.submit(self._resume, False, True)
        return self.last_future

    def _resume(self, begin=False, skip=False):
        try:
            if self.closed:
                return SetupOutcome(None)
            coordinator = self.coordinator_getter()
            measured = (
                self.skip_handler()
                if skip
                else self.begin_handler()
                if begin
                else self.refresh_handler()
                if self.refresh_handler
                else coordinator.resume()
            )
            return SetupOutcome(
                OnboardingStatus.model_validate(measured),
                coordinator.options().get("ai_mode") == "skip",
            )
        except Exception:
            return SetupOutcome(None)

    def close(self):
        if not self.closed:
            self.closed = True
            if self.last_future:
                self.last_future.cancel()
            # Disposal waits behind a running probe; the GUI never blocks or
            # closes its engine/fence while that operation is still using it.
            self.cleanup_future = self.executor.submit(self.close_handler)
            self.executor.shutdown(wait=False, cancel_futures=False)
        return self.cleanup_future


class SetupDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller, self.pending = controller, None
        self.restore_connection = None
        self.setWindowTitle("Thiết lập Telegram AI")
        self.resize(640, 660)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 27, 27)
        panel = ArtPanel()
        outer.addWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        title = QLabel("Telegram AI")
        title.setProperty("artRole", "title")
        layout.addWidget(title)
        self.owner = QLabel("Chưa xác minh tài khoản Telegram")
        self.next_action = QLabel("Đang kiểm tra thiết lập đã lưu…")
        self.next_action.setAccessibleName("Bước tiếp theo")
        self.limitations = QLabel()
        for label in (self.owner, self.next_action, self.limitations):
            label.setWordWrap(True)
            layout.addWidget(label)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        contents = QWidget()
        states = QVBoxLayout(contents)
        self.connections = {
            name: QLabel(f"{label}: Chưa kiểm tra") for name, label in CONNECTIONS.items()
        }
        for label in self.connections.values():
            label.setWordWrap(True)
            states.addWidget(label)
        self.stages = QLabel()
        self.stages.setWordWrap(True)
        states.addWidget(self.stages)
        scroll.setWidget(contents)
        layout.addWidget(scroll)
        self.connection_buttons = {}
        self.begin_button = QPushButton("Bắt đầu thiết lập")
        self.begin_button.setEnabled(False)
        self.begin_button.clicked.connect(self.begin)
        layout.addWidget(self.begin_button)
        self.skip_ai_button = QPushButton("Tiếp tục khi chưa bật AI")
        self.skip_ai_button.setEnabled(False)
        self.skip_ai_button.clicked.connect(self.skip_ai)
        layout.addWidget(self.skip_ai_button)
        for name, label in DIALOGS.items():
            button = QPushButton(label)
            button.setEnabled(False)
            button.clicked.connect(
                lambda _checked=False, selected=name: self.open_connection(selected)
            )
            self.connection_buttons[name] = button
            layout.addWidget(button)
        self.refresh_button = QPushButton("Kiểm tra lại và tiếp tục")
        self.refresh_button.setProperty("artTone", "primary")
        self.refresh_button.clicked.connect(self.refresh)
        layout.addWidget(self.refresh_button)
        close = QPushButton("Đóng")
        close.clicked.connect(self.reject)
        layout.addWidget(close)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.refresh()

    def refresh(self):
        if self.pending is not None:
            return
        self.begin_button.setEnabled(False)
        self.skip_ai_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        for button in self.connection_buttons.values():
            button.setEnabled(False)
        self.pending = self.controller.refresh()

    def begin(self):
        if self.pending is not None or self.controller.begin_handler is None:
            return
        self.begin_button.setEnabled(False)
        self.skip_ai_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        for button in self.connection_buttons.values():
            button.setEnabled(False)
        self.pending = self.controller.begin()

    def skip_ai(self):
        if self.pending is not None or self.controller.skip_handler is None:
            return
        self.skip_ai_button.setEnabled(False)
        self.begin_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        for button in self.connection_buttons.values():
            button.setEnabled(False)
        self.pending = self.controller.skip_ai()

    def poll(self):
        if self.pending is None or not self.pending.done():
            return
        try:
            outcome = self.pending.result()
        except Exception:
            outcome = SetupOutcome(None)
        self.pending = None
        self.refresh_button.setEnabled(True)
        self.begin_button.setEnabled(False)
        self.skip_ai_button.setEnabled(False)
        if outcome.status is None:
            self.owner.setText("Chưa xác minh tài khoản Telegram")
            self.next_action.setText(
                "Chưa thể kiểm tra thiết lập. Hãy kiểm tra kết nối rồi thử lại."
            )
            for name, label in self.connections.items():
                label.setText(f"{CONNECTIONS[name]}: Chưa kiểm tra")
            self.stages.clear()
            self.limitations.setText("Chưa xác minh khả năng sử dụng hiện tại.")
            return
        status = outcome.status
        self.begin_button.setEnabled(
            self.controller.begin_handler is not None
            and any(
                stage not in status.stage_evidence_ids for stage in ("welcome", "storage_ready")
            )
        )
        owner = status.profile.owner_id
        self.owner.setText(
            f"Tài khoản Telegram đã xác minh: {owner}"
            if owner
            else "Chưa xác minh tài khoản Telegram"
        )
        completed = status.stage_evidence_ids
        self.skip_ai_button.setEnabled(
            self.controller.skip_handler is not None
            and "storage_ready" in completed
            and "ai_configured" not in completed
        )
        if not completed:
            action = "Bắt đầu thiết lập kết nối AI và tài khoản Telegram."
        else:
            remaining = next(
                (
                    stage
                    for stage in STAGES
                    if stage not in completed
                    and not (stage == "first_answer" and outcome.ai_skipped)
                ),
                None,
            )
            action = (
                f"Tiếp tục: {STAGES[remaining]}."
                if remaining
                else "Thiết lập đã hoàn tất. Mở dashboard để quản lý."
            )
        self.next_action.setText(action)
        lines = []
        for stage, label in STAGES.items():
            state = "Đã hoàn tất" if stage in completed else "Chưa hoàn tất"
            if stage == "first_answer" and outcome.ai_skipped:
                state = "Chưa bật AI"
            lines.append(f"{label}: {state}")
        self.stages.setText("\n".join(lines))
        for measured in status.connections:
            name = measured.service.value
            state = STATES[measured.state.value]
            if name in {"chat_ai", "embeddings"} and any(
                capability in {"chat_metadata", "embedding_metadata"}
                for capability in measured.capabilities
            ):
                state = "Metadata đã kiểm tra; chưa chạy AI"
            self.connections[name].setText(f"{CONNECTIONS[name]}: {state}")
        limited = any(name in status.disabled_capabilities for name in ("chat_ai", "embeddings"))
        self.limitations.setText(
            "Bạn đã chọn chưa bật AI. Có thể kết nối sau trong ứng dụng."
            if outcome.ai_skipped
            else "AI hoặc chỉ mục AI chưa sẵn sàng; các khả năng còn thiếu được giữ tắt."
            if limited
            else ""
        )
        for name, button in self.connection_buttons.items():
            button.setEnabled(name in self.controller.dialog_handlers)
        if self.restore_connection is not None:
            opener = self.connection_buttons[self.restore_connection]
            if opener.isEnabled():
                self.activateWindow()
                opener.setFocus()
            self.restore_connection = None

    def open_connection(self, name):
        handler = self.controller.dialog_handlers.get(name)
        if handler is None or self.pending is not None:
            return
        try:
            handler(self)
        except Exception:
            self.next_action.setText("Chưa thể mở kết nối. Hãy kiểm tra ứng dụng rồi thử lại.")
        finally:
            self.restore_connection = name
            self.refresh()

    def done(self, result):
        self.timer.stop()
        super().done(result)

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)
