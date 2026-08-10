"""Initial complete application schema.

Revision ID: 0001
Revises:
"""

from alembic import op
from tg_assistant.db import models  # noqa: F401
from tg_assistant.db.base import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # Chỉ chạy thủ công trong môi trường phát triển; ứng dụng không tự downgrade.
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
