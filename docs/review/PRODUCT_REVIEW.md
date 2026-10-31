# Product and Telegram usability review

Date: 2026-10-31. Scope: local source and documentation, with offline dispatcher and SQLite tests. No live Telegram messages, AI requests, credentials, production database, deployment or publishing were used.

## Product decision

The useful first release is **reliable first start and recoverable everyday interactions**: start the app, choose an authorized source, ask/search, inspect a result, leave an unfinished prompt, and recover from a bad input. Preserve the existing authorization and confirmation model.

This patch fixes Telegram recovery and date/search inconsistencies. It does not establish that every existing product feature is release ready. Historical UAT reports are evidence of earlier observations, not fresh verification of the current checkout or the owner's current data.

## Confirmed findings fixed in this patch

| Priority | Finding and concrete failure | Evidence in current source | Acceptance criteria / verification |
|---|---|---|---|
| P1 | A command sent during a text prompt was consumed as a question, coin name, search, file reminder, model name or quota input. `/groups` could never reach its handler. | `control_bot.py` state handlers now exclude slash-prefixed commands, starting at line 1772; eight waiting states covered. | From every waiting state, `/groups` opens the group hub. All eight cases reproduced the old failure and pass now. |
| P1 | Opening Help, a group category, a group page or a group detail left a stale text prompt active. The next ordinary message could enter the old flow. | `help_command` at line 1720 and group menu callbacks around lines 3294-3496. | Help and tested group/AI navigation destinations clear state. Dispatcher regression covers Help and five destinations. |
| P1 | `/cancel` without an action ID was interpreted as search text while waiting for a query. There was no command to leave the current input flow. | `cancel` at line 5012. | No-ID cancel clears input state/data, acknowledges cancellation and creates no action. Explicit `/cancel <id>` retains its existing pending-action meaning. |
| P1 | Direct `/search after:not-a-date` raised an uncaught error. A list of ten long results was sent as one message exceeding Telegram's message limit. The button flow already handled these cases. | `search` at line 4475 now calls `_search_telegram`, then `split_telegram_text`. | Invalid dates return `YYYY-MM-DD` guidance. Long results arrive in multiple messages of at most 4096 characters, including the final result. Both failures reproduced before implementation. |
| P1 | `/confirm` and `/cancel` with an unknown, expired or reused action could raise through the message handler, giving no useful Telegram reply. Callback confirmations already caught these service errors. | `confirm` at line 4996; `cancel` at line 5012. | Unknown IDs produce a response. Expired confirmation explains expiry and does not confirm the action. Existing service single-use and owner tests remain applicable. |
| P1 | At 01:00 in Vietnam, `/today` used UTC's previous date: it included yesterday's tasks and omitted tasks later in the Vietnamese day. | `task_views` at line 4932 now uses `digest_window("today")` for its day boundary. | At 2026-11-01 01:00 +07:00, include 01:30 and 19:00 tasks on Nov 1; exclude a 17:00 task on Oct 31. Regression passes. |
| P2 | Group AI consent and several docs promised answers from all learned sources, while runtime deliberately restricts group answers to the invoking chat. `USER_GUIDE.md` already described the narrower behavior. The README also omitted required `/group_block` arguments. | Runtime `chat_ids=[chat_id]` around lines 1086-1096; bot Help/consent, README group-AI section, ARCHITECTURE and PRIVACY now agree. | Group documentation and consent describe group-only data. Multi-source questions belong in the owner's private bot. `/group_block` example includes `keep|archive|delete`. Prose reviewed against runtime; retrieval behavior was not broadened. |

Line numbers refer to this working tree and may move during integration.

## Prioritized remaining backlog

1. **P1: confirmed background actions need an outcome in Telegram.** `confirm_action_button` around line 4174 tells the user to wait a few seconds and then exposes the next step. In `runtime.py:2950-2972`, ordinary success/failure is saved to audit, while only a single-source learning success sends a completion message. A failed sync can therefore look like a slow sync; users retry scans without knowing it failed. Acceptance: each confirmed action exposes its ID, pending/running/completed/failed state, actionable error and a refresh/review path; do not declare completion before commit. Cover success, revoked permission and worker failure offline. This requires runtime work outside this patch.

2. **P1 for reminder users: evening and unsupported time phrases are silently misread.** `services/timeparse.py:47-54` handles morning/afternoon but not evening, and accepts input with no time/date meaning. A local deterministic check returned `2026-11-01T09:00:00+07:00` for `9 gio toi mai` (expected 21:00 tomorrow), and `2026-10-31T10:00:00+07:00` for `xyz` at 10:30. Acceptance: support documented dayparts, reject unrecognized input, and show the interpreted local date/time before creation. Add evening, invalid-input, relative-time and date-boundary cases. Parser redesign was outside the assigned implementation scope.

