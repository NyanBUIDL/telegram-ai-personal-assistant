"""Durable coalescing edit/delete vector outbox."""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "telegram_messages",
        sa.Column("vector_dirty", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.create_index("ix_telegram_messages_vector_dirty", "telegram_messages", ["vector_dirty"])


def downgrade():
    op.drop_index("ix_telegram_messages_vector_dirty", table_name="telegram_messages")
    op.drop_column("telegram_messages", "vector_dirty")
