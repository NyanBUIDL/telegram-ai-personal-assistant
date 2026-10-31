# Review coverage

Baseline: `7429fcff`; inventory: **28,222 tracked paths**.

All tracked paths were inventoried. This is a responsibility-based review of authored code and workflows, not a claim that every generated or historical line was read.

- Full runtime/CLI/setup/config/path/logging and migration sources: DevOps reviewer.
- Telegram modules, selected user workflows and related services/tests: PM/BA/UX implementer; long historical UAT reports inspected by headings/matrices/conclusions, not reread line by line.
- AI/services/db/policy/admin API and relevant tests: architecture reviewer; deeper regression review on RAG cache.
- Dashboard authored source/config/tests and design handoff: frontend reviewer; deeper tests on group controls/drafts/navigation.
- Root startup scripts, packaging, security/session boundaries, integration diff and evidence: main implementer.
- Independent reviewer examined final authored diff, regression quality, current-read locking and migration preservation.

Excluded from content review: `.env`, credentials/sessions, personal browser logs/captures/CSV output, vendored dependency source/binaries, bytecode, caches and generated build assets. No project-specific root `.agents`/`.codex` skills exist in the tracked inventory. Only dashboard-prototype/AGENTS.md applies to frontend; vendor-owned dependency skills are not repository instructions.

The lists below identify baseline authored files under review areas. Presence does not mean every line received equal depth. Newly added regression/CI/review files are listed in the final commit/patch.

## Backend / Telegram / AI / services (43)

- `src/tg_assistant/__init__.py`
- `src/tg_assistant/admin_api/__init__.py`
- `src/tg_assistant/admin_api/app.py`
- `src/tg_assistant/admin_api/auth.py`
- `src/tg_assistant/admin_api/schemas.py`
- `src/tg_assistant/ai/__init__.py`
- `src/tg_assistant/ai/budget.py`
- `src/tg_assistant/ai/engine.py`
- `src/tg_assistant/ai/local_first.py`
- `src/tg_assistant/ai/rag.py`
- `src/tg_assistant/ai/router.py`
- `src/tg_assistant/ai/vector.py`
- `src/tg_assistant/cli.py`
- `src/tg_assistant/config.py`
- `src/tg_assistant/db/__init__.py`
- `src/tg_assistant/db/base.py`
- `src/tg_assistant/db/models.py`
- `src/tg_assistant/logging.py`
- `src/tg_assistant/paths.py`
- `src/tg_assistant/policy.py`
- `src/tg_assistant/runtime.py`
- `src/tg_assistant/security.py`
- `src/tg_assistant/services/__init__.py`
- `src/tg_assistant/services/actions.py`
- `src/tg_assistant/services/coingecko.py`
- `src/tg_assistant/services/daily_digest.py`
- `src/tg_assistant/services/files.py`
- `src/tg_assistant/services/history_export.py`
- `src/tg_assistant/services/knowledge_inventory.py`
- `src/tg_assistant/services/memory.py`
- `src/tg_assistant/services/ollama.py`
- `src/tg_assistant/services/operations.py`
- `src/tg_assistant/services/search.py`
- `src/tg_assistant/services/tasks.py`
- `src/tg_assistant/services/timeparse.py`
- `src/tg_assistant/services/vector_reliability.py`
- `src/tg_assistant/setup/__init__.py`
- `src/tg_assistant/setup/mysql.py`
- `src/tg_assistant/setup/wizard.py`
- `src/tg_assistant/telegram/__init__.py`
- `src/tg_assistant/telegram/control_bot.py`
- `src/tg_assistant/telegram/pairing.py`
- `src/tg_assistant/telegram/user_client.py`

## Tests Python (27)

