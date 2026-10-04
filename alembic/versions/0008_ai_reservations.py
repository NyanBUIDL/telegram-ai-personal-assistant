"""V01 budget reservations and embedding endpoint identity."""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_budget_locks",
        sa.Column("profile_id", sa.String(64), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "ai_budget_reservations",
        sa.Column("request_id", sa.String(64), primary_key=True),
        sa.Column(
            "profile_id", sa.String(64), sa.ForeignKey("ai_budget_locks.profile_id"), nullable=False
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("pricing_version", sa.String(64), nullable=False),
        sa.Column("pricing_rates", sa.JSON(), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("feature", sa.String(64), nullable=False),
        sa.Column("route", sa.String(64)),
        sa.Column("chat_id", sa.BigInteger()),
        sa.Column("reserved_input_tokens", sa.Integer(), nullable=False),
        sa.Column("reserved_output_tokens", sa.Integer(), nullable=False),
        sa.Column("reserved_cost_usd", sa.Numeric(24, 12), nullable=False),
        sa.Column("is_local", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.Column("actual_input_tokens", sa.Integer()),
        sa.Column("actual_output_tokens", sa.Integer()),
        sa.Column("cached_tokens", sa.Integer()),
        sa.Column("cache_write_tokens", sa.Integer()),
        sa.Column("actual_cost_usd", sa.Numeric(24, 12)),
        sa.Column("last_error_code", sa.String(64)),
    )
    for name in ("profile_id", "occurred_at", "chat_id", "state"):
        op.create_index(f"ix_ai_budget_reservations_{name}", "ai_budget_reservations", [name])
    # SQLite requires batch for foreign keys; MySQL supports the same explicit schema.
    with op.batch_alter_table("ai_usage") as batch:
        batch.add_column(sa.Column("reservation_id", sa.String(64)))
        batch.add_column(sa.Column("pricing_version", sa.String(64)))
        batch.add_column(
            sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default="0")
        )
        batch.create_foreign_key(
            "fk_ai_usage_reservation", "ai_budget_reservations", ["reservation_id"], ["request_id"]
        )
        batch.create_unique_constraint("uq_ai_usage_reservation", ["reservation_id"])
    op.add_column("vector_stores", sa.Column("endpoint_id", sa.String(64)))


def downgrade():
    # Not an automatic corpus conversion; irreversible application work remains guarded.
    op.drop_column("vector_stores", "endpoint_id")
    with op.batch_alter_table("ai_usage") as batch:
        batch.drop_constraint("fk_ai_usage_reservation", type_="foreignkey")
        batch.drop_constraint("uq_ai_usage_reservation", type_="unique")
        for name in ("cache_write_tokens", "pricing_version", "reservation_id"):
            batch.drop_column(name)
    op.drop_table("ai_budget_reservations")
    op.drop_table("ai_budget_locks")
