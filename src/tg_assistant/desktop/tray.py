"""Native tray and explicit graceful-exit choices, sharing approved art."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
)

from .theme import TOKENS


class ExitDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.choice = "cancel"
        self.setWindowTitle("Đóng Telegram AI")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        label = QLabel("Bạn muốn ứng dụng tiếp tục chạy hay dừng an toàn?")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.buttons = {}
        for choice, title in (
            ("background", "Đóng cửa sổ, tiếp tục chạy"),
            ("stop", "Dừng an toàn rồi thoát"),
            ("cancel", "Quay lại"),
        ):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, choice=choice: self.choose(choice))
            layout.addWidget(button)
            self.buttons[choice] = button
        self.buttons["cancel"].setFocus()

    def choose(self, choice):
        self.choice = choice
        self.accept()


def tray_icon():
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(TOKENS["colors"]["paper"]))
    painter = QPainter(pixmap)
    painter.setPen(QColor(TOKENS["colors"]["ink"]))
    painter.fillRect(4, 4, 24, 24, QColor(TOKENS["colors"]["teal"]))
    painter.drawRect(3, 3, 25, 25)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "T")
    painter.end()
    return QIcon(pixmap)


class TrayController(QObject):
    def __init__(self, window, runtime, *, quit_application=None):
        super().__init__(window)
        self.window, self.runtime = window, runtime
        self.quit_application = quit_application or QApplication.instance().quit
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self.icon = QSystemTrayIcon(tray_icon(), self)
        self.icon.setToolTip("Telegram AI")
        self.menu = QMenu(window)
        for title, handler in (
            ("Mở ứng dụng", self.open),
            ("Khởi động", window.start),
            ("Dừng an toàn", runtime.stop),
            ("Thoát…", self.request_exit),
        ):
            action = QAction(title, self.menu)
            action.triggered.connect(handler)
            self.menu.addAction(action)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self.activated)
        self.pending = None
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.wait_for_stop)
        if self.available:
            self.icon.show()
            window.hide_on_close = True

    def activated(self, reason):
        if reason in {QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick}:
            self.open()

    def open(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def request_exit(self):
        dialog = ExitDialog(self.window)
        dialog.exec()
        self.finish_exit(dialog.choice)

    def finish_exit(self, choice):
        if choice == "background":
            self.quit_application()
        elif choice == "stop":
            self.runtime.stop()
            self.timer.start()

    def wait_for_stop(self):
        if self.pending and self.pending.done():
            state = self.pending.result()
            self.pending = None
            if state.phase == "stopped":
                self.timer.stop()
                self.quit_application()
                return
            if state.phase == "error":
                self.timer.stop()
                self.open()
                return
        if self.pending is None:
            self.pending = self.runtime.refresh()

    def close(self):
        self.timer.stop()
        self.icon.hide()
