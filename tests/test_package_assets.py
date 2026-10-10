"""Exercise built wheels, rather than assuming source resources ship."""

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def build_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("windows_build", ROOT / "packaging/build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wrapper_fixture(tmp_path):
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/build-windows.ps1", checkout / "scripts/build-windows.ps1")
    return checkout


def _failed_wrapper(checkout):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    assert shell, "PowerShell is a Windows packaging test prerequisite"
    return subprocess.run(
        [shell, "-NoProfile", "-File", str(checkout / "scripts/build-windows.ps1"),
         "-Python", str(checkout / "missing-python.exe"), "-NpmCli", "missing-npm-cli.js"],
        cwd=checkout, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows junction containment")
@pytest.mark.parametrize("relative", ["build", "build/windows", "build/windows/work/linked", "dashboard-prototype/dist", "dashboard-prototype/node_modules"])
def test_build_rejects_junction_ancestors_without_touching_target(tmp_path, build_module, relative):
    checkout = _wrapper_fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "untouched.txt"
    sentinel.write_text("preserve outside")
    junction = checkout / relative
    junction.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([shutil.which("cmd.exe"), "/c", "mklink", "/J", str(junction), str(outside)], check=True, capture_output=True)
    assert junction.is_junction()
    assert hasattr(build_module, "validate_output_tree"), "Physical containment guard is absent"
    target = checkout / "build/windows" if relative.startswith("build") else junction / "child"
    with pytest.raises(ValueError, match="[Rr]eparse|[Cc]ontainment"):
        build_module.validate_output_tree(checkout, target)
    if relative.startswith("build"):
        result = _failed_wrapper(checkout)
        assert result.returncode != 0 and "reparse" in (result.stdout + result.stderr).lower()
    assert sentinel.read_text() == "preserve outside"


def test_build_accepts_new_output_tree(tmp_path, build_module):
    build_module.validate_output_tree(tmp_path, tmp_path / "build/windows")
    assert not (tmp_path / "build").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows wrapper")
def test_wrapper_invalidates_manifest_before_missing_python(tmp_path):
    checkout = _wrapper_fixture(tmp_path)
    manifest = checkout / "build/windows/manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("old accepted manifest")
    result = _failed_wrapper(checkout)
    assert result.returncode != 0
    assert not manifest.exists(), "Early prerequisite failure retained an accepted manifest"


def test_helper_invalidates_manifest_before_python_version_failure(tmp_path, monkeypatch, build_module):
    manifest = tmp_path / "build/windows/manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("old accepted manifest")
    monkeypatch.setattr(build_module, "__file__", str(tmp_path / "packaging/build.py"))
    monkeypatch.setattr(build_module.platform, "python_version", lambda: "0.0.0")
    monkeypatch.setattr(sys, "argv", ["build.py", "--node", "missing", "--npm-cli", "missing"])
    with pytest.raises(ValueError, match="CPython"):
        build_module.main()
    assert not manifest.exists(), "Direct-helper prerequisite failure retained an accepted manifest"


def test_wheel_resources_work_outside_checkout(tmp_path):
    python = os.environ.get("PACKAGE_BUILD_PYTHON", sys.executable)
    result = subprocess.run(
        [python, "-m", "hatchling", "build", "-t", "wheel", "-d", str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheel = next(tmp_path.glob("*.whl"))
    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "tg_assistant/_resources/alembic/env.py" in names
        assert "tg_assistant/_resources/README.md" in names
        assert "tg_assistant/_resources/USER_GUIDE.md" in names
        assert "tg_assistant/_resources/dashboard-prototype/dist/client/index.html" in names
        assert "tg_assistant/desktop/design_tokens.json" in names
        assert "tg_assistant/desktop/installer_check.py" in names
        assert "tg_assistant/db/legacy_0005.json" in names
        assert not any(part in name.split("/") for name in names for part in ("node_modules", ".venv", ".env", "tests", ".superpowers"))
        archive.extractall(installed)
    env = dict(os.environ, PYTHONPATH=str(installed))
    check = subprocess.run([python, "-c", "from tg_assistant.paths import resource_path; assert resource_path('alembic','env.py').is_file(); assert resource_path('dashboard-prototype','public','fonts','PeterObscure.ttf').is_file(); assert resource_path('dashboard-prototype','public','fonts','DarleySans-Regular.otf').is_file()"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert check.returncode == 0, check.stderr


def test_build_rejects_extra_and_missing_locked_distributions():
    import importlib.util

    spec = importlib.util.spec_from_file_location("windows_build", ROOT / "packaging" / "build.py")
    assert spec.origin and Path(spec.origin).is_file(), "Windows build validation is absent"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match="environment"):
        module.validate_environment({"a": "1"}, {"a": "1", "pytest": "9"})
    with pytest.raises(ValueError, match="environment"):
        module.validate_environment({"a": "1"}, {})
    module.validate_environment({"a": "1"}, {"a": "1"})


def test_build_rejects_lock_stale_against_project_requirements():
    import importlib.util

    spec = importlib.util.spec_from_file_location("windows_build", ROOT / "packaging" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "validate_requirements"), "Project requirements are not checked against the lock"
    with pytest.raises(ValueError, match="stale"):
        module.validate_requirements(["aiosqlite>=1"], {"aiosqlite": "0.22.1"})
    with pytest.raises(ValueError, match="stale"):
        module.validate_requirements(["aiosqlite>=0.20"], {})
    module.validate_requirements(["aiosqlite>=0.20,<1"], {"aiosqlite": "0.22.1"})
