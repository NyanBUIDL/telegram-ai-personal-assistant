# Windows public-beta implementation constraints

The approved design is `docs/superpowers/specs/2026-10-02-windows-public-beta-design.md`.
The execution ledger is `.superpowers/sdd/windows-public-beta-2026-10-02/`.
Work from this real Git checkout and its reviewed feature branch, not an audit snapshot.
Preserve existing features; change modules by the assigned task. Do not directly merge
master, rewrite history, force-push, publicize the repository, or release external
artifacts without final review and explicit authorization.

## Product and storage

- V1 targets Windows 10/11 x64 with one active owner/profile per Windows SID. No SaaS,
  LAN dashboard, remote dashboard, or multi-account switch is in scope.
- The normal flow must work without installing Python, Node, MySQL, or using a
  terminal. Package the runtime, use PySide6 Qt Widgets and the default browser;
  do not add QtWebEngine.
- Default new installs to SQLite. Preserve existing/advanced MySQL configuration,
  drivers, and migrations; never silently migrate or switch the storage backend.
- Mutable data belongs under `%LOCALAPPDATA%/TelegramAIPersonalAssistant`, outside
  the install directory. Keep config, DB, Telegram session, vectors, logs and backups
  separate. Do not default the SQLite DB to a cloud-synced folder.
- Telegram is the primary assistant interface; the desktop dashboard is the
  secondary owner/operations console. Keep Vietnamese UX and owner-only/default deny.
- Learning means authorized sync, cleanup/deduplication, embedding and retrieval
  indexing. Do not claim automatic model fine-tuning or automatically ALLOW all chats.
- Preserve all 19 `PermissionName` permissions, all six group AI modes (`inherit`,
  `local_only`, `local_first`, `cloud_only`, `cloud_first`, `off`), confirmation and
  audit semantics, group `/ask`, retention/quota controls, and AUTO link deletion
  preview plus one-time owner confirmation.

## Credentials, identity and browser boundary

- API keys, bot tokens, Telegram API hash, OTP and 2FA are native write-only inputs
  for the current SID. Never send them through browser/bot/query/log/analytics or
  prefill saved secrets. Secret input lives only in native process memory and the
  approved credential store; no public DTO may add a secret field.
- Native IPC is SID-authenticated named pipe. Keep Admin HTTP on loopback with
  Host/Origin validation, HttpOnly/SameSite sessions and CSRF. No firewall opening.
- Dashboard tickets are random 256-bit values, TTL 30 seconds, one-use, profile and
  audience bound, atomically redeemed with hash-only storage. Deliver via URL
  fragment, clear it from history immediately, and redeem with a same-origin POST.
- Pairing nonces expire after 300 seconds, are one-use and bind the already verified
  Telegram owner ID. Start payloads must fit Telegram's 64-character limit. The
  first sender does not become the owner by default.
- An unpaired profile has nullable owner identity and `setup_only` authority.
  Management requires verified owner and pairing; never invent `owner_id=0`.
  Setup public views are sanitized/read-only; writes require the SID-issued session.
- Browser commands must use the `NativeCommand` allowlist with payload size/profile
  validation and session/CSRF/Origin checks. They open native dialogs; they cannot
  execute arbitrary paths, programs or URLs. DTO validation does not establish
  authentication, verified evidence, authorization or writer fencing by itself.
- Rejecting invalid input must not echo it into logs or browser errors. DTO error
  display hides inputs; when extracting structured Pydantic errors use
  `errors(include_input=False)` and return sanitized codes/messages. Native payloads
  revalidate before serialization because nested dictionaries remain mutable.

## Shared contracts and reliability

- Section 9 DTOs live in `src/tg_assistant/contracts.py`; the frontend mirror is
  `dashboard-prototype/src/contracts/generated.js`. These names and enums are
  frozen for parallel work. Propose schema changes to the coordinator/reviewer
  before changing consumers; never hand-edit the generated mirror.
- Telegram/chat/owner IDs are canonical decimal strings in JSON, Python ints
  internally. Owner IDs are positive; chat IDs may be negative. JSON numeric IDs,
  booleans, floats, zero and noncanonical strings are rejected.
- `ConnectionStatus.checked_at` is nullable timezone-aware ISO 8601, normalized
  to UTC. Unknown/stale status must not appear Online. `OperationResult.progress`
  is a nullable integer percentage from 0 to 100. Endpoint IDs are sanitized
  identifiers, not credential-bearing URLs.
- Internal `LaunchTicket`, `AdminSession`, `JobLease` and `MaintenanceLease` objects
  are excluded from the public schema/status endpoints. Protocol signatures are
  interfaces only; each owning service must enforce its approved invariants.
- Regenerate with `python scripts/generate_contracts.py` in an installed development
  environment. Check drift with the same command plus `--check`. During bootstrap,
  `python scripts/check.py generate_contracts [--check]` uses the audit dependencies.
- LOCAL ONLY forbids sending that source's content to cloud chat or embeddings,
  including shared retrieval and fallback. Cloud fallback is opt-in and must pass
  global consent, source policy and budget checks. Check chat/embedding capabilities
  separately. Secret detection happens before model/embedding submission.
- Model/store identity includes provider, sanitized endpoint, model, version and
  dimension. Do not overwrite a corpus silently; preview/confirm reindex and switch
  only after verification. Vector point identity includes source+message+store;
  deduplicate only within the same scope and eligibility rules.
- BLOCK/revoke wins over stale jobs/actions/requests. Recheck epochs before commit,
  indexing and delivery. Atomically consume actions and use compare-and-set leases.
  An uncertain external side effect requires reconciliation, never blind retry.
- Restore requires fencing API/scheduler/worker and legacy writers, backup before
  restore, staged validation, migration and vector reconciliation. A stopped
  process is not proof of `writers_fenced`. Portable backup excludes credentials,
  OTP/tokens and raw Telegram sessions; manifests use real revision/checksums.
- Budget reservation must include pending/concurrent/retried requests and use
  versioned prices. Do not call characters/4 an accurate token count.

## Art, accessibility and release evidence

- Keep the approved retro neo-brutalist art: paper `#f3efdf`, panel `#fffdf5`, ink
  `#090909`, teal `#00c8c8`, magenta `#ef00c8`, yellow `#ffd51f`; square corners,
  3px black borders and hard 7px shadows. No replacement gradient/glass/rounded layout.
- PeterObscure is for logo/main title only. DarleySans is for all other text and
  controls. Verify font and Qt/PySide6 distribution licenses before public artifacts.
  Preserve the reference image and font asset hashes recorded by the coordinator.
- Match native dialogs to the same art spirit with DPI-aware adaptation. Browser
  touch targets are at least 44 CSS px. Verify 360/390/1280/1440 browser widths and
  Windows DPI 100/125/150/200%, keyboard focus/traps/ESC/focus restoration, long
  Vietnamese copy, and states conveyed beyond color. See frontend `AGENTS.md` too.
- Use real Admin API data only. Never fabricate operational values, completed
  setup stages, successful actions, toast-only controls or mock banners.
- Every Done needs command/test/artifact/evidence. Record baseline failures
  separately. Human/VM tests remain Pending until performed; code-ready is not
  beta-ready. Release gates G0-G6 include clean Windows install, upgrade/regression,
  restore, UI/art and UAT. Do not use real account credentials or buy/sign/release
  artifacts outside explicitly authorized scope.
