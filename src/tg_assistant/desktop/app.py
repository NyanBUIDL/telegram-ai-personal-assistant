"""Qt launcher; startup and readiness never wait for terminal input."""

from __future__ import annotations

import sys

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
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
        self.dashboard_pending = None
        self.hide_on_close = False
        self.backup_dialog = None
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
        self.dashboard_button = QPushButton("Mở dashboard")
        self.dashboard_button.setProperty("artTone", "primary")
        self.dashboard_button.setEnabled(False)
        self.dashboard_button.clicked.connect(self.open_dashboard)
        layout.addWidget(self.dashboard_button)
        self.start_button = QPushButton("Khởi động")
        self.start_button.setProperty("artTone", "primary")
        self.start_button.clicked.connect(self.start)
        layout.addWidget(self.start_button)
        self.stop_button = QPushButton("Dừng an toàn")
        self.stop_button.clicked.connect(self.controller.stop)
        layout.addWidget(self.stop_button)
        self.backup_button = QPushButton("Dữ liệu và sao lưu")
        self.backup_button.clicked.connect(self.open_backup)
        layout.addWidget(self.backup_button)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        QTimer.singleShot(0, self.start)

    def start(self):
        if self.backup_busy:
            return
        self.controller.start()
        self.poll()

    @property
    def backup_busy(self):
        return self.backup_dialog is not None and self.backup_dialog.pending is not None

    def poll(self):
        if self.dashboard_pending and self.dashboard_pending.done():
            try:
                url = self.dashboard_pending.result()
                if not QDesktopServices.openUrl(QUrl(url)):
                    raise OSError("browser_unavailable")
            except (OSError, PermissionError, ValueError):
                self.status.setText("Không thể mở dashboard. Hãy thử lại từ ứng dụng.")
            finally:
                self.dashboard_pending = None
        if self.pending and self.pending.done():
            state = self.pending.result()
            self.pending = None
            self.status.setText(TEXT.get(state.code, "Đang kiểm tra trạng thái ứng dụng…"))
            active = state.phase in {"starting", "ready", "stopping"}
            self.start_button.setEnabled(not active and not self.backup_busy)
            self.stop_button.setEnabled(state.phase in {"starting", "ready"})
            self.dashboard_button.setEnabled(state.phase == "ready" and self.dashboard_pending is None and not self.backup_busy)
        if self.pending is None:
            self.pending = self.controller.refresh()

    def open_dashboard(self):
        if self.backup_busy:
            return
        if self.dashboard_pending is None:
            self.dashboard_button.setEnabled(False)
            self.dashboard_pending = self.controller.open_dashboard()

    def open_backup(self):
        from .backup_ui import BackupDialog, NativeBackupController

        if self.backup_dialog is not None:
            self.backup_dialog.raise_()
            return
        dialog = BackupDialog(
            NativeBackupController(self.controller), self.controller.settings.data_dir, self
        )
        self.backup_dialog = dialog
        try:
            dialog.exec()
        finally:
            dialog.finish()
            self.backup_dialog = None
            self.backup_button.setFocus()

    def finish_backup(self):
        if self.backup_dialog is not None:
            self.backup_dialog.finish()

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
        window.finish_backup()
        controller.close()


if __name__ == "__main__":
    main()
