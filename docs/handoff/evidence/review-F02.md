# F02 — migration review acceptance

Reviewed implementation: `f32812ca73239e033939e8bc75b9a933a6c6952c`.
Review fixes: `6d7a40f9` and `ce5619b4`. Review date: 2026-10-03.
Evidence: [task-F02.md](task-F02.md). Scoped status: Verified.

Independent review required two conservative-classifier fixes. SQLite table flags, key collations, partial/descending/expression indexes and version-table metadata now distinguish unsupported layouts. Actual MySQL column/table character metadata and complete key prefix/direction/expression/visibility checks likewise reject altered schemas while preserving legitimate inherited database defaults. Both fixes were accepted after focused re-review, with no remaining Critical/Important finding.

The final actual SQLite/MySQL8.4.11 migration matrix passed **71 cases with four intentional other-dialect skips** in79.38s. It includes historical upgrades, large/negative IDs, immutable initial schema, unversioned/expanded/partial repair, snapshots reopened/restored, unknown-schema refusal before mutation, actual revisions and downgrade. Focused RED/GREEN, command corrections and clean lint/format/diff evidence remain in the linked report.

This acceptance does not sign off live-profile writer fencing, storage/restore orchestration, native onboarding, package resources, clean Windows installation or public release. Those belong to the remaining tasks. Beta readiness remains false.
