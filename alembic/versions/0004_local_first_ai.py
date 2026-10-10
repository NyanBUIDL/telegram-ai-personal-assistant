"""Add local-first embedding, cache and token-efficiency telemetry.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply the historical additions; schema repair runs before revisions."""

    def add_column(table: str, column: sa.Column) -> None:
        with op.batch_alter_table(table) as batch:
            batch.add_column(column)

    def create_index(name: str, table: str, columns: list[str]) -> None:
        op.create_index(name, table, columns, unique=False)

    message_columns = (
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("embedding_version", sa.String(64), nullable=True),
        sa.Column("embedding_provider", sa.String(32), nullable=True),
        sa.Column("embedding_model", sa.String(160), nullable=True),
        sa.Column("vector_status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("embedded_at", sa.DateTime(), nullable=True),
        sa.Column("embedding_error", sa.Text(), nullable=True),
        sa.Column("embedding_skip_reason", sa.String(32), nullable=True),
    )
    for column in message_columns:
        add_column("telegram_messages", column)
    create_index("ix_telegram_messages_content_hash", "telegram_messages", ["content_hash"])
    create_index("ix_telegram_messages_vector_status", "telegram_messages", ["vector_status"])
    create_index(
        "ix_telegram_messages_embedding_skip_reason",
        "telegram_messages",
        ["embedding_skip_reason"],
    )

    for column in (
        sa.Column("ai_efficiency_preset", sa.String(32), nullable=True),
        sa.Column("filtering_level", sa.String(32), server_default="standard", nullable=False),
        sa.Column("rag_top_k", sa.Integer(), nullable=True),
        sa.Column("rag_max_context_tokens", sa.Integer(), nullable=True),
    ):
        add_column("telegram_chat_policies", column)

    for column in (
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("model", sa.String(160), nullable=True),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("stale", sa.Boolean(), server_default=sa.false(), nullable=False),
    ):
        add_column("summaries", column)
    create_index("ix_summaries_content_hash", "summaries", ["content_hash"])
    create_index("ix_summaries_stale", "summaries", ["stale"])

    usage_columns = (
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("feature", sa.String(64), nullable=True),
        sa.Column("route", sa.String(64), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("cached_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("embedding_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_local", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
    )
    for column in usage_columns:
        add_column("ai_usage", column)
    create_index("ix_ai_usage_provider", "ai_usage", ["provider"])
    create_index("ix_ai_usage_feature", "ai_usage", ["feature"])
    create_index("ix_ai_usage_route", "ai_usage", ["route"])
    create_index("ix_ai_usage_chat_id", "ai_usage", ["chat_id"])

    op.create_table(
        "ai_query_cache",
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("normalized_query_hash", sa.String(64), nullable=False),
        sa.Column("scope_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("knowledge_version", sa.String(128), nullable=False),
        sa.Column("route", sa.String(64), nullable=False),
        sa.Column("feature", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("model", sa.String(160), nullable=True),
        sa.Column("response", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("hit_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_hit_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False
        ),
        sa.PrimaryKeyConstraint("cache_key"),
    )
    create_index(
        "ix_ai_query_cache_normalized_query_hash", "ai_query_cache", ["normalized_query_hash"]
    )
    create_index("ix_ai_query_cache_scope_chat_id", "ai_query_cache", ["scope_chat_id"])
    create_index("ix_ai_query_cache_route", "ai_query_cache", ["route"])
    create_index("ix_ai_query_cache_feature", "ai_query_cache", ["feature"])
    create_index("ix_ai_query_cache_expires_at", "ai_query_cache", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_query_cache_expires_at", table_name="ai_query_cache")
    op.drop_index("ix_ai_query_cache_feature", table_name="ai_query_cache")
    op.drop_index("ix_ai_query_cache_route", table_name="ai_query_cache")
    op.drop_index("ix_ai_query_cache_scope_chat_id", table_name="ai_query_cache")
    op.drop_index("ix_ai_query_cache_normalized_query_hash", table_name="ai_query_cache")
    op.drop_table("ai_query_cache")

    op.drop_index("ix_ai_usage_chat_id", table_name="ai_usage")
    op.drop_index("ix_ai_usage_route", table_name="ai_usage")
    op.drop_index("ix_ai_usage_feature", table_name="ai_usage")
    op.drop_index("ix_ai_usage_provider", table_name="ai_usage")
    for name in (
        "error_code",
        "fallback_used",
        "is_local",
        "cache_hit",
        "latency_ms",
        "embedding_tokens",
        "cached_tokens",
        "chat_id",
        "route",
        "feature",
        "provider",
    ):
        op.drop_column("ai_usage", name)

    op.drop_index("ix_summaries_stale", table_name="summaries")
    op.drop_index("ix_summaries_content_hash", table_name="summaries")
    for name in ("stale", "output_tokens", "input_tokens", "provider", "model", "content_hash"):
        op.drop_column("summaries", name)

    for name in (
        "rag_max_context_tokens",
        "rag_top_k",
        "filtering_level",
        "ai_efficiency_preset",
    ):
        op.drop_column("telegram_chat_policies", name)

    op.drop_index("ix_telegram_messages_embedding_skip_reason", table_name="telegram_messages")
    op.drop_index("ix_telegram_messages_vector_status", table_name="telegram_messages")
    op.drop_index("ix_telegram_messages_content_hash", table_name="telegram_messages")
    for name in (
        "embedding_skip_reason",
        "embedding_error",
        "embedded_at",
        "vector_status",
        "embedding_model",
        "embedding_provider",
        "embedding_version",
        "content_hash",
        "normalized_text",
    ):
        op.drop_column("telegram_messages", name)
