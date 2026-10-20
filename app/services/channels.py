"""Subscriptions per user and the shared channels behind them."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Channel,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
    User,
)
from app.services import jobs
from app.youtube.api import ApiError, ApiUnavailable, ChannelInfo, QuotaExhausted, YouTubeApi
from app.youtube.ids import ChannelInputError, ChannelRefKind, parse_channel_input

FETCH_JOB = "fetch_channel"
FETCH_BATCH = 50


class ChannelError(Exception):
    """A channel could not be added. `key` is an i18n message key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


@dataclass
class ImportResult:
    added: int = 0
    existing: int = 0
    invalid: int = 0


async def resolve(text: str, api: YouTubeApi, user_id: int) -> ChannelInfo:
    """Turn user input into channel info. Handles and usernames need the Data API."""
    try:
        ref = parse_channel_input(text)
    except ChannelInputError as exc:
        raise ChannelError(exc.key) from exc
    if ref.kind == ChannelRefKind.ID:
        return ChannelInfo(channel_id=ref.value, title="", handle=None, avatar_url=None)
    if not api.available:
        raise ChannelError("error.channel_needs_api_key")
    try:
        if ref.kind == ChannelRefKind.HANDLE:
            info = await api.channel_by_handle(ref.value, user_id=user_id)
        else:
            info = await api.channel_by_username(ref.value, user_id=user_id)
    except (QuotaExhausted, ApiUnavailable) as exc:
        raise ChannelError("error.quota_exhausted") from exc
    except ApiError as exc:
        raise ChannelError("error.youtube_api") from exc
    if info is None:
        raise ChannelError("error.channel_not_found")
    return info


def get_or_create_channel(db: Session, info: ChannelInfo) -> Channel:
    channel = db.scalars(
        select(Channel).where(Channel.youtube_channel_id == info.channel_id)
    ).first()
    if channel is None:
        channel = Channel(youtube_channel_id=info.channel_id, title=info.title)
        db.add(channel)
    if info.title and not channel.title:
        channel.title = info.title
    if info.handle:
        channel.handle = info.handle
    if info.avatar_url:
        channel.avatar_url = info.avatar_url
    db.flush()
    return channel


def _subscribe(
    db: Session, user: User, channel: Channel, source: SubscriptionSource
) -> tuple[Subscription, bool]:
    existing = db.scalars(
        select(Subscription).where(
            Subscription.user_id == user.id, Subscription.channel_id == channel.id
        )
    ).first()
    if existing is not None:
        return existing, False
    sub = Subscription(user_id=user.id, channel_id=channel.id, source=source.value)
    db.add(sub)
    db.flush()
    return sub, True


def _enqueue_fetch(db: Session, user: User, channel_ids: list[int]) -> None:
    for start in range(0, len(channel_ids), FETCH_BATCH):
        jobs.enqueue(
            db,
            FETCH_JOB,
            {"channel_ids": channel_ids[start : start + FETCH_BATCH]},
            priority=10,
            created_by=user.id,
        )


async def add_channel(db: Session, api: YouTubeApi, user: User, text: str) -> Subscription:
    info = await resolve(text, api, user.id)
    channel = get_or_create_channel(db, info)
    sub, created = _subscribe(db, user, channel, SubscriptionSource.MANUAL)
    if not created:
        db.rollback()
        raise ChannelError("error.channel_already_subscribed")
    db.commit()
    _enqueue_fetch(db, user, [channel.id])
    return sub


def import_channels(db: Session, user: User, rows: list[tuple[str, str]]) -> ImportResult:
    """Subscribe to many channels at once (no API quota needed: rows carry channel IDs)."""
    result = ImportResult()
    new_channels: list[int] = []
    for channel_id, title in rows:
        channel = get_or_create_channel(
            db, ChannelInfo(channel_id=channel_id, title=title, handle=None, avatar_url=None)
        )
        _sub, created = _subscribe(db, user, channel, SubscriptionSource.IMPORT)
        if created:
            result.added += 1
            new_channels.append(channel.id)
        else:
            result.existing += 1
    db.commit()
    _enqueue_fetch(db, user, new_channels)
    return result


def list_subscriptions(db: Session, user: User) -> list[tuple[Subscription, Channel]]:
    stmt = (
        select(Subscription, Channel)
        .join(Channel, Channel.id == Subscription.channel_id)
        .where(Subscription.user_id == user.id)
        .order_by(Channel.title, Channel.id)
    )
    return [(sub, channel) for sub, channel in db.execute(stmt).all()]


def get_subscription(db: Session, user: User, subscription_id: int) -> Subscription | None:
    """Only ever returns a subscription of the given user."""
    return db.scalars(
        select(Subscription).where(
            Subscription.id == subscription_id, Subscription.user_id == user.id
        )
    ).first()


def set_muted(db: Session, sub: Subscription, muted: bool) -> None:
    sub.status = (SubscriptionStatus.MUTED if muted else SubscriptionStatus.ACTIVE).value
    db.commit()


def unsubscribe(db: Session, sub: Subscription) -> None:
    db.delete(sub)
    db.commit()
