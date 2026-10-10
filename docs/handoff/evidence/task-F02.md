# F02 — immutable migrations and conservative legacy repair

Status: implementation ready for independent review; not Verified or committed.
Execution date: 2026-10-03. Shared approved branch: `codex/windows-public-beta`;
resumed from HEAD `25d060a1` without discarding prior working changes.

## Behavior and scope

Revision 0001 now contains explicit historical DDL and no runtime model imports.
The historical commit `26827cfb231373ce0f326553067f8f1ad849649d` already imported
the expanded live ORM schema, including additions from 0002–0005. Following the
coordinator's recorded ruling, the frozen intended base subtracts those later
additions. Revisions then apply their own changes exactly once. Generated IDs
use INTEGER primary keys in SQLite and BIGINT in MySQL; full-text indexing
remains MySQL-specific. SQLite column additions in 0004 use Alembic batch ALTER.
The old blanket existence skips in 0004/0005 were removed.

Before Alembic advances, the classifier accepts only an empty database, exact
known revision schemas, an exact unversioned historical layout, the immutable
original expanded layout, or a verified dependency-order prefix left by the
original failed create_all. It checks columns, types/signedness, nullability,
defaults, computed/identity status, keys, foreign-key behavior, indexes including
MySQL FULLTEXT, check constraints, transactional engines, and unexpected views
or triggers. Malformed, unknown or inconsistent revision records are refused.
An existing table alone never authorizes skipping a migration or stamping head.

Repair requires a pre-mutation snapshot, completes only the missing historical
tables, rechecks the whole schema and stamps the verified schema revision.
Expanded legacy layouts are recognized at 0005, then actually upgrade through
the current 0006 owned by F01. SQLite uses the backup API with integrity checking;
MySQL uses a consistent InnoDB logical snapshot with streamed rows and driver
escaping. The caller must fence application/legacy writers before repair.

The runner consumes an explicit connection and returns the database's actual
revision in MigrationReport. The legacy MySQL wizard invokes that runner with
its existing backend and backup directory, then displays the actual revision.
Its existing CLI provisioning flow remains; native default-SQLite onboarding
belongs to later storage/setup tasks. No F01 model or 0006 edits were made here.

## Red evidence and environment distinctions

An ignored recovery script loads the exact historical base/models and revisions
0001/0002 from Git into a separate process, then runs those revisions against an
in-memory SQLite database without application configuration:

```text
Python .superpowers/sdd/windows-public-beta-2026-10-02/f02-original-chain-red.py
EXPECTED RED: exact 26827cfb 0001 -> 0002 fails: table knowledge_sources already exists
exit 1
```

Additional new semantic negatives were run before strengthening the classifier:

```text
Python scripts/check.py pytest tests/integration/test_migrations.py -k unknown_semantics
  -o addopts='' -q --basetemp .superpowers/sdd/windows-public-beta-2026-10-02/f02-red-semantic-1
3 failed (altered default/view/trigger accepted), 1 passed, 4 MySQL skipped
exit 1
```

The first actual expanded dialect run found a real MySQL partial-repair defect:
26 passed, 1 failed in 39.25s, exit 1, because a generated foreign-key name
exceeded MySQL's 64-character limit. Operations.f() now permits dialect
truncation; subsequent actual MySQL partial repair passes.

The first resumed command also had 18 fixture setup errors from an unavailable
sandbox pytest temp directory. A writable task-local --basetemp resolved this;
these setup errors are environment failures, not behavior red. Disposable
loopback MySQL tests require outside-sandbox execution because sandbox WFP
blocks loopback connections. No service or firewall setting was changed.

## Final executed checks

Bundled Python:
`C:/Users/brian/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`.
`scripts/check.py` adds the existing `../audit-deps` bootstrap dependencies.
The coordinator supplied TG_TEST_MYSQL_URL for its official portable MySQL
8.4.11 fixture at 127.0.0.1:13307 with artificial fixture credentials. No real
application config, saved credentials, Telegram session or user data was used.
Every integration case blocks SecretStore.get.

```text
Python scripts/check.py pytest tests/integration/test_migrations.py tests/test_api_key_setup.py
  -o addopts='' -q --basetemp .superpowers/sdd/windows-public-beta-2026-10-02/f02-final-matrix-3
55 passed, 4 skipped in 72.75s; exit 0
```

