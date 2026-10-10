"""Native-only bot enrollment UI; the parent context owns drain and resources."""

from __future__ import annotations

import base64
import math
import re
from datetime import UTC, datetime
from time import monotonic

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
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

from ..theme import ArtPanel
from .telegram import _qr_pixmap

_MESSAGES = {
    "bot_check_required": "Cần kiểm tra lại bot và tài khoản Telegram.",
    "bot_verified": "Bot đã được xác minh. Tạo liên kết để ghép với tài khoản của bạn.",
    "bot_account_required": "Cần xác minh tài khoản Telegram trong ứng dụng trước khi ghép bot.",
    "bot_token_revoked": "Token bot đã bị thu hồi. Nhập token mới từ BotFather.",
    "bot_polling_conflict": "Bot đang được dùng ở nơi khác. Đóng phiên bot khác hoặc kiểm tra webhook.",
    "bot_timeout": "Kiểm tra bot hết thời gian chờ. Kiểm tra mạng rồi thử lại.",
    "bot_pairing_expired": "Liên kết ghép đã hết hạn. Tạo liên kết mới để tiếp tục.",
    "bot_unavailable": "Không thể xác minh bot. Kiểm tra thông tin rồi thử lại.",
}


class NativeBotDialog(QDialog):
    """Borrow one real native context; never create or close its runner/account."""

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self._context, self._service, self._runner = context, context.service, context.runner
        self._future, self._action, self._error = None, None, None
        self._closed = False
        self._invitation = self._url = self._invitation_deadline = None
        self._cancel_future = None
        self._next_poll, self._next_refresh = monotonic() + 1, monotonic() + 20
        self._next_render = monotonic() + 1
        self._restore_focus = parent.focusWidget() if parent is not None else None
        self.setModal(True)
        self.setWindowTitle("Kết nối và ghép bot • Telegram AI")
        self.resize(650, 720)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 27, 27)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        panel = ArtPanel()
        body = QVBoxLayout(panel)
        body.setContentsMargins(20, 20, 20, 20)
        title = self._label("Kết nối bot Telegram")
        font = title.font()
        font.setPointSize(18)
        font.setBold(True)
        title.setFont(font)
        body.addWidget(title)
        body.addWidget(
            self._label(
                "Tạo bot trong BotFather rồi nhập token ở cửa sổ này. "
                "Token không được điền sẵn. Bot cần được ghép với tài khoản Telegram "
                "đã xác minh của bạn trước khi dùng các chức năng quản lý."
            )
        )
        form = QFormLayout()
        self.token = QLineEdit()
        self.token.setObjectName("bot_token")
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setMaxLength(512)
        self.token.setMinimumHeight(44)
        self.token.setAccessibleName("Token bot chỉ nhập trong ứng dụng Windows")
        form.addRow("Token từ BotFather", self.token)
        self.manual_code = QLineEdit()
        self.manual_code.setObjectName("native_pairing_code")
        self.manual_code.setReadOnly(True)
        self.manual_code.setMinimumHeight(44)
        self.manual_code.setAccessibleName("Mã ghép thủ công dùng với lệnh /pair trong bot")
        form.addRow("Mã thủ công (/pair)", self.manual_code)
        body.addLayout(form)
        self.identity_label = self._label("Chưa xác minh bot.")
        body.addWidget(self.identity_label)
        self.qr_label = self._label("Chưa có liên kết ghép.")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setAccessibleName("Mã QR liên kết ghép bot chỉ hiển thị trong cửa sổ này")
        self.qr_label.setMinimumHeight(200)
        body.addWidget(self.qr_label)
        self.expiry_label = self._label("")
        body.addWidget(self.expiry_label)
        self.status_label = self._label("")
        self.status_label.setAccessibleName("Trạng thái kết nối và ghép bot Telegram")
        body.addWidget(self.status_label)
        grid = QGridLayout()
        self.connect_button = QPushButton("Xác minh token")
        self.connect_button.setProperty("artTone", "primary")
        self.pair_button = QPushButton("Tạo / làm mới liên kết ghép")
        self.open_button = QPushButton("Mở bot để ghép")
        self.cancel_button = QPushButton("Hủy liên kết ghép")
        self.help_button = QPushButton("Mở BotFather")
        self.close_button = QPushButton("Đóng (Esc)")
        self.close_button.setProperty("artTone", "danger")
        for index, button in enumerate(
            (
                self.connect_button,
                self.help_button,
                self.pair_button,
                self.open_button,
                self.cancel_button,
                self.close_button,
            )
        ):
            button.setMinimumHeight(44)
            button.setAutoDefault(False)
            grid.addWidget(button, index // 2, index % 2)
        body.addLayout(grid)
        scroll.setWidget(panel)
        outer.addWidget(scroll)
        self.connect_button.clicked.connect(self._connect)
        self.pair_button.clicked.connect(lambda: self._start("pair", self._service.issue_pairing))
        self.cancel_button.clicked.connect(self._cancel_pairing)
        self.open_button.clicked.connect(self._open_bot)
        self.help_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://t.me/BotFather"))
        )
        self.close_button.clicked.connect(self.reject)
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._start("refresh", self._context.refresh_async)
        self.token.setFocus()

    @staticmethod
    def _label(text):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        return label

    def _clear_invitation(self):
        self._invitation = self._url = self._invitation_deadline = None
        self.manual_code.clear()
        self.qr_label.clear()
        self.expiry_label.clear()
        self.open_button.setEnabled(False)

    def _start(self, action, operation):
        if self._closed or self._future is not None:
            return
        if action in {"pair", "connect", "cancel"}:
            self._clear_invitation()
        self._error = None
        self._action = action
        coroutine = None
        try:
            coroutine = operation()
            self._future = self._runner.submit(coroutine)
        except Exception:
            if coroutine is not None:
                coroutine.close()
            self._future = None
            self._error = "bot_unavailable"
        self._render()

    def _connect(self):
        if self._closed or self._future is not None:
            return
        token = self.token.text()
        self.token.clear()
        self._start("connect", lambda: self._service.connect(token))

    def _cancel_pairing(self):
        self._start("cancel", self._service.cancel_pairing)

    def _accept_invitation(self, invitation):
        username = self._service.pairing_username()
        if self._service.verification() is None:
            raise ValueError("bot_pairing_unavailable")
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,32}", username):
            raise ValueError("bot_pairing_unavailable")
        payload, code, expiry = invitation.payload, invitation.manual_code, invitation.expires_at
        if not isinstance(payload, str) or not re.fullmatch(r"pair_[A-Za-z0-9_-]{43}", payload):
            raise ValueError("bot_pairing_unavailable")
        raw = base64.b64decode(payload[5:] + "=", altchars=b"-_", validate=True)
        if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode().rstrip("=") != payload[5:]:
            raise ValueError("bot_pairing_unavailable")
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z2-7]{4}(?:-[A-Z2-7]{4}){3}", code):
            raise ValueError("bot_pairing_unavailable")
        if not isinstance(expiry, datetime) or expiry.tzinfo is None or expiry.utcoffset() is None:
            raise ValueError("bot_pairing_unavailable")
        remaining = (expiry.astimezone(UTC) - datetime.now(UTC)).total_seconds()
        if not 0 < remaining <= 300:
            raise ValueError("bot_pairing_unavailable")
        deadline = monotonic() + remaining
        if invitation is self._invitation:
            deadline = min(deadline, self._invitation_deadline)
        self._invitation, self._invitation_deadline = invitation, deadline
        self._url = f"https://t.me/{username}?start={payload}"
        self.manual_code.setText(code)
        self.qr_label.setPixmap(_qr_pixmap(self._url))

    def _open_bot(self):
        if self._closed or self._future is not None or self._invitation is None:
            return
        try:
            # Revalidate the actual current username/proof/expiry at click time.
            if monotonic() >= self._invitation_deadline:
                raise ValueError("bot_pairing_unavailable")
            invitation, previous_url = self._invitation, self._url
            self._accept_invitation(invitation)
            if self._url != previous_url:
                raise ValueError("bot_pairing_unavailable")
            QDesktopServices.openUrl(QUrl(self._url))
        except Exception:
            self._clear_invitation()
            self._error = "bot_check_required"
            self._render()

    def _poll(self):
        if self._closed:
            return
        if self._invitation is not None and (
            monotonic() >= self._invitation_deadline
            or datetime.now(UTC) >= self._invitation.expires_at
        ):
            self._clear_invitation()
            self._error = "bot_pairing_expired"
            self._next_render = 0
        completed = False
        if self._future is not None and self._future.done():
            completed = True
            future, action = self._future, self._action
            self._future = self._action = None
            try:
                result = future.result()
                if action == "pair":
                    self._accept_invitation(result)
            except Exception as exc:
                self._clear_invitation()
                code = getattr(exc, "code", None)
                self._error = (
                    code if isinstance(code, str) and code in _MESSAGES else "bot_unavailable"
                )
        current = monotonic()
        if completed or current >= self._next_render:
            self._next_render = current + 1
            self._render()
        if self._future is None:
            if current >= self._next_refresh:
                self._next_refresh = current + 20
                self._start("refresh", self._context.refresh_async)
            elif current >= self._next_poll:
                self._next_poll = current + 1
                self._start("poll", self._service.poll_once)

    def _render(self):
        try:
            proof = self._service.verification()
            paired = self._service.pairing_verification() if proof is not None else None
            username = self._service.pairing_username() if proof is not None else None
            valid_username = isinstance(username, str) and re.fullmatch(
                r"[A-Za-z0-9_]{1,32}", username
            )
            self.identity_label.setText(
                f"Bot đã xác minh: @{username}" if valid_username else "Chưa xác minh bot."
            )
            if paired is not None or proof is None:
                self._clear_invitation()
            elif self._invitation is not None:
                remaining = min(
                    (
                        self._invitation.expires_at.astimezone(UTC) - datetime.now(UTC)
                    ).total_seconds(),
                    self._invitation_deadline - monotonic(),
                )
                if remaining <= 0:
                    self._clear_invitation()
                    self._error = "bot_pairing_expired"
                else:
                    self.expiry_label.setText(
                        f"Liên kết hết hạn sau {math.ceil(remaining)} giây. Mở bot rồi bấm Start; hoặc gửi /pair cùng mã thủ công."
                    )
            code = self._service.connection_status().code
            message = _MESSAGES.get(code, _MESSAGES["bot_unavailable"])
            if paired is not None:
                message = "Đã ghép bot với tài khoản Telegram đã xác minh."
            if self._error is not None:
                message = _MESSAGES[self._error]
            busy = self._future is not None
            self.status_label.setText("Đang kiểm tra… có thể đóng bằng Esc." if busy else message)
            self.connect_button.setEnabled(not busy)
            self.pair_button.setEnabled(not busy and proof is not None and paired is None)
            self.open_button.setEnabled(not busy and self._invitation is not None)
            self.cancel_button.setEnabled(not busy and self._invitation is not None)
        except Exception:
            self._clear_invitation()
            self.identity_label.setText("Chưa xác minh bot.")
            self.status_label.setText(_MESSAGES["bot_unavailable"])
            self.pair_button.setEnabled(False)
            self.connect_button.setEnabled(self._future is None)

    def done(self, result):
        if not self._closed:
            self._closed = True
            self._timer.stop()
            self.token.clear()
            self._clear_invitation()
            if self._future is not None:
                # Request cancellation only. The parent must await actual SDK
                # drain before it closes the shared account or releases guard.
                self._future.cancel()
            coroutine = self._service.cancel_pairing()
            try:
                self._cancel_future = self._runner.submit(coroutine)
            except Exception:
                coroutine.close()
                self._cancel_future = None
        super().done(result)
        if self._restore_focus is not None:
            self._restore_focus.window().activateWindow()
            self._restore_focus.setFocus()
