# U03 Guided First Value Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the paired owner select one Telegram source, review and confirm its permissions, follow actual learning progress, and receive a source-bound answer in the existing bot.

**Architecture:** One `FirstValueService` belongs to the current Application and uses its SQLite database, maintenance fence, onboarding coordinator, RAG, vector owner and paired bot. The dashboard writes only source-selection intent and uses existing one-use actions for permission changes and learning. Actual retrieval, provider settlement, all answer deliveries and a guarded durable receipt establish completion; browser status and model text cannot establish it.

**Tech Stack:** Python 3.12/3.13, SQLAlchemy/SQLite, existing FastAPI/aiogram/Telethon/QdrantLocal, PySide6, React/Vite and Playwright on Node 24.

**Spec:** `docs/superpowers/specs/2026-10-02-windows-public-beta-design.md`, the SQLite-only amendment `docs/superpowers/specs/2026-10-06-sqlite-only-amendment.md`, and U03 in `docs/superpowers/plans/2026-10-02-windows-public-beta-plan.md`. This is an implementation refinement of the already authorized roadmap, not another product scope.

## Global Constraints

- Windows 10/11 x64; one active owner/profile per Windows SID; SQLite-only; no real credentials, paid live calls or existing-profile reset in development checks.
- O04, V03 and U02 must be Verified before implementation. This dependency gate is now satisfied: exact CB6 O04 candidate passed all required jobs in run38011226810 and root accepted its receipt on10/10/2026. Earlier `3f4913a838526af382e778d769c7f0d4ffe8739f`/run37918805090 and2229 failure receipts remain historical. New U03 candidates require their own verification; O04 success does not accept U03.
- Reuse the actual application/engine/account/poller/vector/guard. No second runtime or portable bearer proof. Management still requires current owner pairing, session, CSRF, Host/Origin and writer admission.
- Preserve all 19 permissions, six AI modes, group `/ask`, AUTO confirmation, retention/quota, ordinary RAG/cache behavior and existing recovery/backup boundaries.
- Telegram IDs are canonical nonzero decimal JSON strings. Reject numeric JSON, booleans, zero and noncanonical IDs; Python uses exact ints internally.
- No secret, raw question/answer, raw endpoint, path, SDK exception or pairing nonce enters public status or durable first-value receipts.
- Keep paper `#f3efdf`, panel `#fffdf5`, ink `#090909`, teal `#00c8c8`, magenta `#ef00c8`, yellow `#ffd51f`, square 3px borders and hard 7px shadows. Preserve font/reference bytes; 44px targets, keyboard and 360/390/1280/1440 widths.
- Unknown, stale, partial, cancelled and uncertain outcomes cannot complete onboarding. A verified first answer proves authorized source-bound generation and delivery, not independent semantic correctness or completeness of all source history.
- Root owns shared integration, Git, public contracts and trackers. Workers own only assigned files and tests. A task commit is made by root after review; do not push during the current O04 CI run.

## Review Focus

1. Source A→B→A, BLOCK/regrant and delayed responses cannot revive old completion: Tasks 1, 4 and 5.
2. Unrelated completed jobs, missing metrics and actual zero must remain distinguishable: Tasks 4 and 5.
3. Partial Telegram delivery or ambiguous SQLite commit must retain uncertainty without replay: Task 4.
4. Incremental vector mutation, restore and successful provider fallback must bind to the actual current owner and evidence: Tasks 2–4.
5. Pre-READY test access must work for the paired owner without widening setup-only authority; malformed/stale bot identity yields no link: Tasks 1, 4 and 5.

## Shared interfaces and ownership

Root approves one additive public DTO in `src/tg_assistant/contracts.py`:

```python
class FirstSourceStatus(PublicDTO):
    profile_id: Identifier
    source_id: TelegramId | None
    learning_operation: OperationResult | None
    answer_operation: OperationResult | None
    bot_username: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_]{1,32}$")] | None
    test_available: StrictBool
    code: Identifier
    message: Text
    next_action: Text | None
```

