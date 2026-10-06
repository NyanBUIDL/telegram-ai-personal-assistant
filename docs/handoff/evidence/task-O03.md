# O03 — Isolated native Telegram login producer

<!-- five-current -->
## Integrated acceptance — 07/10/2026

Current status: **InReview**. The coordinator's [integrated review](review-five-completion.md) and [execution manifest](five-completion.json) supersede historical code-integration Pending statements below. SQLite-only source follows the directly approved amendment. Live accounts/provider/downloads, physical Windows/installer, licensing, human UAT and soak remain Pending; this is not beta-ready or publication. O04 bot setup and U03 first-answer UX remain separate roadmap work.
<!-- /five-current -->



2026-10-06: owned implementation and focused verification are available for independent root review. **Activation integration, encrypted runtime publisher, O01 trusted evidence bridge, physical Windows/live account QA and whole-task acceptance are Pending.** This is not TaskVerified/Done/Ready, release approval or an account-authentication claim. No Git writes, real Telegram authentication, real credentials/secret-store writes, model downloads or external release occurred.

The default service uses installed Telethon 1.45.0 APIs with MemorySession; tests substitute only external transport and credential/session publication. It never opens or writes account.session/account.session.enc. API ID/hash remain required for QR. Native QR wait starts before display, has bounded expiry, explicit refresh and phone/OTP/2FA fallback. Three invalid code/password attempts terminate a candidate; FloodWait returns a countdown without automatic sleep/retry. All connect/request/wait/disconnect operations have deadlines; cancellation interrupts pending IO and is terminal for this service instance. New dialogs need fresh services.

Owner identity comes from is_user_authorized plus get_me; positive integer, non-bot/non-deleted identity is required. The service checks actual current SID and exact ownership marker/profile plus real MaintenanceService admission around publication/checks; an existing verified owner cannot silently change. Missing publisher or current-owner adapter cannot activate a session. Revocation/restart rechecks actual authorization/get_me and runtime session identity. Native progress is not an O01 completion or pairing capability. Active progress expires after 30 seconds; authoritative downstream health must call check_session through the runtime bridge.

No OTP/password/QR/phone is persisted, returned in LoginStatus, sent to browser/model/log or provided to on_changed. QR URI is native-only (repr suppressed) and renders through qrcode matrix plus QImage/QPainter, without Pillow/files. Saved inputs are blank and consumed fields clear immediately. Telethon's derived loggers use an isolated silent logger. Unknown errors produce fixed codes only. The dialog uses immutable ArtPanel/theme/font roles, 44-logical-pixel controls, modal focus/Tab/ESC restoration and one dedicated background event loop.

