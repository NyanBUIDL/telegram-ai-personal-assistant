from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from tg_assistant.config import get_settings
from tg_assistant.db import models  # noqa: F401
from tg_assistant.db.base import Base
from tg_assistant.security import SecretStore

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

settings = get_settings()
password = SecretStore().get("database_password")
if password:
    config.set_main_option(
        "sqlalchemy.url", settings.database_url(password, async_driver=False).replace("%", "%%")
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
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
