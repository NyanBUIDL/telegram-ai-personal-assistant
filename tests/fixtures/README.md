# Historical migration fixtures

`legacy_26827cfb_sqlite.ddl` and `legacy_26827cfb_mysql.ddl` freeze the expanded
ORM schema at `26827cfb231373ce0f326553067f8f1ad849649d`, before F02. They are
independent of the current `Base.metadata`; integration tests execute this DDL
directly to reproduce the legacy initial-revision defect. Do not regenerate
these fixtures from today's models.

The matching immutable application descriptor is
`src/tg_assistant/db/legacy_0005.json`. This old expanded schema includes features
through 0005 but has ORM defaults differing from the proper revision chain.
Classification checks both layouts separately. The intended 0001 DDL subtracts
all additions in revisions 0002–0005, as approved in the implementation ledger.

SHA256:

- SQLite DDL: `93a3aa5207bf250cee28ccf0f3bc3e9544d5ef7e018ea7599a66a1412fae9881`
- MySQL DDL: `1c15d7bdcae01eefd9f5fa5b32f2a5353f6c3baaf7c2ed7f43747432a97b6a10`
- Legacy descriptor: `9fd5c482732fddb54f65fe9ede8df08840e1027b7432da735baa931b001e83df`

Every MySQL test creates a random `codex_f02_*` database and drops that database
afterward. `TG_TEST_MYSQL_URL` must name a synthetic disposable fixture with a
`codex_` database prefix. Real application configuration and saved credentials
are forbidden in this test suite.
