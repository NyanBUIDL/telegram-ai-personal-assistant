"""Conservative schema preflight for Alembic, including the old create_all defect.

Only exact historical layouts or a dependency-order prefix of the original
create_all layout may be repaired. Inspection always precedes snapshot and DDL.
The caller must fence application writers before invoking an upgrade/repair.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection

from ..contracts import MigrationReport

LEGACY = Path(__file__).with_name("legacy_0005.json")
# These columns were BIGINT autoincrement in MySQL and INTEGER in SQLite.
BIGINT_IDS = {
    table["name"]
    for table in json.loads(LEGACY.read_text(encoding="utf-8"))
    if any(
        c["name"] == "id" and c["type"] == "BigInteger" and c["autoincrement"]
        for c in table["columns"]
    )
}


@dataclass(frozen=True)
class SchemaState:
    previous_revision: str | None
    schema_revision: str | None
    kind: str
    missing_tables: tuple[str, ...] = ()


def _type(column, table: str, dialect: str) -> str:
    value = column["type"]
    if getattr(value, "unsigned", False):
        return "unsigned:" + str(value).lower()
    if (
        column["name"] == "id"
        and table in BIGINT_IDS
        and (
            (dialect == "sqlite" and column.get("declared_type", "").upper() == "INTEGER")
            or (
                dialect == "mysql"
                and isinstance(value, sa.BigInteger)
                and column.get("autoincrement")
            )
        )
    ):
        return "generated_integer"
    if type(value).__name__ in {"SMALLINT", "TINYINT", "MEDIUMINT", "DOUBLE", "REAL", "TIMESTAMP"}:
        if type(value).__name__ == "TINYINT" and getattr(value, "display_width", None) == 1:
            return "boolean"
        return str(value).lower()
    if isinstance(value, sa.Boolean) or (
        type(value).__name__ == "TINYINT" and getattr(value, "display_width", None) == 1
    ):
        return "boolean"
    if isinstance(value, sa.BigInteger):
        return "bigint"
    if isinstance(value, sa.Integer):
        return "integer"
    if isinstance(value, sa.Text):
        return "text"
    if isinstance(value, sa.String):
        return f"string:{value.length}"
    if isinstance(value, sa.DateTime):
        return "datetime"
    if isinstance(value, sa.Float):
        return "float"
    if isinstance(value, sa.JSON):
        return "json"
    return str(value).lower()


def _default(column: dict) -> str | None:
    value = column.get("default")
    if value is None:
        return None
    value = str(value).strip()
    if value.lower().strip("()") in ("current_timestamp", "now"):
        return "current_timestamp"
    if len(value) >= 2 and value[0] == value[-1] == "'":
        value = value[1:-1]
    if isinstance(column["type"], sa.Boolean) or type(column["type"]).__name__ == "TINYINT":
        return {"false": "0", "true": "1"}.get(value.lower(), value)
    return value


def fingerprint(connection: Connection) -> dict:
    inspector = sa.inspect(connection)
    result = {}
    for table in inspector.get_table_names():
        if table == "alembic_version":
            continue
        foreign_keys = inspector.get_foreign_keys(table)
        reflected_columns = inspector.get_columns(table)
        if connection.dialect.name == "sqlite":
            quoted = connection.dialect.identifier_preparer.quote(table)
            declared = {
                row[1]: row[2] for row in connection.exec_driver_sql(f"PRAGMA table_info({quoted})")
            }
            for column in reflected_columns:
                column["declared_type"] = declared[column["name"]]
        unique = {tuple(c["column_names"]) for c in inspector.get_unique_constraints(table)}
        indexes = {}
        for index in inspector.get_indexes(table):
            columns = tuple(index["column_names"])
            # MySQL reports unique constraints as indexes, and automatically
            # creates FK-supporting indexes named after the constraint.
            if index["unique"] and columns in unique:
                continue
            if connection.dialect.name == "mysql" and any(
                index["name"] == fk["name"] and columns == tuple(fk["constrained_columns"])
                for fk in foreign_keys
            ):
                continue
            kind = index.get("dialect_options", {}).get("mysql_prefix", "").upper()
            if connection.dialect.name == "sqlite" and index["name"] == "ft_messages_text":
                kind = "FULLTEXT"  # SQLite's historical equivalent is a normal index.
            indexes[index["name"]] = (columns, bool(index["unique"]), kind)
        result[table] = {
            "columns": {
                c["name"]: (
                    _type(c, table, connection.dialect.name),
                    bool(c["nullable"]),
                    _default(c),
                    bool(c.get("computed")),
                    bool(c.get("identity")),
                )
                for c in reflected_columns
            },
            "pk": tuple(inspector.get_pk_constraint(table)["constrained_columns"]),
            "unique": sorted(unique),
            "fk": sorted(
                (
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    tuple(fk["referred_columns"]),
                    fk.get("options", {}).get("ondelete", "").upper(),
                    fk.get("options", {}).get("onupdate", "").upper(),
                    fk.get("options", {}).get("deferrable", False),
                    fk.get("options", {}).get("initially", "").upper(),
                )
                for fk in foreign_keys
            ),
            "indexes": indexes,
            "checks": sorted(c["sqltext"] for c in inspector.get_check_constraints(table)),
            "transactional": connection.dialect.name == "sqlite"
            or (inspector.get_table_options(table).get("mysql_engine", "").lower() == "innodb"),
        }
    views = inspector.get_view_names()
    if connection.dialect.name == "sqlite":
        triggers = (
            connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'")
            .scalars()
            .all()
        )
    elif connection.dialect.name == "mysql":
        triggers = [row[0] for row in connection.exec_driver_sql("SHOW TRIGGERS")]
    else:
        raise RuntimeError("unsupported_migration_dialect")
    if views or triggers:
        result["__unexpected_objects__"] = {"views": sorted(views), "triggers": sorted(triggers)}
    return result


def _legacy_tables() -> list[dict]:
    return json.loads(LEGACY.read_text(encoding="utf-8"))


def _create_legacy_table(operations: Operations, table: dict) -> None:
    columns = []
    for column in table["columns"]:
        typ = getattr(sa, column["type"])
        datatype = typ(column["length"]) if column["length"] else typ()
        if column["autoincrement"] and isinstance(datatype, sa.BigInteger):
            datatype = datatype.with_variant(sa.Integer(), "sqlite")
        options = {"nullable": column["nullable"]}
        if column["default"] is not None:
            options["server_default"] = sa.func.current_timestamp()
        if column["autoincrement"]:
            options["autoincrement"] = True
        columns.append(sa.Column(column["name"], datatype, **options))
    constraints = [sa.PrimaryKeyConstraint(*table["pk"])]
    constraints.extend(sa.UniqueConstraint(*c) for c in table["unique"])
    constraints.extend(
        sa.ForeignKeyConstraint(
            fk["columns"],
            fk["target"],
            ondelete=fk["ondelete"],
            name=operations.f(
                f"fk_{table['name']}_{fk['columns'][0]}_{fk['target'][0].split('.')[0]}"
            ),
        )
        for fk in table["foreign_keys"]
    )
    operations.create_table(table["name"], *columns, *constraints)
    for index in table["indexes"]:
        options = {"mysql_prefix": index["mysql_prefix"]} if index.get("mysql_prefix") else {}
        operations.create_index(
            index["name"], table["name"], index["columns"], unique=index["unique"], **options
        )


@lru_cache(maxsize=4)
def _expected(script_location: str) -> tuple[dict[str, dict], dict]:
    """Evaluate frozen revisions in disposable storage, never ORM create_all.

    Following the script chain automatically includes later reviewed revisions;
    the original legacy fingerprint remains independent of today's ORM models.
    """
    script = ScriptDirectory(script_location)
    engine = sa.create_engine("sqlite://")
    schemas = {}
    with engine.connect() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for revision in reversed(list(script.walk_revisions())):
                revision.module.upgrade()
                schemas[revision.revision] = fingerprint(connection)
    engine.dispose()
    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        operations = Operations(MigrationContext.configure(connection))
        for table in _legacy_tables():
            _create_legacy_table(operations, table)
        legacy = {"0005": fingerprint(connection)}
        past_legacy = False
        with Operations.context(MigrationContext.configure(connection)):
            for revision in reversed(list(script.walk_revisions())):
                if past_legacy:
                    revision.module.upgrade()
                    legacy[revision.revision] = fingerprint(connection)
                if revision.revision == "0005":
                    past_legacy = True
    engine.dispose()
    return schemas, legacy


def actual_revision(connection: Connection) -> str | None:
    inspector = sa.inspect(connection)
    if "alembic_version" not in inspector.get_table_names():
        return None
    columns = inspector.get_columns("alembic_version")
    if (
        len(columns) != 1
        or columns[0]["name"] != "version_num"
        or _type(columns[0], "alembic_version", connection.dialect.name) != "string:32"
        or columns[0]["nullable"]
        or _default(columns[0]) is not None
        or tuple(inspector.get_pk_constraint("alembic_version")["constrained_columns"])
        != ("version_num",)
        or inspector.get_check_constraints("alembic_version")
        or inspector.get_foreign_keys("alembic_version")
        or inspector.get_unique_constraints("alembic_version")
        or inspector.get_indexes("alembic_version")
    ):
        raise RuntimeError("unknown_schema")
    values = connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalars().all()
    if len(values) > 1:
        raise RuntimeError("unknown_schema")
    return values[0] if values else None


def classify_schema(connection: Connection, script_location: str) -> SchemaState:
    previous = actual_revision(connection)
    actual = fingerprint(connection)
    if not actual and previous is None:
        return SchemaState(None, None, "empty")
    schemas, legacy = _expected(script_location)
    if previous in schemas and (actual == schemas[previous] or actual == legacy.get(previous)):
        return SchemaState(previous, previous, "revision")
    if previous is not None and previous not in schemas:
        raise RuntimeError("unknown_schema")
    # Full verified schemas can be stamped only with a snapshot. This includes
    # unversioned historical schemas and expanded create_all stamped too early.
    chain = list(schemas)
    for revision, expected in schemas.items():
        if actual == expected and (
            previous is None or chain.index(previous) < chain.index(revision)
        ):
            return SchemaState(previous, revision, "unversioned")
    if actual == legacy["0005"] and previous in (None, "0001", "0002", "0003", "0004", "0005"):
        return SchemaState(previous, "0005", "legacy_expanded")
    order = [table["name"] for table in _legacy_tables()]
    if previous in (None, "0001") and 0 < len(actual) < len(order):
        prefix = order[: len(actual)]
        if set(actual) == set(prefix) and all(
            actual[name] == legacy["0005"][name] for name in prefix
        ):
            return SchemaState(previous, "0005", "legacy_partial", tuple(order[len(actual) :]))
    raise RuntimeError("unknown_schema")


def snapshot_sqlite(connection: Connection, directory: Path) -> Path:
    driver = connection.connection.driver_connection
    if not isinstance(driver, sqlite3.Connection) or driver.in_transaction:
        raise RuntimeError("snapshot_requires_idle_connection")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"schema-before-repair-{uuid4().hex}.sqlite3"
    with sqlite3.connect(path) as destination:
        driver.backup(destination)
        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("snapshot_verification_failed")
    return path


def snapshot_mysql(connection: Connection, directory: Path) -> Path:
    """Consistent InnoDB logical snapshot, requiring externally fenced writers.

    Use the existing engine privately; credentials never enter the artifact or
    command arguments. Streaming rows keeps snapshot memory bounded.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"schema-before-repair-{uuid4().hex}.sql"
    with connection.engine.connect().execution_options(isolation_level="REPEATABLE READ") as saved:
        saved.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        try:
            tables = saved.exec_driver_sql("SHOW TABLE STATUS").mappings().all()
            if any(table["Engine"] != "InnoDB" for table in tables):
                raise RuntimeError("snapshot_requires_innodb")
            quote = saved.dialect.identifier_preparer.quote
            driver = saved.connection.driver_connection
            with path.open("x", encoding="utf-8", newline="\n") as output:
                output.write("-- Consistent pre-repair snapshot; restore into an empty schema.\n")
                output.write("SET FOREIGN_KEY_CHECKS=0;\n")
                for table in tables:
                    name = quote(table["Name"])
                    ddl = saved.exec_driver_sql(f"SHOW CREATE TABLE {name}").one()[1]
                    output.write(ddl + ";\n")
                    rows = saved.exec_driver_sql(
                        f"SELECT * FROM {name}",  # noqa: S608 - dialect-quoted reflected identifier
                        execution_options={"stream_results": True},
                    )
                    for row in rows:
                        values = ",".join(driver.escape(value) for value in row)
                        output.write(f"INSERT INTO {name} VALUES ({values});\n")  # noqa: S608 - quoted identifier and driver-escaped literals
                output.write("SET FOREIGN_KEY_CHECKS=1;\n")
                output.flush()
        finally:
            saved.rollback()
    return path


