"""Read-only source/history audit. Output never contains matched secret values.

Does not initialize application settings, access credential stores, validate keys,
rewrite Git history, or grant redistribution rights. Pattern scanning is bounded
evidence, not proof that every possible credential has been detected.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

GENERATED = {
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".test-temp",
    ".superpowers",
    ".ci-work",
    ".ci-artifacts",
    "ci-artifacts",
    "htmlcov",
    "testdata",
    "data",
    "logs",
    "backups",
    "downloads",
    "qdrant_data",
    "dist",
    "build",
    "venv",
}
PRIVATE_SUFFIXES = {
    ".session",
    ".session-journal",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".sql",
    ".dump",
    ".key",
    ".pem",
    ".log",
    ".pyc",
    ".pyo",
}
TEXT_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".md",
    ".txt",
    ".html",
    ".css",
    ".ps1",
    ".bat",
    ".sh",
    ".ini",
    ".cfg",
    ".example",
    ".template",
}
HISTORY_ROOTS = [
    "src",
    "scripts",
    "tests",
    "docs",
    "pyproject.toml",
    ".gitignore",
    ".env",
    ".env.*",
    "README.md",
    "dashboard-prototype/src",
    "dashboard-prototype/package.json",
    "dashboard-prototype/package-lock.json",
]
PATTERNS = [
    ("openai_key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}")),
    ("telegram_bot_token", re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("credential_url", re.compile(r"[a-z][a-z0-9+.-]*://[^\s/:\"']+:[^\s/@\"']+@[^\s\"']+", re.I)),
    ("telegram_api_hash", re.compile(r"api_hash\s*[=:]\s*[\"']?([a-f0-9]{32})", re.I)),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def path_issue(path: str) -> str | None:
    parts = PurePosixPath(path.replace("\\", "/")).parts
    lower = [part.lower() for part in parts]
    if any(
        part in GENERATED or part.startswith(".venv") or part.endswith(".egg-info")
        for part in lower
    ):
        return "generated_or_runtime_path"
    name = lower[-1] if lower else ""
    if name.startswith(".env") and name not in {".env.example", ".env.template"}:
        return "private_env_path"
    if Path(name).suffix in PRIVATE_SUFFIXES or name in {
        ".coverage",
        ".tg-assistant.pid",
        ".tg-assistant.lock",
    }:
        return "private_or_generated_file"
    return None


def scan_text(text: str | bytes, path: str, commit: str) -> list[dict]:
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    findings, seen = [], set()
    patterns = list(PATTERNS)
    if PurePosixPath(path).name.lower().startswith(".env"):
        patterns.append(
            (
                "credential_assignment",
                re.compile(
                    r"(?im)^(?P<env_name>[A-Z0-9_]*(?:KEY|TOKEN|HASH|PASSWORD|SECRET)[A-Z0-9_]*)\s*=\s*[\"']?(?P<env_value>[^\s\"'#]+)"
                ),
            )
        )
    for kind, pattern in patterns:
        for match in pattern.finditer(text):
            value = (
                match.group("env_value")
                if kind == "credential_assignment"
                else match.group(1)
                if kind == "telegram_api_hash"
                else match.group(0)
            )
            # Placeholder-shaped values remain candidates until an exact
            # path/kind/hash disposition confirms their fixture provenance.
            body = value.split(":", 1)[-1].removeprefix("sk-proj-").removeprefix("sk-")
            placeholder = len(set(body)) == 1 or value.lower().startswith(
                ("your_", "replace_", "${")
            )
            actual_kind = "synthetic_placeholder" if placeholder else kind
            if kind == "credential_assignment":
                name = match.group("env_name")
                source_names = {
                    "TG_ASSISTANT_OPENAI_API_KEY_SOURCE",
                    "TG_ASSISTANT_OPENROUTER_API_KEY_SOURCE",
                    "TG_ASSISTANT_COINGECKO_API_KEY_SOURCE",
                }
                budget_names = {
                    "TG_ASSISTANT_MAX_INPUT_TOKENS_PER_REQUEST",
                    "TG_ASSISTANT_MAX_OUTPUT_TOKENS_PER_REQUEST",
                }
                # Exact source-selector value identity from the tracked template;
                # arbitrary values in these fields remain credential candidates.
                template_selector = (
                    "22d40057afc170eca2a58bdcc273247196dc6b237ecbfc126eb23f8254be12f1"
                )
                if (
                    name in source_names
                    and (value == "native" or digest(value.encode()) == template_selector)
                ) or (name in budget_names and value.isdecimal()):
                    actual_kind = "configuration_selector"
            item = {
                "path": path,
                "commit": commit,
                "kind": actual_kind,
                "sha256": digest(value.encode()),
            }
            if (actual_kind, item["sha256"]) not in seen:
                findings.append(item)
                seen.add((actual_kind, item["sha256"]))
    return findings


def asset_issues(root: Path, manifest: dict, *, public: bool = False) -> list[dict]:
    findings = []
    for asset in manifest.get("assets", []):
        relative = PurePosixPath(asset["path"])
        path = root / relative
        if relative.is_absolute() or ".." in relative.parts or not path.is_file():
            findings.append({"path": asset["path"], "kind": "asset_missing_or_invalid"})
        elif digest(path.read_bytes()).lower() != asset["sha256"].lower():
            findings.append({"path": asset["path"], "kind": "asset_hash_mismatch"})
        if public and asset.get("redistribution") != "verified":
            findings.append({"path": asset["path"], "kind": "asset_license_unresolved"})
    return findings


def classify_findings(findings: list[dict], entries: list[dict]) -> list[dict]:
    reviewed = {
        (item["path"], item["kind"], item["sha256"]): item["classification"]
        for item in entries
        if item.get("classification") in {"synthetic_test_fixture", "ci_disposable_password"}
        and item.get("reason")
    }
    return [
        {**item, **({"classification": reviewed[key]} if key in reviewed else {})}
        for item in findings
        for key in [(item["path"], item["kind"], item.get("sha256"))]
    ]


def git(root: Path, *args: str, optional: bool = False) -> bytes:
    executable = shutil.which("git")
    if not executable:
        raise ValueError("git_unavailable")
    result = subprocess.run([executable, "-C", str(root), *args], capture_output=True, check=False)
    if result.returncode and not optional:
        # Git stderr may echo input/content. Return only a fixed diagnostic.
        raise ValueError("git_read_failed")
    return b"" if result.returncode else result.stdout


def source_file(path: str, *, historical: bool = False) -> bool:
    if any(
        part.lower() in GENERATED or part.lower().startswith(".venv")
        for part in PurePosixPath(path).parts
    ):
        return False
    name = PurePosixPath(path).name.lower()
    if historical and name.startswith(".env"):
        return True  # bytes-only historical code analysis, never Settings/SecretStore
    return path_issue(path) is None and (
        Path(name).suffix in TEXT_SUFFIXES or name in {".gitignore", "license"}
    )


def audit_tree(root: Path) -> dict:
    paths = git(root, "ls-files", "-z").decode().split("\0")
    findings, scanned = [], 0
    for path in filter(None, paths):
        issue = path_issue(path)
        if issue:
            findings.append({"path": path, "commit": "index", "kind": issue})
        elif source_file(path):
            # Read index blobs only. Untracked .env/runtime files are never opened.
            content = git(root, "show", ":" + path)
            scanned += 1
            findings.extend(scan_text(content, path, "index"))
    return {
        "tracked_paths": len(list(filter(None, paths))),
        "scanned_source_files": scanned,
        "findings": findings,
    }


def audit_history(root: Path, base: str, head: str) -> dict:
    for revision in (base, head):
        if not re.fullmatch(r"[a-fA-F0-9]{7,40}|HEAD", revision):
            raise ValueError("invalid_history_revision")
    first = git(root, "rev-parse", "--verify", base + "^{commit}").decode().strip()
    last = git(root, "rev-parse", "--verify", head + "^{commit}").decode().strip()
    git(root, "merge-base", "--is-ancestor", first, last)
    commits = [
        first,
        *git(root, "rev-list", "--reverse", first + ".." + last).decode().splitlines(),
    ]
    findings, seen, scanned = [], set(), 0
    for commit in commits:
        if commit == first:
            # ls-tree path arguments are prefixes, not wildcard pathspecs:
            # enumerate names only and filter before reading any blob.
            paths = git(root, "ls-tree", "-r", "--name-only", "-z", commit)
        else:
            paths = git(
                root,
                "diff-tree",
                "-m",
                "--root",
                "--no-commit-id",
                "--name-only",
                "-r",
                "-z",
                commit,
                "--",
                *HISTORY_ROOTS,
            )
        for path in filter(None, paths.decode().split("\0")):
            if not any(
                fnmatch.fnmatchcase(path, named) or path.startswith(named + "/")
                for named in HISTORY_ROOTS
            ):
                continue
            if not source_file(path, historical=True):
                continue
            content = git(root, "show", commit + ":" + path, optional=True)
            identity = (path, digest(content))
            if not content or identity in seen:
                continue
            seen.add(identity)
            scanned += 1
            if PurePosixPath(path).name.lower().startswith(".env") and path_issue(path):
                findings.append(
                    {"path": path, "commit": commit, "kind": "historical_private_env_path"}
                )
            findings.extend(scan_text(content, path, commit))
    return {
        "base": first,
        "head": last,
        "commits": len(commits),
        "roots": HISTORY_ROOTS,
        "excluded": "generated/runtime paths, binary assets, paths outside named roots",
        "unique_source_blobs": scanned,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--history-base")
    parser.add_argument("--history-head", default="HEAD")
    parser.add_argument("--public", action="store_true")
    parser.add_argument(
        "--no-assets", action="store_true", help="Content-only audit; never release approval"
    )
    options = parser.parse_args()
    try:
        result = audit_tree(options.root)
        result["coverage"] = "tracked index text only; pattern candidates require classification"
        if options.history_base:
            result["history"] = audit_history(
                options.root, options.history_base, options.history_head
            )
            result["findings"].extend(result["history"].pop("findings"))
        if not options.no_assets:
            manifest = json.loads(
                (options.root / "docs/release/assets-manifest.json").read_text(encoding="utf-8")
            )
            result["findings"].extend(asset_issues(options.root, manifest, public=options.public))
            if options.public:
                result["findings"].extend(
                    {
                        "path": "docs/release/release-gates.json",
                        "kind": "public_release_gate_unresolved",
                        "gate": gate["id"],
                    }
                    for gate in json.loads(
                        (options.root / "docs/release/release-gates.json").read_text()
                    )
                    if gate["status"] != "verified"
                )
        elif options.public:
            result["findings"].append({"path": "assets", "kind": "public_asset_check_required"})
        dispositions = options.root / "docs/release/secret-scan-dispositions.json"
        if dispositions.is_file():
            result["findings"] = classify_findings(
                result["findings"], json.loads(dispositions.read_text(encoding="utf-8"))
            )
        result["public_release_approved"] = False  # audited evidence is not owner authorization
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return int(
            any(
                item["kind"] != "configuration_selector"
                and not item.get("classification")
                for item in result["findings"]
            )
        )
    except (OSError, ValueError, KeyError):
        print(json.dumps({"error": "audit_incomplete", "public_release_approved": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
