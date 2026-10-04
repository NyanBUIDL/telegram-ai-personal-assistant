"""Native backup actions use actual portable data and OS/writer locks."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tg_assistant.config import Settings
from tg_assistant.contracts import PublicProfile
from tg_assistant.db.models import Task
from tg_assistant.desktop.instance import InstanceGuard
from tg_assistant.desktop.runtime_controller import RuntimeSnapshot
from tg_assistant.services.storage import StorageService


class StoppedRuntime:
    def __init__(self, settings):
        self.settings = settings
        self.stop_calls = 0

    def stop(self):
        self.stop_calls += 1

    def refresh(self):
        future = Future()
        future.set_result(RuntimeSnapshot("stopped", "runtime_stopped"))
        return future


@pytest.fixture
def native_data(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "profile", profile_id="native_backup")

    class NoSecrets:
        def get(self, name):
            pytest.fail("SQLite native backup accessed credentials")

    storage = StorageService(settings, NoSecrets())
    database = storage.open(
        PublicProfile(
            profile_id=settings.profile_id,
            owner_id=None,
            storage_backend="sqlite",
            setup_stage="storage_ready",
            version=1,
        )
    )
    asyncio.run(database.close())
    storage.migrate()
    engine = create_engine(settings.database_url("", async_driver=False))
    with Session(engine) as session, session.begin():
        session.add(Task(id="native-task", title="Original"))
    yield settings, storage, engine, NoSecrets
    storage.fence.close()
    engine.dispose()


def controller_for(native_data, tmp_path):
    from tg_assistant.desktop.backup_ui import NativeBackupController

    settings, _, _, secrets = native_data
    runtime = StoppedRuntime(settings)
    controller = NativeBackupController(
        runtime,
        secret_store_factory=secrets,
        instance_directory=tmp_path / "control",
    )
    return controller, runtime


def test_native_backup_creates_real_portable_archive(native_data, tmp_path):
    controller, runtime = controller_for(native_data, tmp_path)
    try:
        output = tmp_path / "portable.zip"
        result = controller.backup(output).result(timeout=30)
        assert result.success, result.code
        assert runtime.stop_calls == 0
        with ZipFile(output) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["backend"] == "sqlite"
            assert manifest["profile_id"] == "native_backup"
            assert set(archive.namelist()) == {"manifest.json", "database.sqlite3"}
    finally:
        controller.close()


def test_native_restore_prebackup_and_truthful_degraded_result(native_data, tmp_path):
    settings, storage, engine, _ = native_data
    archive = tmp_path / "portable.zip"
    storage.backup(archive)
    with Session(engine) as session, session.begin():
        session.get(Task, "native-task").title = "Changed"
    engine.dispose()
    controller, runtime = controller_for(native_data, tmp_path)
    try:
        result = controller.restore_confirmed(archive).result(timeout=30)
        assert result.success and result.needs_recovery
        assert runtime.stop_calls == 1
        assert "chỉ mục" in result.message.casefold()
        assert list((settings.data_dir / "backups").glob("before-restore-*.zip"))
        with Session(engine) as session:
            assert session.get(Task, "native-task").title == "Original"
        with storage.fence.operation():
            pass
    finally:
        controller.close()


def test_native_restore_rejects_busy_writer(native_data, tmp_path):
    _, storage, engine, _ = native_data
    archive = tmp_path / "portable.zip"
    storage.backup(archive)
    controller, _ = controller_for(native_data, tmp_path)
    try:
        with storage.fence.operation():
            result = controller.restore_confirmed(archive).result(timeout=30)
        assert not result.success and result.code == "maintenance_in_progress"
        with Session(engine) as session:
            assert session.get(Task, "native-task").title == "Original"
    finally:
        controller.close()


def test_native_restore_rejects_live_instance_even_if_snapshot_stopped(native_data, tmp_path):
    _, storage, engine, _ = native_data
    archive = tmp_path / "portable.zip"
    storage.backup(archive)
    controller, _ = controller_for(native_data, tmp_path)
    try:
        with InstanceGuard(tmp_path / "control"):
            result = controller.restore_confirmed(archive).result(timeout=30)
        assert not result.success and result.code == "runtime_already_running"
        with Session(engine) as session:
            assert session.get(Task, "native-task").title == "Original"
    finally:
        controller.close()


def test_invalid_restore_releases_only_proven_preserved_original(native_data, tmp_path):
    _, storage, _, _ = native_data
    invalid = tmp_path / "invalid.zip"
    invalid.write_bytes(b"not a portable archive")
    controller, _ = controller_for(native_data, tmp_path)
    try:
        result = controller.restore_confirmed(invalid).result(timeout=30)
        assert not result.success
        assert str(invalid) not in result.message
        with storage.fence.operation():
            pass
    finally:
        controller.close()


def test_native_restore_gracefully_stops_actual_worker(native_data, tmp_path):
    from tg_assistant.desktop.backup_ui import NativeBackupController
    from tg_assistant.desktop.runtime_controller import RuntimeController

    settings, storage, engine, secrets = native_data
    archive = tmp_path / "portable.zip"
    storage.backup(archive)
    engine.dispose()
    directory = tmp_path / "control"
    code = (
        "import sys; from pathlib import Path; "
        "from tg_assistant.config import Settings; "
        "from tg_assistant.desktop.worker import serve_worker; "
        "serve_worker(Settings(_env_file=None,profile_id='native_backup',"
        "data_dir=Path(sys.argv[1])),instance_directory=Path(sys.argv[2]))"
    )
    runtime = RuntimeController(
        settings,
        worker_command=[
            sys.executable,
            "-c",
            code,
            str(settings.data_dir),
            str(directory),
        ],
    )
    controller = None
    try:
        runtime.start().result(timeout=10)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if runtime.refresh().result(timeout=3).phase == "ready":
                break
            time.sleep(0.05)
        else:
            pytest.fail("Disposable native worker did not become ready")
        controller = NativeBackupController(
            runtime,
            secret_store_factory=secrets,
            instance_directory=directory,
        )
        result = controller.restore_confirmed(archive).result(timeout=40)
        assert result.success and result.needs_recovery
        assert runtime.process.wait(timeout=5) == 0
        assert runtime.refresh().result(timeout=3).phase == "stopped"
    finally:
        if controller is not None:
            controller.close()
        runtime.stop()
        if runtime.process is not None:
            runtime.process.wait(timeout=15)
        runtime.close()


def test_native_restore_confirmation_escape_is_cancel(qt_application):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from tg_assistant.desktop.backup_ui import RestoreConfirmation

    dialog = RestoreConfirmation()
    dialog.show()
    QTest.keyClick(dialog, Qt.Key_Escape)
    assert dialog.result() == 0
    assert not dialog.isVisible()


def test_busy_backup_dialog_refuses_escape(qt_application, native_data):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from tg_assistant.desktop.backup_ui import BackupDialog

    class PendingController:
        closed = False

        def close(self):
            self.closed = True

    controller = PendingController()
    dialog = BackupDialog(controller, native_data[0].data_dir)
    dialog.pending = Future()
    dialog.show()
    QTest.keyClick(dialog, Qt.Key_Escape)
    assert dialog.isVisible() and not controller.closed
    dialog.pending.set_result(None)
    dialog.pending = None
    dialog.reject()
    assert not dialog.isVisible() and controller.closed


def test_busy_restore_refuses_launcher_and_tray_start_or_dashboard(qt_application, native_data):
    from PySide6.QtTest import QTest

    from tg_assistant.desktop.app import LauncherWindow

    class Runtime(StoppedRuntime):
        starts = 0
        dashboards = 0

        def start(self):
            self.starts += 1

        def open_dashboard(self):
            self.dashboards += 1
            raise AssertionError("A busy restore opened dashboard")

    runtime = Runtime(native_data[0])
    window = LauncherWindow(runtime)
    window.show()
    QTest.qWait(60)
    before = runtime.starts
    window.backup_dialog = SimpleNamespace(pending=Future())
    window.start()
    window.open_dashboard()
    assert runtime.starts == before and runtime.dashboards == 0
    window.backup_dialog = None
    window.close()


def test_native_backup_art_targets_and_focus(qt_application, native_data, tmp_path):
    from PySide6.QtGui import QPalette
    from PySide6.QtTest import QTest

    from tg_assistant.desktop.backup_ui import BackupDialog
    from tg_assistant.desktop.theme import ArtPanel

    controller, _ = controller_for(native_data, tmp_path)
    dialog = BackupDialog(controller, native_data[0].data_dir)
    try:
        dialog.show()
        dialog.activateWindow()
        dialog.backup_button.setFocus()
        QTest.qWait(60)
        assert dialog.palette().color(QPalette.Window).name() == "#f3efdf"
        panel = dialog.findChild(ArtPanel)
        image = panel.grab().toImage()
        assert image.pixelColor(0, 0).name() == "#090909"
        inside = round(5 * image.devicePixelRatio())
        assert image.pixelColor(inside, inside).name() == "#fffdf5"
        for button in (dialog.backup_button, dialog.restore_button, dialog.close_button):
            assert button.height() >= 44
            assert "darley" in button.font().family().lower()
        assert dialog.backup_button.hasFocus()
        evidence = Path(os.environ.get("ART_EVIDENCE_DIR", str(tmp_path)))
        evidence.mkdir(parents=True, exist_ok=True)
        assert dialog.grab().save(str(evidence / "native-backup-dialog.png"))
    finally:
        dialog.reject()
