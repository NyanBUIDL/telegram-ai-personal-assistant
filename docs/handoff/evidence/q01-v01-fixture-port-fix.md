# V01 MySQL CI fixture portability

On HEAD `a1bd93fcdf39f2282cb0155bcd28c7c7daad90fa`, GitHub PR run
[37179372876](https://github.com/NyanBUIDL/telegram-ai-personal-assistant/actions/runs/37179372876)
ran 108 required MySQL cases: 107 passed, one failed. The failing atomic budget
reservation test rejected the CI service's port 3306 before connecting because
its fixture guard hardcoded the local development port 13307 and local fixture
credentials. This was not a budget assertion failure.

The guard now accepts only loopback hosts and ports 3306/13307 and requires the
configured database name to start with `codex_`. The test still creates and drops
only its generated `codex_v01_<uuid>` schema. It uses the fixture's configured
credentials; no production connection, production database or provider call is
introduced. This matches the existing migration/runtime fixture boundaries.

Local verification: `.venv-q01/Scripts/python.exe -m pytest
tests/test_embedding_profiles.py::test_mysql_budget_reservations_are_atomic_across_independent_services -q`
against the disposable MySQL 8.4.11 service on loopback port 13307: one passed,
exit 0. Focused Ruff and `git diff --check` passed. Independent D02 implementer,
who did not author this change, inspected the exact two-line diff: scoped
Approved, no findings.

A new exact-HEAD GitHub run is required to prove the CI service's port 3306 path.
The failed historical run is retained as evidence; it is not reported as green.
