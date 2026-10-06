# O02 — Native provider connection evidence

<!-- five-current -->
## Integrated acceptance — 07/10/2026

Current status: **InReview**. The coordinator's [integrated review](review-five-completion.md) and [execution manifest](five-completion.json) supersede historical code-integration Pending statements below. SQLite-only source follows the directly approved amendment. Live accounts/provider/downloads, physical Windows/installer, licensing, human UAT and soak remain Pending; this is not beta-ready or publication. O04 bot setup and U03 first-answer UX remain separate roadmap work.
<!-- /five-current -->



2026-10-05: implementation and focused test evidence available. Root runtime wiring, independent review and Windows release gates are **Pending**; this is not a Done/Verified claim.

New native service and Qt dialog perform real effects through an injected approved credential store and transactional settings callback. Tests use only synthetic keys/fake stores/transports and an actually owned loopback HTTP server. No account access, actual Windows credential access, provider billing, installations or downloads occurred.

Default checks call only OpenAI model metadata, OpenRouter key/model metadata, or local Ollama tags/show. Chat and embedding are separate selections and observations. Unknown metadata is degraded; explicitly saved unverified choices remain unknown and never issue ready evidence. API-key edits stay blank, clear after submission, and never enter dashboard DTOs/config/logs. Invalid replacement preserves existing credentials; uncertain rollback invalidates all shared-key measurements. SID changes, bad endpoints, auth/quota failures, redirects and remote Ollama models are rejected conservatively. Native Esc cancels saving after a pending check.

