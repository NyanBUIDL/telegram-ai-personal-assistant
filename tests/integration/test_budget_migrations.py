"""V01 frozen migration compatibility and cost-type refusal controls."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from alembic import command
from tg_assistant.db.migrations import fingerprint

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(params=["sqlite", "mysql"])
def budget_connection(tmp_path, request):
    admin = None
    if request.param == "mysql":
        value = os.environ.get("TG_TEST_MYSQL_URL")
        if not value:
            pytest.skip("Disposable MySQL URL not configured")
        url = sa.engine.make_url(value)
        assert url.host in {"127.0.0.1", "localhost"} and url.port in {3306, 13307}
        assert (url.database or "").startswith("codex_")
        name = "codex_v01_" + uuid4().hex
        admin = sa.create_engine(url.set(database=None))
        with admin.connect() as setup:
            setup.exec_driver_sql(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
        engine = sa.create_engine(url.set(database=name))
    else:
        engine = sa.create_engine(f"sqlite:///{tmp_path / 'budget.db'}")
    try:
        with engine.connect() as connection:
            value = Config(str(ROOT / "alembic.ini"))
            value.set_main_option("script_location", str(ROOT / "alembic"))
            value.attributes["connection"] = connection
            yield connection, value
    finally:
        engine.dispose()
        if admin:
            with admin.connect() as cleanup:
                cleanup.exec_driver_sql(f"DROP DATABASE `{name}`")
            admin.dispose()


def test_budget_cost_migration_repeated_upgrade_retains_exact_type(budget_connection):
    connection, config = budget_connection
    command.upgrade(config, "head")
    columns = {
        column["name"]: column
        for column in sa.inspect(connection).get_columns("ai_budget_reservations")
    }
    for name in ("reserved_cost_usd", "actual_cost_usd"):
        value = columns[name]["type"]
        assert isinstance(value, sa.Numeric)
        assert (value.precision, value.scale) == (24, 12)
    before = fingerprint(connection)
    command.upgrade(config, "head")
    assert fingerprint(connection) == before
    assert (
        connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
        == ScriptDirectory.from_config(config).get_current_head()
    )


@pytest.mark.parametrize(
    "replacement",
    ["DECIMAL(23,12)", "DECIMAL(24,11)", "DECIMAL(24,12) UNSIGNED", "DECIMAL(24,12) ZEROFILL"],
)
def test_mysql_changed_budget_precision_scale_or_flags_refused(budget_connection, replacement):
    connection, config = budget_connection
    if connection.dialect.name != "mysql":
        pytest.skip("MySQL physical decimal alteration")
    command.upgrade(config, "head")
    connection.exec_driver_sql(
        f"ALTER TABLE ai_budget_reservations MODIFY reserved_cost_usd {replacement} NOT NULL"
    )
    connection.commit()
    before = fingerprint(connection)
    with pytest.raises(RuntimeError, match="unknown_schema"):
        command.upgrade(config, "head")
    assert fingerprint(connection) == before
