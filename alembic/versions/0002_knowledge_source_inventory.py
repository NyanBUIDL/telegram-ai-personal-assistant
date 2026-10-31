"""Add persistent knowledge source inventory.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    def create_index(name: str, table: str, columns: list[str], *, unique: bool = False) -> None:
        indexes = {row["name"] for row in sa.inspect(bind).get_indexes(table)}
        if name not in indexes:
            op.create_index(name, table, columns, unique=unique)

    # The initial revision creates current metadata on a fresh installation.
    if not sa.inspect(bind).has_table("knowledge_sources"):
        op.create_table(
            "knowledge_sources",
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column(
                "status",
                sa.String(length=32),
                server_default="not_learned",
                nullable=False,
            ),
            sa.Column(
                "requested_for_learning",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            ),
            sa.Column(
                "mysql_message_count",
                sa.Integer(),
                server_default="0",
                nullable=False,
            ),
            sa.Column(
                "text_message_count",
                sa.Integer(),
                server_default="0",
                nullable=False,
            ),
            sa.Column(
                "last_indexed_count",
                sa.Integer(),
                server_default="0",
                nullable=False,
            ),
            sa.Column("last_job_id", sa.String(length=36), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("owner_note", sa.Text(), nullable=True),
            sa.Column("last_learned_at", sa.DateTime(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["chat_id"],
                ["telegram_chats.chat_id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("chat_id"),
        )
    create_index(
        "ix_knowledge_sources_status",
        "knowledge_sources",
        ["status"],
        unique=False,
    )
    create_index(
        "ix_knowledge_sources_requested_for_learning",
        "knowledge_sources",
        ["requested_for_learning"],
        unique=False,
    )
    create_index(
        "ix_knowledge_sources_last_job_id",
        "knowledge_sources",
        ["last_job_id"],
        unique=False,
    )
    create_index(
        "ix_knowledge_sources_last_learned_at",
        "knowledge_sources",
        ["last_learned_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_sources_last_learned_at",
        table_name="knowledge_sources",
    )
    op.drop_index("ix_knowledge_sources_last_job_id", table_name="knowledge_sources")
    op.drop_index(
        "ix_knowledge_sources_requested_for_learning",
        table_name="knowledge_sources",
    )
    op.drop_index("ix_knowledge_sources_status", table_name="knowledge_sources")
    op.drop_table("knowledge_sources")
