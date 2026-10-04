"""Exercise CI fail-closed gates and artifact sanitation with disposable fixtures."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run(tmp_path, *args, configured=True, mysql_url=None):
    env = dict(os.environ)
    for name in ("TG_TEST_MYSQL_URL", "TG_TEST_F01_MYSQL_URL"):
        env.pop(name, None)
    if configured:
        env["TG_TEST_MYSQL_URL"] = "mysql+pymysql://root:fixture@127.0.0.1:13307/codex_migrations"
        env["TG_TEST_F01_MYSQL_URL"] = (
            "mysql+asyncmy://root:fixture@127.0.0.1:13307/codex_revocation"
        )
    if mysql_url is not None:
        env["TG_TEST_MYSQL_URL"] = mysql_url
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


def fixtures(
    tmp_path, *, skipped=False, include_new=True, include_budget=True, include_runtime=True,
    skip_wave=None,
):
    migration = tmp_path / "test_migrations.py"
    revocation = tmp_path / "test_revocation_jobs.py"
    migration.write_text(
        "import pytest\n@pytest.mark.parametrize('connection', ['sqlite', 'mysql'])\n"
        "def test_upgrade(connection):\n"
        + ("    pytest.skip('unavailable')\n" if skipped else "    assert connection == 'mysql'\n"),
        encoding="utf-8",
    )
    with migration.open("a", encoding="utf-8") as file:
        file.write(
            "@pytest.mark.parametrize('connection', ['sqlite', 'mysql'])\n"
            "def test_sqlite_integer_pk_alias_refused(connection):\n"
            "    if connection == 'mysql': pytest.skip('SQLite-only semantics')\n"
            "    assert connection == 'sqlite'\n"
        )
    revocation.write_text("def test_mysql_committed_revoke():\n    assert True\n", encoding="utf-8")
    if not include_new:
        return str(migration), str(revocation)
    jobs = tmp_path / "test_job_concurrency.py"
    profiles = tmp_path / "test_embedding_profiles.py"
    jobs.write_text(
        "import pytest\n@pytest.mark.parametrize('storage', ['sqlite', 'mysql'])\ndef test_atomic(storage):\n    assert storage == 'mysql'\n",
        encoding="utf-8",
    )
    with jobs.open("a", encoding="utf-8") as output:
        output.write(
            "@pytest.mark.parametrize('storage', ['sqlite', 'mysql'])\ndef test_sqlite_busy_is_bounded(storage):\n    if storage == 'mysql': pytest.skip('SQLite-only semantics')\n"
        )
    profiles.write_text("def test_mysql_atomic_budget():\n    assert True\n", encoding="utf-8")
    selected = (str(migration), str(revocation), str(jobs), str(profiles))
    if not include_budget:
        return selected
    costs = tmp_path / "test_budget_migrations.py"
    costs.write_text(
        "import pytest\n@pytest.mark.parametrize('budget_connection', ['sqlite', 'mysql'])\n"
        "def test_budget_precision(budget_connection):\n    assert budget_connection == 'mysql'\n",
        encoding="utf-8",
    )
    selected = (*selected, str(costs))
    if not include_runtime:
        return selected
    runtime = tmp_path / "test_runtime_embeddings.py"
    runtime.write_text(
        "import pytest\n@pytest.mark.parametrize('runtime_case', ['sqlite', 'mysql'])\n"
        "def test_runtime_authority_and_caps(runtime_case):\n    assert runtime_case == 'mysql'\n",
        encoding="utf-8",
    )
    backup = tmp_path / "test_backup_restore.py"
    backup.write_text(
        "import pytest\ndef test_mysql_restore():\n"
        + ("    pytest.skip('unavailable')\n" if skip_wave == backup.name else "    assert True\n"),
        encoding="utf-8",
    )
    vector = tmp_path / "test_vector_incremental.py"
    body = (
        "    pytest.skip('unavailable')\n"
        if skip_wave == vector.name
        else "    assert {parameter} == 'mysql'\n"
    )
    vector.write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('incremental_case', ['sqlite', 'mysql'])\n"
        "def test_reuse(incremental_case):\n" + body.format(parameter="incremental_case")
        + "@pytest.mark.parametrize('incremental_connection', ['sqlite', 'mysql'])\n"
        "def test_restore_metadata(incremental_connection):\n"
        + body.format(parameter="incremental_connection"),
        encoding="utf-8",
    )
    return (*selected, str(runtime), str(backup), str(vector))


def test_mysql_gate_rejects_omitted_runtime_policy_and_budget_controls(tmp_path):
    result = run(tmp_path, "--mysql-required", *fixtures(tmp_path, include_runtime=False))
    assert result.returncode != 0
    assert "required MySQL cases did not pass" in result.stdout + result.stderr


def test_mysql_gate_rejects_omitted_budget_migration_controls(tmp_path):
    result = run(tmp_path, "--mysql-required", *fixtures(tmp_path, include_budget=False))
    assert result.returncode != 0
    assert "required MySQL cases did not pass" in result.stdout + result.stderr


def test_mysql_gate_rejects_omitted_new_storage_and_budget_coverage(tmp_path):
    result = run(tmp_path, "--mysql-required", *fixtures(tmp_path, include_new=False))
    assert result.returncode != 0
    assert "required MySQL cases did not pass" in result.stdout + result.stderr


def test_mysql_gate_runs_eight_required_modules_and_deselects_sqlite(tmp_path):
    result = run(tmp_path, "--mysql-required", *fixtures(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "summary.json").read_text())
    assert report == {"passed": 9, "failed": 0, "skipped": 0, "errors": 0, "exit_code": 0}


@pytest.mark.parametrize("omitted", [
    "test_migrations.py", "test_revocation_jobs.py", "test_job_concurrency.py",
    "test_embedding_profiles.py", "test_budget_migrations.py", "test_runtime_embeddings.py",
    "test_backup_restore.py", "test_vector_incremental.py",
])
def test_mysql_gate_fails_when_any_required_module_is_omitted(tmp_path, omitted):
    paths = [path for path in fixtures(tmp_path) if Path(path).name != omitted]
    result = run(tmp_path, "--mysql-required", *paths)
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads((tmp_path / "summary.json").read_text())
    assert report["exit_code"] == 1
    assert "required MySQL cases did not pass" in result.stdout + result.stderr


@pytest.mark.parametrize("skipped", ["test_backup_restore.py", "test_vector_incremental.py"])
def test_mysql_gate_fails_when_new_wave_required_cases_skip(tmp_path, skipped):
    result = run(tmp_path, "--mysql-required", *fixtures(tmp_path, skip_wave=skipped))
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads((tmp_path / "summary.json").read_text())
    assert report["skipped"] > 0
    assert report["exit_code"] == 1


def test_mysql_gate_rejects_skips_in_required_cases(tmp_path):
    result = run(tmp_path, "--mysql-required", *fixtures(tmp_path, skipped=True))
    assert result.returncode != 0
    assert "required MySQL cases did not pass" in result.stdout + result.stderr


def test_mysql_gate_rejects_unconfigured_service_without_echoing_secrets(tmp_path):
    result = run(tmp_path, "--mysql-required", *fixtures(tmp_path), configured=False)
    assert result.returncode != 0
    assert "disposable MySQL URLs required" in result.stdout + result.stderr
    assert "fixture@" not in result.stdout + result.stderr


def test_mysql_gate_rejects_missing_revocation_module(tmp_path):
    migration, _ = fixtures(tmp_path, include_new=False)
    result = run(tmp_path, "--mysql-required", migration)
    assert result.returncode != 0
    assert "required MySQL cases did not pass" in result.stdout + result.stderr


@pytest.mark.parametrize("mysql_url", [
    "invalid-fixture-payload",
    "mysql+asyncmy://root:fixture@127.0.0.1:3306/codex_fixture",
    "mysql+pymysql://root:fixture@example.invalid:3306/codex_fixture",
    "mysql+pymysql://root:fixture@127.0.0.1:3307/codex_fixture",
    "mysql+pymysql://root:fixture@127.0.0.1:3306/production",
    "mysql+pymysql://root@127.0.0.1:3306/codex_fixture",
])
def test_s03_fixture_refuses_unsafe_mysql_target_before_connecting(tmp_path, mysql_url):
    item = str(ROOT / "tests/integration/test_backup_restore.py") + "::test_mysql_snapshot_consistent"
    result = run(tmp_path, item, mysql_url=mysql_url)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "summary.json").read_text())
    assert report == {"passed": 0, "failed": 0, "skipped": 1, "errors": 0, "exit_code": 0}
    assert mysql_url not in result.stdout + result.stderr


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
        "d02-browser-360.png", "d02-browser-390.png", "d02-browser-1280.png",
        "d02-browser-1440.png", "native-backup-dialog.png",
    }
    for name in approved | {"d02-browser-999.png", "owner-ticket.png"}:
        (source / name).write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
    for name in ("stdout.log", "trace.zip", "ticket.json"):
        (source / name).write_text("private-fixture-payload", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/ci_artifacts.py"),
         str(source), str(tmp_path / "staged")],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert {path.name for path in (tmp_path / "staged").iterdir()} == approved
    assert "private-fixture-payload" not in result.stdout + result.stderr
