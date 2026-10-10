# U01 — shared design tokens and art baseline

Status: Ready for independent task review; coordinator owns staging, review acceptance and commit. This evidence describes the working tree based on `25d060a1` on `codex/windows-public-beta`, checked 2026-10-03. It does not claim beta readiness or public distribution clearance.

## Change and scope

The browser and new Qt Widgets primitives share `src/tg_assistant/desktop/design_tokens.json`. `scripts/generate_design_tokens.py` generates `dashboard-prototype/src/design-tokens.css`; `--check` detects drift. Browser styles retain the locked colors and original fonts, square geometry, and black rules. Small/icon/toggle controls now have at least 44px targets and visible keyboard outlines. IconButton explicitly uses `type="button"` to avoid submitting an enclosing form. `pyproject.toml` declares the approved `desktop` extra with `PySide6-Essentials==6.11.2`; CI/desktop consumers install `.[dev,desktop]`. Qt `apply_theme` loads the actual supplied fonts, supplies the palette, logical target sizes and visible focus styling; ArtPanel supplies a square border and a hard 7-unit shadow. The future launcher must call apply_theme and supply the font asset directory; launcher wiring and package resource inclusion belong to D01/P01.

Intentional art normalization: baseline desktop login form used a 4px border and 11px magenta shadow (mobile override: 7px magenta); it now uses the approved 3px border and 7px black shadow. The mobile panel override changes 5px to canonical 7px. The login poster's decorative gradient is removed in accordance with the locked no-gradient direction. These are token corrections; layout, font binaries, product behavior and backend contracts are retained. Existing smaller secondary-control shadows remain as part of the baseline visual hierarchy.

The existing eight-digit browser login and terminal instruction are an earlier flow, kept in U01's art-only scope. D02/U02 will implement the approved native entry/ticket flow. The isolated browser component page is expressly labeled art QA, imports actual production primitives and contains no operational metrics. Login captures use the real unauthenticated screen with Admin API requests aborted. The native BEFORE image is an unthemed Qt specimen of the same test dialog, not evidence of a pre-existing native product.

## Owned files for coordinator staging