3. **P2: group AI setup has a missing recovery step.** `group_actions_keyboard` at line 564 offers AI and moderation controls but no plain ALLOW/permissions action. The AI-enable handler around line 3818 rejects a blocked group with “ALLOW first.” A beginner must find a raw-ID command, leave Telegram for the dashboard, or apply a broader learning/moderation preset. Acceptance: show a least-privilege authorization step or an explicit dashboard recovery instruction, followed by the same review/confirm guard; never grant moderation simply to enable questions.

4. **P2: media filters overpromise.** `services/search.py:138-139` maps every non-link `has:` filter to `has_media=True`. Consequently `has:image` can return documents/audio/video. `has:link` only checks literal HTTP/HTTPS although auto-moderation recognizes more link forms. Acceptance: each advertised filter returns its stated type and shares the appropriate link/media classification; cover image, video, document, audio, hidden URL and plain domain separately.

5. **P2: `/start` is a long reference manual.** `help_command` at line 1720 sends all Help pages, with the main navigation buttons only on the last page. Acceptance: `/start` gives a short status and immediate group/AI entry buttons; `/help` retains the reference. Include an actionable empty-data state pointing to source authorization/learning. No feature expansion is needed.

6. **P2: task and action lists still expose internal IDs and unlocalized timestamps.** `task_list`, `task_info`, `pending_actions` and task view replies require copying UUIDs into subsequent commands. `/remind` acknowledges UTC even though its parser accepts Vietnam time. Acceptance: local timestamps explicitly show their zone; common operations provide buttons; large lists paginate or state their limit. Keep explicit action confirmation and current owner checks.

7. **Documentation follow-up:** `ACTIVITY_DIAGRAM.md` still diagrams the older group shared-corpus route. Update it to group-only retrieval and label older UAT/design reports as historical when presenting them in the documentation UI. The reports contain earlier product and vector-count observations that must not be presented as current runtime health. The current vector registry/reliability code requires its own fresh verification; this review does not infer physical vector deletion from old counts.

## Verification evidence

- Tests were written before implementation in `tests/test_control_bot_recovery.py` and exercised the real dispatcher, policy/search/action services and SQLite models. Only Telegram's transport was replaced; no external API endpoint was called.
- Initial complete recovery run: **18 failures / 3 passes**. The no-ID cancel assertion was then strengthened because mere state clearing also occurred when the old search handler consumed `/cancel`; its targeted RED run demonstrated the incorrect search reply. Thus all 19 previously broken scenarios were observed failing before their implementation change. Two existing navigation scenarios were already correct and remain guarded.
- GREEN: **41 tests passed** across `test_control_bot_recovery.py`, `test_control_bot_formatting.py`, and `test_tasks_time.py`.
- Ruff: `control_bot.py` and `test_control_bot_recovery.py` pass.
- The sandbox blocked Python asyncio's internal loopback socket and caused the initial run to hang before tests. Those processes were interrupted. Tests completed with reviewed escalation permitting the local event loop; Telegram transport remained replaced.
- Pytest emitted the existing unset `asyncio_default_fixture_loop_scope` deprecation warning. The integration owner is running the final full suite; this focused result does not substitute for that run.

## Review coverage and limits

- Read the README, user guide, architecture, privacy, security and troubleshooting documents. Inventoried every root Markdown document; inspected the historical UAT V1/V2/V3 and UX audit summaries, matrices/headings and release conditions, plus the vector-reliability proposal's conclusions. Large historical reports were not reread line by line.
- Inspected all Telegram modules (`control_bot.py`, `user_client.py`, `pairing.py`, package init), tracing selected onboarding, group selection/search, learning confirmation, owner AI/search, moderation handoff, task/reminder and confirm/cancel paths. This is a workflow review, not a claim of exhaustive security analysis of every line.
- Read related task/time parser, search/action services, runtime group scope and worker outcome handling, and relevant control formatting, action, pairing/search and task tests.
- Excluded `.env`, credentials, personal logs/output, virtual-environment contents, generated dependencies and live services. Used the prepared test interpreter without inspecting its dependency files.
- No live Telegram UX, real account permissions, real model quality, production MySQL, current vector coverage or background action delivery was tested. No deployment, push or commit was performed by this scoped implementer.