- `tests/conftest.py`
- `tests/test_actions.py`
- `tests/test_admin_api.py`
- `tests/test_ai_rate_limit.py`
- `tests/test_ai_router.py`
- `tests/test_api_key_setup.py`
- `tests/test_coingecko.py`
- `tests/test_control_bot_formatting.py`
- `tests/test_daily_digest.py`
- `tests/test_files.py`
- `tests/test_group_ai_ask.py`
- `tests/test_history_export.py`
- `tests/test_knowledge_inventory.py`
- `tests/test_knowledge_learning.py`
- `tests/test_ollama_engine.py`
- `tests/test_ollama_service.py`
- `tests/test_openai_endpoint.py`
- `tests/test_operations.py`
- `tests/test_policy.py`
- `tests/test_qdrant_local.py`
- `tests/test_rag_shared_corpus.py`
- `tests/test_schema.py`
- `tests/test_search_budget_pairing.py`
- `tests/test_security_memory.py`
- `tests/test_sync.py`
- `tests/test_tasks_time.py`
- `tests/test_vector_reliability.py`

## Migration (6)

- `alembic/env.py`
- `alembic/versions/0001_initial_schema.py`
- `alembic/versions/0002_knowledge_source_inventory.py`
- `alembic/versions/0003_ai_routing_operations.py`
- `alembic/versions/0004_local_first_ai.py`
- `alembic/versions/0005_vector_store_reliability.py`

## Dashboard authored source/config/tests (27)

- `dashboard-prototype/.openai/hosting.json`
- `dashboard-prototype/AGENTS.md`
- `dashboard-prototype/design-brief.md`
- `dashboard-prototype/design-qa.md`
- `dashboard-prototype/eslint.config.js`
- `dashboard-prototype/index.html`
- `dashboard-prototype/package-lock.json`
- `dashboard-prototype/package.json`
- `dashboard-prototype/scripts/prepare-sites-build.mjs`
- `dashboard-prototype/src/App.jsx`
- `dashboard-prototype/src/api.js`
- `dashboard-prototype/src/format.js`
- `dashboard-prototype/src/hooks.js`
- `dashboard-prototype/src/main.jsx`
- `dashboard-prototype/src/styles.css`
- `dashboard-prototype/src/ui.jsx`
- `dashboard-prototype/src/useDialogA11y.js`
- `dashboard-prototype/src/views/AiViews.jsx`
- `dashboard-prototype/src/views/GroupsView.jsx`
- `dashboard-prototype/src/views/KnowledgeView.jsx`
- `dashboard-prototype/src/views/OperationsViews.jsx`
- `dashboard-prototype/src/views/OverviewView.jsx`
- `dashboard-prototype/src/views/SystemViews.jsx`
- `dashboard-prototype/tests/sites-worker.test.mjs`
- `dashboard-prototype/tests/uat-dashboard.spec.js`
- `dashboard-prototype/vite.config.mjs`
- `dashboard-prototype/worker/index.js`

## Design handoff (6)

- `design-audit-html/DESIGN_IMPLEMENTATION_SPEC.md`
- `design-audit-html/README.md`
- `design-audit-html/app.js`
- `design-audit-html/design-qa.md`
- `design-audit-html/index.html`
- `design-audit-html/styles.css`

## Root documentation/setup (19)

- `ACTIVITY_DIAGRAM.md`
- `ARCHITECTURE.md`
- `PRIVACY.md`
- `README.md`
- `SECURITY.md`
- `TROUBLESHOOTING.md`
- `USER_ACCEPTANCE_TEST_REPORT.md`
- `USER_ACCEPTANCE_TEST_REPORT_V2.md`
- `USER_ACCEPTANCE_TEST_REPORT_V3.md`
- `USER_GUIDE.md`
- `UX_UI_DESIGN_AUDIT_REPORT.md`
- `VECTOR_STORE_RELIABILITY_PROPOSAL.md`
- `alembic.ini`
- `pyproject.toml`
- `run.bat`
- `security_best_practices_report.md`
- `setup.ps1`
- `start.bat`
- `start.ps1`