Final command, `.venv-q01/Scripts/python.exe` Python 3.12.14, real repository cwd, approved outside sandbox to permit synthetic Windows sockets:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:ART_EVIDENCE_DIR='docs/handoff/evidence/o02'
& '.venv-q01/Scripts/python.exe' -m pytest tests/test_provider_connection.py tests/test_api_key_setup.py tests/test_ollama_service.py tests/test_onboarding.py -v -rs --tb=short -o faulthandler_timeout=20 --basetemp=.test-temp/o02-final-2
```

Exit 0: **95 passed, 47 skipped, 23.30s**. All 40 O02 cases passed. Skips: 46 unconfigured disposable MySQL fixtures and one backend-specific gap-lock case. Ruff check of all three owned Python modules/test file: exit 0, All checks passed. No full suite run by the O02 agent.

RED evidence preceded implementation: 17 missing behaviors; later reproduced partial key-write rollback, stale independent embedding measurement after key change, missing cancellation/dialog, remote Ollama incorrectly READY, disconnect skipping key deletion on settings failure, missing safe saved-selection reload, 8px horizontal overflow, and uncertain rollback retaining embedding READY. Each was fixed in owned files and rerun green. Initial WindowsApps Python launch error, missing-module fixture errors and sandbox asyncio socketpair stall were recorded separately as environment/setup failures.

Shared native art/font, >=44px controls, modal keyboard focus, Esc restoration and no horizontal overflow passed at Qt scales 1, 1.25, 1.5 and 2. Screenshots are actual Qt offscreen renders with synthetic model ID and unknown operational state. Manual Windows display/accessibility verification remains Pending.

| Artifact | SHA256 |
|---|---|
| [100% render](o02/provider-scale-1.png) | 1e768aebf0f6eef2735abffd8bc4d37c19f00c38248269afa95abcd2fd2153d1 |
| [125% render](o02/provider-scale-1.25.png) | 9e1b2b9f02f8cb479f5736255976d9cfb66d4b4823266e280f504ed693af80c1 |
| [150% render](o02/provider-scale-1.5.png) | dba3bb4a822ad491427ec1565eea8c16be43b7eb8974ce8acb88eba84fc48bd0 |
| [200% render](o02/provider-scale-2.png) | 9c5a785f777466ba6fb6e6e21aa98e26c3cdc29c71428b8519462fe1c4ea197b |

Original tested source packet (superseded by fix1 below):

| File | SHA256 |
|---|---|
| src/tg_assistant/services/provider_connections.py | 1bf7db011f49fcfd3aaf9b9a04e601058ccce44dc739defa34ab20938319a9f2 |
| src/tg_assistant/desktop/dialogs/provider.py | abecc83f77bb1b0bcce21a6fd6381d32c992ce2126bbdd4d3617527d0ceb2129 |
| src/tg_assistant/desktop/dialogs/__init__.py | 3fb647bc27cc46c5c8530aa27332bc46613d1ac864f338acc3003f89f58a7b90 |
| tests/test_provider_connection.py | 2009850e24f0baeb4c7bcfaa51b10bab61f1639995e15d09a51cb69d65fc1891 |

Pending integration: trusted native factory and frozen launcher/worker callback registrations; fenced settings writer preserving separate embedding identity/corpus; O01 verified-stage adapter and prevention of duplicated health-cache TTL extending READY; actual keychain/current-SID behavior; explicit paid first answer/embedding after owner consent/budget; genuine embedding dimension; owner-selected positive-size cancellable Ollama download/resume UI; Windows install/upgrade/UAT. The dialog truthfully reports no native downloader integrated. Source policy, local-only route, secret detection and budget enforcement remain the runtime's authoritative boundaries.

Endpoint and capability decisions use current official [OpenAI model API](https://developers.openai.com/api/reference/resources/models/methods/list), [OpenRouter key API](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key), [model API](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties), [embedding model API](https://openrouter.ai/docs/api/api-reference/embeddings/list-all-embeddings-models), and [Ollama OpenAPI source](https://github.com/ollama/ollama/blob/main/docs/openapi.yaml). Metadata readiness does not promise quota, a paid inference result or compatibility with a selected vector store.

## Scoped review fix1 — 2026-10-05

Independent review I1/I2 have author fixes and fresh scoped evidence; root capture/fresh independent review remain **Pending**. O02 is not Done/Verified.

I1: a failed probe of the currently saved key now replaces contrary evidence and revokes READY/fingerprints for both roles sharing that key when authentication fails. Exact current-model failure revokes only that selection. A rejected distinct replacement key or proposed model preserves the existing key, settings and original measurement timestamp; it cannot renew the old READY TTL. Same-SID checks still guard reads/probes/writes/publication and the production default remains the actual process SID.

I2: successful save/test and failed-measurement cache publication use deep private snapshots. Clearing or adding `inference_verified` to a returned DTO's capabilities cannot change subsequent status, measured health capabilities or fingerprint. Tests exercise both returned result paths and mutation of a status getter result.

M1: clipped panel shadow remains deferred to U04/native art polish. Dialog and package init hashes are unchanged. The art/keyboard test passed again with synthetic renders under `.test-temp/o02-fix1-art`; no shadow repair or manual Windows validation is claimed.

Behavioral RED preceded production edits:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:ART_EVIDENCE_DIR='.test-temp/o02-fix1-art'
& '.venv-q01/Scripts/python.exe' -m pytest tests/test_provider_connection.py -k 'saved_key_auth_failure or rejected_distinct_replacement or current_model_failure or failed_proposed_model or returned_capabilities' -v --tb=short -o faulthandler_timeout=20 --basetemp=.test-temp/o02-fix1-red
```

Exit 1: **6 failed, 3 passed, 40 deselected in 1.26s**. Failures reproduced saved-key chat/embedding READY retention, save-path current-model READY retention, and save/test capability aliases. No setup/environment failure. Raw RED is the tool transcript (no disk RED log). Identical selector after fixes: 9 passed/0.35s. Follow-up coverage adds proposed-model saved-key failure and deterministic original-TTL expiry; these additions are not claimed as a separate RED cycle.

