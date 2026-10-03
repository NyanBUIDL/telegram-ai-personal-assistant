# R01 follow-through: preserve exact Chat IDs in dashboard writes

Coordinator authorized this narrow repair after frontend cleanup commit06ec88d4. Learning selection and the existing “Luôn giữ” action now transport canonical decimal strings, including large positive/negative IDs beyond JavaScript's exact integer range. Safe legacy preference integers normalize to strings; unsafe numbers, booleans, floats, zero and noncanonical strings refuse with the fixed message “Chat ID không hợp lệ.” before any HTTP request. The browser helper reuses the generated contract Chat-ID pattern; the generated schema is unchanged. Numeric page sizes/quotas remain numeric.

Preferences GET and PUT responses now serialize the two ID lists as canonical decimal strings before JavaScript parses JSON. Stored legacy JSON integers remain exact Python integers, with no data migration or historical rewrite. Recommendation exclusion compares the public decimal strings as Python integers, preserving both keep and ignore behavior. Existing Pydantic request lists remain compatible and unchanged. No other API/public DTO, production mock, toast action, navigation, styling or font change was introduced.

Owned source/tests: `dashboard-prototype/src/chatIds.js`, `dashboard-prototype/src/api.js`, `dashboard-prototype/src/views/GroupsView.jsx`, `src/tg_assistant/admin_api/app.py`, `dashboard-prototype/tests/chat-ids.test.mjs`, `tests/test_admin_api.py` (one added round-trip test).

## Test evidence

Tests ran before implementation using bundled Node24 and installed `.venv-q01` Python with synthetic fixtures only:

| Command/check | RED | GREEN |
| --- | --- | --- |
| Frontend `node --test tests/chat-ids.test.mjs` |21failed: actual fetch JSON rounds ±9007199254740993; numeric legacy arrays and invalid HTTP transport |21passed,0skipped; exact signed string payloads, legacy normalization, deduplication and sanitized invalid refusal |
| `python -m pytest tests/test_admin_api.py -k preferences_large_chat_ids -q --basetemp=.test-temp/cleanup/chatids-red` |1failed on actual PUT response numeric9007199254740993 instead of string |Entire `tests/test_admin_api.py`11passed,0skipped with basetemp`.test-temp/cleanup/chatids-green` |
| Isolated real GroupsView keep button probe `node dashboard-prototype/node_modules/@playwright/test/cli.js test --config=.test-temp/cleanup/chatids-browser.config.mjs` |Not required as a separate RED; actual payload and endpoint RED above |2passed6.0s;390px actual button for each large signed ID captures exact JSON and reload removes kept row |
| `npm run lint` / `npm run build` |— |Exit0;4583modules; Sites build files generated |
| `node --test tests/sites-worker.test.mjs` |— |4passed,0skipped |
| `python -m ruff check src/tg_assistant/admin_api/app.py tests/test_admin_api.py` |— |Pass |
| `python -m ruff format --check tests/test_admin_api.py` |— |Pass; only appended test formatted |

Execution logs: `.test-temp/cleanup/chatids-node-red.log`, `chatids-python-red.log`, `chatids-node-green.log`, `chatids-python-green.log`, `chatids-browser-green.log`. The browser uses installed Chromium139/build1181, CI=true, local Vite5175 and synthetic in-memory API responses. Unexpected console/page errors fail via the existing browser guard. Backend tests use the existing disposable SQLite Admin API fixture, fake auth and mocked provider; no real credentials, secret store, profile, Telegram or paid/network provider calls.

`ruff format --check app.py` still reports historical formatting in unrelated source blocks; the repair keeps those outside its scope. No external publication or new remote CI run was performed. Coordinator independently inspected all six source/test files and approved this scoped repair with no unresolved Critical/Important finding. Fresh independent execution: Node21pass/0skip and complete Admin API11pass/0skip, `.test-temp/cleanup/root-chatids-review.log`. Coordinator also added the explicit Node command to the owned frontend CI job; remote execution remains pending. Code/test readiness alone does not establish packaged Windows readiness.