This includes 52 actual migration cases and 3 existing API-key setup
regressions. Three skips are SQLite instances of MySQL-only unsigned,
FULLTEXT and engine cases; their actual MySQL counterparts passed. One skip is
the MySQL instance of SQLite's exact INTEGER generated-PK declaration case;
its SQLite counterpart passed. All other cases execute against both SQLite
and actual MySQL. Coverage includes fresh
head, 0001/0004 upgrades preserving negative chat IDs and large message/sender
IDs, repeated upgrades, model-independent 0001, current revision reports,
unversioned 0001/0004 snapshots, expanded schemas mis-stamped at 0001/0004,
partial original create_all repair, missing/bad snapshots, untouched unknown
schemas, malformed/ahead version records, narrowed integer types,
changed schema semantics and a real
downgrade to base.

The expanded-schema snapshots were reopened/restored into separate empty
databases and compared to the pre-repair schema and data. MySQL restores passed
with Vietnamese text, quotes, semicolons, backslashes, newlines and percent
characters. Snapshot revision is retained, and 0006's authorization_epoch is
absent until the live upgrade. Repeated upgrade creates no second repair backup.

```text
Python scripts/check.py ruff check alembic/env.py alembic/versions/0001_initial_schema.py
  alembic/versions/0002_knowledge_source_inventory.py alembic/versions/0003_ai_routing_operations.py
  alembic/versions/0004_local_first_ai.py alembic/versions/0005_vector_store_reliability.py
  src/tg_assistant/db/migrations.py src/tg_assistant/setup/wizard.py tests/integration/test_migrations.py
All checks passed; exit 0

Python scripts/check.py ruff format --check [same 9 files]
9 files already formatted; exit 0

git diff --check -- [F02 tracked paths]
exit 0
```

## Remaining gates

Independent scoped review and coordinator staging/commit are pending. The
coordinator owns ledger status changes and the integrated wave regression run.
No live profile upgrade, clean Windows VM, native onboarding, automated writer
fencing/restore orchestration, packaging or release is signed off by F02. The
coordinator owns graceful shutdown of the disposable shared MySQL fixture.

## Review fix round 1 — SQLite table and key semantics

The independent review found altered SQLite layouts classified as the known expanded legacy schema: `WITHOUT ROWID` changed generated-ID behavior, `COLLATE NOCASE` changed primary-key equality, and a same-named partial index changed index eligibility. The fix inspects actual SQLite `table_list`, `index_list` and `index_xinfo` metadata, including automatically created PK/UNIQUE indexes. Ordinary rowid tables and ascending BINARY keys keep the existing normalized fingerprint. Unsupported WITHOUT ROWID/STRICT flags, effective key collations, descending/expression keys and partial indexes add distinguishing metadata so classification refuses them before snapshot, repair or stamping. The same guard covers `alembic_version`, which the ordinary schema fingerprint deliberately excludes.

No frozen revision, current model, 0006, legacy descriptor or MySQL implementation changed. Nine additional standalone SQLite cases assert `unknown_schema`, unchanged complete SQLite schema definitions, preserved fixture data/revision and no backup directory. These standalone cases introduce no new MySQL skips. SQLite remains conservative: a deviation is refused rather than silently repaired.

RED evidence used the fresh installed QA Python environment and the existing F02 classifier:

- The corrected application-table negative run produced **five failures / two passes**, exit 1: WITHOUT ROWID, PK NOCASE, index NOCASE, index DESC and partial-index cases reached upgrade instead of raising. The UNIQUE-NOCASE and valid STRICT layouts already failed closed through existing reflected/type differences; they are retained as safety coverage, not claimed as new RED.
- Two revision-table negatives produced **two failures**, exit 1: altered WITHOUT ROWID and PK NOCASE version tables were accepted. Both now fail closed.
- The initial STRICT test fixture used JSON as a declared STRICT type and was invalid SQLite DDL. It was corrected to canonical TEXT before accepting RED evidence. This setup error is separate from classifier failures.

| Command / scope | Result |
|---|---|
| `.venv-q01/Scripts/python.exe -m pytest tests/integration/test_migrations.py -k 'table_key_and_index_semantics or revision_table_semantics' --basetemp=.test-temp/f02-fix1-green` | Nine passed, 56 deselected, exit 0, 1.06s |
| `.venv-q01/Scripts/python.exe -m pytest tests/integration/test_migrations.py --basetemp=.test-temp/f02-fix1-matrix/pytest`, approved disposable `TG_TEST_MYSQL_URL` set | **61 passed, four intentional dialect-only skips**, exit 0, 61.66s; actual SQLite and MySQL migrations/snapshots/restore checks |
| `.venv-q01/Scripts/python.exe -m ruff check src/tg_assistant/db/migrations.py tests/integration/test_migrations.py` | All checks passed, exit 0 |
| `.venv-q01/Scripts/python.exe -m ruff format --check src/tg_assistant/db/migrations.py tests/integration/test_migrations.py` | Two files already formatted, exit 0; initial formatting-only findings corrected |
| `git diff --check -- src/tg_assistant/db/migrations.py tests/integration/test_migrations.py` | Exit 0 |