Final focused command in repository cwd (approved outside sandbox because sandbox asyncio/socketpair creation stalled):

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:O03_ART_EVIDENCE_DIR = 'docs/handoff/evidence/o03'
$env:ART_EVIDENCE_DIR = 'docs/handoff/evidence/o03/theme'
$env:QT_SCALE_FACTOR = '1'
.venv-q01/Scripts/python.exe -m pytest tests/test_telegram_login.py tests/test_security_memory.py tests/test_sync.py tests/test_desktop_theme.py -v --basetemp=.superpowers/sdd/windows-public-beta-2026-10-02/temp-O03-final > .superpowers/sdd/windows-public-beta-2026-10-02/O03-final.txt 2>&1
$o03Exit = $LASTEXITCODE
Get-Content .superpowers/sdd/windows-public-beta-2026-10-02/O03-final.txt -Tail 28
exit $o03Exit
```

Exit 0: **48 passed in 4.67s** (33 O03, 4 security/memory, 10 sync, 1 existing native theme). No skips, no broad/full suite. Ruff for the three owned Python files: exit 0, All checks passed. Separate real Qt offscreen art/focus/geometry processes at QT_SCALE_FACTOR 1.25, 1.5, 2 each passed (1 passed, 32 deselected; 0.61/0.60/0.69s). These establish logical scale evidence, not physical DPI or live QA. Native QR rendering/fallback tested with synthetic token only; screenshots contain idle blank fields and no login QR/token/canary. Screenshots show the viewport after keyboard navigation, which may be vertically scrolled.

RED preceded behavior implementation: a minimal callable service interface yielded 21 behavior failures (not import failures), then corrected restart/native-UI cases yielded 4 behavior failures. Subsequent negative cases reproduced Telethon child-logger leakage and queued login restarting after cancellation (2 failed, 29 passed), both fixed. Interactive QR-to-phone fallback caught the consumed API hash with uncleared API ID (1 failed, 46 passed); clearing consumed API ID/hash together fixed it. Final code/security checks above are fresh.

Environment/process evidence: initial pytest temp creation failed with 21 errors (not RED); a workspace-basetemp sandbox run stalled at collection and was terminated. All actual behavior runs used authorized synthetic-only escalation. Initial sandbox pip DNS failure exited 1; the authorized escalated `pip install --disable-pip-version-check --retries 0 qrcode==8.2` succeeded. One planned green run was not executed because automatic approval review hit usage limit (review unavailable, not unsafe); retry after coordinator/user continue and reset succeeded. Early restart/UI RED contained a NameError and missing resume method; these harness gaps were corrected before the recorded 4 behavior RED failures.

Scope mistake: the earlier 47-case green run invoked existing test_desktop_theme without ART_EVIDENCE_DIR and may have refreshed its default `docs/handoff/evidence/u01/after/native-dialog-scale-1.png`. Root was informed and owns comparing/restoring that artifact; O03 did not edit/restore it and makes no new U01 claim. Final art runs explicitly write only O03-owned destinations.

Native QR dependency: coordinator explicitly authorized one desktop dependency line, `qrcode==8.2`, plus install into repository/.venv-q01. Before pyproject SHA256 `0bd6b29ac83de246fb8b4ca7715f878044814c4a99f7cfb88dd7452e7d5010de`; removing exactly that line from the current file reconstructs the original byte hash. No other dependency/package policy change. PyPI current release supports Python >=3.9,<4; installed license is BSD (project BSD-3-Clause), license SHA256 `40dfb903c94ee3f789500131311186548ecba5cefd3557623d7700e2522ab994`. Existing colorama was already present. Distribution notices and font/Qt release licenses remain the coordinator's release gate.

| Owned source | SHA256 |
|---|---|
| `src/tg_assistant/services/telegram_login.py` | `4d4a39e7c49cda7637eedb841c9fd1228e1d3d6a3adcc859392c755950595734` |
| `src/tg_assistant/desktop/dialogs/telegram.py` | `832c3d0fa4f33fb75db798924302f065eab468e267fd248b6a74947c6da2f725` |
| `tests/test_telegram_login.py` | `a8491c28dff1c6fe429f8dcf2fa2042af6c884c4209f7e62e3e581b872a1b06c` |
| `pyproject.toml` | `a62a4a20f43b1f49d2635d1f08a20c27924ceb37d593fa3517d5a3e26b154477` |

| Owned idle art artifact | SHA256 |
|---|---|
| `docs/handoff/evidence/o03/telegram-idle-scale-1.25.png` | `80d8d0882c629a8a4b5be15cf173c02f9bece87765cf59050d54287d5ea15b23` |
| `docs/handoff/evidence/o03/telegram-idle-scale-1.5.png` | `21d6f013a8c11c646d749535e05e86dbe02ddc10a6cd2d3689af67ae8fb8be87` |
| `docs/handoff/evidence/o03/telegram-idle-scale-1.png` | `cf813cdfc1fa6eeb81227f4c9b04b9aabd72b47452595ac5075e6c04b8c258e9` |
| `docs/handoff/evidence/o03/telegram-idle-scale-2.png` | `bae18169fd79d59f00368d0229e4d2a9ae9546847265ea333f18d594feab3c50` |
| `docs/handoff/evidence/o03/theme/native-dialog-scale-1.png` | `80779ef39bd83b85caff7a82116fd1f4635b4983db494656646f6304d593618a` |

Primary source verification:
- [Telegram API ID/hash and manual application registration](https://core.telegram.org/api/obtaining_api_id)
- [Official API application page (static native help link)](https://my.telegram.org/apps)
- [Telethon auth/QR/phone APIs](https://docs.telethon.dev/en/stable/modules/client.html)
- [Telethon QRLogin expiry/recreate/wait](https://docs.telethon.dev/en/stable/modules/custom.html#telethon.tl.custom.qrlogin.QRLogin)
- [qrcode 8.2 release/compatibility](https://pypi.org/project/qrcode/8.2/)
- [qrcode matrix API implementation](https://github.com/lincolnloop/python-qrcode/blob/main/qrcode/main.py)
- [qrcode production license declaration](https://github.com/lincolnloop/python-qrcode/blob/main/pyproject.toml)

Stable web docs displayed Telethon 1.44.0; installed 1.45.0 signatures/source were inspected directly (TelegramClient, sign_in(phone,code,*,password,phone_code_hash), send_code_request(phone), qr_login(), QRLogin.wait(timeout), recreate). No start() interactive input path is used. Spec paragraphs 46–55 and ownership/native-secret/DTO/maintenance/art constraints informed this implementation.


## O03 F1 scoped repair — 2026-10-06

Independent isolated review requested correction of local OTP/2FA input validation. The reviewed `_Denied("code_invalid")` / `_Denied("password_invalid")` paths disposed a valid challenge. Local empty/malformed OTP or empty/oversized 2FA now returns the current code/password step with a distinct fixed field-validation code and actionable Vietnamese message. The candidate, phone-code hash and remaining server retry count are preserved; local rejection makes no Telegram request and causes no resend/disposal/persistence. Correct input can finish the same challenge. SID/cancellation binding is still checked before local validation; server invalid credentials remain bounded, and expired codes, FloodWait, cancelled candidate and failed bindings retain their original semantics. No runtime writer/activation interface changed.

New service and real offscreen native-dialog regressions first yielded **15 failed, 33 deselected** on the reviewed code (exit 1, expected step-preservation failures), then the focused command below passed **63 tests in 7.02s** (48 O03, 4 security/memory, 10 sync, 1 native theme; exit 0). Ruff on the three owned Python files passes. All authentication uses synthetic transports and fake publication/credential storage; no actual account/credential access, Git writes, full suite, model download or release. The service must preserve the challenge even for callers that bypass UI input validation.

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:O03_ART_EVIDENCE_DIR = 'docs/handoff/evidence/o03/fix1'
$env:ART_EVIDENCE_DIR = 'docs/handoff/evidence/o03/fix1/theme'
$env:QT_SCALE_FACTOR = '1'
.venv-q01/Scripts/python.exe -m pytest tests/test_telegram_login.py tests/test_security_memory.py tests/test_sync.py tests/test_desktop_theme.py -v --basetemp=.superpowers/sdd/windows-public-beta-2026-10-02/temp-O03-fix1-green > .superpowers/sdd/windows-public-beta-2026-10-02/O03-fix1-green.txt 2>&1
$o03Exit = $LASTEXITCODE
Get-Content .superpowers/sdd/windows-public-beta-2026-10-02/O03-fix1-green.txt -Tail 24
exit $o03Exit
```

