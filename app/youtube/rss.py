"""Read a channel's long-form uploads via the public RSS feed of its UULF playlist.

RSS costs no API quota and is the primary source. It returns the ~15 newest videos.
"""

from __future__ import annotations

import asyncio
import calendar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx

from app import __version__
from app.youtube.ids import is_channel_id, is_video_id, uploads_playlist_id

FEED_URL = "https://www.youtube.com/feeds/videos.xml"
USER_AGENT = f"kurator/{__version__} (+https://github.com/gingershred/kurator)"
DEFAULT_TIMEOUT = 15.0
RETRY_DELAY = 2.0


class FeedError(Exception):
    """The feed could not be fetched or parsed. `not_found` marks an HTTP 404."""

    def __init__(self, message: str, *, not_found: bool = False, transient: bool = False) -> None:
        super().__init__(message)
        self.not_found = not_found
        self.transient = transient


@dataclass(frozen=True)
class FeedEntry:
    video_id: str
    channel_id: str
    title: str
    description: str
    published_at: datetime
    thumbnail_url: str | None


@dataclass(frozen=True)
class ChannelFeed:
    channel_id: str | None
    channel_title: str | None
    entries: list[FeedEntry]


def _published(entry: Any) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed is None:
        return None
    return datetime.fromtimestamp(calendar.timegm(parsed), UTC)


def parse_feed(content: bytes) -> ChannelFeed:
    parsed = feedparser.parse(content)
    if parsed.get("bozo") and not parsed.entries:
        raise FeedError(f"invalid feed: {parsed.get('bozo_exception')}")
    feed_channel = parsed.feed.get("yt_channelid")
    entries: list[FeedEntry] = []
    for entry in parsed.entries:
        video_id = entry.get("yt_videoid", "")
        channel_id = entry.get("yt_channelid") or feed_channel or ""
        published = _published(entry)
        if not is_video_id(video_id) or not is_channel_id(channel_id) or published is None:
            continue  # skip malformed entries instead of failing the whole feed
        thumbs = entry.get("media_thumbnail") or []
        entries.append(
            FeedEntry(
                video_id=video_id,
                channel_id=channel_id,
                title=entry.get("title", ""),
                description=entry.get("summary", ""),
                published_at=published,
                thumbnail_url=thumbs[0].get("url") if thumbs else None,
            )
        )
    return ChannelFeed(
        channel_id=feed_channel if feed_channel and is_channel_id(feed_channel) else None,
        channel_title=parsed.feed.get("author") or None,
        entries=entries,
    )


async def _fetch_once(client: httpx.AsyncClient, channel_id: str, timeout: float) -> ChannelFeed:
    params = {"playlist_id": uploads_playlist_id(channel_id)}
    try:
        response = await client.get(
            FEED_URL, params=params, timeout=timeout, headers={"User-Agent": USER_AGENT}
        )
    except httpx.HTTPError as exc:
        raise FeedError(f"{type(exc).__name__}: {exc}", transient=True) from exc
    if response.status_code == 404:
        raise FeedError(
            "feed not found (channel deleted or without long-form videos)", not_found=True
        )
    if response.status_code != 200:
        raise FeedError(f"HTTP {response.status_code}", transient=response.status_code >= 500)
    return parse_feed(response.content)


async def fetch_uploads(
    client: httpx.AsyncClient,
    channel_id: str,
    timeout: float = DEFAULT_TIMEOUT,
    retry_delay: float | None = None,
) -> ChannelFeed:
    """Fetch the long-form uploads (UULF playlist) of a channel.

    Timeouts, connection errors and 5xx answers are retried once after a short pause.
    """
    try:
        return await _fetch_once(client, channel_id, timeout)
    except FeedError as exc:
        if not exc.transient:
            raise
    await asyncio.sleep(RETRY_DELAY if retry_delay is None else retry_delay)
    return await _fetch_once(client, channel_id, timeout)
