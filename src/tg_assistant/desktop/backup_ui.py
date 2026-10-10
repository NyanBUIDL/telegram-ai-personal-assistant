"""Native portable backup workflow; restore holds actual OS and writer fences."""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QFileDialog, QLabel, QPushButton, QVBoxLayout

from ..contracts import PublicProfile
from ..security import SecretStore
from ..services.backup import BackupError
from ..services.maintenance import MaintenanceBusy
from ..services.storage import StorageService
from .instance import AlreadyRunning, InstanceGuard
from .theme import ArtPanel


@dataclass(frozen=True)
class BackupOutcome:
    success: bool
    code: str
    message: str
    needs_recovery: bool = False


class NativeBackupController:
    def __init__(self, runtime, *, secret_store_factory=SecretStore, instance_directory=None):
        self.runtime = runtime
        self.settings = runtime.settings
        self.secret_store_factory = secret_store_factory
        self.instance_directory = instance_directory
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="native-backup")

    def backup(self, destination: Path):
        return self.executor.submit(self._run, Path(destination), False)

    def restore_confirmed(self, source: Path):
        return self.executor.submit(self._run, Path(source), True)

    def _open_storage(self):
        storage = StorageService(self.settings, self.secret_store_factory())
        database = storage.open(
            PublicProfile(
                profile_id=self.settings.profile_id,
                owner_id=None,
                storage_backend=self.settings.storage_backend,
                setup_stage="storage_ready",
                version=1,
            )
        )
        try:
            asyncio.run(database.close())
        except BaseException:
            storage.fence.close()
            raise
        return storage

    def _stop_runtime(self):
        self.runtime.stop()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = self.runtime.refresh().result(timeout=3)
            if state.phase not in {"ready", "starting", "stopping"}:
                return
            time.sleep(0.05)
        raise AlreadyRunning("runtime_already_running")

    def _run(self, path: Path, restore: bool):
        storage = None
        guard = None
        try:
            if restore:
                self._stop_runtime()
                # A stopped/readiness DTO is not proof: hold the real SID-wide
                # instance lock and then the cross-process profile writer fence.
                guard = InstanceGuard(self.instance_directory).acquire()
            storage = self._open_storage()
            if not restore:
                storage.backup(path)
                return BackupOutcome(
                    True,
                    "backup_complete",
                    "Đã tạo bản sao lưu dữ liệu. Khóa API và phiên Telegram không được xuất.",
                )
            lease = storage.fence.acquire("native-backup-restore", lease_seconds=1800, timeout=0)
            try:
                storage.restore(path, lease)
            except BackupError as error:
                if error.original_preserved:
                    storage.fence.release(lease)
                else:
                    return BackupOutcome(
                        False,
                        "restore_recovery_required",
                        "Chưa xác minh được dữ liệu sau khôi phục. Ứng dụng giữ chế độ bảo trì; giữ các bản sao lưu để khôi phục an toàn.",
                        True,
                    )
                return BackupOutcome(
                    False,
                    error.code,
                    "Không thể khôi phục bản sao lưu. Dữ liệu hiện tại được giữ nguyên.",
                )
            storage.fence.release(lease)
            return BackupOutcome(
                True,
                "restore_complete",
                "Đã khôi phục dữ liệu và giữ bản sao trước khi khôi phục. Chỉ mục AI cần được kiểm tra, khôi phục trước khi dùng lại; ứng dụng đang dừng.",
                True,
            )
        except AlreadyRunning:
            return BackupOutcome(
                False,
                "runtime_already_running",
                "Ứng dụng vẫn đang chạy ở một cửa sổ khác. Hãy dừng an toàn rồi thử lại.",
            )
        except MaintenanceBusy:
            return BackupOutcome(
                False,
                "maintenance_in_progress",
                "Còn tác vụ đang ghi hoặc dữ liệu đang bảo trì. Hãy kiểm tra trạng thái bảo trì trước khi thử lại.",
            )
        except Exception:
            return BackupOutcome(
                False,
                "restore_failed" if restore else "backup_failed",
                "Không thể hoàn tất. Kiểm tra cấu hình, dung lượng ổ đĩa và quyền truy cập. Nếu đã bắt đầu khôi phục, hãy kiểm tra bảo trì trước khi khởi động lại.",
            )
        finally:
            if storage is not None:
                storage.fence.close()
            if guard is not None:
                guard.close()

    def close(self):
        # A restore cannot be interrupted by closing the launcher/tray.
        self.executor.shutdown(wait=True, cancel_futures=False)