The username additionally requires full-match validation, including rejection of a trailing newline. It is obtained only from the current paired bot's verified identity. The browser may construct only `https://t.me/<username>` with `noopener noreferrer`; it must not open the native pairing dialog to ask a question because that dialog quiesces the runtime.

Root owns `services/first_value.py`, `services/first_source_preview.py`, `services/onboarding.py`, `admin_api/first_value.py`, `admin_api/app.py`, `runtime.py`, `desktop/setup_context.py`, `desktop/runtime_bot_context.py`, `desktop/worker.py`, `telegram/control_bot.py`, shared contracts and their integration tests. AI owns `ai/observations.py`, `ai/engine.py`, `ai/router.py`, `ai/rag.py` and new `tests/ai/test_first_value_*.py`. Index owns `services/first_value_index.py`, `ai/vector.py`, the narrow restore call in `services/backup.py` and new index/restore tests. Experience owns dashboard consumers, generated-schema consumer tests and first-value Playwright tests; root regenerates `generated.js`.

Root additionally approves a narrow AI-owned `SearchService.keyword(..., parsed_query: SearchQuery | None = None)` adjunct in `services/search.py`, reusing its existing scoped SQL. The default retains ordinary parsing; only the observed purpose passes its server-parsed immutable query so keyword search cannot reparse and corrupt natural question text. Do not combine this with a broad Unicode-search rewrite or schema change.

Private carriers live in `ai/observations.py`: frozen dataclasses with immutable nested tuples. `SourceIndexBinding` contains profile/owner, pair fingerprint, selection generation, restore epoch, selected chat ID, source epoch/policy fingerprint, embedding provider/endpoint/model/version/dimension/store, active index generation, vector-owner fingerprint, retrieval-eligibility fingerprint and current chat capabilities. `VerifiedChatModel(provider, endpoint_id, requested_model, capability_fingerprint)` has no invented model-alias allowlist. Actual response-reported model is recorded separately as a bounded nullable provider assertion, not independently verified alias equivalence. Budget cost is the authoritative application's versioned requested-model ledger, not a provider invoice.

`CitedReference(chat_id, message_id, reference_id, content_hash, context_hash, semantic_used, keyword_used)` uses actual rows and exact submitted context hashes. `FirstValueAnswerObservation(binding, execution, query_embedding_request_id, cited_refs, retrieval_mode, after, before, sender_id, has, content_type)` captures the fixed retrieval scope and exact settled query-embedding reservation. AI also owns frozen `RetrievalScope(selected_chat_id, after, before, sender_id, has, content_type, query_route, embedding_route)` as the immutable view of that captured scope. `QualifyingRagSuccess(answer, observation)` differs from `NonqualifyingRagResult(code, answer=None)`; it carries the observation separately and leaves `AuthorizedAnswer` unchanged. Issued objects and captured actual owners are retained by the consuming service; matching field values alone never grant delivery or receipt authority.

## Task 1: Add strict projection and atomic source selection

**Files:** root-owned contracts, coordinator adjunct, `admin_api/first_value.py`, app wiring; `tests/test_contracts.py`, new `tests/test_first_value_api.py`, `tests/test_first_value_selection.py`.

**Interfaces:**

- `GET /api/v1/onboarding/first-source -> FirstSourceStatus`: read-only projection; no SDK/provider/Qdrant work or freshness renewal.
- `POST /api/v1/onboarding/source-selection` accepts exactly `{"source_id":"<canonical ID>"}` and returns `FirstSourceStatus`. It saves an intent and grants/enqueues nothing.
- `OnboardingCoordinator.save_source_selection(source_id: int, *, transaction_update: Callable[[Connection], None]) -> OnboardingStatus` updates existing `SetupOptions.source_id`, invalidates downstream history and invokes the first-value metadata update in the same owning `_mutate` transaction. No nested BEGIN or duplicate source options store.
- `FirstValueService.select_source(chat_id: int) -> None` uses that adjunct under actual admission; selection generation changes only on a changed source.

