"""Local thumbnail cache: images are downloaded once by the worker and served by kurator,
so browsers never contact Google for thumbnails."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import utcnow
from app.models import Video
from app.youtube.ids import is_video_id

log = logging.getLogger(__name__)

MAX_BYTES = 2 * 1024 * 1024
CONCURRENCY = 8
ALLOWED_HOST_SUFFIX = ".ytimg.com"


@dataclass
class ThumbResult:
    cached: int = 0
    missing: int = 0
    failed: int = 0


def thumbs_dir(settings: Settings) -> Path:
    return settings.data_dir / "thumbs"


def thumb_path(settings: Settings, video_id: str) -> Path:
    if not is_video_id(video_id):
        raise ValueError(f"not a video id: {video_id!r}")
    return thumbs_dir(settings) / f"{video_id}.jpg"


def source_url(video: Video) -> str:
    """The feed's thumbnail URL if it points to YouTube's image host, else the standard one."""
    url = video.thumbnail_url
    if url:
        host = urlparse(url).hostname or ""
        if urlparse(url).scheme == "https" and host.endswith(ALLOWED_HOST_SUFFIX):
            return url
    return f"https://i.ytimg.com/vi/{video.youtube_video_id}/hqdefault.jpg"


async def download(client: httpx.AsyncClient, url: str, target: Path) -> bool:
    """Download an image atomically. Returns False if it does not exist (404)."""
    async with client.stream("GET", url, timeout=15.0) as response:
        if response.status_code == 404:
            return False
        response.raise_for_status()
        if not response.headers.get("content-type", "").startswith("image/"):
            raise ValueError(f"unexpected content type {response.headers.get('content-type')}")
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise ValueError("thumbnail too large")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(bytes(data))
    os.replace(tmp, target)
    return True


async def cache_pending(
    session_factory: sessionmaker[Session],
    settings: Settings,
    client: httpx.AsyncClient,
    limit: int = 300,
) -> ThumbResult:
    result = ThumbResult()
    with session_factory() as db:
        pending = [
            (v.id, v.youtube_video_id, source_url(v))
            for v in db.scalars(
                select(Video)
                .where(Video.thumb_cached_at.is_(None), Video.is_short.is_(False))
                .order_by(Video.published_at.desc())
                .limit(limit)
            )
        ]
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def fetch(row: tuple[int, str, str]) -> tuple[int, bool | None]:
        pk, video_id, url = row
        async with semaphore:
            try:
                return pk, await download(client, url, thumb_path(settings, video_id))
            except (httpx.HTTPError, ValueError, OSError) as exc:
                log.warning("thumbnail for %s failed: %s", video_id, exc)
                return pk, None

    outcomes = await asyncio.gather(*(fetch(row) for row in pending))
    with session_factory() as db:
        now = utcnow()
        for pk, ok in outcomes:
            if ok is None:
                result.failed += 1  # retried on the next run
                continue
            video = db.get(Video, pk)
            if video is not None:
                # Also set for 404s so they are not requested again; the route serves a placeholder.
                video.thumb_cached_at = now
            result.cached += int(ok)
            result.missing += int(not ok)
        db.commit()
    return result


def prune_orphans(db: Session, settings: Settings) -> int:
    """Delete cached files whose video no longer exists."""
    directory = thumbs_dir(settings)
    if not directory.exists():
        return 0
    known = set(db.scalars(select(Video.youtube_video_id)))
    removed = 0
    for path in directory.glob("*.jpg"):
        if path.stem not in known:
            path.unlink(missing_ok=True)
            removed += 1
    return removed