The preceding source table/48-pass log are historical first-packet evidence. Current repaired source hashes:

| Path | Reviewed before SHA256 | Fix1 SHA256 |
|---|---|---|
| `src/tg_assistant/services/telegram_login.py` | `4d4a39e7c49cda7637eedb841c9fd1228e1d3d6a3adcc859392c755950595734` | `3b18fbc23f94a4a93b0f93a18f4922df9949ec88ec03f2f0c39a735206de509e` |
| `src/tg_assistant/desktop/dialogs/telegram.py` | `832c3d0fa4f33fb75db798924302f065eab468e267fd248b6a74947c6da2f725` | `005cea3a89a295ba81e0fd18331b87e717fb3f75361c0c3fe187d464b89f4538` |
| `tests/test_telegram_login.py` | `a8491c28dff1c6fe429f8dcf2fa2042af6c884c4209f7e62e3e581b872a1b06c` | `91852dcbc5e6d6b678296b34070731aabd961c09a2d2e359dcbe06638e888915` |

Original reviewed packet/manifest and every listed before-byte source/evidence are preserved under `.superpowers/sdd/windows-public-beta-2026-10-02/O03-fix1-before/`. Fix1 review files are `task-O03-fix1-report.md`, `review-O03-fix1.diff.md` and `review-O03-fix1.manifest.json`. Current dependency/initial owned art bytes remain unchanged. The final check saves only idle token-free native screenshots under the O03/fix1 destination. No new physical-DPI/live QA claim. Runtime encrypted publication/writer ownership, trusted current-owner/O01 activation producer bridge, native factory integration, independent scoped review and Windows acceptance remain **Pending**; no whole-task Verified/Done/Ready or release approval.

