"""Fetching new videos (RSS) and enriching them (Data API).

A broken feed or a failing API call never stops the whole run: errors are recorded on the
channel or in the run result and processing continues.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import utcnow
from app.models import Channel, LiveStatus, Subscription, SubscriptionStatus, Video
from app.youtube import quota
from app.youtube.api import ApiError, ApiUnavailable, QuotaExhausted, VideoInfo, YouTubeApi
from app.youtube.ids import uploads_playlist_id
from app.youtube.rss import ChannelFeed, FeedError, fetch_uploads
from app.youtube.thumbs import ThumbResult, cache_pending

log = logging.getLogger(__name__)

FETCH_CONCURRENCY = 8
RECHECK_LIVE_AFTER = timedelta(minutes=30)
MAX_ERROR_LENGTH = 500


@dataclass
class PollResult:
    channels: int = 0
    ok: int = 0
    failed: int = 0
    new_videos: int = 0
    rss_failed: int = 0
    fallback: int = 0


@dataclass(frozen=True)
class Fallback:
    """Fetching uploads via playlistItems.list (1 unit) when RSS fails."""

    api: YouTubeApi
    settings: Settings
    interval: timedelta

    def due(self, channel: Channel, now: datetime) -> bool:
        return channel.last_fallback_at is None or channel.last_fallback_at <= now - self.interval


@dataclass
class EnrichResult:
    requested: int = 0
    enriched: int = 0
    shorts: int = 0
    calls: int = 0
    stopped: str | None = None
    errors: list[str] = field(default_factory=list)


def channels_to_poll(db: Session) -> list[int]:
    """Channels with at least one active (not muted) subscription."""
    stmt = (
        select(Channel.id)
        .join(Subscription, Subscription.channel_id == Channel.id)
        .where(Subscription.status == SubscriptionStatus.ACTIVE.value)
        .distinct()
        .order_by(Channel.id)
    )
    return list(db.scalars(stmt))


def store_feed(db: Session, channel: Channel, feed: ChannelFeed, now: datetime) -> int:
    """Insert new videos from a feed and refresh changed titles. Returns the number of new ones."""
    if feed.channel_title and channel.title != feed.channel_title:
        channel.title = feed.channel_title
    entries = [e for e in feed.entries if e.channel_id == channel.youtube_channel_id]
    ids = [e.video_id for e in entries]
    existing = {
        v.youtube_video_id: v
        for v in db.scalars(select(Video).where(Video.youtube_video_id.in_(ids)))
    }
    new = 0
    for entry in entries:
        video = existing.get(entry.video_id)
        if video is None:
            db.add(
                Video(
                    youtube_video_id=entry.video_id,
                    channel_id=channel.id,
                    title=entry.title,
                    description=entry.description,
                    published_at=entry.published_at,
                    thumbnail_url=entry.thumbnail_url,
                    first_seen_at=now,
                )
            )
            new += 1
        else:
            video.title = entry.title or video.title
            video.description = entry.description or video.description
    channel.last_fetched_at = now
    channel.last_success_at = now
    channel.last_error = None
    channel.error_count = 0
    db.commit()
    return new


def record_channel_error(db: Session, channel: Channel, error: str, now: datetime) -> None:
    channel.last_fetched_at = now
    channel.last_error = error[:MAX_ERROR_LENGTH]
    channel.error_count += 1
    db.commit()


def _record_error(session_factory: sessionmaker[Session], channel_id: int, error: str) -> None:
    with session_factory() as db:
        channel = db.get(Channel, channel_id)
        if channel is not None:
            record_channel_error(db, channel, error, utcnow())


async def _fetch_via_api(
    session_factory: sessionmaker[Session],
    channel_id: int,
    youtube_channel_id: str,
    fallback: Fallback,
    rss_error: FeedError,
) -> ChannelFeed | None:
    """Try the API fallback; returns None (after recording the error) if not possible."""
    if not fallback.api.available:
        _record_error(session_factory, channel_id, str(rss_error))
        return None
    with session_factory() as db:
        channel = db.get(Channel, channel_id)
        if channel is None:
            return None
        now = utcnow()
        if not fallback.due(channel, now) or not quota.can_spend_fallback(db, fallback.settings):
            record_channel_error(db, channel, f"{rss_error} (API fallback postponed)", now)
            return None
        channel.last_fallback_at = now
        db.commit()
    try:
        entries = await fallback.api.playlist_items(uploads_playlist_id(youtube_channel_id))
    except (QuotaExhausted, ApiUnavailable, ApiError) as exc:
        _record_error(session_factory, channel_id, f"{rss_error}; API fallback: {exc}")
        return None
    return ChannelFeed(channel_id=youtube_channel_id, channel_title=None, entries=entries)


async def poll_channels(
    session_factory: sessionmaker[Session],
    client: httpx.AsyncClient,
    channel_ids: list[int],
    concurrency: int = FETCH_CONCURRENCY,
    fallback: Fallback | None = None,
) -> PollResult:
    """Fetch many channels concurrently.

    No database connection is held across an ``await``: with many channels the
    coroutines would otherwise exhaust the connection pool and block the event loop.
    """
    result = PollResult(channels=len(channel_ids))
    semaphore = asyncio.Semaphore(concurrency)

    async def fetch(channel_id: int, youtube_channel_id: str) -> ChannelFeed | None:
        try:
            return await fetch_uploads(client, youtube_channel_id)
        except FeedError as exc:
            log.warning("feed of %s failed: %s", youtube_channel_id, exc)
            result.rss_failed += 1
            if fallback is None:
                _record_error(session_factory, channel_id, str(exc))
                return None
            feed = await _fetch_via_api(
                session_factory, channel_id, youtube_channel_id, fallback, exc
            )
            result.fallback += int(feed is not None)
            return feed

    async def poll_one(channel_id: int) -> None:
        with session_factory() as db:
            channel = db.get(Channel, channel_id)
            if channel is None:
                return
            youtube_channel_id = channel.youtube_channel_id
        async with semaphore:
            feed = await fetch(channel_id, youtube_channel_id)
        if feed is None:
            result.failed += 1
            return
        with session_factory() as db:
            channel = db.get(Channel, channel_id)
            if channel is None:
                return
            try:
                result.new_videos += store_feed(db, channel, feed, utcnow())
                result.ok += 1
            except IntegrityError:
                # Stored concurrently by another process (e.g. the web app); not an error.
                db.rollback()
                result.ok += 1
            except Exception as exc:
                log.exception("storing feed of %s failed", youtube_channel_id)
                db.rollback()
                record_channel_error(db, channel, f"{type(exc).__name__}: {exc}", utcnow())
                result.failed += 1

    await asyncio.gather(*(poll_one(cid) for cid in channel_ids))
    return result


def apply_video_info(video: Video, info: VideoInfo, shorts_max_seconds: int, now: datetime) -> None:
    video.title = info.title or video.title
    video.description = info.description or video.description
    video.tags = info.tags
    video.category_id = info.category_id
    if info.thumbnail_url and not video.thumbnail_url:
        video.thumbnail_url = info.thumbnail_url

    content = info.live_broadcast_content
    if content == "live":
        video.live_status, video.is_live, video.is_premiere = LiveStatus.LIVE.value, True, False
    elif content == "upcoming":
        video.live_status, video.is_live, video.is_premiere = LiveStatus.UPCOMING.value, False, True
    else:
        was_live = video.live_status in (LiveStatus.LIVE.value, LiveStatus.UPCOMING.value)
        video.live_status = LiveStatus.COMPLETED.value if was_live else LiveStatus.NONE.value
        video.is_live = video.is_premiere = False
        video.duration_seconds = info.duration_seconds
        # Shorts fallback; never for upcoming/live items, whose duration is not final.
        if info.duration_seconds is not None and info.duration_seconds <= shorts_max_seconds:
            video.is_short = True
    video.enriched_at = now


def videos_to_enrich(db: Session, now: datetime, limit: int) -> list[Video]:
    stmt = (
        select(Video)
        .where(
            Video.is_short.is_(False),
            or_(
                Video.enriched_at.is_(None),
                (
                    Video.live_status.in_([LiveStatus.LIVE.value, LiveStatus.UPCOMING.value])
                    & (Video.enriched_at < now - RECHECK_LIVE_AFTER)
                ),
            ),
        )
        .order_by(Video.enriched_at.is_not(None), Video.first_seen_at.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt))


async def enrich_pending(
    session_factory: sessionmaker[Session],
    api: YouTubeApi,
    settings: Settings,
    max_calls: int = 20,
) -> EnrichResult:
    """Enrich new videos and re-check live/upcoming ones in batches of 50 (1 unit each)."""
    result = EnrichResult()
    if not api.available:
        result.stopped = "no_api_key"
        return result
    for _ in range(max_calls):
        with session_factory() as db:
            batch = videos_to_enrich(db, utcnow(), 50)
            if not batch:
                break
            result.requested += len(batch)
            try:
                infos = await api.videos([v.youtube_video_id for v in batch])
            except QuotaExhausted:
                result.stopped = "quota"
                break
            except ApiUnavailable:
                result.stopped = "no_api_key"
                break
            except ApiError as exc:
                log.warning("videos.list failed: %s", exc)
                result.errors.append(str(exc))
                result.stopped = "api_error"
                break
            result.calls += 1
            by_id = {info.video_id: info for info in infos}
            now = utcnow()
            for video in batch:
                info = by_id.get(video.youtube_video_id)
                if info is None:
                    # Deleted or private: keep the RSS data, don't ask again.
                    video.enriched_at = now
                    continue
                apply_video_info(video, info, settings.shorts_max_seconds, now)
                result.enriched += 1
                result.shorts += int(video.is_short)
            db.commit()
    return result


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(15.0))


async def poll_and_enrich(
    session_factory: sessionmaker[Session],
    settings: Settings,
    channel_ids: list[int] | None = None,
) -> tuple[PollResult, EnrichResult, ThumbResult]:
    """One complete fetch run: RSS for the given (or all active) channels, enrichment and
    thumbnail caching."""
    if channel_ids is None:
        with session_factory() as db:
            channel_ids = channels_to_poll(db)
    async with http_client() as client:
        api = YouTubeApi(settings, session_factory, client)
        with session_factory() as db:
            interval = quota.fallback_interval_minutes(
                db, settings, len(channels_to_poll(db)), settings.feed_poll_minutes
            )
        fallback = Fallback(api, settings, timedelta(minutes=interval))
        polled = await poll_channels(session_factory, client, channel_ids, fallback=fallback)
        enriched = await enrich_pending(session_factory, api, settings)
        thumbs = await cache_pending(session_factory, settings, client)
    return polled, enriched, thumbs