- [x] Write boundary tests: numeric/bool/zero/noncanonical/extra-field POST rejected without writes; valid negative large ID round-trips as string; setup-only forbidden; lost admission after body await forbidden; selected option and metadata commit/rollback together; selection alone changes no grants or jobs. DTO rejects malformed bot usernames and secrets.
- [x] Run `.venv-q01/Scripts/python.exe -m pytest tests/test_first_value_api.py tests/test_first_value_selection.py tests/test_contracts.py -q`; record expected missing-behavior RED, separately from import/environment errors.
- [x] Implement the strict wire model, approved DTO, transaction adjunct and default-deny service getter. Regenerate mirror with `python scripts/generate_contracts.py`; preserve existing public DTO shapes.
- [x] Run the named tests and contract drift check; all pass and drift exit0. Independent review approved; root201 targeted tests passed with zero skips. Integrated installed product regression1646 passed/1 skipped, guided setup41, native browser8 and vector43 passed. Root records this implementation checkpoint separately from full U03 acceptance; exact new-candidate hosted CI remains pending.

## Task 2: Produce actual provider and selected RAG observations

**Files:** AI-owned observations/engine/router/RAG; `tests/ai/test_first_value_observations.py`, `test_first_value_provider.py`, `test_first_value_rag.py`.

**Interfaces:**

- `AiEngine.answer_observed(session, question, context, *, capability: VerifiedChatModel, feature, route, chat_id, fallback_used=False, source_modes=None, pre_submit=None) -> ProviderAnswerResult`.
- `AiRouter.answer_observed(session, question, context, *, route, capabilities: tuple[VerifiedChatModel, ...], feature, query_route, chat_id, source_modes=None, pre_submit=None) -> ProviderAnswerResult`.
- `RagService.answer_selected_observed(session, question, *, actor_id: int, owner_id: int, binding: SourceIndexBinding, pre_submit) -> RagObservedResult`; the owning final admission callback is mandatory and `None` is rejected before retrieval/provider work.
- `SettledProviderExecution` captures request/profile/provider/endpoint/requested model, nullable reported model, capability fingerprint, route/fallback, pricing version, original UTC settlement, actual usage and Decimal ledger cost. No process-global last-result side channel.

Purpose parsing recognizes only standalone documented filter tokens: natural `admin:Bob`, `admin:5` and `login:abc` remain literal across keyword, embedding and chat submission. True malformed, duplicate or foreign `in:` tokens are nonqualifying before calls; retain the documented date/sender/type/link filters and captured scope. Both retrieval paths apply one window before candidate limits/context: inclusive lower bound, default inclusive upper bound at the captured question time, and an explicit exclusive `before` represented as the previous microsecond in the normalized UTC scope. The keyword SQL receives the corresponding exclusive upper boundary; vector candidates and final actual-row checks use the same captured bounds. Future-dated rows cannot enter the context. Ordinary search/RAG window behavior remains unchanged.

Observed mode requires the actual owning `AiEngine` and `BudgetService`, records keyword and semantic participant keys before merging, and hashes the exact bounded/redacted submitted context separately from the original DB content. It treats every existing early string return as nonqualifying and bypasses cache read/write. Original citation parsing runs with fallback disabled: adjacent `[S1][S2]` works; missing, malformed or out-of-range source markers are nonqualifying, including a mix of valid and invalid markers. The ordinary citation appendix cannot manufacture proof. The existing `embed(..., **kwargs)` already forwards `request_id` through `embed_many`; reuse this interface rather than adding a returned-ID protocol.

