"""Feed queries for the views "feed", "later" and "hidden".

Structural conditions (subscription, Shorts, watched, live/premiere settings, length) are
expressed in SQL; keyword/regex rules are applied in Python. Paging uses a keyset cursor on
(published_at, id) so pages stay stable while new videos arrive.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.orm import Session

from app.models import Channel, Subscription, SubscriptionStatus, User, UserVideoState, Video
from app.services import filters
from app.services import settings as user_settings
from app.services.filters import CompiledRule

FeedSelect = Select[Video, Channel, Subscription, UserVideoState]

PAGE_SIZE = 24
BATCH_FACTOR = 3


class View(StrEnum):
    FEED = "feed"
    LATER = "later"
    HIDDEN = "hidden"


class Length(StrEnum):
    ANY = ""
    SHORT = "short"  # < 20 min
    MEDIUM = "medium"  # 20-60 min
    LONG = "long"  # > 60 min


class ReasonKind(StrEnum):
    MANUAL = "manual"
    MUTED = "muted"
    LIVE = "live"
    PREMIERE = "premiere"
    RULE = "rule"


@dataclass(frozen=True)
class HiddenReason:
    kind: ReasonKind
    rule: CompiledRule | None = None


@dataclass(frozen=True)
class FeedFilters:
    include_watched: bool = False
    channel_id: int | None = None
    length: Length = Length.ANY


@dataclass(frozen=True)
class FeedItem:
    video: Video
    channel: Channel
    subscription: Subscription | None
    state: UserVideoState | None
    reason: HiddenReason | None = None


@dataclass
class FeedPage:
    items: list[FeedItem] = field(default_factory=list)
    next_cursor: str | None = None


def encode_cursor(published_at: datetime, video_id: int) -> str:
    raw = f"{published_at.isoformat()}|{video_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, int] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        stamp, video_id = raw.split("|")
        return datetime.fromisoformat(stamp), int(video_id)
    except (ValueError, UnicodeDecodeError):
        return None


def _base_query(user: User, require_enriched: bool) -> FeedSelect:
    stmt = (
        select(Video, Channel, Subscription, UserVideoState)
        .join(Channel, Channel.id == Video.channel_id)
        .outerjoin(
            Subscription,
            and_(Subscription.channel_id == Video.channel_id, Subscription.user_id == user.id),
        )
        .outerjoin(
            UserVideoState,
            and_(UserVideoState.video_id == Video.id, UserVideoState.user_id == user.id),
        )
        # Never, anywhere: Shorts.
        .where(Video.is_short.is_(False))
        .order_by(Video.published_at.desc(), Video.id.desc())
    )
    if require_enriched:
        # With an API key a video is only shown once its duration is known, so a Short can
        # never flash up before the fallback filter has seen it.
        stmt = stmt.where(Video.enriched_at.is_not(None))
    return stmt


def _apply_filters(stmt: FeedSelect, filters_: FeedFilters) -> FeedSelect:
    if filters_.channel_id is not None:
        stmt = stmt.where(Video.channel_id == filters_.channel_id)
    if filters_.length == Length.SHORT:
        stmt = stmt.where(Video.duration_seconds < 20 * 60)
    elif filters_.length == Length.MEDIUM:
        stmt = stmt.where(Video.duration_seconds.between(20 * 60, 60 * 60))
    elif filters_.length == Length.LONG:
        stmt = stmt.where(Video.duration_seconds > 60 * 60)
    if not filters_.include_watched:
        stmt = stmt.where(
            or_(UserVideoState.watched_at.is_(None), UserVideoState.user_id.is_(None))
        )
    return stmt


def hidden_reason(
    item: FeedItem, rules: list[CompiledRule], show_live: bool, show_premieres: bool
) -> HiddenReason | None:
    state, video = item.state, item.video
    if state is not None and state.hidden_at is not None:
        return HiddenReason(ReasonKind.MANUAL)
    if item.subscription is not None and item.subscription.status == SubscriptionStatus.MUTED.value:
        return HiddenReason(ReasonKind.MUTED)
    if video.is_live and not show_live:
        return HiddenReason(ReasonKind.LIVE)
    if video.is_premiere and not show_premieres:
        return HiddenReason(ReasonKind.PREMIERE)
    rule = filters.first_match(rules, video)
    if rule is not None:
        return HiddenReason(ReasonKind.RULE, rule)
    return None


def feed_page(
    db: Session,
    user: User,
    view: View,
    filters_: FeedFilters,
    *,
    cursor: str | None = None,
    require_enriched: bool = False,
    limit: int = PAGE_SIZE,
) -> FeedPage:
    prefs = user_settings.get_user_settings(db, user.id)
    show_live, show_premieres = bool(prefs["show_livestreams"]), bool(prefs["show_premieres"])
    rules = filters.active_rules(db, user)

    stmt = _base_query(user, require_enriched)
    if view == View.LATER:
        stmt = stmt.where(UserVideoState.watch_later_at.is_not(None))
    else:
        stmt = stmt.where(Subscription.id.is_not(None))
    stmt = _apply_filters(
        stmt,
        filters_
        if view != View.HIDDEN
        else FeedFilters(True, filters_.channel_id, filters_.length),
    )

    position = decode_cursor(cursor) if cursor else None
    page = FeedPage()
    batch = max(limit * BATCH_FACTOR, 50)
    while True:
        query = stmt
        if position is not None:
            published, video_id = position
            query = query.where(
                or_(
                    Video.published_at < published,
                    and_(Video.published_at == published, Video.id < video_id),
                )
            )
        rows = db.execute(query.limit(batch)).all()
        for video, channel, sub, state in rows:
            position = (video.published_at, video.id)
            item = FeedItem(video, channel, sub, state)
            reason = hidden_reason(item, rules, show_live, show_premieres)
            if view == View.FEED and reason is not None:
                continue
            if view == View.HIDDEN:
                if reason is None:
                    continue
                item = FeedItem(video, channel, sub, state, reason)
            page.items.append(item)
            if len(page.items) == limit:
                page.next_cursor = encode_cursor(video.published_at, video.id)
                return page
        if len(rows) < batch:
            return page
