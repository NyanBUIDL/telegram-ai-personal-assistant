"""Qt launcher; startup and readiness never wait for terminal input."""

from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from ..config import get_settings
from ..paths import resource_path
from .runtime_controller import RuntimeController
from .theme import ArtPanel, apply_theme
from .tray import TrayController

TEXT = {
    "runtime_starting": "Đang mở ứng dụng…",
    "runtime_ready": "Ứng dụng đang chạy. Có thể tiếp tục thiết lập kết nối.",
    "runtime_stopped": "Ứng dụng đã dừng.",
    "runtime_stopping": "Đang dừng an toàn…",
    "runtime_already_running": "Ứng dụng đang chạy ở cửa sổ hoặc profile khác.",
    "runtime_start_failed": "Không thể khởi động. Kiểm tra cấu hình hoặc trạng thái bảo trì.",
    "runtime_readiness_stale": "Đang kiểm tra trạng thái ứng dụng…",
}


class LauncherWindow(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.pending = None
        self.hide_on_close = False
        self.setWindowTitle("Telegram AI")
        self.resize(540, 330)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 27, 27)
        panel = ArtPanel()
        outer.addWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 20, 20, 20)
        self.title = QLabel("Telegram AI")
        self.title.setProperty("artRole", "title")
        layout.addWidget(self.title)
        self.status = QLabel(TEXT["runtime_starting"])
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Trạng thái ứng dụng")
        layout.addWidget(self.status)
        self.start_button = QPushButton("Khởi động")
        self.start_button.setProperty("artTone", "primary")
        self.start_button.clicked.connect(self.start)
        layout.addWidget(self.start_button)
        self.stop_button = QPushButton("Dừng an toàn")
        self.stop_button.clicked.connect(self.controller.stop)
        layout.addWidget(self.stop_button)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        QTimer.singleShot(0, self.start)

    def start(self):
        self.controller.start()
        self.poll()

    def poll(self):
        if self.pending and self.pending.done():
            state = self.pending.result()
            self.pending = None
            self.status.setText(TEXT.get(state.code, "Đang kiểm tra trạng thái ứng dụng…"))
            active = state.phase in {"starting", "ready", "stopping"}
            self.start_button.setEnabled(not active)
            self.stop_button.setEnabled(state.phase in {"starting", "ready"})
        if self.pending is None:
            self.pending = self.controller.refresh()

    def closeEvent(self, event):
        self.timer.stop()
        if self.hide_on_close:
            self.hide()
            event.ignore()
            return
        event.accept()

    def showEvent(self, event):
        self.timer.start()
        super().showEvent(event)


def main():
    if sys.argv[1:] == ["--desktop-worker"]:
        from .worker import serve_worker

        serve_worker()
        return
    application = QApplication(sys.argv[:1])
    apply_theme(application, resource_path("dashboard-prototype", "public", "fonts"))
    controller = RuntimeController(get_settings())
    window = LauncherWindow(controller)
    tray = TrayController(window, controller)
    application.setQuitOnLastWindowClosed(not tray.available)
    window.show()
    try:
        application.exec()
    finally:
        tray.close()
        controller.close()


if __name__ == "__main__":
    main()