This run covers the F02 fix worktree after the Q01 freeze (`067bf85d`), including the coordinator's F01 fix; it does not replace or relabel Q01's historical integrated full-suite result. Only `src/tg_assistant/db/migrations.py`, `tests/integration/test_migrations.py` and this evidence append belong to F02 fix round 1. Its recovery report is `.superpowers/sdd/windows-public-beta-2026-10-02/task-F02-fix1-report.md`. Coordinator commit and independent scoped re-review remain pending; no stage, commit or external action was performed by the fix worker.

## Review fix round 2 — MySQL effective character and index semantics

The coordinator's follow-up actual MySQL probe identified the same exact-known-schema risk in the other dialect: changed PK/FK collations and a same-named prefix index were accepted as expanded legacy 0005. Fix round 2 adds private metadata inspection relative to the **current database's own implicit character-set/collation defaults**, preserving legitimate different database defaults. Effective table/character-column deviations and shortened, descending, expression or invisible keys distinguish an unknown fingerprint. Every index participates, including PK, UNIQUE and automatically supporting FK indexes omitted by ordinary reflection normalization. `alembic_version` receives the same guard. Metadata fields follow MySQL 8.4's [STATISTICS](https://dev.mysql.com/doc/refman/8.4/en/information-schema-statistics-table.html), [COLUMNS](https://dev.mysql.com/doc/refman/8.4/en/information-schema-columns-table.html) and [SCHEMATA](https://dev.mysql.com/doc/refman/8.4/en/information-schema-schemata-table.html) references.

Ten real MySQL cases use the existing per-test create/drop `codex_f02_*` fixture and indirect MySQL-only parameterization. Eight new refusal cases demonstrated RED: paired PK/FK collation, column charset, table default collation, normal index prefix, descending index, invisible index, UNIQUE prefix and revision-table collation. Each produced “DID NOT RAISE” before implementation. Expression-index reflection already refused the unknown layout; the positive alternate `utf8mb4_unicode_ci` database-default case already upgraded. These two passing controls are retained without falsely claiming new RED.

Refusal cases compare full `SHOW CREATE TABLE` definitions, preserved fixture rows/revision and absence of backups after rejection. The positive case confirms the alternate default survives a real legacy repair/upgrade and preserves Vietnamese data. No hard-coded production default, implicit charset conversion or backend switch was introduced.

| Command / scope | Result |
|---|---|
| `.venv-q01/Scripts/python.exe -m pytest tests/integration/test_migrations.py -k 'effective_collation_and_index or version_collation or legitimate_alternate' --basetemp=.test-temp/f02-fix2-red`, approved disposable MySQL URL | Eight failed, two passed, 65 deselected, exit 1, 14.66s; expected false acceptance before the fix |
| Same focused selection, `--basetemp=.test-temp/f02-fix2-green` after implementation | Ten passed, 65 deselected, exit 0, 14.25s |
| `.venv-q01/Scripts/python.exe -m pytest tests/integration/test_migrations.py --basetemp=.test-temp/f02-fix2-matrix/pytest`, approved disposable MySQL URL | **71 passed, four intentional dialect-only skips**, exit 0, 79.38s; actual SQLite/MySQL historical migration, backup/restore and new semantic cases |
| `.venv-q01/Scripts/python.exe -m ruff check src/tg_assistant/db/migrations.py tests/integration/test_migrations.py` | All checks passed, exit 0 |
| `.venv-q01/Scripts/python.exe -m ruff format --check src/tg_assistant/db/migrations.py tests/integration/test_migrations.py` | Two files already formatted, exit 0 |
| `git diff --check -- src/tg_assistant/db/migrations.py tests/integration/test_migrations.py` | Exit 0 |

Results cover base `6d7a40f90c76091e1d61c330c701661c0a2ee33a` plus the uncommitted fix round 2. Exact owned files remain `src/tg_assistant/db/migrations.py`, `tests/integration/test_migrations.py` and this evidence append; the recovery report is `.superpowers/sdd/windows-public-beta-2026-10-02/task-F02-fix2-report.md`. SQLite helper, frozen DDL, descriptor, models, 0006, QA workflow/tests and trackers were not changed. No full-suite result, remote CI pass, commit or release is claimed for this fix. Coordinator commit and independent scoped re-review remain pending.