- [ ] Write RED tests for successful current scoped semantic citations; foreign/duplicate `in:` scope; missing original model citations; keyword fallback; no-content/error/refusal; cache read AND write bypass on this purpose only; successful fallback provenance; separate requested/reported models; missing settlement; concurrent responses; policy/consent/cancellation prohibiting fallback. Ordinary RAG and AuthorizedAnswer string/epoch behavior must remain unchanged.
- [ ] Run new `tests/ai/test_first_value_*.py` files separately; expected missing observed-method behavior RED.
- [ ] Reuse actual provider calls with call-local evidence. Cloud and Ollama validate their actual different response shapes. Read settled BudgetService reservation from fresh selected-store session; unsupported legacy budget cannot qualify. Purpose RAG generates one query-embedding request ID, passes it through the actual embed call with selected-source provenance and reads that exact settled reservation before issuing evidence. The existing embedding ledger route is `cloud_embedding` even for local execution; preserve the actual ledger value and use actual provider/profile policy to determine cloud locality. Reservations contain no endpoint field: check actual engine/profile endpoint separately. It forces the selected ID independently of parser input, captures actual semantic participants, and parses original model citations with fallback disabled before the ordinary citation appendix.
- [ ] Run new tests plus existing AI/router/RAG/revocation regressions; expected all pass. Independent review compares recorded provider/index facts with actual owning calls; root integrates and commits.

## Task 3: Add bounded current index and staged restore invalidation

**Files:** Index-owned `services/first_value_index.py`, narrow `ai/vector.py` mutation observation and `services/backup.py` hook; new `tests/test_first_value_index.py`, `tests/test_first_value_restore.py`. Root owns the hook implementation in first_value.py.

**Interfaces:**

- `CurrentIndexReader.read_source_binding(chat_id: int) -> SourceIndexObservation` asynchronously measures the actual selected vector owner outside GET and SQL commit callbacks. An authorized selection may have no usable index binding.
- `CurrentIndexReader.bind_current(observation: SourceIndexObservation, binding: SourceIndexBinding) -> bool` checks its exact issued observation, current actual owner and matching measured fields before retaining the exact shared binding instance constructed by FirstValueService. The reader keeps additional pointer/path/object/revision facts privately; no second binding dataclass.
- `CurrentIndexReader.check_used_references(binding: SourceIndexBinding, scope: RetrievalScope, used: tuple[CitedReference, ...]) -> VerifiedReferences | None` checks actual bounded rows, eligibility, points and settled embedding intents.
- `CurrentIndexReader.still_current_in_transaction(connection, binding: SourceIndexBinding, checked: VerifiedReferences | None) -> bool` is synchronous and performs no external call.
- `CurrentIndexReader.withdraw() -> None`; actual vector instance exposes monotonic RAM mutation revision/incarnation, withdrawn before mutation/close and uncertain after a failed mutation. SQLite budget/options writes do not invalidate it.
- `invalidate_first_value_after_restore(connection, *, profile_id: str, windows_sid: str) -> None` runs in the existing staged restore transaction after `invalidate_restored_index`.

- [ ] Write RED tests for wrong active object/path/store/profile/model/dimension, initial index without actual admission, stale pointer, pending/uncertain embedding intents, equal counts without witness, BLOCK/retention/edit/delete, mutations during awaited check, unrelated budget commit, incomplete/oversized references, and restore failure rollback.
- [ ] Run those two files; expected missing reader/hook or missing mutation withdrawal behavior RED.
- [ ] Borrow actual RAG vectors/knowledge lock and existing V03 checks; never open a second Qdrant client or scan the full corpus on status refresh. Bounds: at most 8 candidates/refs/points, 64 KiB text + 8 KiB metadata per row, 2-second refresh/check eligibility deadline, 100,000 SQLite VM steps, 30-second observation and 5-second immediate point check capped by real freshness/retention. Keep tasks/objects owned until actual drain even if deadline expires.
- [ ] Invoke root's restore hook in the real staging transaction: rotate restore epoch, increment selection generation, retain immutable completed history/replay tombstones, mark unfinished possible effects uncertain. Malformed/oversized namespace aborts staging; rebuilding never revives pre-restore completion.
- [ ] Run new tests and existing vector recovery/backup/revocation regressions; expected all pass. Independent review verifies no count-parity shortcut, full-scan GET or `PRAGMA data_version` authority. Root integrates and commits.

