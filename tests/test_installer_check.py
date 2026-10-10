import json
import os
from pathlib import Path

import pytest

from tg_assistant.desktop import installer_check


def test_legacy_mysql_refused_without_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(installer_check, "native_control_directory", lambda: tmp_path / ".desktop-control")
    config = tmp_path / "config/settings.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"version": 1, "settings": {"storage_backend": "mysql"}}))
    before = config.read_bytes()
    assert installer_check.main(prepare=True) == installer_check.UNSUPPORTED
    assert config.read_bytes() == before
    assert not (tmp_path / ".desktop-control").exists()


def test_valid_prepare_creates_only_control_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(installer_check, "native_control_directory", lambda: tmp_path / ".desktop-control")
    assert installer_check.main(prepare=True) == 0
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == [".desktop-control", ".desktop-control/runtime.lock"]
    from tg_assistant.services.maintenance import FileLock
    first = FileLock(tmp_path / ".desktop-control/runtime.lock", exclusive=True)
    try:
        assert installer_check.main(prepare=True) == installer_check.BUSY
    finally:
        first.close()


@pytest.mark.parametrize("value", ["{", '{"version":1,"settings":{"data_dir":"C:/elsewhere"}}', '{"version":1,"settings":{"storage_backend":"mysql"}}', '{"version":1,"settings":{"qdrant_path":"C:/elsewhere"}}'])
def test_unsafe_config_is_preserved(tmp_path, monkeypatch, value):
    monkeypatch.setattr(installer_check, "native_control_directory", lambda: tmp_path / ".desktop-control")
    config = tmp_path / "config/settings.json"
    config.parent.mkdir()
    config.write_text(value)
    assert installer_check.main(prepare=True) != 0
    assert config.read_text() == value


def test_launcher_dispatch_precedes_desktop_import():
    source = (Path(__file__).parents[1] / "packaging/launcher.py").read_text()
    assert source.index('"--installer-check"') < source.index("from tg_assistant.desktop.app import main")


@pytest.mark.parametrize("args", [["--installer-check", "extra"], ["--installer-unknown"]])
def test_invalid_installer_dispatch_cannot_enter_app(monkeypatch, args):
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["assistant", *args])
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(Path(__file__).parents[1] / "packaging/launcher.py"))
    assert result.value.code == 2


def test_environment_custom_profile_refused_without_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(installer_check, "native_control_directory", lambda: tmp_path / "profile/.desktop-control")
    monkeypatch.setenv("TG_ASSISTANT_DATA_DIR", str(tmp_path / "elsewhere"))
    assert installer_check.main(prepare=True) == installer_check.UNSUPPORTED
    assert not (tmp_path / "profile").exists()


def test_hardlinked_control_refused_without_touching_target(tmp_path, monkeypatch):
    root = tmp_path / "profile"
    control = root / ".desktop-control"
    control.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_bytes(b"must survive")
    os.link(outside, control / "runtime.lock")
    monkeypatch.setattr(installer_check, "native_control_directory", lambda: control)
    assert installer_check.main(prepare=True) == installer_check.UNSAFE
    assert outside.read_bytes() == b"must survive"


def test_large_config_and_inspection_error_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(installer_check, "native_control_directory", lambda: tmp_path / ".desktop-control")
    config = tmp_path / "config/settings.json"
    config.parent.mkdir()
    config.write_bytes(b" " * (1024 * 1024 + 1))
    assert installer_check.main() == installer_check.UNSUPPORTED
    monkeypatch.setattr(installer_check, "_assert_owned_path", lambda _: (_ for _ in ()).throw(PermissionError()))
    assert installer_check.main() == installer_check.UNSAFE


