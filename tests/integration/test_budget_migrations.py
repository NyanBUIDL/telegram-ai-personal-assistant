"""V01 frozen migration compatibility and cost-type refusal controls."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from alembic import command
from tg_assistant.db.migrations import fingerprint

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(params=["sqlite"])
def budget_connection(tmp_path, request):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'budget.db'}")
    try:
        with engine.connect() as connection:
            value = Config(str(ROOT / "alembic.ini"))
            value.set_main_option("script_location", str(ROOT / "alembic"))
            value.attributes["connection"] = connection
            yield connection, value
    finally:
        engine.dispose()


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
