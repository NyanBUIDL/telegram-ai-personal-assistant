# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

## Prototype design direction

- Product: Telegram AI Admin desktop dashboard.
- Product goal: turn the permitted chat history from all joined Telegram groups and channels into one continuously improving personal-assistant brain for the owner.
- Learning semantics: “learning” means Telegram sync to MySQL, cleanup/deduplication, embedding, and retrieval indexing. Never imply that the application automatically fine-tunes the selected AI model.
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
