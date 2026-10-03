"""Add append-only metadata for local-first vector-store reliability.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vector_stores",
        sa.Column("store_id", sa.String(128), primary_key=True),
        sa.Column("path", sa.String(512), nullable=False, unique=True),
        sa.Column("collection", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(160)),
        sa.Column("embedding_version", sa.String(64)),
        sa.Column("dimension", sa.Integer()),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("last_reconciled_at", sa.DateTime()),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False
        ),
    )
    op.create_index("ix_vector_stores_role", "vector_stores", ["role"])
    op.create_index("ix_vector_stores_state", "vector_stores", ["state"])
    op.create_table(
        "vector_source_coverage",
        sa.Column(
            "store_id",
            sa.String(128),
            sa.ForeignKey("vector_stores.store_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("mysql_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("eligible_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("active_vector_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("missing_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("orphan_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("coverage_state", sa.String(32), server_default="unknown", nullable=False),
        sa.Column("reconciled_at", sa.DateTime(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False
        ),
    )
    op.create_index("ix_vector_source_coverage_chat_id", "vector_source_coverage", ["chat_id"])


def downgrade() -> None:
    op.drop_index("ix_vector_source_coverage_chat_id", table_name="vector_source_coverage")
    op.drop_table("vector_source_coverage")
    op.drop_index("ix_vector_stores_state", table_name="vector_stores")
    op.drop_index("ix_vector_stores_role", table_name="vector_stores")
    op.drop_table("vector_stores")
