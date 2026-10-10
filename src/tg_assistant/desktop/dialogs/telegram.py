"""Native QR/phone/OTP/2FA dialog; auth IO stays on one dedicated event loop."""
from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime
from threading import Thread

import qrcode
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
)

from ...services.telegram_login import API_HELP_URL, LoginStatus
from ..theme import ArtPanel

_MESSAGES = {
    "login_idle": "Nhập API ID/hash để bắt đầu. QR vẫn cần cả hai giá trị.",
    "api_credentials_required": "Cần API ID dương và API hash 32 ký tự từ trang chính thức.",
    "qr_pending": "Telegram trên điện thoại: Cài đặt → Thiết bị → Liên kết thiết bị.",
    "qr_expired": "QR đã hết hạn. Bấm Tạo/làm mới QR hoặc dùng số điện thoại.",
    "code_required": "Nhập mã do Telegram gửi. Ứng dụng không tự đọc SMS, email hoặc OTP.",
    "password_required": "Tài khoản cần mật khẩu xác minh hai bước (2FA).",
    "code_input_invalid": "Nhập mã gồm 3–10 chữ số từ Telegram rồi xác nhận lại. Không cần gửi mã mới.",
    "password_input_invalid": "Nhập mật khẩu 2FA (1–1024 ký tự) rồi xác nhận lại, hoặc hủy.",
    "code_invalid": "Mã không hợp lệ. Kiểm tra lại mã trong Telegram.",
    "password_invalid": "Mật khẩu 2FA không hợp lệ. Thử lại hoặc hủy.",
    "code_expired": "Mã đã hết hạn. Bắt đầu lại bằng số điện thoại.",
    "phone_invalid": "Nhập số điện thoại quốc tế, bắt đầu bằng + và mã quốc gia.",
    "retry_limit": "Đã hết lượt thử. Bắt đầu một lần đăng nhập mới.",
    "flood_wait": "Telegram yêu cầu chờ trước khi thử tiếp.",
    "session_verified": "Đã xác minh tài khoản và phiên trong native runtime.",
    "session_check_required": "Cần xác minh lại phiên Telegram. Đăng nhập lại khi cần.",
    "publication_unavailable": "Đã xác thực nhưng bộ lưu phiên runtime chưa khả dụng. Thiết lập chưa hoàn tất.",
    "owner_invalid": "Telegram không trả về tài khoản người dùng hợp lệ.",
    "owner_mismatch": "Tài khoản khác owner hiện tại. Ứng dụng không tự thay owner.",
    "profile_binding_invalid": "Không xác minh được quyền sở hữu Windows/profile.",
    "maintenance_busy": "Ứng dụng đang bảo trì. Hãy thử lại sau khi kết thúc.",
    "auth_timeout": "Kết nối hết thời gian chờ. Kiểm tra mạng rồi thử lại.",
    "auth_checking": "Đang xác thực kết nối Telegram… có thể hủy bằng Esc.",
    "login_cancelled": "Đã hủy. OTP, 2FA và QR không được lưu.",
    "auth_state_invalid": "Bước xác thực đã kết thúc. Bắt đầu lại.",
    "auth_failed": "Không thể xác thực. Kiểm tra trong Telegram rồi thử lại.",
}


