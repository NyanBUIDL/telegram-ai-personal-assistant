"""Real Qt Widgets geometry, focus and pixel evidence; no operational data."""
import os
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app(qt_application):
    return qt_application


def unthemed():
    return os.environ.get("ART_PHASE") == "before" or os.environ.get("ART_NATIVE_UNTHEMED") == "1"


def test_native_dialog_readable_targets_focus_escape_and_art(app):
    window = QWidget()
    layout = QVBoxLayout(window)
    opener = QPushButton("Mở hộp thoại")
    layout.addWidget(opener)
    window.show()
    window.activateWindow()
    opener.setFocus()
    dialog = QDialog(window)
    dialog.setWindowTitle("Telegram AI · kiểm tra art")
    dialog.setModal(True)
    dialog.resize(560, 370)
    outer = QVBoxLayout(dialog)
    outer.setContentsMargins(18, 18, 25, 25)
    if unthemed():
        panel = QFrame()
    else:
        from tg_assistant.desktop.theme import ArtPanel
        panel = ArtPanel()
    outer.addWidget(panel)
    content = QVBoxLayout(panel)
    content.setContentsMargins(18, 18, 18, 18)
    title = QLabel("Telegram AI")
    title.setProperty("artRole", "title")
    content.addWidget(title)
    copy = QLabel("Đây là câu tiếng Việt dài để kiểm tra bố cục: hãy xem nội dung, dùng bàn phím để di chuyển giữa các điều khiển và đóng hộp thoại trước khi tiếp tục.")
    copy.setWordWrap(True)
    content.addWidget(copy)
    field = QLineEdit()
    field.setAccessibleName("Ghi chú không bí mật")
    content.addWidget(field)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.button(QDialogButtonBox.Ok).setText("Tiếp tục")
    buttons.button(QDialogButtonBox.Cancel).setText("Quay lại")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    content.addWidget(buttons)
    dialog.open()
    dialog.activateWindow()
    field.setFocus()
    QTest.qWait(100)
    evidence = (
        Path(os.environ["ART_EVIDENCE_DIR"])
        if os.environ.get("ART_EVIDENCE_DIR")
        else ROOT / "docs/handoff/evidence/u01" / os.environ.get("ART_PHASE", "after")
    )
    evidence.mkdir(parents=True, exist_ok=True)
    scale = os.environ.get("QT_SCALE_FACTOR", "1")
    image = dialog.grab()
    assert image.save(str(evidence / f"native-dialog-scale-{scale}.png"))
    assert dialog.palette().color(QPalette.Window).name() == "#f3efdf"
    # Actual pixels detect palette/border mistakes instead of mirroring QSS text.
    pixels = panel.grab().toImage()
    assert pixels.pixelColor(0, 0).name() == "#090909"
    assert pixels.pixelColor(2, 2).name() == "#090909"
    # Pixel coordinates scale with the grabbed pixmap's DPR, widget sizes do not.
    inside = round(5 * pixels.devicePixelRatio())
    assert pixels.pixelColor(inside, inside).name() == "#fffdf5"
    for button in buttons.buttons():
        assert button.height() >= 44
        assert button.width() >= 44
    assert field.height() >= 44
    assert copy.geometry().bottom() < field.geometry().top()
    assert app.font().family().startswith("Darley")
    assert title.font().family() == "LNTH-Peter Obscure"
    assert copy.font().family().startswith("Darley")
    assert field.accessibleName() == "Ghi chú không bí mật"
    shadow = panel.graphicsEffect()
    assert shadow.blurRadius() == 0
    assert shadow.offset().x() == shadow.offset().y() == 7
    assert shadow.color().name() == "#090909"
    for _ in range(8):
        QTest.keyClick(app.focusWidget(), Qt.Key_Tab)
        assert dialog.isAncestorOf(app.focusWidget())
    QTest.keyClick(app.focusWidget(), Qt.Key_Escape)
    QTest.qWait(50)
    assert not dialog.isVisible()
    window.activateWindow()
    QTest.qWait(50)
    assert app.focusWidget() is opener
    window.close()
