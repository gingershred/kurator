"""feed: channels, videos, subscriptions, per-user video state, watch events, filter rules

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-20 08:20:27.223090
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "channel",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("youtube_channel_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("handle", sa.String(length=128), nullable=True),
        sa.Column("avatar_url", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_fetched_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("error_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channel")),
        sa.UniqueConstraint("youtube_channel_id", name=op.f("uq_channel_youtube_channel_id")),
    )
    op.create_table(
        "filter_rule",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "type IN ('keyword_title', 'keyword_desc', 'channel', 'min_duration', 'max_duration', 'regex')",
            name=op.f("ck_filter_rule_type"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_filter_rule_user_id_user"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_filter_rule")),
    )
    with op.batch_alter_table("filter_rule", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_filter_rule_user_id"), ["user_id"], unique=False)

    op.create_table(
        "subscription",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "source IN ('manual', 'import', 'recommendation')", name=op.f("ck_subscription_source")
        ),
        sa.CheckConstraint("status IN ('active', 'muted')", name=op.f("ck_subscription_status")),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channel.id"],
            name=op.f("fk_subscription_channel_id_channel"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_subscription_user_id_user"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscription")),
        sa.UniqueConstraint("user_id", "channel_id", name="uq_subscription_user_channel"),
    )
    with op.batch_alter_table("subscription", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_subscription_channel_id"), ["channel_id"], unique=False
        )

    op.create_table(
        "video",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("youtube_video_id", sa.String(length=16), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("thumbnail_url", sa.String(length=512), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("category_id", sa.String(length=16), nullable=True),
        sa.Column("live_status", sa.String(length=16), server_default="none", nullable=False),
        sa.Column("is_short", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_live", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_premiere", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_members_only", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("enriched_at", sa.DateTime(), nullable=True),
        sa.Column("thumb_cached_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "live_status IN ('none', 'upcoming', 'live', 'completed')",
            name=op.f("ck_video_live_status"),
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channel.id"],
            name=op.f("fk_video_channel_id_channel"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video")),
        sa.UniqueConstraint("youtube_video_id", name=op.f("uq_video_youtube_video_id")),
    )
    with op.batch_alter_table("video", schema=None) as batch_op:
        batch_op.create_index(
            "ix_video_channel_id_published_at", ["channel_id", "published_at"], unique=False
        )
        batch_op.create_index("ix_video_published_at", ["published_at"], unique=False)

    op.create_table(
        "user_video_state",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("video_id", sa.Integer(), nullable=False),
        sa.Column("watched_at", sa.DateTime(), nullable=True),
        sa.Column("progress_seconds", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("hidden_at", sa.DateTime(), nullable=True),
        sa.Column("hidden_reason", sa.String(length=255), nullable=True),
        sa.Column("rating", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("watch_later_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("rating IN (-1, 0, 1)", name=op.f("ck_user_video_state_rating")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_user_video_state_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["video.id"],
            name=op.f("fk_user_video_state_video_id_video"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "video_id", name=op.f("pk_user_video_state")),
    )
    with op.batch_alter_table("user_video_state", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_user_video_state_video_id"), ["video_id"], unique=False
        )

    op.create_table(
        "watch_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("video_id", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(length=16), nullable=False),
        sa.Column("position_seconds", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "event IN ('play', 'pause', 'progress', 'ended')", name=op.f("ck_watch_event_event")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_watch_event_user_id_user"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["video.id"],
            name=op.f("fk_watch_event_video_id_video"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watch_event")),
    )
    with op.batch_alter_table("watch_event", schema=None) as batch_op:
        batch_op.create_index(
            "ix_watch_event_user_id_created_at", ["user_id", "created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_watch_event_video_id"), ["video_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("watch_event", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_watch_event_video_id"))
        batch_op.drop_index("ix_watch_event_user_id_created_at")

    op.drop_table("watch_event")
    with op.batch_alter_table("user_video_state", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_user_video_state_video_id"))

    op.drop_table("user_video_state")
    with op.batch_alter_table("video", schema=None) as batch_op:
        batch_op.drop_index("ix_video_published_at")
        batch_op.drop_index("ix_video_channel_id_published_at")

    op.drop_table("video")
    with op.batch_alter_table("subscription", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_subscription_channel_id"))

    op.drop_table("subscription")
    with op.batch_alter_table("filter_rule", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_filter_rule_user_id"))

    op.drop_table("filter_rule")
    op.drop_table("channel")
