"""Small builders for feed/video test data."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from xml.sax.saxutils import escape

import httpx
from sqlalchemy.orm import Session

from app.models import Channel, Subscription, Video

BASE_TIME = datetime(2026, 10, 1, 12, tzinfo=UTC)


def channel_id(n: int) -> str:
    return f"UC{n:022d}"


def video_id(n: int) -> str:
    return f"v{n:010d}"


def feed_xml(cid: str, entries: list[tuple[str, str, datetime]], title: str = "Kanal") -> bytes:
    items = "".join(
        f"""<entry><id>yt:video:{vid}</id><yt:videoId>{vid}</yt:videoId>
        <yt:channelId>{cid}</yt:channelId><title>{escape(t)}</title>
        <published>{published.isoformat()}</published>
        <media:group><media:title>{escape(t)}</media:title>
        <media:thumbnail url="https://i1.ytimg.com/vi/{vid}/hqdefault.jpg"/>
        <media:description>Beschreibung von {escape(t)}</media:description></media:group></entry>"""
        for vid, t, published in entries
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/"
 xmlns="http://www.w3.org/2005/Atom"><yt:channelId>{cid}</yt:channelId><title>Videos</title>
<author><name>{escape(title)}</name></author>{items}</feed>""".encode()


def rss_router(feeds: dict[str, bytes]) -> Callable[[httpx.Request], httpx.Response]:
    """respx side effect: serve feeds by UULF playlist id, 404 for unknown ones."""

    def handler(request: httpx.Request) -> httpx.Response:
        playlist = request.url.params.get("playlist_id", "")
        cid = "UC" + playlist[4:]
        if cid in feeds:
            return httpx.Response(200, content=feeds[cid])
        return httpx.Response(404)

    return handler


def api_video(vid: str, seconds: int | None = 900, live: str = "none") -> dict[str, Any]:
    duration = "P0D" if seconds is None else f"PT{seconds}S"
    return {
        "id": vid,
        "snippet": {
            "title": f"API {vid}",
            "description": "",
            "publishedAt": "2026-10-01T10:00:00Z",
            "liveBroadcastContent": live,
            "tags": ["tag"],
            "categoryId": "27",
        },
        "contentDetails": {"duration": duration},
    }


def add_channel(db: Session, n: int, title: str = "") -> Channel:
    channel = Channel(youtube_channel_id=channel_id(n), title=title or f"Kanal {n}")
    db.add(channel)
    db.commit()
    return channel


def subscribe(db: Session, user_id: int, channel: Channel, status: str = "active") -> Subscription:
    sub = Subscription(user_id=user_id, channel_id=channel.id, status=status)
    db.add(sub)
    db.commit()
    return sub


def add_video(
    db: Session,
    channel: Channel,
    n: int,
    *,
    title: str = "",
    age_hours: int = 0,
    seconds: int | None = 900,
    enriched: bool = True,
    **fields: Any,
) -> Video:
    video = Video(
        youtube_video_id=video_id(n),
        channel_id=channel.id,
        title=title or f"Video {n}",
        description=fields.pop("description", ""),
        published_at=BASE_TIME - timedelta(hours=age_hours),
        duration_seconds=seconds,
        enriched_at=BASE_TIME if enriched else None,
        **fields,
    )
    db.add(video)
    db.commit()
    return video


def api_playlist_item(
    vid: str, cid: str, published: str = "2026-10-01T10:00:00Z"
) -> dict[str, Any]:
    return {
        "snippet": {
            "title": f"API {vid}",
            "description": "aus der API",
            "videoOwnerChannelId": cid,
            "resourceId": {"videoId": vid},
            "thumbnails": {"high": {"url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"}},
        },
        "contentDetails": {"videoId": vid, "videoPublishedAt": published},
    }
