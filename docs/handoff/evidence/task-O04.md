# O04 — Bot connection and verified owner pairing

Current status: **InReview**, 08/10/2026. Dependency O03 is Verified on product commit `5f56dda48a7cb170b4e1d704fc0b130a644ed853` and its successful exact-candidate hosted CI. O04 production code is integrated and independently reviewed; corrected full local regression passed, while exact-candidate hosted CI remains pending. This is not completed acceptance or a release.

Implementation is split across the durable SQLite pairing repository, scoped native bot credentials/actual Bot API transport, and an opaque borrow of the current verified Telegram account context. The coordinator owns the connecting service, restricted pairing event handler, native dialog, worker/runtime management admission and final integration. Tokens remain native write-only inputs; the first bot sender never becomes the owner. The Start challenge remains hash-only, one-use and 300 seconds, with the same atomic manual-code fallback.

The production repository, scoped credentials, installed aiogram HTTP adapter, native bot dialog, native handoff and paired runtime now share the actual account owner and lifecycle. The native dialog keeps tokens write-only and displays a verified bot identity, fixed BotFather guide, QR/Start invitation, manual fallback and expiry/cancel controls. Durable SQL binds the already verified owner; a stranger cannot claim ownership. No second account client, runner or poller is created by the native borrower.

Current code denies management unless durable pairing, the current account candidate and fresh account/getMe/getUpdates observations agree. Checks cover dashboard tickets and requests, bot messages/callbacks/outbound calls, account handlers and mutations, source/model submission and SQLite writes. The runtime keeps long handlers alive through actual same-offset health polling, acknowledges updates only after completed dispatch, and retains account/profile ownership until bot tasks and SDK sessions actually drain.

## Review fixes and local evidence

Independent review reproduced and closed stale post-SQL bot proof, missing final reply admission, same-client account auth/DC mutation, authority loss while a request body is awaited, SQL listener ordering after awaited job/source checks, and worker cancellation releasing an uncertain runtime owner. Tests use actual migrated SQLite, native OS guards and installed Bot API SDK against disposable loopback HTTP. Account SDK and credential-store doubles remain explicitly synthetic.

| Current scoped check | Actual result | Qualification |
|---|---|---|
| Pairing repository | 93 passed | Atomic consumption, deadlines, owner/candidate/profile/fence binding and publication history |
| Latest transport and credentials | 103 passed | Includes borrowed SDK error sanitization and definitively failed session cleanup retry |
| Latest connection service | 43 passed | Includes fresh reply admission and post-SQL proof revalidation |
| Binding and runtime context | 36 passed | 21 native binding + 15 runtime context; includes actual measured health while a handler remains pending |
| Original runtime observer | 9 passed | Existing account observation regressions retained |
| Root runtime/gateway integration | 30 passed | Includes actual UVicorn and InstanceGuard retention during worker cancellation |
| Latest DB/job/policy slice | 61 passed | Final private admission after actual job/source SQL; rollback retained |
| Native setup plus amended service races | 6 passed | One actual coordinator journey through owner_paired and five review race cases |
| Corrected existing vector recovery fixtures | 81 passed | Original owner/source/model/generation assertions retained; synthetic private composition is not native Telegram proof |
| Corrected existing embedding cleanup fixture | 39 passed | Initialize the missing bot_runtime lifecycle field; actual HTTP transport closure and no-call assertions retained |
| Native bot dialog and theme scale | 19 passed at each of 1.25, 1.5, 2 | Logical offscreen scale; physical Windows DPI remains pending |
| Native-to-browser regressions | 8 passed in Chrome | Real Qt/worker/ticket fixtures, bot availability, all three HTML newline variants |
| Dashboard build/lint and Node checks | Build/lint exit 0; 36 Node passed | Build used installed Vite directly because bundled Node has no npm.cmd |
| Whole Python lint, contracts and art-token drift | Exit 0 | No public DTO or approved art asset change |
| Complete corrected Python suite | 1,543 passed, one skipped, zero failures/errors; exit 0 | 611.69 seconds; existing local foreign-owner ACL privilege limitation |

These suites overlap and are not added together. The [review manifest](o04-review.json) records current source hashes, available count-only report hashes, representative commands and separately identified coordinator tool results for build/browser/Node checks. Local portable Chromium was missing; that environment failure is retained separately from the successful Chrome run. Hosted CI retains its mandatory installed-Chromium gate. A first native integration test had an incorrect teardown expectation about an already closed HTTP session; its failure is retained, and the actual closed-session assertion passed afterward. The initial full-suite run was interrupted while that earlier test version was loaded; no successful full-suite result is claimed from it.

The completed full baseline returned **1,531 passed, 12 failed, one skipped, two teardown errors, exit 1**. All failures were reproduced in existing synthetic Application.__new__ fixtures: eleven vector startup cases omitted the new private composition and stopped the runtime before callback assertions; one embedding cleanup case omitted bot_runtime. Fixture-only corrections preserve the original assertions and passed the entire vector and embedding files, 81 and 39 cases respectively. The fresh full suite on the corrected frozen candidate returned **1,543 passed, one skipped, zero failures/errors, exit 0 in 611.69 seconds**. The local skip was independently identified as the existing foreign-owner ACL fixture: the actual token cannot create an Administrators-owned disposable file. No Windows privileges were enabled; its confirming boundary slice returned 107 passed, one skipped, exit 0. Hosted results must state their own actual skip counts.

The actual native coordinator integration reaches only owner_paired. SOURCE_SELECTED, FIRST_ANSWER and READY remain unearned, with no automatic source permission grant. The accepted five-task manifest remains a historical snapshot of product commit 5f56, not evidence for these new O04 source bytes.

## Remaining acceptance

Final exact-candidate hosted CI and coordinator acceptance remain pending. Live Telegram Start/revocation/conflicting-poller checks, human Credential Manager use, physical Windows DPI/interaction, installer/clean Windows QA, font redistribution rights, human UAT and soak remain separately pending until actually performed. Canonical progress remains 18/26 Verified; beta_ready and published remain false.