## Task 4: Own permission preview, delivery, receipt and coordinator stages

**Files:** root-owned first-value/preview services, action/runtime/control-bot/native wiring and integration tests `tests/services/test_first_value.py`, `tests/test_first_source_preview.py`, `tests/integration/test_first_value_journey.py`.

**Interfaces:**

- `FirstValueService.refresh_observation() -> None`, synchronous `source_verification() -> StageVerification | None`, `first_answer_verification() -> StageVerification | None`, `selection_status() -> FirstSourceStatus`, `answer_selected(message, question: str) -> bool`, `close() -> None`.
- Selection verifier proves actual selected authorization, not learning completion. First-answer verifier requires a still-current completed receipt; READY follows existing coordinator ownership, including truthful limited AI-skip readiness.
- `FirstSourcePreviewService.create(session, chat_id: int, limit: int) -> PendingAction` reuses existing `enable-selection-action`/pending confirmation for exactly one guided source. Server-derived bound preview describes all permission grants/removals, actual scope/route/cloud consent/limit and original expiry. Execution rechecks that captured source/policy/model/consent snapshot before the existing preset/enqueue. Advanced bulk action behavior remains available.

- [ ] Write RED tests with disposable SQLite and actual installed SDK through disposable transport: source intent alone no grants; preview changes reject stale confirmation; no automatic cloud consent; unpaired/foreign/group sender denied; ack does not qualify; every actual answer chunk returns IDs; failures on first/last chunk, selection change, BLOCK/regrant, route/consent/model/store change, timeout/cancel/restore, and ambiguous final commit prevent completion. Same incoming request cannot resend.
- [ ] Run three new files; expected missing current producer/receipt journey RED. Synthetic transport does not count as live Telegram or paid-provider evidence.
- [ ] Implement schema-1 `fv1` AppSetting namespace derived from profile/SID with CAS, strict counters and restore epoch. Limits: metadata 8 KiB; 128 attempts + 128 immutable receipts; 32 KiB/row, 8 MiB aggregate; one active attempt; question 2048 Unicode characters in RAM; 8 cited refs; 16 answer chunks; original 300-second UTC+monotonic deadline. Full history returns fixed `history_full`; no silent deletion of uncertainty/replay history.
- [ ] Implement accepted→generating→delivering→completed phases. Persist submission ordinal before each SDK send and returned message ID after. Prevalidate balanced HTML/chunks <=3900 UTF-16 units before any send. Recheck owner/pair/selection/source/model/index before model, before each send, after last send and final BEGIN IMMEDIATE receipt CAS. Definite no-effect failures may fail/cancel; partial or indeterminate effects remain uncertain. Reconcile durable commit only; never repeat an external effect to repair a receipt.
- [ ] Wire current Application and narrow owner-private `/ask in:<selected ID> <question>` first test; retain normal `/ask` behavior. Before READY allow only genuinely eligible first test, not broader capability upgrades. Withdraw verifiers and drain before bot/vector/account/guard close. Fresh restart can recheck completed immutable history; no resend and no inferred freshness.
- [ ] Require exact private-chat type, chat ID equal to verified owner ID, and matching non-bot sender for the first-test path. A denied first test is consumed before generic `/ask`, price or acknowledgement fallthrough. Reuse the already paired Application's gated ControlBot before READY; no second setup bot or admission bypass. Catch management withdrawal/cancellation in the owning service before the update gate acknowledges the request. Preserve the already committed send ordinal if maintenance or lost admission prevents terminal bookkeeping; any narrowly guarded bookkeeping may only demote that existing owned attempt, never create a receipt, grant or SDK submission. Normal `Database.session()` denies SQL after management loss, so it cannot be assumed to persist uncertainty. Tests must lose admission between chunks and during this bookkeeping.
- [ ] Add hostile-output cases for astral emoji, escaped ampersands/angle brackets, overlapping inline markup and code fences containing blank lines. Validate every complete purpose chunk before any SDK send; unsendable output produces a fixed nonqualifying status/next action. Keep the ordinary chunker and group `/ask` unchanged. Partial/uncertain guidance must not invite blindly repeating the same attempt.
- [ ] Run new integration tests plus group AI, revocation, jobs, onboarding and native-runtime tests; expected all pass. Independent review checks actual SDK IDs/current final fences and complete stage chain; root integrates and commits.