def _panel(dialog):
    outer = QVBoxLayout(dialog)
    outer.setContentsMargins(20, 20, 27, 27)
    panel = ArtPanel()
    outer.addWidget(panel)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(20, 20, 20, 20)
    return layout


def _copy(layout, text):
    label = QLabel(text)
    label.setWordWrap(True)
    layout.addWidget(label)
    return label


class RestoreConfirmation(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Xác nhận khôi phục dữ liệu")
        self.setModal(True)
        self.resize(540, 300)
        layout = _panel(self)
        _copy(
            layout,
            "Khôi phục sẽ thay thế dữ liệu của profile hiện tại. Ứng dụng sẽ dừng an toàn và tạo bản sao trước khi thay đổi. Khóa API, phiên Telegram và quyền đã thu hồi được giữ theo thiết bị hiện tại. Chỉ mục AI cần khôi phục riêng.",
        )
        self.cancel_button = QPushButton("Hủy, giữ dữ liệu hiện tại")
        self.cancel_button.setDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(self.cancel_button)
        self.confirm_button = QPushButton("Xác nhận khôi phục")
        self.confirm_button.setProperty("artTone", "danger")
        self.confirm_button.setAutoDefault(False)
        self.confirm_button.clicked.connect(self.accept)
        layout.addWidget(self.confirm_button)
        self.cancel_button.setFocus()


class BackupDialog(QDialog):
    def __init__(self, controller, data_dir, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.data_dir = Path(data_dir)
        self.pending = None
        self._closed_controller = False
        self.setWindowTitle("Dữ liệu và sao lưu")
        self.setModal(True)
        self.resize(540, 360)
        layout = _panel(self)
        self.status = _copy(
            layout,
            "Sao lưu dữ liệu để giữ một bản dự phòng. Bản sao lưu không chứa khóa API hoặc phiên đăng nhập Telegram; chỉ mục AI được tạo lại từ dữ liệu đã cho phép.",
        )
        self.status.setAccessibleName("Kết quả sao lưu và khôi phục")
        self.backup_button = QPushButton("Tạo bản sao lưu…")
        self.backup_button.setProperty("artTone", "primary")
        self.backup_button.clicked.connect(self.choose_backup)
        layout.addWidget(self.backup_button)
        self.restore_button = QPushButton("Khôi phục từ bản sao lưu…")
        self.restore_button.clicked.connect(self.choose_restore)
        layout.addWidget(self.restore_button)
        self.close_button = QPushButton("Đóng")
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(self.close_button)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def choose_backup(self):
        if self.pending is not None:
            return
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Lưu bản sao lưu",
            str(self.data_dir / "backups" / f"backup-{stamp}.zip"),
            "Bản sao lưu (*.zip)",
            options=QFileDialog.DontUseNativeDialog,
        )
        if destination:
            self._begin(self.controller.backup(Path(destination)), "Đang tạo bản sao lưu…")

    def choose_restore(self):
        if self.pending is not None:
            return
        source, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn bản sao lưu",
            str(self.data_dir / "backups"),
            "Bản sao lưu (*.zip)",
            options=QFileDialog.DontUseNativeDialog,
        )
        if source and RestoreConfirmation(self).exec() == QDialog.Accepted:
            self._begin(
                self.controller.restore_confirmed(Path(source)),
                "Đang dừng ứng dụng, kiểm tra và khôi phục…",
            )

    def _begin(self, future, message):
        self.pending = future
        self.status.setText(message)
        for button in (self.backup_button, self.restore_button, self.close_button):
            button.setEnabled(False)

    def poll(self):
        if self.pending is None or not self.pending.done():
            return
        try:
            outcome = self.pending.result()
            self.status.setText(outcome.message)
        except Exception:
            self.status.setText(
                "Không thể hoàn tất. Hãy kiểm tra trạng thái dữ liệu trước khi thử lại."
            )
        finally:
            self.pending = None
            for button in (self.backup_button, self.restore_button, self.close_button):
                button.setEnabled(True)
            self.close_button.setFocus()

    def _close_controller(self):
        if not self._closed_controller:
            self.controller.close()
            self.timer.stop()
            self._closed_controller = True

    def reject(self):
        if self.pending is not None:
            return
        self._close_controller()
        super().reject()

    def closeEvent(self, event):
        if self.pending is not None:
            event.ignore()
            return
        self._close_controller()
        event.accept()

    def finish(self):
        self._close_controller()