## Subsequent isolated publication acceptance — 2026-10-06

The concrete encrypted runtime publisher has now passed independent spec and quality review. Its final **156 cases passed with zero skips** on actual migrated SQLite and disposable MySQL in 39.95s. It retains a real per-SID instance guard, uses the inspected Telethon 1.45.0 SQLite format only in RAM, journals encrypted cross-store publication, reconciles uncertain SQL commits through a fresh connection, preserves prior ciphertext/API credentials on rollback, refuses a different owner and missing/corrupt key material, and preserves pairing only for the same existing owner. Synthetic crash/fault controls are not physical power-loss proof.

This supersedes only the earlier isolated-publisher Pending statement. Source SHA256 `fc46ea88f54f23c390a3f95d3bc9cdabf14fe592c011ad928df9ebe0e0cda3f3`; test SHA256 `9eb0d97d7fbac6c298dbedbec0ee7deefe76733c1cdb51849f2bacddbd5087ab`. Native graceful worker handoff/factory, actual SDK resume verification, owning O01 observation relay and activation, bot pairing, combined regression/CI and real Windows/live acceptance remain Pending. Whole O03 remains InProgress.

## Native context and owning runtime observation producer — 2026-10-06

The native factory/session context and setup-worker read-only borrowed-guard probe are implemented with actual selected migrated storage and retained SID InstanceGuard ownership. Private SDK authorization/get_me observations feed an original-age 30-second health/evidence reader; O01 callbacks perform read-only durable identity checks without nested SQL write locks or journal recovery. Closing a verified shared native dialog preserves its context until O01 completion; cancellation/shutdown drains the SDK before native guard release, and timeout retains ownership until a later drain succeeds. Worker borrowed guards cannot publish/reconcile and are never released/adopted by probes. Saved API inputs remain blank; no OTP/password/QR is resumed or persisted.

The active assistant runtime uses a separate RuntimeTelegramObservation over its already-owned UserClientAdapter on that client's loop. It checks actual authorization/get_me, real positive owner agreement, active account/publication marker/credentials/decrypted ciphertext/live session identity, SID/profile/actual guard and selected storage. It does not construct/connect/close another client, mutate session files or weaken the publisher's plaintext-working-session refusal. Its close clears observation authority only. Native and runtime StageVerification fingerprints match the same committed publication; stale or revoked evidence cannot appear Ready.

New context freeze: **42 passed**, SQLite plus owned disposable MySQL, zero skips, 28.14s. Runtime bridge RED demonstrated **9 assertion failures** before implementation; current bridge+context GREEN is **60 passed**, zero skips, 22.78s. Earlier focused combined publication/login/security/sync run was **252 passed**, zero skips, 86.00s; later additions and final scoped freeze are detailed in `.superpowers/sdd/windows-public-beta-2026-10-02/task-O03-completion-report.md`. Root worker/controller/native registration/glue review and combined CI remain separate requirements. Live/human Telegram, physical Windows install/upgrade/ACL/power-loss/DPI/focus/UAT, O04 pairing and public-beta acceptance remain Pending; no whole-task Verified/Done/Ready or release approval.

Final producer scoped freeze after the existing-runtime addition: **278 passed, zero skips, exit0 in67.71s** across both actual backends (18existing-runtime+42native-context+156encrypted-publication+48login+4security/memory+10sync); final Ruff and new-file formatting pass. Raw `O03-completion-final.txt` SHA256 `879cd39524d970b6f67e8b3ce59bdce98dc6ea013f743c61818aa1fd20a277e8`. Current context SHA256 `74434387aea8fb0cde994ca2b878a6380e3d5be95fca9174d141622c7ba0416b`. This supersedes the earlier scoped-run counts and covers all current O03 producer changes, while root consumer/CI and physical/live/release gates remain independently Pending.