Fresh final scoped command, approved outside sandbox for Windows Qt/owned loopback server:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:ART_EVIDENCE_DIR='.test-temp/o02-fix1-art'
& '.venv-q01/Scripts/python.exe' -m pytest tests/test_provider_connection.py tests/test_api_key_setup.py tests/test_ollama_service.py -o addopts='' -v --tb=short -o faulthandler_timeout=20 --basetemp=.test-temp/o02-fix1-final | Tee-Object -FilePath '.test-temp/o02-fix1-final.log'
exit $LASTEXITCODE
```

Exit 0: **56 passed in 6.26s** (50 O02 + 3 API-key + 3 Ollama), zero skips/warnings/failures. Ruff check of the service, unchanged dialog and tests: exit 0, All checks passed. Only owned service/tests and O02 reports changed. No Git, subagents, full suite/onboarding rerun, real credentials/provider accounts, paid calls, actual model downloads/installations or MySQL use. This service needs no MySQL.

Final fix1 raw SHA256 packet:

| File | SHA256 |
|---|---|
| src/tg_assistant/services/provider_connections.py | b6c3e0bc37e21e2f6b8aaab2156cab586dd5a07a72a18bad506279cd1ae00831 |
| src/tg_assistant/desktop/dialogs/provider.py (unchanged) | abecc83f77bb1b0bcce21a6fd6381d32c992ce2126bbdd4d3617527d0ceb2129 |
| src/tg_assistant/desktop/dialogs/__init__.py (unchanged) | 3fb647bc27cc46c5c8530aa27332bc46613d1ac864f338acc3003f89f58a7b90 |
| tests/test_provider_connection.py | 2cc9cf54fbb2f5ece9a5fa09c025fd737450a3ae614a6ffef0fc67f18e730485 |
| .test-temp/o02-fix1-final.log | 512a2a9c58d5e0ff5af0d58deaaec337dd8afdf0fcd17232102d7646fc7a7aad |
| .test-temp/o02-fix1-ruff.log | a4443afdcfb6d7363adb285762515ccf7cf50473b1a05c20c1a50f6bed4d26b0 |

Full Settings health/native integration, onboarding TTL composition, actual Credential Manager/current-SID, owner-consented paid first answer/embedding and source/budget fences, genuine embedding dimensions/reindex, size/progress/cancel/resume Ollama downloads and Windows installation/upgrade/DPI/art/UAT are still **Pending**. Root owns Git, shared integration and fresh review acceptance.

## Scoped review fix2 — 2026-10-05

Fix1 re-review accepted I2 and measured auth/model invalidation, but found an Important I1 residual: a missing saved credential returned early while both roles retained READY. Fix2 now records `disconnected/credential_required` for configured roles sharing that provider when a successful same-SID native store read proves the key is absent. Both roles' fingerprints become unusable; no settings/key writes occur. Blank saved-key checks make no provider request. A supplied candidate may be checked independently, but its rejection cannot preserve evidence supported by a key already known to be gone. Rejected distinct replacements when the original key still exists retain their original evidence and TTL. SID is rechecked after the missing read, before cache publication. This fix concerns confirmed absence; it does not change credential-read exception behavior.

RED before production edits:

```powershell
& '.venv-q01/Scripts/python.exe' -m pytest tests/test_provider_connection.py -k 'missing_saved_key' -o addopts='' -v --tb=short --basetemp=.test-temp/o02-fix2-red | Tee-Object -FilePath '.test-temp/o02-fix2-red.log'
exit $LASTEXITCODE
```

Exit 1: **4 failed, 50 deselected in 1.04s**, meaningful failures from fake-key deletion after both roles were measured READY, plus SID change during the missing read. No environment/setup failure.

Final targeted command after formatting:

```powershell
& '.venv-q01/Scripts/python.exe' -m pytest tests/test_provider_connection.py -k 'missing_saved_key or saved_key_auth_failure or rejected_distinct_replacement or current_model_failure or failed_proposed_model or returned_capabilities' -o addopts='' -v --tb=short --basetemp=.test-temp/o02-fix2-green | Tee-Object -FilePath '.test-temp/o02-fix2-green.log'
exit $LASTEXITCODE
```

Exit 0: **14 passed, 40 deselected in 0.33s**, covering four new cases and ten relevant fix1 regressions, no warnings/failures/skips. Ruff check of service/tests: exit 0, All checks passed. Default sandbox; fake store and `httpx.MockTransport` only. Fix1's 56-case GREEN is historical, not a current fix2 broad-suite claim; no broad/full-suite rerun, GUI expansion, shared-source edits, Git, subagents, real credentials/provider calls/paid calls/downloads or MySQL use occurred.

Final fix2 raw hashes supersede service/test hashes above:

| File | SHA256 |
|---|---|
| src/tg_assistant/services/provider_connections.py | d38097780b0effa8bd4dd5ec32bd979da50f888c6633771b30abcaa20a08dbdb |
| tests/test_provider_connection.py | 8508b0dd0f97df5288adba43ecb823958b15744c5a9eb13ffb12008fc66afefe |
| .test-temp/o02-fix2-red.log | fdd94adb3d49cfe0025fb2608b09c7f4c5f8224d13dbcdb781761004062547f1 |
| .test-temp/o02-fix2-green.log | a8841ac0a214560b3a356721a43147b07934dfcd96754c9439e3a71eb6509be4 |
| .test-temp/o02-fix2-ruff.log | a4443afdcfb6d7363adb285762515ccf7cf50473b1a05c20c1a50f6bed4d26b0 |

Dialog/init stay unchanged. Root capture/fresh scoped review remain Pending. M1 remains deferred to U04; full O02 Settings/native/download/Windows gates remain **Pending**, not Done/Verified.


## Native integration phase A author freeze — 2026-10-06

Actual LauncherWindow → NativeSetupController → ProviderDialog wiring now uses the selected migrated storage and lazy native credential store. Fenced SID/profile persistence saves nonsecret choices, preserves unrelated settings and embedding identity, and reopens UNKNOWN. Provider health retains the owning original TTL; external selection changes/expiry/missing keys withdraw accepted metadata evidence. Native close cancels/drains provider callbacks before storage/fence disposal. UI says metadata was checked and runtime activation still needs confirmation.

Meaningful RED on real SQLite/MySQL preceded missing handler, stale configuration evidence, generic AI readiness label, shared-endpoint embedding identity change, embedding-disconnect gate, stale defaults and changed-backend refusal corrections. Fake stores and httpx.MockTransport only; no actual secrets/provider calls/downloads/paid APIs.

Final current source: **52 passed in 417.96s**, exit 0, no skips/failures, covering new provider integration plus existing native setup/context/UI/worker/health tests on actual SQLite and owned UUID MySQL schemas with Qt. MySQL emitted 30-second faulthandler diagnostics during migration DDL waits; affected tests subsequently passed. Tee log is stdout only; stderr diagnostics remain in execution transcript. Earlier **102 passed in 373.55s** includes approved provider regressions but precedes the final current-default/backend refusal correction. Logical Qt scales 1.25/1.5/2 each passed (1 passed,32 deselected); physical Windows evidence remains Pending. Frozen-source Ruff passed.

Exact commands, raw hashes, before-byte ownership, RED results, limitations and proposed owning producer interfaces are in `.superpowers/sdd/windows-public-beta-2026-10-02/task-O02-integration-report.md`. Review packet: `review-O02-integration.diff.md` and `review-O02-integration.manifest.json` in that same ledger. app.py and approved provider service/original tests are unchanged anchors. Only provider_context.py/test_provider_context.py, setup_context.py/setup_ui.py and dialogs/provider.py changed.

Runtime engine rebuild/reconciliation, trusted native-to-worker observation relay, real known-size model download, consented/budgeted first inference, dimension/reindex activation and browser NativeCommand relay remain **Pending**. Native metadata/cache is not running worker activation proof. Independent phase A review and root integration/Git acceptance remain Pending; full O02 is not Done/Verified. M1 shadow clipping stays deferred to U04.

| Source/test file | Raw SHA256 |
|---|---|
| src/tg_assistant/desktop/provider_context.py (new) | 7adbee63a293cefb3899e2390fcb0e6bad7489c9c968312fc0b51f6bfcc9c42c |
| tests/desktop/test_provider_context.py (new) | 6616c842c398ddfb9d9f28ed9b9247fe8b9b9e19ed20eaa8fde7e97cadfec646 |
| src/tg_assistant/desktop/setup_context.py (modified) | 3080b9f0b19fbb5707edc939985e727d1d428d59a0332dc602776bfbf7295aba |
| src/tg_assistant/desktop/setup_ui.py (modified) | f67c291715be40422c34aca78c820fa29be4914d493f767e3e8bf05015d265ae |
| src/tg_assistant/desktop/dialogs/provider.py (modified) | 33721a529ef8880c38f93b45640341786957aaab222db1c47f3eb9dd732d5fcd |
| src/tg_assistant/desktop/app.py (unchanged-anchor) | f61fccbdc74ba123160c2c2a27fb1cae2b4aaa8d6bc20bbe04d9431bb2d28b98 |
| src/tg_assistant/services/provider_connections.py (unchanged-anchor) | d38097780b0effa8bd4dd5ec32bd979da50f888c6633771b30abcaa20a08dbdb |
| tests/test_provider_connection.py (unchanged-anchor) | 8508b0dd0f97df5288adba43ecb823958b15744c5a9eb13ffb12008fc66afefe |
