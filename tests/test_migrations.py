from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tg_assistant.db import models as _models  # noqa: F401
from tg_assistant.db.base import Base

VERSIONS = Path(__file__).parents[1] / "alembic" / "versions"


def upgrade(connection, first: str = "0001") -> None:
    with Operations.context(MigrationContext.configure(connection)):
        for path in sorted(VERSIONS.glob("*.py")):
            if path.name[:4] < first:
                continue
            spec = importlib.util.spec_from_file_location(path.stem, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.upgrade()


def test_fresh_migration_chain_creates_current_schema() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            upgrade(connection)
            inspector = sa.inspect(connection)
            assert set(Base.metadata.tables) <= set(inspector.get_table_names())
            for name, table in Base.metadata.tables.items():
                assert set(table.columns.keys()) <= {
                    column["name"] for column in inspector.get_columns(name)
                }
    finally:
        engine.dispose()


@pytest.mark.parametrize("partially_upgraded", [False, True])
def test_legacy_migration_preserves_rows_and_finishes_partial_schema(partially_upgraded) -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    chats = sa.Table(
        "telegram_chats", metadata,
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("title", sa.String(255)),
    )
    for name in ("telegram_chat_policies", "telegram_messages", "summaries", "ai_usage", "background_jobs"):
        sa.Table(name, metadata, sa.Column("id", sa.Integer(), primary_key=True))
    try:
        with engine.begin() as connection:
            metadata.create_all(connection)
            connection.execute(chats.insert().values(chat_id=-1001, title="Keep this source"))
            if partially_upgraded:
                with Operations.context(MigrationContext.configure(connection)):
                    path = VERSIONS / "0002_knowledge_source_inventory.py"
                    spec = importlib.util.spec_from_file_location(path.stem, path)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    module.upgrade()
                connection.exec_driver_sql("INSERT INTO knowledge_sources (chat_id, owner_note) VALUES (-1001, 'Keep this note')")
                connection.exec_driver_sql("ALTER TABLE telegram_chat_policies ADD COLUMN ai_mode VARCHAR(32) NOT NULL DEFAULT 'local_only'")
                connection.exec_driver_sql("INSERT INTO telegram_chat_policies (id, ai_mode) VALUES (1, 'local_only')")
                connection.exec_driver_sql("ALTER TABLE knowledge_sources ADD COLUMN vector_count INTEGER NOT NULL DEFAULT 0")
                connection.exec_driver_sql("DROP INDEX ix_knowledge_sources_status")
                Base.metadata.tables["runtime_metrics"].create(connection)
            upgrade(connection, first="0002")
            inspector = sa.inspect(connection)
            assert connection.execute(sa.select(chats.c.title)).scalar_one() == "Keep this source"
            assert {"ai_mode", "max_vectors", "filtering_level"} <= {
                column["name"] for column in inspector.get_columns("telegram_chat_policies")
            }
            assert {"vector_count", "message_storage_bytes", "media_storage_bytes"} <= {
                column["name"] for column in inspector.get_columns("knowledge_sources")
            }
            assert "paused_at" in {
                column["name"] for column in inspector.get_columns("background_jobs")
            }
            assert "ix_knowledge_sources_status" in {
                index["name"] for index in inspector.get_indexes("knowledge_sources")
            }
            if partially_upgraded:
                assert connection.exec_driver_sql("SELECT owner_note FROM knowledge_sources WHERE chat_id=-1001").scalar_one() == "Keep this note"
                assert connection.exec_driver_sql("SELECT ai_mode FROM telegram_chat_policies WHERE id=1").scalar_one() == "local_only"
    finally:
        engine.dispose()
