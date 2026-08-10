# Design QA — Telegram AI Admin

## Phạm vi kiểm tra

- Visual reference: `reference/neo-brutalist-reference.png`
- Local preview: `http://127.0.0.1:4173/`
- Desktop viewport: 1440 × 1000
- Mobile viewport: 390 × 844
- Desktop screenshot: `output/playwright/final-backend-aligned-desktop.png`
- Mobile screenshot: `output/playwright/final-backend-aligned-mobile.png`
- QA state: Overview, 24H, three PendingActions, data simulation enabled

## Kết quả giao diện

- Retro neo-brutalist direction is retained: paper beige surfaces, square corners, heavy black strokes, hard offset shadows, and teal/magenta/yellow accents.
- Peter Obscure is limited to the brand and main page title. Darley Sans is used for panel headings, body copy, navigation, forms, tables, metrics, and controls.
- The persistent prototype banner clearly states that Admin API, owner authentication, and realtime are not connected.
- Mock KPIs, charts, service health, queues, workers, storage, audit entries, and model resource values are marked as `MOCK` or `DEMO DATA`.
- Desktop Overview has no clipped actions or unintended horizontal overflow.
- Mobile Overview collapses to one column and remains readable at 390px.
- The mobile group directory was revised after QA to show the source, type/ALLOW state, and detail action without document-level horizontal overflow.

## Backend-aligned product coverage

- Overview: production integration block, global AI provider summary, mock KPI/data chart, PendingAction queue, audit samples.
- Telegram directory: 12 realistic groups/channels, full Chat ID, type, username, ALLOW/BLOCK, AI mode, `/ask`, AUTO link rule, learning state, last sync, search, category filters, and 10-row pagination.
- Group detail: all 18 backend permissions, six group AI modes, OpenAI/OpenRouter fallback summary, retention/quota presets, `/ask`, AUTO non-admin link rule, knowledge status, and PendingAction preview.
- AI & RAG: `OLLAMA`, `OPENAI`, `OPENROUTER`, `OFF`; actual configured chat and embedding model names; unsupported global hybrid behavior is not presented as implemented.
- Local Models: `qwen3:8b`, `nomic-embed-text:latest`, dimension check, RAM/VRAM samples, pull and delete impact preview.
- Knowledge: source/job table, bulk selection, CSV import/export, queue pause/resume, job drawer, retry flow, and all required learning states.
- Storage: six allocation categories, growth samples, cleanup policies, dry-run impact preview, and PendingAction before execution.
- Workers: state, PID, resource use, queue/job, scheduling, errors, lag, retry, durations, and fallback samples.
- Telegram features: nine primary assistant capabilities with dashboard clearly positioned as a secondary operations console.

## Interaction and accessibility checks

- 24H / 7D / 30D updates the Overview chart.
- Sidebar and mobile drawer navigation work.
- Search, category filters, and pagination work in the group directory.
- Group detail settings create a visible dirty state and PendingAction preview.
- PendingAction review modal and Learning Job drawer trap focus, close with Escape, and return focus.
- Destructive and sensitive workflows use preview/confirmation.
- Chart data has a screen-reader table.
- Toast feedback uses a live region.
- Visible keyboard focus and control labels are retained.
- Browser console: 0 errors, 0 warnings.

## Automated verification

- `npm run build`: passed.
- `npm run test:sites`: 4/4 passed.
- Backend `pytest`: 131/131 passed.
- Production bundle and Sites packaging artifacts were generated locally; no deployment was performed.

## Production blockers

- Admin API
- Owner authentication and authorization
- Secure session cookie
- CSRF protection
- Rate limiting
- API-backed state persistence
- Realtime health, queue, audit, and worker updates
- Real Telegram deep link / bot configuration for the Telegram CTA

## Final conclusion

Visual and interaction prototype: PASSED.

Production integration: BLOCKED until Admin API, owner authentication, secure session, CSRF protection, rate limiting and realtime are implemented.
