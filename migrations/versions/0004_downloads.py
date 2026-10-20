"""downloads: download, download_request, user.can_download, subscription.auto_download_since

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-20 12:35:10.590202
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "download",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("video_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("progress", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("format", sa.String(length=128), nullable=True),
        sa.Column("subtitles", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_hint", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("last_played_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("kind IN ('video', 'audio')", name=op.f("ck_download_kind")),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed', 'deleted')",
            name=op.f("ck_download_status"),
        ),
        sa.ForeignKeyConstraint(
            ["video_id"], ["video.id"], name=op.f("fk_download_video_id_video"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_download")),
        sa.UniqueConstraint("video_id", "kind", name="uq_download_video_kind"),
    )
    with op.batch_alter_table("download", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_download_status"), ["status"], unique=False)

    op.create_table(
        "download_request",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("download_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("source IN ('manual', 'auto')", name=op.f("ck_download_request_source")),
        sa.ForeignKeyConstraint(
            ["download_id"],
            ["download.id"],
            name=op.f("fk_download_request_download_id_download"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_download_request_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "download_id", name=op.f("pk_download_request")),
    )
    with op.batch_alter_table("download_request", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_download_request_download_id"), ["download_id"], unique=False
        )

    # Plain ALTER TABLE ADD COLUMN: batch mode would recreate the tables and, for "user",
    # drop the COLLATE NOCASE of username.
    op.add_column("subscription", sa.Column("auto_download_since", sa.DateTime(), nullable=True))
    op.add_column(
        "user",
        sa.Column("can_download", sa.Boolean(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    # SQLite >= 3.35 supports DROP COLUMN without recreating the table.
    op.execute('ALTER TABLE "user" DROP COLUMN can_download')
    op.execute("ALTER TABLE subscription DROP COLUMN auto_download_since")

    with op.batch_alter_table("download_request", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_download_request_download_id"))

    op.drop_table("download_request")
    with op.batch_alter_table("download", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_download_status"))

    op.drop_table("download")
