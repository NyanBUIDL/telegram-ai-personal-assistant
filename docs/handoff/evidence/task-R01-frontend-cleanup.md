# R01 follow-through: unused frontend inputs and unsupported status claims

User steering authorized removal of unusable project parts. Coordinator approved this narrow frontend cleanup: `dashboard-prototype/package.json`, `package-lock.json`, and `src/views/SystemViews.jsx`. It does not change core Telegram/API/bot/dashboard actions, public DTOs, navigation, or bundled art/font files. Root independently reviewed the exact diff, lack of Archivo imports, passing probe log and actual390px Connections rendering: approved, no scoped Critical/Important finding. No push, publication or remote CI run was performed here.

Removed `@fontsource/archivo-black` and `@fontsource/archivo-narrow` from the declared dependencies and their lock entries. They had no authored source/script/test imports; current browser CSS and native tokens use bundled PeterObscure/DarleySans. Clean npm install now installs136packages instead of138.

The system pages no longer infer ONLINE from no request error or any metric, label every database MySQL, fix the API port to8765, or assert READY/RUNTIME ACTIVE/AVAILABLE/security ACTIVE without measured readiness. Descriptive policy/feature/version badges retain those pages. Missing status uses “Chưa kiểm tra”/“Chưa có snapshot”; real measured zero remains0B/0jobs. Existing actual SSE state and API data remain visible. Documentation distinguishes having a snapshot from a verified current connection. No new readiness service or fabricated offline state was added.

## Local validation

Runtime: bundled Node24, cached npm11.6.2, installed Chromium139/build1181. Node executable: `C:/Users/brian/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe`. npm invocation uses that Node with `.../dependencies/node/node_modules/pnpm/bin/pnpm.cjs dlx npm@11.6.2`.

| Check | Actual result |
| --- | --- |
| `npm ci --no-audit --no-fund` after package/lock cleanup | Exit0;136packages installed |
| `node node_modules/eslint/bin/eslint.js src --max-warnings=0` | Exit0; no lint output |
| `node node_modules/vite/bin/vite.js build`, then `node scripts/prepare-sites-build.mjs` | Exit0;4581modules; regenerated local client/server/hosting files |
| `node --test tests/sites-worker.test.mjs` |4passed,0failed, exit0 |
| Existing `node node_modules/@playwright/test/cli.js test --config playwright.ci.config.js`, CI=true, workspace browsers, ART_EVIDENCE_DIR=`../.test-temp/cleanup/art` |11passed in11.5s, exit0; fonts, four widths, geometry, targets, focus/trap/Escape and intentional browser-error negatives |
| Isolated edited SystemViews browser probe `...cli.js test --config ../.test-temp/cleanup/playwright.config.mjs` |18passed in26.5s, exit0; four pages at360/390/1280/1440, no overflow, no unsupported labels, loaded DarleySans; real zero controls for vector bytes/RAM/scheduler |
| Owned `git diff --check` |Exit0 |

Browser probes used temporary rendering fixtures, in-memory unavailable/synthetic API responses, and a separate loopback Vite server. They did not access owner data, secrets, vaults, Telegram, providers or paid endpoints. Unexpected console/page errors fail through the existing guard. Evidence: `.test-temp/cleanup/system-views.log`, synthetic screenshots at390/1440, and isolated art screenshots under `.test-temp/cleanup/art`. The cleanup probes/config are ignored execution scratch, not production test/dependency additions. The final absent-vs-zero display adjustment was followed by another lint/build and18-case probe; unchanged art primitives were already checked by the11-case suite.

Bundled fonts, CSS and art tokens have no Git diff. SHA256 observed before/after final checks:

- PeterObscure.ttf: `9119C8FF92B9C7E6C886E5E07CAB0D997D8CA6E1C41F352B0FDC696043637314`
- DarleySans-Regular.otf: `8102EBD72FE1EF445F869FFA90C7A89D997E315FD424EBA52FE4DA7F3D555971`
- styles.css: `22BB3802E2CE5F737B56D52EFAFC592AC2FE23D079AE3C0F5A65859F678A76A2`
- design-tokens.css: `71CB5FD83EA32E013B452A39C3335B5D84245082009C8934847BFD6672251EF6`

Read-only inventory is recorded in `.superpowers/sdd/windows-public-beta-2026-10-02/task-cleanup-inventory.md`. No current production toast-only mutation was identified. Planned gated index recovery and required build/Sites glue were retained. Bulk Chat-ID Number conversions and obsolete historical prototype QA wording need appropriately scoped repair/docs handling; they were not removed or modified in this cleanup. No claim is made about font distribution rights, packaged Windows/VM readiness, whole-branch review, or new remote CI.
