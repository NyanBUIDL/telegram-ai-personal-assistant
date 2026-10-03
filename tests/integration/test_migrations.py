"""Real Alembic upgrades against disposable databases; no ORM schema creation."""

from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from alembic import command

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def forbid_application_credentials(monkeypatch):
    from tg_assistant.security import SecretStore

    def forbidden(*args, **kwargs):
        raise AssertionError("Explicit migration connection must not access saved credentials")

    monkeypatch.setattr(SecretStore, "get", forbidden)


def config(connection, *, backups=None, unchecked=False):
    value = Config(str(ROOT / "alembic.ini"))
    value.set_main_option("script_location", str(ROOT / "alembic"))
    value.attributes["connection"] = connection
    value.attributes["backup_dir"] = backups
    value.attributes["skip_schema_check"] = unchecked
    return value


@pytest.fixture(params=["sqlite", "mysql"])
def connection(tmp_path, request):
    admin = None
    if request.param == "mysql":
        value = os.environ.get("TG_TEST_MYSQL_URL")
        if not value:
            pytest.skip("TG_TEST_MYSQL_URL disposable MySQL schema not configured")
        url = sa.engine.make_url(value)
        assert (url.database or "").startswith("codex_"), "Disposable fixture URL required"
        admin = sa.create_engine(url.set(database=None))
        name = "codex_f02_" + uuid4().hex
        with admin.connect() as setup:
            setup.exec_driver_sql(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
        engine = sa.create_engine(url.set(database=name).update_query_dict({"charset": "utf8mb4"}))
    else:
        engine = sa.create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    with engine.connect() as connection:
        if request.param == "sqlite":
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
        yield connection
    engine.dispose()
    if admin:
        with admin.connect() as cleanup:
            cleanup.exec_driver_sql(f"DROP DATABASE `{name}`")
        admin.dispose()


def revision(connection):
    return connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()


def head(connection):
    return ScriptDirectory.from_config(config(connection)).get_current_head()


def insert_chat(connection):
    connection.execute(
        sa.text(
            "INSERT INTO telegram_chats (chat_id, title, chat_type, created_at, updated_at) "
            "VALUES (-1009007199254740993, 'Tiếng Việt', 'supergroup', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    )
    connection.commit()


def test_empty_database_upgrade_head_sqlite_mysql(connection):
    command.upgrade(config(connection), "head")
    assert revision(connection) == head(connection)
    assert {"knowledge_sources", "vector_stores", "telegram_messages"} <= set(
        sa.inspect(connection).get_table_names()
    )
    # A SQLite INTEGER PK must allocate row IDs without explicit values.
    connection.execute(
        sa.text(
            "INSERT INTO runtime_metrics (collected_at, process_id, process_name) "
            "VALUES (CURRENT_TIMESTAMP, 1, 'worker')"
        )
    )
    assert connection.execute(sa.text("SELECT id FROM runtime_metrics")).scalar() == 1


@pytest.mark.parametrize("old_revision", ["0001", "0004"])
def test_legacy_revision_upgrade_without_loss(connection, old_revision):
    command.upgrade(config(connection), old_revision)
    insert_chat(connection)
    connection.execute(
        sa.text(
            "INSERT INTO telegram_messages (id, chat_id, message_id, sender_id, text, sent_at, "
            "is_outgoing, is_deleted, has_media, created_at, updated_at) "
            "VALUES (9007199254740995, -1009007199254740993, 9007199254740997, "
            "9007199254740993, 'Giữ nội dung', CURRENT_TIMESTAMP, 0, 0, 0, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    )
    connection.commit()
    command.upgrade(config(connection), "head")
    assert revision(connection) == head(connection)
    assert connection.execute(sa.text("SELECT chat_id, title FROM telegram_chats")).one() == (
        -1009007199254740993,
        "Tiếng Việt",
    )
    assert connection.execute(
        sa.text("SELECT id, chat_id, message_id, sender_id, text FROM telegram_messages")
    ).one() == (
        9007199254740995,
        -1009007199254740993,
        9007199254740997,
        9007199254740993,
        "Giữ nội dung",
    )


def test_unknown_schema_refused_untouched(connection, tmp_path):
    connection.execute(sa.text("CREATE TABLE unrelated (id INTEGER PRIMARY KEY, note TEXT)"))
    connection.execute(sa.text("INSERT INTO unrelated VALUES (7, 'keep')"))
    connection.commit()
    with pytest.raises(RuntimeError, match="unknown_schema"):
        command.upgrade(config(connection, backups=tmp_path / "backups"), "head")
    assert sa.inspect(connection).get_table_names() == ["unrelated"]
    assert connection.execute(sa.text("SELECT note FROM unrelated")).scalar() == "keep"
    assert not (tmp_path / "backups").exists()


def test_repeated_upgrade_noop(connection, tmp_path):
    command.upgrade(config(connection), "head")
    insert_chat(connection)
    command.upgrade(config(connection, backups=tmp_path / "backups"), "head")
    assert revision(connection) == head(connection)
    assert connection.execute(sa.text("SELECT count(*) FROM telegram_chats")).scalar() == 1
    assert not (tmp_path / "backups").exists()


def original_create_all(
    connection,
    *,
    partial=False,
    stamped=False,
    altered_default=False,
    altered_check=False,
    altered_fk=False,
    altered_integer=False,
):
    dialect = connection.dialect.name
    sql = (ROOT / f"tests/fixtures/legacy_26827cfb_{dialect}.ddl").read_text(encoding="utf-8")
    if altered_default:
        timestamp = "CURRENT_TIMESTAMP" if dialect == "sqlite" else "(now())"
        sql = sql.replace(timestamp, "'2000-01-01 00:00:00'", 1)
    if altered_check:
        sql = sql.replace("summary TEXT", "summary TEXT CHECK(length(summary) < 500)", 1)
    if altered_fk:
        sql = sql.replace(
            "REFERENCES ai_memories (id)", "REFERENCES ai_memories (id) ON UPDATE CASCADE", 1
        )
    if altered_integer:
        sql = sql.replace("queued_jobs INTEGER", "queued_jobs SMALLINT", 1)
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    if partial:
        # First complete table in the original dependency order: ai_conversations.
        statements = statements[:1]
    for statement in statements:
        connection.exec_driver_sql(statement)
    if stamped:
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
        connection.exec_driver_sql("INSERT INTO alembic_version VALUES ('0001')")
        if isinstance(stamped, str):
            connection.execute(
                sa.text("UPDATE alembic_version SET version_num=:revision"), {"revision": stamped}
            )
    connection.commit()


@pytest.mark.parametrize(
    "alteration", ["default", "view", "trigger", "check", "fk_update", "smallint"]
)
def test_known_layout_with_unknown_semantics_refused(connection, tmp_path, alteration):
    original_create_all(
        connection,
        altered_default=alteration == "default",
        altered_check=alteration == "check",
        altered_fk=alteration == "fk_update",
        altered_integer=alteration == "smallint",
    )
    if alteration == "view":
        connection.exec_driver_sql("CREATE VIEW unexpected_view AS SELECT id FROM ai_conversations")
    elif alteration == "trigger":
        statement = (
            "CREATE TRIGGER unexpected_trigger AFTER INSERT ON ai_conversations "
            "BEGIN UPDATE ai_conversations SET summary='changed' WHERE id=NEW.id; END"
            if connection.dialect.name == "sqlite"
            else "CREATE TRIGGER unexpected_trigger BEFORE INSERT ON ai_conversations "
            "FOR EACH ROW SET NEW.summary='changed'"
        )
        connection.exec_driver_sql(statement)
    connection.commit()
    from tg_assistant.db.migrations import fingerprint

    before = fingerprint(connection)
    with pytest.raises(RuntimeError, match="unknown_schema"):
        command.upgrade(config(connection, backups=tmp_path / "backups"), "head")
    assert fingerprint(connection) == before
    assert "alembic_version" not in sa.inspect(connection).get_table_names()
    assert not (tmp_path / "backups").exists()


def test_sqlite_integer_pk_alias_refused(connection, tmp_path):
    if connection.dialect.name != "sqlite":
        pytest.skip("SQLite requires exact INTEGER declaration for generated rowid")
    dialect = connection.dialect.name
    sql = (ROOT / f"tests/fixtures/legacy_26827cfb_{dialect}.ddl").read_text(encoding="utf-8")
    sql = sql.replace("id INTEGER NOT NULL", "id INT NOT NULL", 1)
    for statement in sql.split(";"):
        if statement.strip():
            connection.exec_driver_sql(statement)
    connection.commit()
    with pytest.raises(RuntimeError, match="unknown_schema"):
        command.upgrade(config(connection, backups=tmp_path / "backups"), "head")
    assert "alembic_version" not in sa.inspect(connection).get_table_names()
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize(
    "alteration",
    [
        "without_rowid",
        "pk_nocase",
        "unique_nocase",
        "index_nocase",
        "index_desc",
        "partial_index",
        "strict",
    ],
)
def test_sqlite_table_key_and_index_semantics_refused_untouched(tmp_path, alteration):
    """Unknown SQLite key/storage semantics must be rejected before snapshot/DDL."""
    sql = (ROOT / "tests/fixtures/legacy_26827cfb_sqlite.ddl").read_text(encoding="utf-8")
    statements = [statement.strip() for statement in sql.split(";") if statement.strip()]
    for number, statement in enumerate(statements):
        if alteration in {"without_rowid", "strict"} and statement.startswith(
            "CREATE TABLE runtime_metrics"
        ):
            if alteration == "strict":
                # STRICT accepts only SQLite's canonical scalar type names.
                statement = re.sub(r"VARCHAR\(\d+\)|DATETIME|JSON", "TEXT", statement)
                statement = statement.replace("FLOAT", "REAL").replace("BIGINT", "INTEGER")
            statement += " WITHOUT ROWID" if alteration == "without_rowid" else " STRICT"
        elif alteration == "pk_nocase" and statement.startswith("CREATE TABLE ai_conversations"):
            statement = statement.replace(
                "id VARCHAR(36) NOT NULL", "id VARCHAR(36) COLLATE NOCASE NOT NULL", 1
            )
        elif alteration == "unique_nocase" and statement.startswith("CREATE TABLE tags"):
            statement = statement.replace("UNIQUE (name)", "UNIQUE (name COLLATE NOCASE)")
        elif statement.startswith("CREATE INDEX ix_ai_memories_scope_id"):
            if alteration == "index_nocase":
                statement = statement.replace("(scope_id)", "(scope_id COLLATE NOCASE)")
            elif alteration == "index_desc":
                statement = statement.replace("(scope_id)", "(scope_id DESC)")
            elif alteration == "partial_index":
                statement += " WHERE status='active'"
        statements[number] = statement
    engine = sa.create_engine("sqlite://")
    try:
        with engine.connect() as database:
            for statement in statements:
                database.exec_driver_sql(statement)
            database.exec_driver_sql(
                "INSERT INTO ai_conversations (id, owner_id) VALUES ('ABC', 1)"
            )
            database.commit()
            schema_query = "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
            before = database.exec_driver_sql(schema_query).all()
            with pytest.raises(RuntimeError, match="unknown_schema"):
                command.upgrade(config(database, backups=tmp_path / "backups"), "head")
            assert database.exec_driver_sql(schema_query).all() == before
            assert database.exec_driver_sql("SELECT id, owner_id FROM ai_conversations").all() == [
                ("ABC", 1)
            ]
            assert "alembic_version" not in sa.inspect(database).get_table_names()
            assert not (tmp_path / "backups").exists()
    finally:
        engine.dispose()


@pytest.mark.parametrize("alteration", ["without_rowid", "pk_nocase"])
def test_sqlite_revision_table_semantics_refused_untouched(tmp_path, alteration):
    engine = sa.create_engine("sqlite://")
    try:
        with engine.connect() as database:
            original_create_all(database)
            collation = " COLLATE NOCASE" if alteration == "pk_nocase" else ""
            flags = " WITHOUT ROWID" if alteration == "without_rowid" else ""
            database.exec_driver_sql(
                f"CREATE TABLE alembic_version (version_num VARCHAR(32){collation} "
                f"NOT NULL PRIMARY KEY){flags}"
            )
            database.exec_driver_sql("INSERT INTO alembic_version VALUES ('0001')")
            database.commit()
            schema_query = "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            before = database.exec_driver_sql(schema_query).all()
            with pytest.raises(RuntimeError, match="unknown_schema"):
                command.upgrade(config(database, backups=tmp_path / "backups"), "head")
            assert database.exec_driver_sql(schema_query).all() == before
            assert database.exec_driver_sql("SELECT version_num FROM alembic_version").all() == [
                ("0001",)
            ]
            assert not (tmp_path / "backups").exists()
    finally:
        engine.dispose()


def mysql_schema_definition(connection):
    quote = connection.dialect.identifier_preparer.quote
    return {
        table: connection.exec_driver_sql(f"SHOW CREATE TABLE {quote(table)}").one()[1]
        for table in sa.inspect(connection).get_table_names()
    }


@pytest.mark.parametrize("connection", ["mysql"], indirect=True)
@pytest.mark.parametrize(
    "alteration",
    [
        "key_collation",
        "column_charset",
        "table_collation",
        "prefix",
        "descending",
        "invisible",
        "expression",
        "unique_prefix",
    ],
)
def test_mysql_effective_collation_and_index_semantics_refused_untouched(
    connection, tmp_path, alteration
):
    original_create_all(connection)
    if alteration == "key_collation":
        fk = "fk_ai_conversation_messages_conversation_id_ai_conversations"
        connection.exec_driver_sql(f"ALTER TABLE ai_conversation_messages DROP FOREIGN KEY {fk}")
        for table, column in (
            ("ai_conversations", "id"),
            ("ai_conversation_messages", "conversation_id"),
        ):
            connection.exec_driver_sql(
                f"ALTER TABLE {table} MODIFY {column} VARCHAR(36) CHARACTER SET utf8mb4 "
                "COLLATE utf8mb4_bin NOT NULL"
            )
        connection.exec_driver_sql(
            f"ALTER TABLE ai_conversation_messages ADD CONSTRAINT {fk} FOREIGN KEY "
            "(conversation_id) REFERENCES ai_conversations(id) ON DELETE CASCADE"
        )
    elif alteration == "column_charset":
        connection.exec_driver_sql(
            "ALTER TABLE ai_memories MODIFY scope_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin"
        )
    elif alteration == "table_collation":
        connection.exec_driver_sql(
            "ALTER TABLE ai_memories DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
        )
    elif alteration == "invisible":
        connection.exec_driver_sql(
            "ALTER TABLE ai_memories ALTER INDEX ix_ai_memories_scope_id INVISIBLE"
        )
    elif alteration == "unique_prefix":
        connection.exec_driver_sql(
            "ALTER TABLE tags DROP INDEX uq_tags_name, ADD UNIQUE INDEX uq_tags_name (name(1))"
        )
    else:
        connection.exec_driver_sql("DROP INDEX ix_ai_memories_scope_id ON ai_memories")
        key = {
            "prefix": "scope_id(1)",
            "descending": "scope_id DESC",
            "expression": "(lower(scope_id))",
        }[alteration]
        connection.exec_driver_sql(f"CREATE INDEX ix_ai_memories_scope_id ON ai_memories ({key})")
    connection.exec_driver_sql("INSERT INTO ai_conversations (id, owner_id) VALUES ('ABC', 1)")
    connection.commit()
    before = mysql_schema_definition(connection)
    with pytest.raises(RuntimeError, match="unknown_schema"):
        command.upgrade(config(connection, backups=tmp_path / "backups"), "head")
    assert mysql_schema_definition(connection) == before
    assert connection.exec_driver_sql("SELECT id, owner_id FROM ai_conversations").all() == [
        ("ABC", 1)
    ]
    assert "alembic_version" not in sa.inspect(connection).get_table_names()
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("connection", ["mysql"], indirect=True)
def test_mysql_version_collation_refused_untouched(connection, tmp_path):
    original_create_all(connection, stamped=True)
    connection.exec_driver_sql(
        "ALTER TABLE alembic_version MODIFY version_num VARCHAR(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL"
    )
    connection.commit()
    before = mysql_schema_definition(connection)
    with pytest.raises(RuntimeError, match="unknown_schema"):
        command.upgrade(config(connection, backups=tmp_path / "backups"), "head")
    assert mysql_schema_definition(connection) == before
    assert revision(connection) == "0001"
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("connection", ["mysql"], indirect=True)
def test_mysql_legitimate_alternate_database_default_preserved(connection, tmp_path):
    quote = connection.dialect.identifier_preparer.quote
    database = connection.exec_driver_sql("SELECT DATABASE()").scalar_one()
    connection.exec_driver_sql(
        f"ALTER DATABASE {quote(database)} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    original_create_all(connection)
    insert_chat(connection)
    command.upgrade(config(connection, backups=tmp_path / "backups"), "head")
    assert revision(connection) == head(connection)
    assert (
        connection.exec_driver_sql("SELECT title FROM telegram_chats").scalar_one() == "Tiếng Việt"
    )
    assert (
        connection.exec_driver_sql(
            "SELECT TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ai_conversations'"
        ).scalar_one()
        == "utf8mb4_unicode_ci"
    )


def test_unverified_snapshot_aborts_before_repair(connection, tmp_path):
    from tg_assistant.db.migrations import fingerprint

    original_create_all(connection, partial=True, stamped=True)
    before = fingerprint(connection)
    value = config(connection)
    value.attributes["snapshot"] = lambda unused: tmp_path / "does-not-exist"
    with pytest.raises(RuntimeError, match="snapshot_verification_failed"):
        command.upgrade(value, "head")
    assert fingerprint(connection) == before
    assert revision(connection) == "0001"


@pytest.mark.parametrize("alteration", ["unsigned", "fulltext", "engine"])
def test_mysql_changed_type_index_or_engine_refused(connection, tmp_path, alteration):
    if connection.dialect.name != "mysql":
        pytest.skip("MySQL type, index and engine semantics")
    original_create_all(connection)
    statement = {
        "unsigned": "ALTER TABLE ai_conversations MODIFY owner_id BIGINT UNSIGNED NOT NULL",
        "fulltext": "ALTER TABLE telegram_messages DROP INDEX ft_messages_text, ADD INDEX ft_messages_text (text(100))",
        "engine": "ALTER TABLE runtime_metrics ENGINE=MyISAM",
    }[alteration]
    connection.exec_driver_sql(statement)
    connection.commit()
    with pytest.raises(RuntimeError, match="unknown_schema"):
        command.upgrade(config(connection, backups=tmp_path / "backups"), "head")
    assert "alembic_version" not in sa.inspect(connection).get_table_names()
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("version", ["unknown", "0006", "malformed"])
def test_unknown_or_ahead_version_refused_before_mutation(connection, tmp_path, version):
    command.upgrade(config(connection), "0001")
    if version == "malformed":
        connection.exec_driver_sql("ALTER TABLE alembic_version ADD COLUMN extra INTEGER")
    else:
        connection.execute(
            sa.text("UPDATE alembic_version SET version_num=:version"), {"version": version}
        )
    connection.commit()
    tables = sa.inspect(connection).get_table_names()
    with pytest.raises(RuntimeError, match="unknown_schema"):
        command.upgrade(config(connection, backups=tmp_path / "backups"), "head")
    assert sa.inspect(connection).get_table_names() == tables
    assert not (tmp_path / "backups").exists()


def test_reports_actual_revision_and_frozen_initial_ignores_live_models(connection):
    from tg_assistant.db.base import Base
    from tg_assistant.db.migrations import upgrade_database

    marker = sa.Table(
        "future_model_only", Base.metadata, sa.Column("id", sa.Integer, primary_key=True)
    )
    try:
        command.upgrade(config(connection), "0001")
        assert "future_model_only" not in sa.inspect(connection).get_table_names()
    finally:
        Base.metadata.remove(marker)
    connection.commit()
    report = upgrade_database(connection, script_location=ROOT / "alembic")
    assert report.previous_revision == "0001"
    assert report.current_revision == head(connection)
    assert report.changed and report.code == "schema_upgraded"
    connection.commit()
    report = upgrade_database(connection, script_location=ROOT / "alembic")
    assert report.previous_revision == report.current_revision == head(connection)
    assert not report.changed and report.code == "schema_current"


@pytest.mark.parametrize("stamped", [None, "0001", "0004"])
def test_original_expanded_schema_snapshot_before_repair(connection, tmp_path, stamped):
    from tg_assistant.db.migrations import fingerprint

    original_create_all(connection, stamped=stamped)
    insert_chat(connection)
    preserved_text = "Tiếng Việt '; backslash \\ and newline\n100%"
    connection.execute(
        sa.text(
            "INSERT INTO ai_conversations (id, owner_id, summary, created_at, updated_at) "
            "VALUES ('snapshot-row', 9007199254740993, :summary, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"summary": preserved_text},
    )
    connection.commit()
    before = fingerprint(connection)
    connection.commit()
    command.upgrade(config(connection, backups=tmp_path / "backups"), "head")
    assert revision(connection) == head(connection)
    assert connection.execute(sa.text("SELECT title FROM telegram_chats")).scalar() == "Tiếng Việt"
    extension = "sqlite3" if connection.dialect.name == "sqlite" else "sql"
    snapshots = list((tmp_path / "backups").glob(f"*.{extension}"))
    assert len(snapshots) == 1
    if connection.dialect.name == "mysql":
        dump = snapshots[0].read_text(encoding="utf-8")
        assert "-1009007199254740993" in dump and "Tiếng Việt" in dump
        assert "authorization_epoch" not in dump
        assert "database_password" not in dump
        from pymysql.constants import CLIENT

        restored_name = "codex_f02_restore_" + uuid4().hex
        connection.exec_driver_sql(f"CREATE DATABASE `{restored_name}` CHARACTER SET utf8mb4")
        restored = sa.create_engine(
            connection.engine.url.set(database=restored_name),
            connect_args={"client_flag": CLIENT.MULTI_STATEMENTS},
        )
        try:
            with restored.connect() as saved:
                cursor = saved.connection.driver_connection.cursor()
                try:
                    cursor.execute(dump)
                    while cursor.nextset():
                        pass
                finally:
                    cursor.close()
                saved.commit()
                assert fingerprint(saved) == before
                assert (
                    saved.execute(sa.text("SELECT summary FROM ai_conversations")).scalar()
                    == preserved_text
                )
                assert (
                    saved.execute(sa.text("SELECT chat_id FROM telegram_chats")).scalar()
                    == -1009007199254740993
                )
                if stamped:
                    assert revision(saved) == stamped
                else:
                    assert "alembic_version" not in sa.inspect(saved).get_table_names()
        finally:
            restored.dispose()
            connection.exec_driver_sql(f"DROP DATABASE `{restored_name}`")
        command.upgrade(config(connection, backups=tmp_path / "backups"), "head")
        assert len(list((tmp_path / "backups").glob("*.sql"))) == 1
        return
    backup = sa.create_engine(f"sqlite:///{snapshots[0]}")
    with backup.connect() as saved:
        assert fingerprint(saved) == before
        assert (
            saved.execute(sa.text("SELECT summary FROM ai_conversations")).scalar()
            == preserved_text
        )
        assert "authorization_epoch" not in {
            c["name"] for c in sa.inspect(saved).get_columns("knowledge_sources")
        }
        assert saved.execute(sa.text("SELECT chat_id FROM telegram_chats")).scalar() == (
            -1009007199254740993
        )
        if stamped:
            assert revision(saved) == stamped
        else:
            assert "alembic_version" not in sa.inspect(saved).get_table_names()
    backup.dispose()
    command.upgrade(config(connection, backups=tmp_path / "backups"), "head")
    assert len(list((tmp_path / "backups").iterdir())) == 1


def test_partially_failed_original_create_all_preserves_rows(connection, tmp_path):
    original_create_all(connection, partial=True)
    connection.exec_driver_sql(
        "INSERT INTO ai_conversations VALUES ('conversation-1', 9007199254740993, 'keep', "
        "NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    connection.commit()
    command.upgrade(config(connection, backups=tmp_path / "backups"), "head")
    assert revision(connection) == head(connection)
    assert connection.exec_driver_sql("SELECT owner_id, summary FROM ai_conversations").one() == (
        9007199254740993,
        "keep",
    )
    assert len(list((tmp_path / "backups").iterdir())) == 1


def test_repair_requires_snapshot_and_refuses_tampered_known_schema(connection, tmp_path):
    original_create_all(connection)
    with pytest.raises(RuntimeError, match="snapshot_required"):
        command.upgrade(config(connection), "head")
    assert "alembic_version" not in sa.inspect(connection).get_table_names()
    if connection.dialect.name == "mysql":
        connection.exec_driver_sql("DROP INDEX ix_messages_chat_date ON telegram_messages")
    else:
        connection.exec_driver_sql("DROP INDEX ix_messages_chat_date")
    connection.commit()
    with pytest.raises(RuntimeError, match="unknown_schema"):
        command.upgrade(config(connection, backups=tmp_path / "backups"), "head")
    assert not (tmp_path / "backups").exists()


def test_database_downgrade_to_base(connection):
    command.upgrade(config(connection), "head")
    insert_chat(connection)
    command.downgrade(config(connection), "base")
    assert sa.inspect(connection).get_table_names() == ["alembic_version"]
    assert revision(connection) is None


@pytest.mark.parametrize("old_revision", ["0001", "0004"])
def test_unversioned_historical_schema_requires_snapshot(connection, tmp_path, old_revision):
    command.upgrade(config(connection), old_revision)
    insert_chat(connection)
    connection.exec_driver_sql("DROP TABLE alembic_version")
    connection.commit()
    with pytest.raises(RuntimeError, match="snapshot_required"):
        command.upgrade(config(connection), "head")
    value = config(connection, backups=tmp_path / "backups")
    command.upgrade(value, "head")
    assert revision(connection) == head(connection)
    assert value.attributes["migration_report"].previous_revision is None
    assert (
        connection.execute(sa.text("SELECT chat_id FROM telegram_chats")).scalar()
        == -1009007199254740993
    )
    assert len(list((tmp_path / "backups").iterdir())) == 1