def test_readonly_check_does_not_take_guard_or_initialize_profile(tmp_path, monkeypatch):
    root = tmp_path / "profile"
    monkeypatch.setattr(installer_check, "native_control_directory", lambda: root / ".desktop-control")
    monkeypatch.setattr(installer_check, "InstanceGuard", lambda _: pytest.fail("read-only check acquired guard"))
    assert installer_check.main() == 0
    assert not root.exists()


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows file-lock interop")
def test_pascal_lock_api_fences_real_native_guard_both_directions(tmp_path):
    import ctypes
    from ctypes import wintypes

    from tg_assistant.desktop.instance import AlreadyRunning, InstanceGuard

    control = tmp_path / ".desktop-control"
    with InstanceGuard(control):
        pass
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.LockFile.argtypes = [wintypes.HANDLE] + [wintypes.DWORD] * 4
    kernel.LockFile.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(control / "runtime.lock"), 0xC0000000, 3, None, 3, 0x00200000, None)
    assert handle != wintypes.HANDLE(-1).value
    try:
        assert kernel.LockFile(handle, 0, 0, 1, 0)
        with pytest.raises(AlreadyRunning):
            InstanceGuard(control).acquire()
    finally:
        kernel.CloseHandle(handle)
    with InstanceGuard(control):
        handle = kernel.CreateFileW(str(control / "runtime.lock"), 0xC0000000, 3, None, 3, 0x00200000, None)
        assert handle != wintypes.HANDLE(-1).value
        try:
            assert not kernel.LockFile(handle, 0, 0, 1, 0)
            assert ctypes.get_last_error() == 33
        finally:
            kernel.CloseHandle(handle)


def test_fresh_install_control_only_root_can_initialize_profile(tmp_path, monkeypatch):
    from tg_assistant import paths
    from tg_assistant.desktop import instance

    root = tmp_path / "TelegramAIPersonalAssistant"
    control = root / ".desktop-control"
    monkeypatch.setattr(installer_check, "native_control_directory", lambda: control)
    monkeypatch.setattr(instance, "native_control_directory", lambda: control)
    monkeypatch.setattr(paths, "default_user_data_root", lambda: root)
    assert installer_check.main(prepare=True) == 0
    assert not (root / ".tg-assistant-data").exists()
    paths.ensure_runtime_dirs(root)
    assert (root / ".tg-assistant-data").exists()


@pytest.mark.parametrize("shape", ["extra-root", "extra-control", "nonempty-lock", "missing-lock", "custom-root", "foreign-owner", "hardlink", "lock-directory", "linked-control"])
def test_control_root_adoption_refuses_other_shapes(tmp_path, monkeypatch, shape):
    from tg_assistant import paths
    from tg_assistant.desktop import instance

    root = tmp_path / "native"
    control = root / ".desktop-control"
    control.mkdir(parents=True)
    lock = control / "runtime.lock"
    lock.touch()
    monkeypatch.setattr(instance, "native_control_directory", lambda: control)
    if shape == "extra-root":
        (root / "config").mkdir()
    elif shape == "extra-control":
        (control / "pid").touch()
    elif shape == "nonempty-lock":
        lock.write_bytes(b"1")
    elif shape == "missing-lock":
        lock.unlink()
    elif shape == "custom-root":
        monkeypatch.setattr(instance, "native_control_directory", lambda: tmp_path / "actual-native/.desktop-control")
        monkeypatch.setattr(paths, "default_user_data_root", lambda: root)
    elif shape == "foreign-owner":
        monkeypatch.setattr(instance, "_assert_owned_path", lambda _: (_ for _ in ()).throw(PermissionError()))
    elif shape == "hardlink":
        os.link(lock, tmp_path / "outside")
    elif shape == "lock-directory":
        lock.unlink()
        lock.mkdir()
    elif shape == "linked-control":
        lock.unlink()
        control.rmdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "runtime.lock").touch()
        if os.name == "nt":
            import subprocess

            command = "New-Item -ItemType Junction -Path '{}' -Target '{}' | Out-Null".format(
                str(control).replace("'", "''"), str(outside).replace("'", "''")
            )
            powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", command], check=True, capture_output=True)
        else:
            control.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="nonempty app data"):
        paths.ensure_runtime_dirs(root)
    assert not (root / ".tg-assistant-data").exists()