def _qr_pixmap(uri):
    """Encode/render only in native RAM, without image files/Pillow/browser."""
    qr = qrcode.QRCode(border=4, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(uri)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    scale = max(4, 248 // len(matrix))
    side = len(matrix) * scale
    image = QImage(side, side, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("black"))
    for row, values in enumerate(matrix):
        for col, black in enumerate(values):
            if black:
                painter.drawRect(col * scale, row * scale, scale, scale)
    painter.end()
    return QPixmap.fromImage(image)


class _NativeLoop:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = Thread(target=self._run, name="native-telegram", daemon=True)
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            if pending:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.close()

    def submit(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def close(self, service):
        service.request_cancel()
        future = self.submit(service.cancel())
        future.add_done_callback(lambda _: self.loop.call_soon_threadsafe(self.loop.stop))


class TelegramDialog(QDialog):
    """service must be freshly created by trusted current-SID runtime.

    on_changed sees sanitized LoginStatus only. It cannot mark setup complete.
    The dialog owns the service/event loop until close; don't share this service
    with another event loop or parallel desktop/runtime client writer.
    """
    def __init__(self, service, *, parent=None, on_changed=None, runner=None):
        super().__init__(parent)
        self._service, self._changed = service, on_changed
        self._owns_runner = runner is None
        self._worker = runner if runner is not None else _NativeLoop()
        self._future = None
        self._closed = False
        self._displayed_qr = None
        self._last_status = None
        self._restore_focus = parent.focusWidget() if parent is not None else None
        self.setModal(True)
        self.setWindowTitle("Đăng nhập Telegram • Telegram AI")
        self.resize(650, 780)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 27, 27)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        panel = ArtPanel()
        body = QVBoxLayout(panel)
        body.setContentsMargins(20, 20, 20, 20)
        title = QLabel("Đăng nhập Telegram")
        title.setProperty("artRole", "title")
        body.addWidget(title)
        intro = QLabel(
            "API ID/hash xác định ứng dụng Telegram của bạn, kể cả đăng nhập QR. "
            "Tạo thủ công ở my.telegram.org → API development tools. "
            "Không tự đăng ký API hoặc đọc OTP/email. Các ô bí mật luôn để trống."
        )
        intro.setWordWrap(True)
        body.addWidget(intro)
        form = QFormLayout()
        self.api_id = QLineEdit()
        self.api_id.setObjectName("api_id")
        self.api_id.setMaxLength(12)
        form.addRow("API ID", self.api_id)
        for name, label, limit in (
            ("api_hash", "API hash (native)", 32),
            ("phone", "Điện thoại (+mã quốc gia)", 16),
            ("otp", "Mã đăng nhập (OTP)", 10),
            ("password", "Mật khẩu hai bước (2FA)", 1024),
        ):
            widget = QLineEdit()
            widget.setObjectName(name)
            widget.setEchoMode(QLineEdit.EchoMode.Password)
            widget.setMaxLength(limit)
            widget.setMinimumHeight(44)
            setattr(self, name, widget)
            form.addRow(label, widget)
        self.api_id.setMinimumHeight(44)
        body.addLayout(form)
        self.qr_label = QLabel("QR chưa được tạo")
        self.qr_label.setObjectName("native_qr")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setAccessibleName("Mã QR Telegram chỉ hiển thị trong cửa sổ native")
        self.qr_label.setMinimumHeight(200)
        body.addWidget(self.qr_label)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("Trạng thái đăng nhập Telegram")
        body.addWidget(self.status_label)
        grid = QGridLayout()
        self.qr_button = QPushButton("Tạo / làm mới QR")
        self.qr_button.setProperty("artTone", "primary")
        self.phone_button = QPushButton("Gửi mã tới điện thoại")
        self.code_button = QPushButton("Xác nhận mã OTP")
        self.password_button = QPushButton("Xác nhận 2FA")
        self.help_button = QPushButton("Mở trang API chính thức")
        self.close_button = QPushButton("Hủy / đóng (Esc)")
        self.close_button.setProperty("artTone", "danger")
        self._actions = (self.qr_button, self.phone_button, self.code_button, self.password_button)
        for index, button in enumerate((*self._actions, self.help_button, self.close_button)):
            button.setMinimumHeight(44)
            grid.addWidget(button, index // 2, index % 2)
        body.addLayout(grid)
        scroll.setWidget(panel)
        outer.addWidget(scroll)
        self.qr_button.clicked.connect(lambda: self._start("qr"))
        self.phone_button.clicked.connect(lambda: self._start("phone"))
        self.code_button.clicked.connect(lambda: self._start("code"))
        self.password_button.clicked.connect(lambda: self._start("password"))
        self.help_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(API_HELP_URL)))
        self.close_button.clicked.connect(self.reject)
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._render(service.status())
        self.api_id.setFocus()

    def _credentials(self):
        value = self.api_id.text()
        secret = self.api_hash.text()
        self.api_id.clear()
        self.api_hash.clear()
        if not value and not secret:
            return {}
        return {"api_id": int(value) if value.isascii() and value.isdecimal() else None,
                "api_hash": secret}

    def _start(self, action):
        if self._closed or self._future is not None:
            return
        if action == "qr":
            coroutine = self._service.begin_qr(**self._credentials())
        elif action == "phone":
            value = self.phone.text()
            self.phone.clear()
            coroutine = self._service.submit_phone(value, **self._credentials())
        elif action == "code":
            value = self.otp.text()
            self.otp.clear()
            coroutine = self._service.submit_code(value)
        else:
            value = self.password.text()
            self.password.clear()
            coroutine = self._service.submit_password(value)
        # Clear obsolete QR immediately when refresh/fallback starts.
        self.qr_label.clear()
        self._displayed_qr = None
        self._future = self._worker.submit(coroutine)
        for button in self._actions:
            button.setEnabled(False)
        self.status_label.setText("Đang xác thực… có thể hủy bằng Esc.")

    def _poll(self):
        if self._closed:
            return
        if self._future is not None:
            if not self._future.done():
                return
            try:
                self._future.result()
            except Exception:
                self.status_label.setText(_MESSAGES["auth_failed"])
            self._future = None
        self._render(self._service.status())

    def _render(self, status):
        if not isinstance(status, LoginStatus):
            self.status_label.setText(_MESSAGES["auth_failed"])
            return
        message = _MESSAGES.get(status.code, _MESSAGES["auth_failed"])
        if status.retry_after:
            message += f" Chờ {status.retry_after} giây."
        if status.state in {"code", "password"}:
            message += f" Còn {status.attempts_remaining} lượt thử."
        qr = self._service.native_qr()
        if qr is not None and status.state == "qr":
            if qr is not self._displayed_qr:
                self.qr_label.setPixmap(_qr_pixmap(qr.uri))
                self._displayed_qr = qr
            seconds = max(0, math.ceil((qr.expires - datetime.now(UTC)).total_seconds()))
            message += f" QR hết hạn sau {seconds} giây."
        else:
            self.qr_label.clear()
            self.qr_label.setText("QR đã hết hạn" if status.code == "qr_expired" else "QR chưa khả dụng")
            self._displayed_qr = None
        self.status_label.setText(message)
        enabled = status.retry_after == 0
        self.qr_button.setEnabled(enabled)
        self.phone_button.setEnabled(enabled)
        self.code_button.setEnabled(enabled and status.state == "code")
        self.password_button.setEnabled(enabled and status.state == "password")
        self.otp.setEnabled(status.state == "code")
        self.password.setEnabled(status.state == "password")
        if status != self._last_status:
            previous = self._last_status
            self._last_status = status
            if self._changed:
                self._changed(status)
            if status.state == "code" and (previous is None or previous.state != "code"):
                self.otp.setFocus()
            elif status.state == "password" and (previous is None or previous.state != "password"):
                self.password.setFocus()

    def done(self, result):
        if not self._closed:
            self._closed = True
            self._timer.stop()
            for widget in (self.api_id, self.api_hash, self.phone, self.otp, self.password):
                widget.clear()
            self.qr_label.clear()
            self._displayed_qr = None
            if self._owns_runner:
                self._worker.close(self._service)
            elif self._service.status().state != "active":
                self._service.request_cancel()
                self._worker.submit(self._service.cancel())
        super().done(result)
        if self._restore_focus is not None:
            self._restore_focus.setFocus()
