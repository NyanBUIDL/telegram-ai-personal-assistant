"""SQLite CI preserves failure counts and excludes private artifact contents."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(tmp_path, *args):
    env = dict(os.environ)
    for name in ("TG_TEST_MYSQL_URL", "TG_TEST_F01_MYSQL_URL"):
        env.pop(name, None)
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/ci_checks.py"),
            "--report",
            str(tmp_path / "summary.json"),
            *args,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_summary_never_contains_failure_payload(tmp_path):
    test = tmp_path / "test_secret.py"
    test.write_text(
        "def test_failure():\n    assert False, 'raw-private-fixture-payload'\n", encoding="utf-8"
    )
    result = run(tmp_path, str(test))
    assert result.returncode == 1
    report = (tmp_path / "summary.json").read_text()
    assert "raw-private-fixture-payload" not in report
    assert json.loads(report)["failed"] == 1


def test_artifact_staging_ignores_private_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "summary.json").write_text(
        json.dumps({"passed": 1, "failed": 0, "skipped": 0, "errors": 0, "exit_code": 0})
    )
    (source / ".env").write_text("private")
    (source / "owner.session").write_text("private")
    (source / "database.dump").write_text("private")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/ci_artifacts.py"),
            str(source),
            str(tmp_path / "staged"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert [p.name for p in (tmp_path / "staged").iterdir()] == ["summary.json"]


def test_artifact_staging_rejects_untrusted_summary_fields(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "summary.json").write_text('{"passed": 1, "token": "private"}')
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/ci_artifacts.py"),
            str(source),
            str(tmp_path / "staged"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "unsafe or missing CI artifact evidence" in result.stdout + result.stderr
    assert "private" not in result.stdout + result.stderr
    assert not (tmp_path / "staged/summary.json").exists()


def test_artifact_staging_keeps_only_named_synthetic_wave_images(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    approved = {
        "d02-browser-360.png",
        "d02-browser-390.png",
        "d02-browser-1280.png",
        "d02-browser-1440.png",
        "native-backup-dialog.png",
    }
    for name in approved | {"d02-browser-999.png", "owner-ticket.png"}:
        (source / name).write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
    for name in ("stdout.log", "trace.zip", "ticket.json"):
        (source / name).write_text("private-fixture-payload", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/ci_artifacts.py"),
            str(source),
            str(tmp_path / "staged"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert {path.name for path in (tmp_path / "staged").iterdir()} == approved
    assert "private-fixture-payload" not in result.stdout + result.stderr


def test_summary_records_setup_error_and_success_without_exception_text(tmp_path):
    test = tmp_path / "test_setup.py"
    test.write_text(
        "import pytest\n@pytest.fixture\ndef broken():\n"
        "    raise RuntimeError('synthetic-private-error')\n"
        "def test_setup(broken): pass\ndef test_positive(): assert True\n",
        encoding="utf-8",
    )
    result = run(tmp_path, str(test))
    assert result.returncode == 1
    report = (tmp_path / "summary.json").read_text()
    assert "synthetic-private-error" not in report
    assert json.loads(report) == {
        "passed": 1,
        "failed": 0,
        "skipped": 0,
        "errors": 1,
        "exit_code": 1,
    }


def test_summary_records_collection_error(tmp_path):
    test = tmp_path / "test_broken.py"
    test.write_text("def broken(\n", encoding="utf-8")
    result = run(tmp_path, str(test))
    assert result.returncode == 2
    report = json.loads((tmp_path / "summary.json").read_text())
    assert report == {"passed": 0, "failed": 0, "skipped": 0, "errors": 1, "exit_code": 2}


def test_summary_distinguishes_skips_from_passes(tmp_path):
    test = tmp_path / "test_skips.py"
    test.write_text(
        "import pytest\ndef test_optional(): pytest.skip('optional-platform')\n"
        "def test_required(): assert True\n",
        encoding="utf-8",
    )
    result = run(tmp_path, str(test))
    assert result.returncode == 0
    report = json.loads((tmp_path / "summary.json").read_text())
    assert report == {"passed": 1, "failed": 0, "skipped": 1, "errors": 0, "exit_code": 0}