## Task 5: Deliver guided dashboard without changing art

**Files:** new `dashboard-prototype/src/components/FirstSourceAssistant.jsx`; existing `src/api.js`, `src/views/GroupsView.jsx`, `KnowledgeView.jsx`, `OnboardingView.jsx`, `src/App.jsx`, `src/styles.css`; new `tests/e2e/first-value.spec.js` and optional shared test-only fixture; `playwright.setup.config.js` discovers the new file.

**Interfaces:** `FirstSourceAssistant({session, refreshKey, onOpenGroup, onCreatedAction})`; API wrappers `firstSourceStatus(signal)` and `selectFirstSource(sourceId, signal)` consume the generated approved DTO. Existing reviewAction/modal and exact-source job/action IDs provide confirmation and correlation. Recovery wrappers permit only actual existing pause/resume controls.

- [ ] Write five exact named RED cases: `test_pick_one_source_confirm`, `test_no_automatic_allow_all`, `test_job_accounting_visible`, `test_block_progress_cancels`, `test_recovery_verified`. Use A=`-1009007199254740993` and two untouched controls; assert exact requests, CSRF and no unexpected writes; missing progress is indeterminate, actual zero stays zero; no unrelated job success; stale A/B/profile responses rejected; pre-READY valid bot username creates exact t.me link, null/malformed/stale removes it; BLOCK and recovery uncertainty win. Test setup-only and AI-skip as separate restricted fixtures.
- [ ] Run via `node node_modules/@playwright/test/cli.js test --config playwright.setup.config.js tests/e2e/first-value.spec.js`; record missing-behavior assertion RED, not missing browser/server errors.
- [ ] Implement searchable/type-filtered single source selection, bound server preview plus existing one-use confirmation, real job accounting, current recovery guidance and owner-private bot test copy. Selection/open/copy/ack do not complete stages. Keep advanced permission/mode/global views. Do not invent cancel/retry endpoints or retain test link after failed refresh.
- [ ] Run five cases at 360/390/1280/1440 with keyboard/long Vietnamese copy and unchanged art; then existing setup/art tests, Node payload/native tests, lint and build. Expected all pass/exit 0. Browser response fixtures establish presentation only; Task 4 supplies actual authority evidence. Independent review and root commit.

## Final acceptance and bookkeeping

- [ ] Root runs full Python regressions with JSON count report; all failures and actual skips remain recorded. Run all required frontend/native-browser/scale gates and scoped secret audit on the exact indexed candidate. No weakening fixture/CI gates.
- [ ] Fresh independent review of the integrated U03 diff against the approved spec and actual evidence, then fixes with meaningful RED→GREEN when needed.
- [ ] Root publishes candidate to the existing draft PR only after local verification and checks all required hosted jobs against its exact head SHA. Do not merge or publish an installer.
- [ ] Update `docs/handoff/evidence/task-U03.md`, status/checklist/roadmap consistently only after actual acceptance. Human Telegram/provider/UAT/Windows VM/font-license/installer/72-hour soak evidence remains Pending until performed.

## Self-review and execution ruling

Coverage: the five required UI cases belong to Task 5; actual source/permission/AI/index/delivery evidence belongs to Tasks 1–4; ordinary advanced features and art remain explicit constraints. The private implementation review packets are preparation, not accepted task evidence. Root must check private carrier fields against the owning producers before assigning code; tasks cannot independently invent a second binding type.

Ruling: continue the already approved Windows roadmap and agent delegation without another product approval prompt. Wait for O04's exact dependency acceptance before U03 implementation; independent read-only preparation and this plan do not change O04's status. Root batches public documentation with the next verified candidate rather than cancelling running CI for a documentation-only push.