def preflight(
    connection: Connection,
    script_location: str,
    *,
    backup_dir: Path | None = None,
    snapshot: Callable[[Connection], Path] | None = None,
) -> SchemaState:
    state = classify_schema(connection, script_location)
    if state.kind in ("empty", "revision"):
        return state
    if snapshot is None:
        if backup_dir is None or connection.dialect.name not in ("sqlite", "mysql"):
            raise RuntimeError("snapshot_required")
        backup = snapshot_sqlite if connection.dialect.name == "sqlite" else snapshot_mysql

        def snapshot(connection: Connection) -> Path:
            return backup(connection, Path(backup_dir))

    artifact = snapshot(connection)
    if not isinstance(artifact, Path) or not artifact.is_file() or artifact.stat().st_size == 0:
        raise RuntimeError("snapshot_verification_failed")
    operations = Operations(MigrationContext.configure(connection))
    for table in _legacy_tables():
        if table["name"] in state.missing_tables:
            _create_legacy_table(operations, table)
    # Revalidate all tables after repair, before assigning any revision.
    expected = (
        _expected(script_location)[1]["0005"]
        if state.kind.startswith("legacy")
        else (_expected(script_location)[0][state.schema_revision])
    )
    if fingerprint(connection) != expected:
        raise RuntimeError("repair_verification_failed")
    context = MigrationContext.configure(connection)
    context.stamp(ScriptDirectory(script_location), state.schema_revision)
    connection.commit()
    return state


def upgrade_database(
    connection: Connection,
    *,
    script_location: Path,
    backup_dir: Path | None = None,
    snapshot: Callable[[Connection], Path] | None = None,
) -> MigrationReport:
    """Upgrade only the caller's explicit database; return its actual revision."""
    from alembic.config import Config

    from alembic import command

    config = Config()
    config.set_main_option("script_location", str(script_location))
    config.attributes.update(connection=connection, backup_dir=backup_dir, snapshot=snapshot)
    command.upgrade(config, "head")
    return config.attributes["migration_report"]
