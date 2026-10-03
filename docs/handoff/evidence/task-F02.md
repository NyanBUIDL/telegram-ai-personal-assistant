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
