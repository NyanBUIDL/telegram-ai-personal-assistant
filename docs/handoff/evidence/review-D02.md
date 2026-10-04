# D02 — scoped independent follow-up review

Verdict: **Approved for scoped D02 integration.** The earlier Minor restart-replay finding and subsequent Important ACL mutation-order finding are **ADDRESSED**. No new Critical or Important findings in the reviewed fixes. This is an implementation review, not a public-beta readiness claim.

## Findings and dispositions

- Initial native-boundary review: approved. Actual SID-restricted named pipe, peer SID/server PID checks, bounded allowlisted commands, locked hash-only ticket consumption, strict desktop Host/Origin/CSRF boundary, nullable setup owner, verified runtime attachment and authority invalidation were reviewed.
- Minor advanced recovery-code lifetime gap: a focused constructor probe showed first use accepted, same-instance replay denied, and recreated-instance replay accepted. The coordinator required consumption to survive standalone API restart. The follow-up introduced `LegacyCodeReplayStore`, supplied by the actual Admin app factory, with profile/SID/secret-fingerprint binding, a consumed-period high-water mark, bounded cross-process locking, fsync and atomic replacement. Invalid, unavailable or foreign-bound state fails closed. Disposition: **ADDRESSED**.
- Important ACL mutation order: the first fix secured the existing configuration directory before validating descendants, allowing inheritable DACL propagation to affect a hardlink alias or foreign child before refusal. Round two validates the entire existing tree through `secure_tree` before applying inheritable DACLs; fresh directories retain `secure_directory`. Parent ownership/reparse checks remain. Replay filenames, binding and consumed-period state remain unchanged. Disposition: **ADDRESSED**.
- Initial packet manifest hashes represented LF-normalized text rather than actual CRLF file bytes. Content matched after normalization. The record was corrected transparently, preserving the original manifest byte copy and distinguishing canonical and actual-byte hashes. Round-two actual-byte checks passed.

## Immutable reviewed packet

Packet: `.test-temp/d02-replay-review-round2/followup.patch`, `manifest.json`, and before/after snapshots of `src/tg_assistant/admin_api/auth.py` and `tests/test_legacy_dashboard_code_replay.py`.

Patch actual-byte and canonical UTF-8 LF SHA256:

`7fd220aaaf22e8a3b3a3c08ba40aa70980435edb3af486ee8bd919ccf51d4560`

The reviewer independently checked the patch and all four snapshot actual-byte hashes against the round-two manifest; all matched. The scoped production change is the existing-directory validation order. The review also inspected the shared `secure_tree` helper's full preflight before ACL mutation. No new Critical or Important fix breakage was found.

## Evidence and remaining limitation

- Supplied focused Python result: **35 passed, 1 skipped, 0 errors**, 16.28 seconds. This includes prior restart/race coverage. The actual Windows hardlink regression verifies refusal without changing outside/configuration SDDL, file content or creating the replay lock.
- The local foreign-owner fixture was honestly skipped when the token could not create a disposable Administrators-owned file (Windows errors 5/1307; the test also recognizes 1314). No privilege enabling, ownership takeover, account creation or workaround was used. **Actual foreign-owner rejection evidence remains Pending on the remote runner.** The source preflight was reviewed; a skipped fixture does not prove execution of that negative case.
- Earlier focused persistence evidence supplied for round one: **34 passed**, including actual Admin app recreation/restart and six processes racing for exactly one successful consumption.
- Coordinator subsequently confirmed the actual CI-channel Chromium browser run: **5 passed**, 41.0 seconds. This is coordinator evidence, not an independent reviewer rerun; it supplements the earlier local Chrome result.

No broad tests or focused tests were rerun during the round-two re-review. No source edits, staging, commits, or child agents were performed by this reviewer. This document was persisted afterward at the coordinator's explicit request. Full integrated regression and release gates remain coordinator-owned.


# D02 — scoped Qt test-harness review

Verdict: **Approved.** The change accurately exercises the launcher's Qt event-loop behavior without extending deadlines or weakening the native dashboard assertions. No Critical or Important findings in this fix.

## Reviewed scope and immutable evidence

Only `tests/test_dashboard_launch_ticket.py` changes. Reviewed packet: `.test-temp/d02-qt-yield-review/followup.patch`, `manifest.json`, exact before/after snapshots, unchanged production `app.py` snapshot, diagnostic script/log and ordered test evidence.

Patch exact-byte SHA256: `48da755799bbf41dc0c8dd32de1611521aa48711375abced83e04a2ac9c80e2f`.

The reviewer independently verified every packet file listed in the manifest against its exact-byte SHA256; all matched. Current production `src/tg_assistant/desktop/app.py` also matched the unchanged snapshot hash `fa135d5cfcc65c66e5d7db0c398c6cecbecfc2d02f111d32506b68f27c17c11a`. No production change is present in the scoped patch.

## Assessment

The frozen heartbeat diagnostic measured Python worker progress over approximately 0.5 seconds: `QTest.qWait` 8 iterations, nested `QEventLoop.exec()` 321, actual `QApplication.exec()` 340, and `QTest.qWait` again 8. This supports the observed harness starvation in this Windows/PySide runtime and shows that the nested loop behaves substantially like the application's real event loop.

