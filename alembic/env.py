from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from tg_assistant.contracts import MigrationReport
from tg_assistant.db import models  # noqa: F401
from tg_assistant.db.base import Base
from tg_assistant.db.migrations import actual_revision, preflight

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

# Only the checked-in placeholder requests legacy application configuration.
# Explicit connections/URLs must never consult a profile or credential store.
if config.attributes.get("connection") is None and config.get_main_option("sqlalchemy.url") == (
    "mysql+pymysql://unused@127.0.0.1/telegram_ai_assistant"
):
    from tg_assistant.config import get_settings
    from tg_assistant.security import SecretStore

    settings = get_settings()
    password = SecretStore().get("database_password")
    config.set_main_option(
        "sqlalchemy.url",
        settings.database_url(password or "", async_driver=False).replace("%", "%%"),
    )
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied = config.attributes.get("connection")
    if supplied is not None:
        migrate(supplied)
        return
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        migrate(connection)


def migrate(connection) -> None:
    had_transaction = connection.in_transaction()
    if not config.attributes.get("skip_schema_check"):
        state = preflight(
            connection,
            config.get_main_option("script_location"),
            backup_dir=config.attributes.get("backup_dir"),
            snapshot=config.attributes.get("snapshot"),
        )
        previous = state.previous_revision
        if not had_transaction and connection.in_transaction():
            connection.commit()
    else:
        previous = None
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
    current = actual_revision(connection)
    if current is not None:
        config.attributes["migration_report"] = MigrationReport(
            previous_revision=previous,
            current_revision=current,
            changed=current != previous,
            code="schema_upgraded" if current != previous else "schema_current",
            next_action=None,
        )


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
