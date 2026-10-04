"""Exercise CI fail-closed gates and artifact sanitation with disposable fixtures."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(tmp_path, *args, configured=True):
    env = dict(os.environ)
    for name in ("TG_TEST_MYSQL_URL", "TG_TEST_F01_MYSQL_URL"):
        env.pop(name, None)
    if configured:
        env["TG_TEST_MYSQL_URL"] = "mysql+pymysql://root:fixture@127.0.0.1:13307/codex_migrations"
        env["TG_TEST_F01_MYSQL_URL"] = (
            "mysql+asyncmy://root:fixture@127.0.0.1:13307/codex_revocation"
        )
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
    tmp_path, *, skipped=False, include_new=True, include_budget=True, include_runtime=True
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
    return (*selected, str(runtime))


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


def test_mysql_gate_runs_both_required_modules_and_deselects_sqlite(tmp_path):
    result = run(tmp_path, "--mysql-required", *fixtures(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "summary.json").read_text())
    assert report == {"passed": 6, "failed": 0, "skipped": 0, "errors": 0, "exit_code": 0}


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
