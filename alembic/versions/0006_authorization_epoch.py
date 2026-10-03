"""Persist source authorization generations for revocation fencing."""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "telegram_chat_policies",
        sa.Column("authorization_epoch", sa.Integer(), nullable=False, server_default="0"),
    )
    # Legacy work has no authorization snapshot. It requires a fresh preview.
    # A destructive job already running may have reached Telegram. Do not
    # claim cancellation proves its external effect was prevented.
    op.execute(
        sa.text(
            "UPDATE background_jobs SET status = 'uncertain' WHERE status = 'running' AND job_type = 'history_link_delete'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE background_jobs SET status = 'cancelled' WHERE status IN ('queued', 'running', 'paused', 'pause_requested') AND job_type IN ('learn_group', 'history_backfill', 'history_link_delete')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE pending_actions SET status = 'cancelled' WHERE status IN ('pending', 'confirmed') AND chat_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.drop_column("telegram_chat_policies", "authorization_epoch")
