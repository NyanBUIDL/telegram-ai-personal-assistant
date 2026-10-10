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
        self.setup_dialog = None
        self.restore_setup_focus = False
        self.native_pending = None
        self.native_closed = False
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
        self.setup_button = QPushButton("Thiết lập kết nối")
        self.setup_button.setEnabled(False)
        self.setup_button.clicked.connect(self.open_setup)
        layout.addWidget(self.setup_button)
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
        # A hidden tray window must continue serving genuine native requests.
        self.native_timer = QTimer(self)
        self.native_timer.setInterval(500)
        self.native_timer.timeout.connect(self.poll_native_dialogs)
        self.native_timer.start()
        QTimer.singleShot(0, self.start)

    def poll_native_dialogs(self):
        if self.native_closed:
            return
        claim = getattr(self.controller, "claim_native_dialogs", None)
        if not callable(claim):
            return
        if self.native_pending is not None:
            if not self.native_pending.done():
                return
            try:
                command = self.native_pending.result()
            except Exception:
                command = None
            self.native_pending = None
            if command is not None and self.setup_dialog is None and self.backup_dialog is None:
                self.show()
                self.raise_()
                self.activateWindow()
                self.open_setup(requested_dialog=command.name.value)
        if self.native_closed:
            return
        available = (
            ["open_connection_dialog", "open_telegram_login", "open_bot_dialog"]
            if self.setup_dialog is None and self.backup_dialog is None
            and not getattr(self.controller, "handoff_active", False)
            else []
        )
        self.native_pending = claim(available)

    def finish_native_dialogs(self):
        self.native_closed = True
        self.native_timer.stop()
        if self.native_pending is not None:
            self.native_pending.cancel()
            self.native_pending = None

    def start(self):
        if self.backup_busy or getattr(self.controller, "handoff_active", False):
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
            handoff = getattr(self.controller, "handoff_active", False)
            self.start_button.setEnabled(not active and not self.backup_busy and not handoff)
            self.stop_button.setEnabled(state.phase in {"starting", "ready"})
            self.dashboard_button.setEnabled(state.phase == "ready" and self.dashboard_pending is None and not self.backup_busy)
            self.setup_button.setEnabled(state.phase == "ready" and not self.backup_busy and self.setup_dialog is None)
            if self.restore_setup_focus and self.setup_button.isEnabled():
                self.setup_button.setFocus()
                self.restore_setup_focus = False
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

        if getattr(self.controller, "handoff_active", False):
            return
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

    def open_setup(self, _checked=False, *, requested_dialog=None):
        from .setup_ui import NativeSetupController, SetupDialog

        if self.backup_busy:
            return
        if self.setup_dialog is not None:
            self.setup_dialog.raise_()
            return
        controller = NativeSetupController.for_settings(
            self.controller.settings, runtime_controller=self.controller,
        )
        dialog = SetupDialog(controller, self)
        self.setup_dialog = dialog
        dispatch = QTimer(dialog)
        if requested_dialog is not None:
            from time import monotonic

            deadline = monotonic() + 10

            def open_requested():
                if self.native_closed or self.setup_dialog is not dialog:
                    dispatch.stop()
                    return
                button = dialog.connection_buttons.get(requested_dialog)
                if dialog.pending is None and button is not None and button.isEnabled():
                    dispatch.stop()
                    dialog.open_connection(requested_dialog)
                elif monotonic() >= deadline:
                    dispatch.stop()
                    dialog.next_action.setText(
                        "Chưa thể mở kết nối. Kiểm tra ứng dụng Windows rồi thử lại."
                    )

            dispatch.timeout.connect(open_requested)
            dispatch.start(25)
        try:
            dialog.exec()
        finally:
            dispatch.stop()
            if requested_dialog is not None:
                # Disconnect the timer callback's window/dialog closure before
                # QApplication teardown; stopping alone retains that cycle.
                dispatch.timeout.disconnect()
            controller.close()
            self.setup_dialog = None
            # Readiness polling temporarily replaces the controller snapshot.
            # Restore focus after the next completed measurement enables the
            # opener, rather than focusing a disabled control mid-probe.
            self.restore_setup_focus = True
            if self.isVisible():
                self.activateWindow()
            self.poll()

    def finish_setup(self):
        if self.setup_dialog is not None:
            self.setup_dialog.reject()

    def closeEvent(self, event):
        self.timer.stop()
        if self.hide_on_close:
            self.hide()
            event.ignore()
            return
        self.finish_native_dialogs()
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
        window.finish_setup()
        window.finish_native_dialogs()
        controller.close()


if __name__ == "__main__":
    main()
