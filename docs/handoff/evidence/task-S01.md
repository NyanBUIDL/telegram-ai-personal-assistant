# S01 — SQLite default and per-user storage

Code-ready evidence; independent coordinator review/integration remains required. The worker did not stage, commit, push or sign off its own task.

Fresh profiles open SQLite at the selected LocalAppData app root's `db/assistant.sqlite3` without asking for a MySQL password. `aiosqlite` is now a runtime dependency. Every SQLite connection enforces foreign keys, WAL and a5000ms busy timeout. Explicit existing/advanced MySQL remains on asyncmy/pymysql and retains its database and stored password; an actual disposable MySQL profile preserved Vietnamese data through migration/reopen with no SQLite conversion.

`StorageService.open(PublicProfile)` validates profile/backend and internal Settings before filesystem effects; `.migrate()` delegates to the reviewed F02 runner. Version1 JSON config is atomically saved under `config/settings.json`. New writes never modify .env; legacy installation-relative imports whitelist non-secret settings only, and `_env_file=None` disables that legacy import. Secret keys, credential-bearing local endpoint userinfo/query/fragment and unknown config versions are refused. Relative data-root overrides and runtime directories follow the selected profile rather than cwd. Resources resolve from source/frozen asset roots and reject absolute/traversal/drive-relative paths.

The app data marker binds app/profile_id/native process-token Windows SID. A native Global named mutex serializes first claims and historical empty-marker upgrades; markers are atomically replaced only on those claims and later callers validate the winning marker. Foreign SID/profile markers and unowned nonempty roots are refused. Empty historical app markers are only adopted at the override-independent standard LocalAppData root; an arbitrary TG_ASSISTANT_DATA_DIR override does not prove ownership. The marker/DTO is not authorization proof.

Modified source/test files: `pyproject.toml`, `src/tg_assistant/config.py`, `paths.py`, `db/base.py`, new `services/storage.py`, `cli.py`, narrow `runtime.make_database`, narrow `setup.wizard._save_database_config`, and new `tests/test_storage_profile.py`. The coordinator explicitly approved the wizard adapter so existing MySQL provisioning stores the explicit backend in per-user JSON without changing its reviewed F02 provisioning/migration logic. No historical art/font/reference asset was modified.

TDD: initial15 expected failures in5.03s; named follow-up RED checks covered ownership/SID3, SQLite doctor1, secret endpoint/persistence4, pool-only backend selection1, arbitrary override legacy-marker ownership1, selected/resource paths3, concurrent fresh+legacy profile claims2, and unchecked-storage settings side effects1. Logs are in ignored `.test-temp/s01/*-red.log` and `red.log`. Initial basetemp setup failure and sandbox Windows asyncio initialization hang were environment-only and corrected before counting RED.

GREEN commands used installed `.venv-q01/Scripts/python.exe`:

- `-m pytest tests/test_storage_profile.py -v --basetemp=.test-temp/s01/green-final`: **37 passed in9.00s**, including real MySQL and native ownership races.
- `-m pytest tests/test_storage_profile.py tests/integration/test_migrations.py -v --basetemp=.test-temp/s01/green-required`: **101 passed,4 intentional dialect-specific skips in93.33s** before final negative additions. The final complete suite also includes every migration case.
- Final complete default pytest suite, after all S01 code changes and the coordinator's latest R01 fixture: **419 passed,4 intentional dialect-specific skips in118.77s**, exit0; `.test-temp/s01/full-validated.log`.
- Scoped Ruff lint, owned-file format check and `git diff --check`: pass. The native Windows process-token SID probe passed without printing its identifier.

Exact complete-suite invocation:

```powershell
.venv-q01/Scripts/python.exe -c "from pathlib import Path; from tg_assistant import config; config.project_root=lambda:Path('.test-temp/s01/empty-legacy-root').resolve(); import pytest; raise SystemExit(pytest.main(['--basetemp=.test-temp/s01/full-validated']))" *> .test-temp/s01/full-validated.log
$s01Exit=$LASTEXITCODE
Get-Content .test-temp/s01/full-validated.log -Tail 90
exit $s01Exit
```

Process isolation: TG_ASSISTANT_DATA_DIR=absolute repository `.test-temp/s01/runtime-validated`, ART_EVIDENCE_DIR=absolute `.test-temp/s01/art`, QT_QPA_PLATFORM=offscreen. TG_TEST_MYSQL_URL used the coordinator's synthetic127.0.0.1:13307 synchronous admin factory; TG_TEST_F01_MYSQL_URL the separate codex_f01_epoch async fixture. New S01 schemas were only codex_s01_UUID. The coordinator authorized inherited guarded F01/F02 fixtures for required regressions; no shared schema was dropped outside its own fixture. The wrapper redirects only the legacy config source root to a synthetic nonexistent directory; resource resolution remains real. Storage tests use their own synthetic legacy .env fixtures. No live vault/session/.env was read. Authorized outside-sandbox execution was required for Windows asyncio/MySQL networking. Exit codes were captured before reading logs.

S02 concurrency/maintenance, S03 backup/restore/writer fencing, D01/O02 native launcher/secret inputs, and P01/P02 packaged install/bundled migration assets remain later gates. Unknown partial ownership state is refused conservatively. Human/VM tests, font rights and beta-readiness are not claimed here. The full ignored implementer report is `.superpowers/sdd/windows-public-beta-2026-10-02/task-S01.md`.

## Review fix1 — legacy location preservation

Independent coordinator review of d63d3a12 found one Important issue: legacy relative data/vector/dashboard paths changed location or depended on cwd. `_legacy_config` now anchors only relative imported data_dir/qdrant_path/ollama_qdrant_path/dashboard_dist_path to the fixed legacy file parent. Modern init/environment data-root overrides retain the per-user anchoring, and absolute legacy paths are preserved. Existing files are never moved. A nonstandard historical empty marker still requires explicit adoption and is refused unchanged.

RED: storage tests selected by `-k 'relative_legacy or modern_relative or absolute_legacy'` failed1 with3 passing controls in0.23s (`.test-temp/s01/fix1-red.log`). GREEN: `python -m pytest tests/test_storage_profile.py -v --basetemp=.test-temp/s01/fix1-green` passed **41 tests in25.96s**, including actual MySQL. The new preservation test checks four paths from two cwd locations, byte-identical synthetic data/vector/.env content, unchanged historical marker and no redirected/config-directory creation. Modern init/environment and absolute legacy paths have explicit controls. Direct config/vector/Admin API regressions passed **17 tests in3.49s** (`fix1-regressions.log`), using the same disposable legacy-source wrapper and data/art isolation. Scoped Ruff/format/diff check passed. The419-test full-suite result above remains evidence for the initial package; this scoped fix was verified by its41+17 directly relevant checks and awaits coordinator scoped re-review.
