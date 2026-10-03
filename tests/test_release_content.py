"""Catch prohibited content, secret disclosure and false release approval."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check-release-content.py"
GIT = shutil.which("git")
assert GIT, "Git is required for controlled repository fixtures"
spec = importlib.util.spec_from_file_location("release_content", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.mark.parametrize(
    "path",
    [
        ".venv/Lib/tool.py",
        "nested/.venv-q01/a.py",
        "node_modules/pkg/a.js",
        "src/__pycache__/a.pyc",
        ".pytest_cache/state",
        "data/messages.json",
        "tests/testdata/telegram.json",
        "logs/run.log",
        ".env",
        ".env.production",
        "private/session.session",
        "secret.pem",
        "backup.sql",
        ".ci-artifacts/run.json",
    ],
)
def test_prohibited_runtime_or_generated_paths_fail(path):
    assert audit.path_issue(path) is not None


@pytest.mark.parametrize(
    "path", [".env.example", ".env.template", "tests/fixtures/synthetic.json", "src/a.py"]
)
def test_required_source_and_templates_remain_allowed(path):
    assert audit.path_issue(path) is None


def test_history_does_not_read_generated_dependency_env_files():
    assert not audit.source_file("tests/testdata/.env", historical=True)


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("openai_key", "sk-proj-" + "7Ac91Nx2pQ" * 5),
        ("telegram_bot_token", "123456789:" + "Za72pQ1Lm9" * 4),
        ("github_token", "ghp_" + "L7a21Q9xNm" * 4),
        ("credential_url", "mysql://alice:secretCanaryPass91@localhost/db"),
        ("telegram_api_hash", 'api_hash = "' + "a41e7c29" * 4 + '"'),
    ],
)
def test_candidates_are_classified_without_exposing_values(kind, value):
    findings = audit.scan_text(value, "src/example.py", "a" * 40)
    encoded = json.dumps(findings)
    assert any(item["kind"] == kind for item in findings)
    assert value not in encoded
    assert "secretCanaryPass91" not in encoded
    assert all(set(item) == {"path", "commit", "kind", "sha256"} for item in findings)


def test_known_placeholder_is_reported_as_synthetic_without_losing_real_candidate():
    findings = audit.scan_text("sk-" + "x" * 40 + "\nsk-" + "19Az7KpQ" * 6, "fixture", "HEAD")
    assert [item["kind"] for item in findings] == ["synthetic_placeholder", "openai_key"]


def test_env_assignment_candidate_is_redacted_even_without_vendor_token_shape():
    canary = "databasePasswordCanary79"
    findings = audit.scan_text("TG_DATABASE_PASSWORD=" + canary, ".env", "a" * 40)
    assert any(item["kind"] == "credential_assignment" for item in findings)
    assert canary not in json.dumps(findings)


def test_key_source_selector_is_not_a_credential_but_arbitrary_value_remains_candidate():
    field = "TG_ASSISTANT_OPENAI_API_KEY_SOURCE="
    safe = audit.scan_text(field + "native", ".env", "HEAD")
    unsafe = audit.scan_text(field + "privateCanaryValue87", ".env", "HEAD")
    assert safe[0]["kind"] == "configuration_selector"
    assert unsafe[0]["kind"] == "credential_assignment"
    provider_canary = audit.scan_text(field + "sk-proj-" + "9Az7KpQ" * 8, ".env", "HEAD")
    assert any(item["kind"] == "openai_key" for item in provider_canary)


def test_public_template_budget_and_exact_source_selectors_are_configuration():
    template = (SCRIPT.parent.parent / ".env.example").read_bytes()
    findings = audit.scan_text(template, ".env.example", "index")
    assert all(item["kind"] == "configuration_selector" for item in findings)


def test_dispositions_match_exact_path_kind_hash_without_hiding_other_candidates():
    known = {
        "path": "tests/example.py",
        "kind": "openai_key",
        "sha256": "a" * 64,
        "commit": "index",
    }
    unknown = {**known, "sha256": "b" * 64}
    elsewhere = {**known, "path": "src/production.py"}
    entries = [
        {
            "path": known["path"],
            "kind": known["kind"],
            "sha256": known["sha256"],
            "classification": "synthetic_test_fixture",
            "reason": "Reviewed literal fixture",
        }
    ]
    result = audit.classify_findings([known, unknown, elsewhere], entries)
    assert result[0].get("classification") == "synthetic_test_fixture"
    assert "classification" not in result[1] and "classification" not in result[2]


def test_asset_hash_mismatch_and_unresolved_rights_block_public_release(tmp_path):
    (tmp_path / "font.ttf").write_bytes(b"changed font")
    manifest = {
        "assets": [{"path": "font.ttf", "sha256": "0" * 64, "redistribution": "unresolved"}]
    }
    findings = audit.asset_issues(tmp_path, manifest, public=True)
    assert {item["kind"] for item in findings} == {
        "asset_hash_mismatch",
        "asset_license_unresolved",
    }


def test_cli_uses_tracked_source_only_and_redacts_secret_candidate(tmp_path):
    subprocess.run([GIT, "init", "-q", str(tmp_path)], check=True)
    canary = "sk-proj-" + "6bAx1P7Q9k" * 5
    (tmp_path / "source.py").write_text('KEY = "' + canary + '"', encoding="utf-8")
    (tmp_path / ".env").write_text("DO_NOT_READ_PRIVATE_FILE", encoding="utf-8")
    subprocess.run([GIT, "-C", str(tmp_path), "add", "source.py"], check=True)
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--root", str(tmp_path), "--no-assets"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["scanned_source_files"] == 1
    assert any(item["kind"] == "openai_key" for item in payload["findings"])
    assert canary not in result.stdout + result.stderr
    assert "DO_NOT_READ_PRIVATE_FILE" not in result.stdout + result.stderr


def test_placeholder_shape_cannot_approve_unknown_tracked_assignment(tmp_path):
    subprocess.run([GIT, "init", "-q", str(tmp_path)], check=True)
    candidate = "sk-" + "x" * 40
    (tmp_path / "source.py").write_text('KEY = "' + candidate + '"', encoding="utf-8")
    subprocess.run([GIT, "-C", str(tmp_path), "add", "source.py"], check=True)
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--root", str(tmp_path), "--no-assets"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert candidate not in result.stdout + result.stderr
    assert json.loads(result.stdout)["public_release_approved"] is False


@pytest.mark.parametrize("envfile", [".env", ".env.production"])
def test_history_reports_removed_env_candidate_without_reading_runtime_credentials(
    tmp_path, envfile
):
    subprocess.run([GIT, "init", "-q", str(tmp_path)], check=True)
    canary = "123456789:" + "7AzpQ91nL2" * 4
    (tmp_path / envfile).write_text("TG_BOT_TOKEN=" + canary, encoding="utf-8")
    git = [GIT, "-C", str(tmp_path)]
    subprocess.run([*git, "add", envfile], check=True)
    identity = ["-c", "user.name=R01 Test", "-c", "user.email=r01@example.invalid"]
    subprocess.run([*git, *identity, "commit", "-qm", "synthetic credential"], check=True)
    first = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    subprocess.run([*git, "rm", "-q", envfile], check=True)
    subprocess.run([*git, *identity, "commit", "-qm", "remove synthetic credential"], check=True)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(SCRIPT),
            "--root",
            str(tmp_path),
            "--no-assets",
            "--history-base",
            first,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert any(
        item["kind"] == "telegram_bot_token" and item["commit"] == first
        for item in payload["findings"]
    )
    assert canary not in result.stdout + result.stderr


def test_history_detects_merge_resolution_candidate_removed_before_head(tmp_path):
    git = [GIT, "-C", str(tmp_path)]
    identity = ["-c", "user.name=R01 Test", "-c", "user.email=r01@example.invalid"]

    def run(*args):
        return subprocess.run(
            [*git, *identity, *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    run("init", "-q", "--initial-branch=main")
    source = tmp_path / "src"
    source.mkdir()
    (source / "base.py").write_text("BASE = True\n", encoding="utf-8")
    run("add", "src")
    run("commit", "-qm", "base")
    first = run("rev-parse", "HEAD")
    run("checkout", "-qb", "side")
    (source / "side.py").write_text("SIDE = True\n", encoding="utf-8")
    run("add", "src")
    run("commit", "-qm", "side")
    run("checkout", "-q", "main")
    (source / "main.py").write_text("MAIN = True\n", encoding="utf-8")
    run("add", "src")
    run("commit", "-qm", "main")
    run("merge", "--no-ff", "--no-commit", "side")
    canary = "ghp_" + "L7a21Q9xNm" * 4
    (source / "resolution.py").write_text('KEY = "' + canary + '"\n', encoding="utf-8")
    run("add", "src")
    run("commit", "-qm", "merge resolution introduces fixture")
    merge = run("rev-parse", "HEAD")
    assert len(run("rev-list", "--parents", "-n", "1", merge).split()) == 3
    run("rm", "-q", "src/resolution.py")
    run("commit", "-qm", "remove fixture")
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(SCRIPT),
            "--root",
            str(tmp_path),
            "--no-assets",
            "--history-base",
            first,
            "--history-head",
            run("rev-parse", "HEAD"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    findings = json.loads(result.stdout)["findings"]
    assert any(item["kind"] == "github_token" and item["commit"] == merge for item in findings)
    assert canary not in result.stdout + result.stderr
