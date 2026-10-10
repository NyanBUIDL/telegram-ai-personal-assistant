# V01 — independent embeddings and atomic AI budgets

Implementation and composed source review are complete. Final integration/commit evidence is recorded below; this does not establish installer, real account, public release or beta readiness.

Chat and embedding providers are configured independently. Provider/endpoint/model/version/dimension bind the canonical store identity. Unknown previous corpora are preserved and refused rather than silently promoted. Cloud submissions require consent, current source policy and permission epochs, current worker lease, and active store/intent checks before HTTP, indexing and delivery. LOCAL ONLY sources do not enter shared cloud retrieval/embedding inputs. SDK automatic retries are disabled for uncertain external submissions; durable intents and reservations prevent blind resubmission.

Migration0008 adds exact-decimal reservation accounting and embedding endpoint identity for SQLite and MySQL. The per-profile budget transaction serializes admission and includes pending/submitted/uncertain work. Pricing snapshots are versioned and expire closed; local embeddings and local response-cache delivery have zero provider cost while still observing token caps. Cache admission, linked settled reservation/usage and hit count commit atomically, followed by another source authorization check before delivery.

The coordinator reviewed the author's immutable19-file snapshot and repaired cross-source deduplication and an unclosed independent embedding HTTP client. A separate reviewer identified missing source attribution for direct/routed answers and single-source query embeddings, plus cache delivery bypassing token admission. Those findings were reproduced and repaired. Identical eligible content remains indexed separately in each authorized source; same-source duplicates remain filtered. Known source requests are attributed to their actual chat for group limits.

Independent reviewers accepted the composed original snapshot and both frozen root fix increments with no remaining Critical/Important findings in V01. D01 runtime/CLI lifecycle changes are separately reviewed and excluded from this acceptance. Review reports are retained in the execution ledger: `V01-independent-acceptance-review.md`, `V01-independent-fix2-acceptance.md`, and the q01 scoped root-fix1 review message.

| Actual verification | Result |
| --- | --- |
| Author final focused runtime/provider/knowledge/revocation checks |133passed,0skipped, exit0; `final-intents-green.log` |
| Root duplicate/client-close regressions, SQLite+MySQL |6product failures plus2 controls RED, then8passed GREEN |
| Direct/routed answer and query embedding group attribution |10failed RED, then10passed GREEN, both dialects |
| Cache admission five token caps, zero-USD/no-live-pricing control and two concurrent hits |14failed RED; combined attribution/cache24passed GREEN |
| BLOCK after cache admission |2passed; no unauthorized answer delivered |
| Installed Python regression excluding new desktop modules |591passed,10intentional skips, exit0 in321.05s; `integrated.log`/`integrated-junit.xml` |
| Embedding client closure after D01 protected-cleanup integration |2passed, both dialects |
| CI omitted-runtime/selector negative controls |2failed RED; full helper suite10passed GREEN |
| Python lint, generated public contracts and art token drift |Passed |

Test logs are under `.test-temp/v01/`; immutable snapshots and review manifests are under `.superpowers/sdd/windows-public-beta-2026-10-02/`. Disposable MySQL8.4.11 runs only on127.0.0.1:13307 with synthetic test credentials and guarded `codex_` schemas. Tests use synthetic messages and HTTP MockTransport, never a real account, vault content or paid provider call. An initial unavailable MySQL fixture was an environment failure and excluded from product RED. An initial cache revocation control expected the wrong exception class; the real authorization rejection was preserved and the assertion corrected.

CI now requires the runtime embedding/budget module in addition to schema, revocation, job concurrency, embedding-profile and budget-migration MySQL cases. Missing modules or skipped required cases fail the gate. Source/task verification remains separate from exact-head remote CI and G0–G6 release evidence.

Final strict six-module MySQL gate:108passed,0failed,0skipped,0errors, exit0 in241.49s, `.test-temp/v01/mysql-final-summary.json`. Independent q01 CI review accepted the selector/workflow increment. The V01-only runtime/CLI shared files prepared from the immutable producer snapshot and accepted indexing/answer/embedding-close fixes passed all74runtime cases with both dialects, exit0 (`prepared-regression.log`). This separate preparation keeps D01 lifecycle changes out of the V01 commit without altering the working tree.
