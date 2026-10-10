"""Shared art adapted to Qt's device independent widget coordinates.

Call apply_theme once before creating windows. Font assets are supplied explicitly
by the launcher/package; this module never downloads or replaces a font.
"""
from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsDropShadowEffect, QWidget

TOKENS = json.loads(files(__package__).joinpath("design_tokens.json").read_text(encoding="utf-8"))


def apply_theme(app: QApplication, font_directory: Path) -> None:
    """Use bundled fonts, logical sizes and an obvious, non-color-only focus ring."""
    families = {}
    for role in ("ui", "display"):
        font_id = QFontDatabase.addApplicationFont(str(font_directory / TOKENS["fonts"][role]))
        names = QFontDatabase.applicationFontFamilies(font_id)
        if not names:
            raise ValueError(f"Bundled {role} font could not be loaded")
        families[role] = names[0]
    app.setFont(QFont(families["ui"], 12))
    colors, geometry = TOKENS["colors"], TOKENS["geometry"]
    palette = QPalette()
    for role, color in (
        (QPalette.Window, "paper"), (QPalette.WindowText, "ink"),
        (QPalette.Base, "panel"), (QPalette.Text, "ink"),
        (QPalette.Button, "panel"), (QPalette.ButtonText, "ink"),
        (QPalette.Highlight, "teal"), (QPalette.HighlightedText, "ink"),
    ):
        palette.setColor(role, QColor(colors[color]))
    app.setPalette(palette)
    border, target = geometry["border"], geometry["target"]
    app.setStyleSheet(f"""
        QWidget {{ color: {colors['ink']}; font-family: "{families['ui']}"; }}
        QDialog, QMainWindow {{ background: {colors['paper']}; }}
        QLabel[artRole="title"] {{ font-family: "{families['display']}"; font-size: 34px; }}
        QFrame[artPanel="true"] {{ background: {colors['panel']}; border: {border}px solid {colors['ink']}; border-radius: 0; }}
        QPushButton, QLineEdit, QComboBox, QSpinBox {{
            background: {colors['panel']}; border: {border}px solid {colors['ink']};
            border-radius: 0; min-height: {target - 2 * border}px;
            min-width: {target - 2 * border}px; padding: 0 12px;
        }}
        QPushButton[artTone="primary"] {{ background: {colors['teal']}; }}
        QPushButton[artTone="danger"] {{ background: {colors['magenta']}; }}
        QPushButton:hover {{ background: {colors['yellow']}; }}
        QPushButton:focus, QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
            border: {border}px dashed {colors['ink']}; background: {colors['yellow']};
        }}
        QPushButton:disabled {{ color: #59564f; border-style: dotted; }}
        QCheckBox, QRadioButton {{ min-height: {target}px; spacing: 10px; }}
        QCheckBox::indicator, QRadioButton::indicator {{ width: 22px; height: 22px; }}
        QCheckBox:focus, QRadioButton:focus {{ border: {border}px dashed {colors['ink']}; }}
    """)


class ArtPanel(QFrame):
    """A square panel with a hard shadow; layouts must reserve its shadow margin."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("artPanel", True)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(0)
        shadow.setColor(QColor(TOKENS["colors"]["ink"]))
        shadow.setOffset(QPointF(TOKENS["geometry"]["shadow"], TOKENS["geometry"]["shadow"]))
        self.setGraphicsEffect(shadow)
