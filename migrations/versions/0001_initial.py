"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-20 07:16:39.729751
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64, collation="NOCASE"), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("oidc_subject", sa.String(length=255), nullable=True),
        sa.Column("session_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("role IN ('admin', 'user')", name=op.f("ck_user_role")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user")),
        sa.UniqueConstraint("oidc_subject", name=op.f("uq_user_oidc_subject")),
        sa.UniqueConstraint("username", name=op.f("uq_user_username")),
    )
    op.create_table(
        "global_setting",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["user.id"],
            name=op.f("fk_global_setting_updated_by_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_global_setting")),
    )
    op.create_table(
        "job",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("run_after", sa.DateTime(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("locked_by", sa.String(length=128), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')", name=op.f("ck_job_status")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["user.id"], name=op.f("fk_job_created_by_user"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job")),
    )
    with op.batch_alter_table("job", schema=None) as batch_op:
        batch_op.create_index(
            "ix_job_status_run_after_priority", ["status", "run_after", "priority"], unique=False
        )

    op.create_table(
        "quota_ledger",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quota_day", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("method", sa.String(length=64), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_quota_ledger_user_id_user"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quota_ledger")),
    )
    with op.batch_alter_table("quota_ledger", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_quota_ledger_quota_day"), ["quota_day"], unique=False)

    op.create_table(
        "user_setting",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_user_setting_user_id_user"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "key", name=op.f("pk_user_setting")),
    )
    op.create_table(
        "job_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed')", name=op.f("ck_job_run_status")
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["job.id"], name=op.f("fk_job_run_job_id_job"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_run")),
    )
    with op.batch_alter_table("job_run", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_job_run_started_at"), ["started_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("job_run", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_job_run_started_at"))

    op.drop_table("job_run")
    op.drop_table("user_setting")
    with op.batch_alter_table("quota_ledger", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_quota_ledger_quota_day"))

    op.drop_table("quota_ledger")
    with op.batch_alter_table("job", schema=None) as batch_op:
        batch_op.drop_index("ix_job_status_run_after_priority")

    op.drop_table("job")
    op.drop_table("global_setting")
    op.drop_table("user")
