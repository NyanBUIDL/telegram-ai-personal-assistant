"""Run the no-console binary from a foreign cwd without touching a profile."""

import json
import os
import subprocess
from pathlib import Path

import pytest


def test_diagnostics_dispatch_precedes_settings(monkeypatch, tmp_path):
    from tg_assistant.desktop import app

    def forbidden():
        raise AssertionError("Diagnostics attempted profile settings")

    monkeypatch.setattr(app, "get_settings", forbidden)
    output = tmp_path / "diagnostics.json"
    monkeypatch.setattr(app.sys, "argv", ["assistant", "--diagnostics", str(output)])
    assert hasattr(app, "run_diagnostics"), "Safe diagnostics dispatch is absent"
    assert app.main() == 0
    report = json.loads(output.read_text())
    assert report["status"] == "ok"
    assert report["checks"]["assets"] and report["checks"]["qt_platform"]
    assert "token" not in output.read_text().lower()


def test_actual_frozen_binary_foreign_cwd(tmp_path):
    configured = os.environ.get("FROZEN_EXE")
    if not configured:
        pytest.skip("FROZEN_EXE required for actual built artifact validation")
    executable = Path(configured).resolve()
    output = tmp_path / "diagnostics.json"
    profile = tmp_path / "must-not-create-profile"
    env = dict(os.environ, TG_ASSISTANT_DATA_DIR=str(profile), LOCALAPPDATA=str(profile))
    env.pop("PYTHONPATH", None)
    env.pop("QT_QPA_PLATFORM", None)
    env.pop("QT_PLUGIN_PATH", None)
    env.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
    env.pop("PYTHONHOME", None)
    env["PATH"] = os.pathsep.join([str(Path(os.environ["SystemRoot"]) / "System32"), os.environ["SystemRoot"]])
    result = subprocess.run([str(executable), "--diagnostics", str(output)], cwd=tmp_path, env=env, timeout=60)
    assert result.returncode == 0
    report = json.loads(output.read_text())
    assert report["status"] == "ok" and report["frozen"] is True
    assert all(report["checks"].values())
    assert not profile.exists()
    internal = executable.parent / "_internal"
    assert (internal / "python312.dll").is_file()
    assert (internal / "USER_GUIDE.md").is_file()
    assert list(internal.rglob("qwindows.dll"))
    assert list(internal.rglob("Qt6Widgets.dll"))
    assert (internal / "tg_assistant/db/legacy_0005.json").is_file()
    assert not any(path.name.lower() in {"node_modules", ".venv", ".env", "pytest", "setuptools", "qtwebenginecore.dll", "icudt78.dll"} for path in executable.parent.rglob("*"))


def test_diagnostics_preserves_existing_output(tmp_path):
    from tg_assistant.desktop.diagnostics import run_diagnostics

    output = tmp_path / "existing.json"
    output.write_text("preserve me")
    assert run_diagnostics(str(output)) == 2
    assert output.read_text() == "preserve me"


def test_diagnostics_reports_missing_resources_without_paths(monkeypatch, tmp_path):
    from tg_assistant.desktop import diagnostics

    monkeypatch.setattr(diagnostics, "resource_path", lambda *parts: tmp_path / "missing-private-name")
    output = tmp_path / "report.json"
    assert diagnostics.run_diagnostics(str(output)) == 1
    report = json.loads(output.read_text())
    assert report["status"] == "failed" and report["checks"]["assets"] is False
    assert "missing-private-name" not in output.read_text()
