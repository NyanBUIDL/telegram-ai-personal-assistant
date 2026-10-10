# Review: four CI negative-fixture dispositions

**Verdict: Approved** for the narrow metadata correction. No actionable code or security findings.

Reviewed the working-tree diff against HEAD: only `docs/release/secret-scan-dispositions.json` changes, adding four entries (28 lines). No scanner, CI gate, integration fixture, or product implementation changes; no path-wide or pattern-wide exemption.

## Provenance and exact binding

The four credential-URL candidates are explicit literals in `tests/test_ci_checks.py:186-189`, parameterizing `test_s03_fixture_refuses_unsafe_mysql_target_before_connecting`. They use the synthetic password `fixture` and intentionally supply, respectively, the wrong driver, an `example.invalid` host, an unapproved port, and a schema outside the disposable `codex_` prefix. The helper removes inherited MySQL fixture URLs and sets the selected literal explicitly. These values do not originate from a real account or credential store.

`tests/integration/test_backup_restore.py:244-268` validates the driver, loopback host, approved port, schema prefix, username, and password before engine creation or connection. Each reviewed literal fails one of those checks and skips before the later `FixtureStore` or database operations. The CI test checks the skip-only summary and absence of the URL in output.

Independently recomputed SHA-256 from each literal with the .NET SHA256 API. Each has exactly one matching new disposition and exactly one previously unclassified path/kind/hash finding in `.test-temp/wave-final-index-audit.json`:

| Negative fixture | SHA-256 |
| --- | --- |
| Wrong driver | `f17851529853917f7fdcdd06936ba553bbe374665ebb9fdf30ab8ebded1e4512` |
| Invalid external host | `25554411518e1ad0a984f21d8381c9bfed2e2d39217f3fa5605e79dcae375a0c` |
| Unapproved port | `f06e68c703e57fe7244f8fdaadaaccdac747b12b0aad26ad50938eb29b422702` |
| Non-disposable schema | `64fb1304b952751325f73f3067c637e3bf3e47d48a40b6afaebd34791e6b9152` |

All entries bind `tests/test_ci_checks.py`, `credential_url`, and the exact digest, retain an explanatory reason, and use the existing `synthetic_test_fixture` classification.

## Fail-closed behavior

`scripts/check-release-content.py:203-214` classifies only an exact path/kind/hash key with a recognized classification and nonempty reason. Unknown hashes, different paths, or different kinds remain unclassified. The main return condition still fails for unclassified findings other than configuration selectors, and `public_release_approved` remains false. Existing tests inspect exact-match versus changed-hash/changed-path behavior and an unknown placeholder-shaped tracked assignment that exits 1. This review read those tests and implementation; it did not execute tests.

## Process and verification boundary

The coordinator reported pushing `9ad10d58` before inspecting the final audit's exit 1. The saved audit independently shows these four unclassified findings. This correction accurately records their provenance; it does not retroactively make that earlier gate green. The coordinator must record and inspect the rerun audit and focused scanner/CI controls before the next push. Sandbox temporary-directory failures are separate from product failures and require an actual successful rerun, not reinterpretation as passing evidence.

Approval applies to the reviewed metadata diff only. Full-wave verification, Windows/VM release gates, external artifacts, and public-release authorization are outside this review. No tests, source, index, commits, or remote state were changed by this reviewer; only this report was written.


Focused scanner and CI controls:63 passed in26.80s outside the filesystem sandbox, with an isolated workspace temporary directory. An earlier sandbox run had30 passed33 setup errors because pytest could not create its numbered temporary directory; it is not product RED. Final audit remains the gate before the next push.
