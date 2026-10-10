# SQLite-only integration — 06/10/2026

The owner directly approved SQLite-only and empty new installations. [The dated amendment](../../superpowers/specs/2026-10-06-sqlite-only-amendment.md) replaces advanced MySQL support; it does not authorize resetting an existing profile or touching a human database.

Implemented boundaries reject unsupported saved/init/environment/URL/backend selections before connection factories, credentials or provisioning. Installation `.env` is no longer an implicit settings source. Old SQLite JSON config remains readable with inert legacy database fields. Product contracts are version 2 with SQLite-only StorageBackend; the frontend mirror is generated. Database/job/storage/setup/worker/CLI/backup use SQLite. SQLite WAL, foreign keys, busy timeout, compare-and-set claims, maintenance leases and restore security methods are preserved. Historical migrations and fixture DDL are unchanged.

Fresh native cloud choices remain unconfigured until the profile enrolls a key. A key explicitly enrolled for a provider in that bound profile can then be checked for chat and embeddings separately. Empty-key operations in an unconfigured profile cannot adopt/delete a global key. General Credential Manager namespace isolation and physical installer verification remain Pending; these native boundary tests do not certify every consumer or an installer artifact.

Observed local execution using installed Python 3.12.14:

| Scope | Evidence |
|---|---|
| New entrypoint boundaries + contracts | 84 passed / 5.17s; actual executing-tool exit 0 |
| Database/jobs + actual SQLite WAL/FK | corrected RED 4 failed, 1 passed; GREEN 5 passed; actual exits 1 and 0 |
| Backup unsupported backend/URL | RED 2 failed, 5 passed; GREEN 7 passed / 0.31s; actual exits 1 and 0 |
| CI count/error/collection/artifact handling | 7 passed / 2.28s; actual exit 0 |
| Cross-role enrollment + fresh cloud negatives + provider context | RED 2 failed / 1.08s, actual exit 1; GREEN output 36 passed, 1 native launcher deselected / 10.66s. The execution session became unavailable after app resume, so its actual final process exit is not claimed. Full fresh gate is required. |
| Dashboard | build + Sites preparation + lint exit 0; 25 Node cases exit 0; onboarding 15 / art 11 / real native-browser 8 cases, actual exits 0 |

Final installed full Python run: **1,117 passed, one skipped, zero failures/errors, 372.80s; actual process exit 0**, independently observed through the executing tool and a separate shell exit record. The skipped foreign-owner file fixture does not enable Windows privileges to manufacture a result. The earlier full run with two fixture prerequisite failures remains failed evidence; the corrected run replaces its acceptance result.

No production credentials, Telegram accounts, paid provider calls, downloads, human database changes, global keychain deletion or installer reset occurred. MySQL historical results do not certify this snapshot. Hosted exact-commit CI is still being collected in the five-task integration record; task/gate promotion waits for it.
