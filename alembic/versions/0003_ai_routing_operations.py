"""Add per-source AI routing, quotas and operational telemetry.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "telegram_chat_policies",
        sa.Column("ai_mode", sa.String(length=32), server_default="inherit", nullable=False),
    )
    op.add_column(
        "telegram_chat_policies",
        sa.Column("preferred_cloud_provider", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "telegram_chat_policies",
        sa.Column("cloud_fallback", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "telegram_chat_policies", sa.Column("retention_days", sa.Integer(), nullable=True)
    )
    op.add_column("telegram_chat_policies", sa.Column("max_messages", sa.Integer(), nullable=True))
    op.add_column(
        "telegram_chat_policies", sa.Column("max_storage_mb", sa.Integer(), nullable=True)
    )
    op.add_column("telegram_chat_policies", sa.Column("max_vectors", sa.Integer(), nullable=True))
    op.create_index(
        "ix_telegram_chat_policies_ai_mode",
        "telegram_chat_policies",
        ["ai_mode"],
        unique=False,
    )

    op.add_column(
        "knowledge_sources",
        sa.Column("vector_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "knowledge_sources",
        sa.Column("message_storage_bytes", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "knowledge_sources",
        sa.Column("media_storage_bytes", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column("background_jobs", sa.Column("paused_at", sa.DateTime(), nullable=True))

    op.create_table(
        "runtime_metrics",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("collected_at", sa.DateTime(), nullable=False),
        sa.Column("process_id", sa.Integer(), nullable=False),
        sa.Column("process_name", sa.String(length=128), nullable=False),
        sa.Column("rss_bytes", sa.BigInteger(), nullable=True),
        sa.Column("cpu_percent", sa.Float(), nullable=True),
        sa.Column("vram_bytes", sa.BigInteger(), nullable=True),
        sa.Column("data_bytes", sa.BigInteger(), nullable=True),
        sa.Column("vector_bytes", sa.BigInteger(), nullable=True),
        sa.Column("media_bytes", sa.BigInteger(), nullable=True),
        sa.Column("queued_jobs", sa.Integer(), server_default="0", nullable=False),
        sa.Column("running_jobs", sa.Integer(), server_default="0", nullable=False),
        sa.Column("paused_jobs", sa.Integer(), server_default="0", nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_runtime_metrics_collected_at",
        "runtime_metrics",
        ["collected_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_runtime_metrics_collected_at", table_name="runtime_metrics")
    op.drop_table("runtime_metrics")
    op.drop_column("background_jobs", "paused_at")
    op.drop_column("knowledge_sources", "media_storage_bytes")
    op.drop_column("knowledge_sources", "message_storage_bytes")
    op.drop_column("knowledge_sources", "vector_count")
    op.drop_index("ix_telegram_chat_policies_ai_mode", table_name="telegram_chat_policies")
    op.drop_column("telegram_chat_policies", "max_vectors")
    op.drop_column("telegram_chat_policies", "max_storage_mb")
    op.drop_column("telegram_chat_policies", "max_messages")
    op.drop_column("telegram_chat_policies", "retention_days")
    op.drop_column("telegram_chat_policies", "cloud_fallback")
    op.drop_column("telegram_chat_policies", "preferred_cloud_provider")
    op.drop_column("telegram_chat_policies", "ai_mode")
