"""Keep finite history intervals separate from completed watermarks."""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("baseline_message_id", "catchup_upper_id", "catchup_after_id"):
        op.add_column("sync_states", sa.Column(name, sa.BigInteger(), nullable=True))
    op.add_column("sync_states", sa.Column("authorization_epoch", sa.Integer(), nullable=True))


def downgrade():
    for name in (
        "authorization_epoch",
        "catchup_after_id",
        "catchup_upper_id",
        "baseline_message_id",
    ):
        op.drop_column("sync_states", name)