- dashboard-prototype/src/styles.css
- dashboard-prototype/src/ui.jsx
- dashboard-prototype/src/design-tokens.css
- dashboard-prototype/tests/visual/art-baseline.spec.js
- scripts/generate_design_tokens.py
- pyproject.toml (approved desktop extra only)
- src/tg_assistant/desktop/__init__.py
- src/tg_assistant/desktop/theme.py
- src/tg_assistant/desktop/design_tokens.json
- tests/test_desktop_theme.py
- docs/handoff/evidence/task-U01.md
- docs/handoff/evidence/u01/manifest.json
- docs/handoff/evidence/u01/before/*.png, after/*.png, red-replay/*.png

The ignored execution report `.superpowers/sdd/windows-public-beta-2026-10-02/task-U01-report.md` is retained for recovery. Existing root/frontend AGENTS already carry the approved constraints and were read without edits. Contracts, font assets and reference image were not changed. F01/F02 files and shared trackers are coordinator-owned.

## Fresh verification and negative evidence

Python executable: `C:/Users/brian/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`. Node executable: `C:/Users/brian/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe`. Python checks use `scripts/check.py` and the existing audit dependencies (PySide6 Essentials 6.11.2). Browser checks use installed Chrome through Playwright and local Vite at `127.0.0.1:5174`; server/test run outside the Windows network sandbox after renewed authorization.

| Command / environment | Observed result |
|---|---|
| Node node_modules/@playwright/test/cli.js test tests/visual/art-baseline.spec.js --workers=1 --reporter=line (frontend cwd) | 9 passed, 12.1s, exit 0 |
| QT_QPA_PLATFORM=offscreen, QT_SCALE_FACTOR=1; Python scripts/check.py pytest tests/test_desktop_theme.py -q --basetemp=.test-temp/u01-green-final-1 | 1 passed, exit 0, 2.90s command duration |
| Same Qt command, scale 1.25, basetemp suffix 1.25 | 1 passed, exit 0, 2.63s |
| Same Qt command, scale 1.5, basetemp suffix 1.5 | 1 passed, exit 0, 2.97s |
| Same Qt command, scale 2, basetemp suffix 2 | 1 passed, exit 0, 2.98s |
| Node node_modules/eslint/bin/eslint.js src --max-warnings=0 (frontend cwd) | exit 0, no errors |
| Python scripts/check.py ruff check src/tg_assistant/desktop scripts/generate_design_tokens.py tests/test_desktop_theme.py | All checks passed; exit 0 |
| Python scripts/generate_design_tokens.py --check | exit 0 |
| TOML parser + installed PySide6 version probe | desktop extra exactly PySide6-Essentials==6.11.2; tested runtime version6.11.2; exit0 |
| git diff --check -- dashboard-prototype/src/styles.css dashboard-prototype/src/ui.jsx | exit 0 |
| Root: Node node_modules/vite/bin/vite.js build outside sandbox | exit 0, 4581 modules, 14.33s; CSS index-CAm2C9X1.css 83.13kB; JS index-BBs3CsXJ.js 459.10kB |
| Root: Node scripts/prepare-sites-build.mjs after completed build | exit 0; dist/server/index.js and dist/.openai/hosting.json produced |
| Root: Node --test tests/sites-worker.test.mjs after prepare | 4 passed, exit 0, 110.906ms |

Browser assertions cover 360/390/1280/1440 CSS px without document overflow; canonical palette/panel border/square corners/hard shadow; font roles and loaded fonts; accessible icon/switch labels; all rendered fixture buttons and login inputs/buttons >=44x44; switch ON/OFF text with aria-checked; visible focus outline; modal Tab/Shift+Tab trap, Escape and opener focus restoration. Modal screenshot waits for actual entrance animations to finish and asserts opacity, opaque panel, elementFromPoint ownership, body/footer order and viewport containment. Long Vietnamese dialog copy is visible. Native checks use actual Qt widget geometry, focus cycle/Escape/restoration, readable copy/input ordering, 44-unit input/button targets, actual title/UI families, palette/pixel samples and shadow blur/color/offset. Screenshot pixel sampling converts interior logical coordinates using the pixmap DPR.

Original interruption-era red output was not present on disk. A fresh isolated replay therefore reads `git show HEAD:dashboard-prototype/src/styles.css` into ignored `u01-baseline-styles.css`, sets ART_STYLE_OVERRIDE to that absolute file and ART_PHASE=red-replay, and runs the 360px geometry test only. The production working tree is never reverted. Result: intended assertion failure, actual panel shadow `rgb(9, 9, 9) 5px 5px 0px 0px` versus expected 7px/7px; 1 failed, exit 1. Native ART_NATIVE_UNTHEMED=1, ART_PHASE=red-replay, scale1 runs the same test without apply_theme: intended palette assertion fails on actual #efefef versus expected #f3efdf; exit 1. Both screenshots are under red-replay. These are actual behavioral negative checks, not implementation-mirroring string tests.

Environment/test corrections are kept separate: initial resumed CSS replay used a wrong relative output path and encountered sandbox network access denial; an outside retry found the old server stopped; both are environment failures, not accepted red. A new loopback Vite server resolved this. Chrome emitted dev-HMR websocket local-network warnings while HTTP modules, actual UI rendering and every assertion passed; no product network-success claim is derived from HMR. Newly tightened native font assertion initially assumed a Peter prefix, but actual bundled family is LNTH-Peter Obscure; the assertion was corrected to the real family and all four scales rerun. Previous native DPR sampling error and transient modal screenshot were corrected before final evidence. Earlier default Vite build EPERM / denied escalation are superseded by the root's authorized fresh outside-sandbox build.

## Before/after artifacts and art review boundary

`u01/before/` retains four login and four component browser screenshots, the 390px dialog and unthemed native scale1 specimen. `u01/after/` contains four login and four component screenshots, the final-state 390px dialog, and themed Qt scales 1, 1.25, 1.5, 2. `u01/manifest.json` records hashes of exact owned source and screenshots. The resume worker inspected actual final browser dialog and native scale1.5 images; coordinator previously inspected the original reference, 1280 login before/after, corrected browser dialog and native scale1.5 and accepted the preserved art spirit. This is agent art inspection; human UAT is still Pending. Independent immutable-diff task review and commit remain coordinator steps.

Rechecked SHA256 assets exactly match `art-baseline.json`: PeterObscure `9119C8FF92B9C7E6C886E5E07CAB0D997D8CA6E1C41F352B0FDC696043637314`; DarleySans `8102EBD72FE1EF445F869FFA90C7A89D997E315FD424EBA52FE4DA7F3D555971`; reference `6383ACF8CBDDF6A7A77C66F42C80C7D3A2C5116053275F014F319F2027F96E2E`.

## Pending evidence / limits

- Physical Windows displays/VM at 100/125/150/200% DPI, display text clipping and real OS focus behavior remain Pending. Offscreen QT_SCALE_FACTOR results prove logical-scale rendering only.
- Human art signoff, screen-reader/contrast audit of the full authenticated dashboard, all backend-driven empty/loading/ready/degraded/error states, real account/setup flow and operational UAT remain Pending in later tasks. This test fixture cannot sign them off.
- Font redistribution provenance remains unresolved as documented in font-provenance-research.md; original LyonsType metadata conflicts with related OFL attribution. No font replacement or license approval is claimed. Qt/PySide6 packaging/distribution proof belongs to R01/P01.
- Clean development install with `.[dev,desktop]` is declared for Q01; this bootstrap check used audit dependencies and does not claim a fresh CI install. No Qt tests are skipped.
- Full integrated regression is coordinator-owned once F01/F02/U01 are stable. This report claims only the scoped results above.
