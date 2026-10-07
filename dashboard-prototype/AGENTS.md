# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

## Prototype design direction

- Product: Telegram AI Admin desktop dashboard.
- Product goal: turn the permitted chat history from all joined Telegram groups and channels into one continuously improving personal-assistant brain for the owner.
- Learning semantics: “learning” means Telegram sync to SQLite, cleanup/deduplication, embedding, and retrieval indexing. Never imply that the application automatically fine-tunes the selected AI model.
- Visual reference: `reference/neo-brutalist-reference.png`.
- Style: retro neo-brutalist with paper beige surfaces, condensed black type, squared corners, thick black rules, hard shadows, and teal/magenta/yellow accents.
- Interaction rule: keep controls visibly actionable, keyboard focus obvious, and destructive/sensitive operations behind preview plus owner confirmation.
- Typography: use the bundled `public/fonts/PeterObscure.ttf` only for the logo and main page title. Use `public/fonts/DarleySans-Regular.otf` for panel headings, body copy, navigation, tables, forms, buttons, metrics, and all other controls. The source files are `F:\Font\0204-LNTH-PeterObscure.ttf` and `F:\Font\DarleySans-Regular.otf`.
- Data fidelity: use only Admin API data for operational views. Clearly mark a concept unavailable when the backend has no endpoint; never invent sample values or interactions.
- Security preview: bind local development to `127.0.0.1` and treat Telegram, bot, database, and cloud credentials as write-only inputs; never render stored secret fragments.
- Product hierarchy: Telegram is the primary assistant interface; the desktop dashboard is the secondary owner/operations console.
- Group directory: represent Telegram groups and channels with full Chat ID, type, username, ALLOW/BLOCK, group AI mode, `/ask`, AUTO link rule, learning state, sync time, search, category filters, and 10-row pagination.
- Group policy: expose all 19 backend permissions, the six group AI modes, retention/quota presets, group `/ask`, knowledge controls, and the one-time owner confirmation for enabling AUTO deletion of new non-admin link messages.
- Live integration: the local Admin API, owner authentication, HttpOnly session, CSRF protection and SSE realtime are implemented at `http://127.0.0.1:8765`. Do not reintroduce prototype banners, mock values or toast-only controls.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.

## Approved Windows public-beta feedback (2026-10-02)

- V1 is Windows 10/11 x64, one active owner/profile per Windows SID, local loopback dashboard. The normal installation/setup flow requires no terminal or separately installed Python/Node/MySQL. The owner approved SQLite-only and empty new profiles on 06/10/2026; see `../docs/superpowers/specs/2026-10-06-sqlite-only-amendment.md`. Refuse legacy MySQL profiles before connection; never connect to, migrate, reset or delete existing MySQL data or erase an existing SQLite profile.
- Preserve the exact art tokens: paper `#f3efdf`, panel `#fffdf5`, ink `#090909`, teal `#00c8c8`, magenta `#ef00c8`, yellow `#ffd51f`; square 3px black borders and hard 7px shadows. PeterObscure remains logo/main-title only; DarleySans covers every other UI text/control. Font distribution licensing is a release gate.
- Main navigation: Tổng quan; Kết nối; Nguồn Telegram; Tri thức; Công việc; Vận hành; Cài đặt & trợ giúp. Overview prioritizes actual runtime/Telegram/bot/chat/embedding readiness and the next setup action. Keep advanced permissions/logs/model details accessible and preserve all 19 permissions and all six group AI modes.
- API key/token/API hash/OTP/2FA never enter browser fields, requests, query strings, bot messages, logs or analytics. Request an allowlisted native dialog through the authenticated command bridge. Never prefill saved credentials or show stored fragments. Public setup data is sanitized/read-only; writes require session, CSRF and Origin checks.
- Import shared DTOs/schema validation from `src/contracts/generated.js`; never hand-edit that file or independently rename section 9 fields/enums. Python `../src/tg_assistant/contracts.py` is the source. Telegram/chat/owner IDs are decimal strings on the wire, including large and negative Chat IDs; do not convert them to JavaScript Number. An unpaired profile has `owner_id: null` and setup-only authority; no synthetic zero owner or management UI before verified pairing.
- `checked_at` is a nullable UTC ISO timestamp; checking/unknown/stale/degraded/disconnected states must have truthful labels and a next action. `progress` is a nullable integer percentage from 0 to 100. Endpoint IDs are sanitized labels, not URLs containing credentials. Internal session/ticket/lease/token objects do not belong in public status responses.
- Launcher dashboard ticket: 256-bit, TTL 30 seconds, one-use, profile-bound; fragment is removed immediately before same-origin redeem. Pair nonce: TTL 300 seconds, one-use, verified owner-bound, start payload at most 64 characters. Browser completion clicks do not count as backend/native verified stage evidence.
- LOCAL ONLY forbids source content in cloud chat/embeddings, shared retrieval and fallback. Show the data scope/cost and obtain owner consent before learning or reindexing. Model/store identity includes provider/model/version/dimension; no silent corpus overwrite. BLOCK wins over stale requests, and uncertain actions require reconciliation.
- Touch targets are at least 44 CSS px. Verify browser widths 360/390/1280/1440 and Windows DPI 100/125/150/200%, keyboard/focus trap/ESC/restore-focus behavior and long Vietnamese copy. States require text or icons as well as color. Preserve full Chat IDs in dense tables/cards/scroll views.
- A Done claim needs actual command/test/artifact/evidence; manual human/Windows VM work stays Pending until performed. Code-ready is not beta-ready. No external publication/release, force-push or direct master merge without final review/authorization.