The helper at test lines 219–236 runs a nested event loop while the existing launcher timer and real background IPC future progress. It does not invoke the launcher polling method directly, replace IPC, complete the future artificially, or fabricate a browser result. The application button still receives an actual `QTest.mouseClick`; the added `QSignalSpy` assertion requires exactly one clicked signal.

Browser-open deadlines remain **5000 ms for the button** and **3000 ms for the hidden tray**. The final tests retain exactly-one-open assertions, the actual occupied-port fallback and measured URL, native ticket acquisition, real HTTP redemption, setup-only authority and replay rejection. The tray test retains the assertion that closing to tray hides the launcher and stops its timer before triggering the actual dashboard action.

Supplied ordered evidence first reproduced the tray failure with 25 passed/1 failed after the button-only wait change. After applying the same event-loop wait to both cases, the frozen ordered result is **26 passed, 0 failed, 0 skipped, 0 errors, exit 0**. This is supplied run evidence; the reviewer did not rerun tests.

## Limits and disposition

This approval concerns the minimal test-harness fix. It does not turn the earlier whole-suite failure into a full-suite green claim. Final integrated verification, author freeze confirmation, staging and integration remain coordinator-owned. Earlier D02 restart-replay and ACL findings were already addressed and were not re-audited here; the foreign-owner remote evidence limitation remains recorded in `task-D02-fix2-review.md`.

No broad or focused tests were rerun, no production files were edited, and no staging, commit or child agents were used. The reviewer wrote this requested private review report only.


# D02 — scoped visual CI correction review

Verdict: **Approved.** No Critical or Important findings in this correction. The visual tests now match the approved native-launcher authentication UI without restoring browser code/secret entry or weakening native authentication.

## Scope and verified packet

Only `dashboard-prototype/tests/visual/art-baseline.spec.js` changes. Reviewed packet: `.test-temp/d02-visual-review/followup.patch`, final `manifest.json`, exact before/after test snapshots, source-hash record, commands, RED/GREEN/build logs, four login screenshots and authentication rerun logs.

Patch exact-byte SHA256: `2c293eb32359949ced0c8bf174ad41ecce9d5efe6fa2455721d10de74a896f47`.

Final manifest exact-byte SHA256: `9abbc5117167553a6e93219b3ca9b5bd65ecbd5b52f4ed013a6d560dd97a83e2`.

After the initial packet notification, the author completed the manifest by appending four screenshot and two authentication-log entries. The production sources, test snapshots and patch remained unchanged. The reviewer verified the completed manifest, including those added entries: all listed exact-byte hashes matched. Current App.jsx, styles.css, d02-auth.spec.js and auth-bootstrap.js also matched the unchanged-source hashes.

## Assessment

The four login-reference cases previously required the obsolete browser `Mã đăng nhập` field. They now require the native-launcher heading, actual “Mở dashboard” instructions, single-use/30-second lifetime copy, and the prohibition on entering API keys, OTP or passwords in the browser. They explicitly require no input, textarea or select controls.

All four widths (360/390/1280/1440) and existing screenshot filenames remain. The change preserves border/shadow/overflow and 44-pixel control checks, and adds explicit paper/panel/ink colors, square corners, main-title/logo Peter Obscure, other-copy/control Darley Sans, actual loaded fonts, visible keyboard focus, and Enter-triggered session retry with the native-login screen still present afterward. Narrowing the control loop to buttons matches the intended removal of browser input fields; the new zero-input assertion guards that removal.

The component geometry/typography/toggle tests, modal focus trap/Escape/focus-restoration test, and the two expected-failure JavaScript/console-error negative controls are retained. No test is dropped. The isolated visual fixture continues to deny operational API requests; it does not fabricate owner authentication. Native ticket and real-browser security coverage remain in the unchanged authentication spec.

## Evidence and limits

- Supplied actual RED run: all **four** login-reference cases fail on the removed code-field label. The prior hosted run likewise reported those four failures with seven other cases passing.
- Completed cached-Chromium visual run: **11 passed**, 33.1 seconds. This total includes the two intentionally expected-failure browser-error controls, which demonstrate that injected page/console errors are rejected.
- Unchanged real-authentication rerun: **5 passed**, 44.7 seconds, covering early fragment removal/protected setup session, fresh-context replay denial, real expiry, foreign Host/Origin and HTTP mint refusal, and malformed/duplicate fragment refusal.
- The earlier authentication attempt failed before fixture readiness with `Synthetic native fixture did not reply`; four cases did not run. This boot/setup failure is preserved in the packet and is not treated as a product-security RED result or omitted from the evidence.

The reviewer assessed the immutable source and supplied execution evidence without rerunning tests. This scoped approval is not a full-suite or release-readiness claim. Other D02 findings were already addressed and were not re-audited. Final author freeze and integration remain coordinator-owned.

No production/source edits, staging, commits, broad tests or child agents were performed by this reviewer; only this explicitly requested private review report was written.
