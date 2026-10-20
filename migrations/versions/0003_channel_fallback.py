"""channel: last_fallback_at (RSS fallback via the Data API) and gone_at

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-20 11:40:19.326931
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("channel", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_fallback_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("gone_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("channel", schema=None) as batch_op:
        batch_op.drop_column("gone_at")
        batch_op.drop_column("last_fallback_at")
