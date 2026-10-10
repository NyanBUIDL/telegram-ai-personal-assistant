"""Private compare-and-set tokens for background work."""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("background_jobs", sa.Column("claim_token", sa.String(64), nullable=True))
    op.add_column(
        "background_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "pending_actions",
        sa.Column("execution_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "reminders", sa.Column("delivery_started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("tasks", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))


def downgrade():
    op.drop_column("tasks", "revision")
    op.drop_column("reminders", "delivery_started_at")
    op.drop_column("pending_actions", "execution_started_at")
    op.drop_column("background_jobs", "lease_expires_at")
    op.drop_column("background_jobs", "claim_token")
