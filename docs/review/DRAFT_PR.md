# Draft pull request

URL: https://github.com/NyanBUIDL/telegram-ai-personal-assistant/pull/3
Base: master
Head: codex/ease-of-use-2026-10-31
State: draft; publication authorized; merge/deployment not authorized

## Title

Harden assistant privacy, AI failures and everyday recovery

## Body

First-run setup, migrations, Telegram navigation and cache refreshes had reliability gaps. This release also repairs a cloud classifier that bypassed source permissions, rejects known secrets at AI boundaries, validates AI output before caching, and makes runtime/shutdown failures visible.

Owner controls now operate only in the owner's private bot chat. Classification respects source READ/ALLOW, global AI and the existing route planner. Untrusted sources are separated from the question and redacted; blank/incomplete or unsupported citations are rejected. Ollama downloads require explicit success. Core tasks, scheduler jobs and aiogram updates are drained before resources close; interrupted pauses remain paused.

Dashboard changes make stale/unknown data visible, enforce the existing 500-source selection contract, and prevent old group forms writing to a newly selected group. `/start` is concise, and reminder times have explicit supported grammar/defaults and Vietnam-time acknowledgements.

Validation:313 Python tests,13 automated Chrome regressions with mocked API,4 Sites tests,lint,production build,dependency and whitespace checks pass locally. Independent review findings were reproduced and fixed with regressions. Latest GitHub Actions results must match the PR head SHA; see Checks and final delivery evidence. See docs/review/CYCLE2_REVIEW.md and CYCLE2_VALIDATION.md for scope and residual risks.

No live Telegram,model or MySQL integration was exercised. The formal security-scan service is unavailable; this is scoped source/test hardening, not a completed formal repository audit. Redaction and citation membership checks do not guarantee complete secret detection or factual accuracy.

Build dashboard-prototype with npm ci and npm run build before use. No merge or deployment is included. Readiness handshake and remaining usability/AI/accounting work are documented backlog.
