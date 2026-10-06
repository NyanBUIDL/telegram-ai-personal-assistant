"""Native setup renders actual persisted service state without inventing identity."""

from __future__ import annotations

import hashlib
import importlib
import os
import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QScrollArea
from sqlalchemy import create_engine

from tg_assistant.db.base import Base
from tg_assistant.services.connections import ConnectionHealth, storage_probe
from tg_assistant.services.maintenance import MaintenanceService
from tg_assistant.services.onboarding import OnboardingCoordinator, StageVerification


def ui_module():
    assert importlib.util.find_spec("tg_assistant.desktop.setup_ui"), "Native setup view is missing"
    return importlib.import_module("tg_assistant.desktop.setup_ui")


def wait_until(predicate, timeout=3000):
    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    poll.start(10)
    limit = QTimer()
    limit.setSingleShot(True)
    limit.timeout.connect(loop.quit)
    limit.start(timeout)
    if not predicate():
        loop.exec()
    poll.stop()
    limit.stop()
    assert predicate(), "Native setup did not finish its bounded operation"


@pytest.fixture
def coordinator(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "state.sqlite3"))
    Base.metadata.create_all(engine)
    fence = MaintenanceService(tmp_path / "config", profile_id="setup_ui")
    health = ConnectionHealth(probes={"storage": storage_probe(engine)})
    service = OnboardingCoordinator(
        engine=engine,
        profile_id="setup_ui",
        storage_backend="sqlite",
        fence=fence,
        connections=health,
        verifiers={},
    )
    yield service
    fence.close()
    engine.dispose()


def test_setup_displays_real_health_and_missing_identity(qt_application, coordinator):
    module = ui_module()
    control = module.NativeSetupController(lambda: coordinator)
    dialog = module.SetupDialog(control)
    try:
        dialog.show()
        wait_until(lambda: dialog.pending is None)
        assert "Chưa kiểm tra" in dialog.connections["telegram_account"].text()
        assert "Đã kiểm tra" in dialog.connections["storage"].text()
        assert "Telegram" in dialog.next_action.text()
        assert dialog.owner.text() == "Chưa xác minh tài khoản Telegram"
        assert not dialog.findChildren(QLineEdit), (
            "Credentials must live in their owning native dialog"
        )
        assert len(dialog.connection_buttons) == 3
        assert all(not button.isEnabled() for button in dialog.connection_buttons.values())
        assert coordinator.status().profile.owner_id is None
    finally:
        dialog.close()
        control.close().result(timeout=5)


def test_setup_resume_keeps_persisted_options_and_actual_completed_stage(
    qt_application, coordinator
):
    module = ui_module()
    coordinator.save_options({"ai_mode": "skip"})
    coordinator._verifiers["welcome"] = lambda: StageVerification(
        fingerprint=hashlib.sha256(b"owner acknowledged welcome").hexdigest()
    )
    identifier = coordinator.verify_stage("welcome")
    coordinator.complete_stage("welcome", identifier)
    control = module.NativeSetupController(lambda: coordinator)
    dialog = module.SetupDialog(control)
    try:
        dialog.show()
        wait_until(lambda: dialog.pending is None)
        assert "Dữ liệu" in dialog.next_action.text()
        assert "Chào mừng: Đã hoàn tất" in dialog.stages.text()
        assert "AI" in dialog.limitations.text()
        QTest.mouseClick(dialog.refresh_button, Qt.LeftButton)
        wait_until(lambda: dialog.pending is None)
        assert coordinator.options()["ai_mode"] == "skip"
        assert dialog.owner.text() == "Chưa xác minh tài khoản Telegram"
    finally:
        dialog.close()
        control.close().result(timeout=5)


def test_setup_probe_runs_outside_gui_thread_and_escape_remains_responsive(
    qt_application, coordinator
):
    module = ui_module()
    entered, release = threading.Event(), threading.Event()
    original = coordinator.connections._probes["storage"]

    def slow_storage():
        entered.set()
        assert release.wait(3), "Test did not release its owned health probe"
        return original()

    coordinator.connections._probes["storage"] = slow_storage
    control = module.NativeSetupController(lambda: coordinator)
    dialog = module.SetupDialog(control)
    try:
        dialog.show()
        wait_until(entered.is_set)
        assert dialog.pending is not None
        QTest.keyClick(dialog, Qt.Key_Escape)
        assert not dialog.isVisible()
    finally:
        release.set()
        control.close().result(timeout=5)


def test_setup_hides_private_probe_exception_and_allows_retry(qt_application):
    module = ui_module()

    def unavailable():
        raise RuntimeError("SYNTHETIC_PRIVATE_FAILURE_DO_NOT_DISPLAY")

    control = module.NativeSetupController(unavailable)
    dialog = module.SetupDialog(control)
    try:
        dialog.show()
        wait_until(lambda: dialog.pending is None)
        assert "SYNTHETIC_PRIVATE" not in dialog.next_action.text()
        assert "kiểm tra" in dialog.next_action.text().lower()
        assert dialog.refresh_button.isEnabled()
        assert all("Đã kiểm tra" not in label.text() for label in dialog.connections.values())
    finally:
        dialog.close()
        control.close().result(timeout=5)


def test_setup_explicit_ai_skip_does_not_request_first_answer(qt_application, coordinator):
    module = ui_module()
    coordinator.save_options({"ai_mode": "skip"})
    control = module.NativeSetupController(lambda: coordinator)
    dialog = module.SetupDialog(control)
    try:
        dialog.show()
        wait_until(lambda: dialog.pending is None)
        assert "Câu hỏi đầu tiên: Chưa bật AI" in dialog.stages.text()
        assert "chưa bật ai" in dialog.limitations.text().lower()
        assert not coordinator.status().stage_evidence_ids
    finally:
        dialog.close()
        control.close().result(timeout=5)


def test_setup_art_targets_keyboard_and_escape(qt_application, coordinator, tmp_path):
    module = ui_module()
    control = module.NativeSetupController(lambda: coordinator)
    dialog = module.SetupDialog(control)
    try:
        dialog.open()
        dialog.activateWindow()
        wait_until(lambda: dialog.pending is None)
        dialog.refresh_button.setFocus()
        qt_application.processEvents()
        assert qt_application.font().family().startswith("Darley")
        title = next(
            label for label in dialog.findChildren(QLabel) if label.property("artRole") == "title"
        )
        assert title.font().family() == "LNTH-Peter Obscure"
        assert dialog.palette().window().color().name() == "#f3efdf"
        assert dialog.findChild(QScrollArea).horizontalScrollBar().maximum() == 0
        for button in dialog.findChildren(QPushButton):
            assert button.height() >= 44 and button.width() >= 44
        for _ in range(8):
            QTest.keyClick(qt_application.focusWidget(), Qt.Key_Tab)
            assert dialog.isAncestorOf(qt_application.focusWidget())
        output = Path(os.environ.get("ART_EVIDENCE_DIR", str(tmp_path)))
        output.mkdir(parents=True, exist_ok=True)
        assert dialog.grab().save(
            str(output / f"setup-scale-{os.environ.get('QT_SCALE_FACTOR', '1')}.png")
        )
        QTest.keyClick(qt_application.focusWidget(), Qt.Key_Escape)
        assert not dialog.isVisible()
    finally:
        dialog.close()
        control.close().result(timeout=5)
