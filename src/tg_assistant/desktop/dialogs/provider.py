"""Native provider dialog using shared art, bounded background metadata checks."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from queue import Empty, SimpleQueue
from threading import Event

from PySide6.QtCore import QSignalBlocker, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
)

from ...contracts import ConnectionStatus, OperationResult
from ...services.ollama import format_model_size
from ...services.provider_connections import ENDPOINTS
from ..theme import ArtPanel

_HELP = {
    "openai": "https://platform.openai.com/api-keys",
    "openrouter": "https://openrouter.ai/settings/keys",
    "ollama": "https://ollama.com/download/windows",
}
_STATES = {
    "ready": "Metadata đã kiểm tra",
    "degraded": "Có giới hạn",
    "unknown": "Chưa xác minh",
    "checking": "Đang kiểm tra",
    "disconnected": "Chưa kết nối",
}


class ProviderDialog(QDialog):
    """Service must come from the trusted current-SID native runtime factory.

    on_changed receives sanitized ConnectionStatus only. It is a refresh hook,
    never authorization to complete onboarding stages or send source content.
    """

    def __init__(
        self,
        service,
        *,
        provider="openai",
        service_name="chat_ai",
        model="",
        endpoint=None,
        cloud_consent=False,
        parent=None,
        on_changed=None,
        selection_getter=None,
        activation_notice=None,
    ):
        super().__init__(parent)
        if provider not in ENDPOINTS or service_name not in {"chat_ai", "embeddings"}:
            raise ValueError("provider_dialog_selection_invalid")
        self._service, self._changed = service, on_changed
        self._selection_getter = selection_getter
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="native-provider")
        self._future = None
        self._cancel = Event()
        self._closed = False
        self._download_preview = None
        self._progress = SimpleQueue()
        self._action = None
        self._restore_focus = parent.focusWidget() if parent is not None else None
        self.setWindowTitle("Kết nối AI • Telegram AI")
        self.setModal(True)
        self.resize(680, 650)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 27, 27)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        panel = ArtPanel()
        body = QVBoxLayout(panel)
        body.setContentsMargins(20, 20, 20, 20)
        title = QLabel("Kết nối AI")
        title.setProperty("artRole", "title")
        body.addWidget(title)
        introduction = QLabel(
            "Kiểm tra xác thực và metadata không tạo câu trả lời hoặc embedding có phí. "
            "Quyền dùng nguồn, dữ liệu cloud và ngân sách vẫn cần xác nhận riêng trước khi dùng AI."
        )
        introduction.setWordWrap(True)
        body.addWidget(introduction)
        form = QFormLayout()
        self.provider = QComboBox()
        for name in ENDPOINTS:
            self.provider.addItem(name.title(), name)
        self.provider.setCurrentIndex(self.provider.findData(provider))
        form.addRow("Nhà cung cấp", self.provider)
        self.role = QComboBox()
        self.role.addItem("AI trả lời", "chat_ai")
        self.role.addItem("Chỉ mục embedding (kiểm tra riêng)", "embeddings")
        self.role.setCurrentIndex(self.role.findData(service_name))
        form.addRow("Chức năng", self.role)
        self.model = QLineEdit(model)
        self.model.setMaxLength(256)
        self.model.setPlaceholderText("Model ID chính xác từ nhà cung cấp")
        form.addRow("Model", self.model)
        self.endpoint = QLineEdit(endpoint or ENDPOINTS[provider])
        self.endpoint.setMaxLength(512)
        form.addRow("Endpoint", self.endpoint)
        self.secret_input = QLineEdit()
        self.secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret_input.setMaxLength(4096)
        self.secret_input.setPlaceholderText("Ô luôn trống. Để trống để kiểm tra key đang lưu.")
        form.addRow("API key native", self.secret_input)
        body.addLayout(form)
        self.cloud_consent = QCheckBox("Tôi đồng ý kết nối cloud (chưa gửi nội dung nguồn).")
        self.cloud_consent.setChecked(cloud_consent is True)
        body.addWidget(self.cloud_consent)
        self.allow_unverified = QCheckBox("Lưu chưa xác minh (AI chưa sẵn sàng).")
        body.addWidget(self.allow_unverified)
        self.local_hint = QLabel(
            "Ollama chạy trên máy này, chỉ dùng loopback. Chọn/cài Ollama trên trang chính thức. "
            "Bấm xem dung lượng từ thư viện chính thức trước khi xác nhận tải. "
            "Không tự cài Ollama. Tải lại sẽ dùng phần dữ liệu Ollama đã giữ nếu còn hợp lệ."
        )
        self.local_hint.setWordWrap(True)
        body.addWidget(self.local_hint)
        self.download_size = QLabel("Chưa biết dung lượng; chưa bắt đầu tải.")
        self.download_size.setWordWrap(True)
        body.addWidget(self.download_size)
        self.download_consent = QCheckBox("Tôi xác nhận model và dung lượng trên; cho phép tải.")
        body.addWidget(self.download_consent)
        self.preview_button = QPushButton("Xem dung lượng model")
        self.pull_button = QPushButton("Tải model đã chọn")
        self.cancel_pull_button = QPushButton("Hủy tải")
        downloads = QGridLayout()
        for column, button in enumerate(
            (self.preview_button, self.pull_button, self.cancel_pull_button)
        ):
            button.setMinimumHeight(44)
            downloads.addWidget(button, 0, column)
        body.addLayout(downloads)
        self.download_progress = QProgressBar()
        self.download_progress.setRange(0, 100)
        self.download_progress.setValue(0)
        self.download_progress.setFormat("Layer hiện tại: %p%")
        self.download_progress.setAccessibleName(
            "Tiến độ layer đang tải; hoàn tất model được báo riêng"
        )
        body.addWidget(self.download_progress)
        self.status_label = QLabel("Chưa kiểm tra. Chat và embedding có trạng thái riêng.")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("Kết quả kiểm tra kết nối")
        body.addWidget(self.status_label)
        if activation_notice:
            notice = QLabel(activation_notice)
            notice.setWordWrap(True)
            notice.setAccessibleName("Lựa chọn đã lưu và trạng thái áp dụng")
            body.addWidget(notice)
        buttons = QGridLayout()
        self.connect_button = QPushButton("Kiểm tra và lưu")
        self.connect_button.setProperty("artTone", "primary")
        self.test_button = QPushButton("Kiểm tra key đã lưu")
        self.disconnect_button = QPushButton("Ngắt provider")
        self.disconnect_button.setProperty("artTone", "danger")
        for button in (self.connect_button, self.test_button, self.disconnect_button):
            button.setMinimumHeight(44)
        buttons.addWidget(self.connect_button, 0, 0)
        buttons.addWidget(self.test_button, 0, 1)
        buttons.addWidget(self.disconnect_button, 1, 0, 1, 2)
        body.addLayout(buttons)
        lower = QHBoxLayout()
        self.help_button = QPushButton("Mở trang cấp key chính thức")
        self.close_button = QPushButton("Đóng (Esc)")
        for button in (self.help_button, self.close_button):
            button.setMinimumHeight(44)
            lower.addWidget(button)
        body.addLayout(lower)
        scroll.setWidget(panel)
        outer.addWidget(scroll)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.role.currentIndexChanged.connect(self._role_changed)
        self.connect_button.clicked.connect(lambda: self._start("connect"))
        self.test_button.clicked.connect(lambda: self._start("test"))
        self.disconnect_button.clicked.connect(lambda: self._start("disconnect"))
        self.preview_button.clicked.connect(lambda: self._start_download("preview"))
        self.pull_button.clicked.connect(lambda: self._start_download("pull"))
        self.cancel_pull_button.clicked.connect(lambda: self._cancel.set())
        self.download_consent.toggled.connect(self._apply_provider)
        self.model.textChanged.connect(self._invalidate_preview)
        self.endpoint.textChanged.connect(self._invalidate_preview)
        self.help_button.clicked.connect(self._help)
        self.close_button.clicked.connect(self.reject)
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._poll)
        self._apply_provider()
        self.secret_input.setFocus()

    @property
    def busy(self):
        return self._future is not None and (not self._closed or not self._future.done())

    def _provider_changed(self):
        self.secret_input.clear()
        self.endpoint.setText(ENDPOINTS[self.provider.currentData()])
        self.cloud_consent.setChecked(False)
        self.allow_unverified.setChecked(False)
        self.model.clear()
        self._apply_provider()

    def _apply_provider(self):
        local = self.provider.currentData() == "ollama"
        self.secret_input.setEnabled(not local and not self.busy)
        self.endpoint.setReadOnly(not local)
        self.cloud_consent.setVisible(not local)
        self.local_hint.setVisible(local)
        for widget in (
            self.download_size,
            self.download_consent,
            self.preview_button,
            self.pull_button,
            self.cancel_pull_button,
            self.download_progress,
        ):
            widget.setVisible(local)
        self.preview_button.setEnabled(local and not self.busy)
        self.download_consent.setEnabled(local and not self.busy)
        self.pull_button.setEnabled(
            local
            and not self.busy
            and self._download_preview is not None
            and self.download_consent.isChecked()
        )
        self.cancel_pull_button.setEnabled(local and self.busy and self._action == "pull")
        self.help_button.setText(
            "Mở trang Ollama Windows" if local else "Mở trang cấp key chính thức"
        )

    def _role_changed(self):
        self.secret_input.clear()
        if self._selection_getter is not None:
            selected = self._selection_getter(self.role.currentData())
            with QSignalBlocker(self.provider):
                self.provider.setCurrentIndex(self.provider.findData(selected["provider"]))
            self.model.setText(selected["model"])
            self.endpoint.setText(selected["endpoint"])
            self.cloud_consent.setChecked(selected["cloud_consent"])
            self.allow_unverified.setChecked(False)
            self._apply_provider()

    def _help(self):
        # No user-supplied URL or credential enters a browser argument.
        QDesktopServices.openUrl(QUrl(_HELP[self.provider.currentData()]))

    def _start(self, action):
        if self.busy or self._closed:
            return
        provider = self.provider.currentData()
        options = {
            "service": self.role.currentData(),
            "model": self.model.text(),
            "endpoint": self.endpoint.text(),
            "cloud_consent": self.cloud_consent.isChecked(),
            "allow_unverified": self.allow_unverified.isChecked(),
        }
        secret = self.secret_input.text() if action == "connect" else ""
        self.secret_input.clear()
        self._cancel = Event()
        self._action = action
        if action == "connect":
            self._future = self._executor.submit(
                self._service.validate_and_save, provider, secret, options, cancel=self._cancel
            )
        elif action == "test":
            self._future = self._executor.submit(self._service.test_connection, provider, options)
        else:
            self._future = self._executor.submit(self._service.disconnect, provider)
        for widget in (
            self.provider,
            self.role,
            self.model,
            self.endpoint,
            self.secret_input,
            self.cloud_consent,
            self.allow_unverified,
            self.connect_button,
            self.test_button,
            self.disconnect_button,
        ):
            widget.setEnabled(False)
        self.status_label.setText(
            "Đang kiểm tra… không gửi nội dung nguồn hoặc yêu cầu tạo nội dung."
        )
        self._timer.start()
        self._apply_provider()

    def _invalidate_preview(self):
        self._download_preview = None
        self.download_consent.setChecked(False)
        self.download_size.setText("Chưa biết dung lượng; xem lại trước khi tải.")
        self.download_progress.setValue(0)
        self.pull_button.setText("Tải model đã chọn")
        self._apply_provider()

    def _start_download(self, action):
        if self.busy or self._closed or self.provider.currentData() != "ollama":
            return
        if action == "pull" and (
            self._download_preview is None or not self.download_consent.isChecked()
        ):
            return
        self.secret_input.clear()
        self._cancel = Event()
        self._action = action
        if action == "preview":
            self._future = self._executor.submit(
                self._service.preview_local_download,
                self.model.text(),
                endpoint=self.endpoint.text(),
            )
        else:
            self._future = self._executor.submit(
                self._service.pull_local_model,
                self._download_preview.model,
                endpoint=self.endpoint.text(),
                size_bytes=self._download_preview.size_bytes,
                owner_confirmed=True,
                cancel=self._cancel,
                on_progress=self._progress.put,
            )
        for widget in (
            self.provider,
            self.role,
            self.model,
            self.endpoint,
            self.connect_button,
            self.test_button,
            self.disconnect_button,
        ):
            widget.setEnabled(False)
        self.status_label.setText(
            "Đang xem dung lượng…" if action == "preview" else "Đang tải model đã chọn…"
        )
        self._apply_provider()
        self._timer.start()

    def _poll(self):
        while True:
            try:
                progress = self._progress.get_nowait()
            except Empty:
                break
            value = progress.get("progress")
            self.download_progress.setRange(0, 0 if value is None else 100)
            if value is not None:
                self.download_progress.setValue(value)
        if self._future is None or not self._future.done():
            return
        self._timer.stop()
        try:
            result = self._future.result()
            if self._action == "preview":
                self._download_preview = result
                self.download_size.setText(
                    f"{result.model}: {format_model_size(result.size_bytes)} "
                    f"({result.size_bytes:,} byte) theo manifest. Dung lượng tổng; "
                    "Ollama có thể dùng layer đã tải. Model chưa được kích hoạt."
                )
                self.download_consent.setChecked(False)
                self.status_label.setText("Đã xem dung lượng; xác nhận trước khi tải.")
            elif self._action == "pull":
                result = OperationResult.model_validate(result)
                self.status_label.setText(result.message)
                self.download_consent.setChecked(False)
                self.pull_button.setText(
                    "Tiếp tục tải model" if result.state != "completed" else "Tải lại model"
                )
                self.download_progress.setRange(0, 100)
                if result.state == "completed":
                    self.download_progress.setValue(100)
            else:
                status = ConnectionStatus.model_validate(result)
                self.status_label.setText(
                    f"{_STATES[status.state]} • {status.code}\n{status.message}\n{status.next_action or ''}"
                )
                if self._changed:
                    self._changed(status)
        except Exception:
            self.status_label.setText(
                "Không hoàn tất thao tác. Kiểm tra lại trong ứng dụng Windows."
            )
        self._future = None
        for widget in (
            self.provider,
            self.role,
            self.model,
            self.endpoint,
            self.secret_input,
            self.cloud_consent,
            self.allow_unverified,
            self.connect_button,
            self.test_button,
            self.disconnect_button,
        ):
            widget.setEnabled(True)
        self._apply_provider()
        self.connect_button.setFocus()

    def done(self, result):
        self._cancel.set()
        self.secret_input.clear()
        self._closed = True
        self._timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
        super().done(result)
        if self._restore_focus is not None:
            self._restore_focus.setFocus()
