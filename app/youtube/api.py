"""Minimal YouTube Data API v3 client.

Every call is checked against the daily budget *before* it is made and booked in the quota
ledger. Once the budget is used up, no further calls are made. The API key is sent as a
header so it never appears in URLs or request logs.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.youtube import quota

API_BASE = "https://www.googleapis.com/youtube/v3"
DEFAULT_TIMEOUT = 15.0
MAX_IDS_PER_CALL = 50

_DURATION_RE = re.compile(
    r"P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?"
)


class ApiUnavailable(Exception):
    """No API key configured."""


class QuotaExhausted(Exception):
    """The daily budget (or YouTube's own quota) is used up."""


class ApiError(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(f"YouTube API error {status}: {reason}")
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class ChannelInfo:
    channel_id: str
    title: str
    handle: str | None
    avatar_url: str | None


@dataclass(frozen=True)
class VideoInfo:
    video_id: str
    title: str
    description: str
    published_at: datetime | None
    duration_seconds: int | None
    tags: list[str] = field(default_factory=list)
    category_id: str | None = None
    # "none", "upcoming" or "live" (snippet.liveBroadcastContent)
    live_broadcast_content: str = "none"
    thumbnail_url: str | None = None


def parse_duration(value: str | None) -> int | None:
    """Parse an ISO-8601 duration as used by the API (e.g. PT1H2M3S, P1DT2H, P0D)."""
    if not value:
        return None
    match = _DURATION_RE.fullmatch(value)
    if match is None or value in ("P", "PT"):
        return None
    parts = {k: int(v) for k, v in match.groupdict(default="0").items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


def _best_thumbnail(thumbnails: dict[str, Any]) -> str | None:
    for size in ("high", "medium", "default"):
        url = thumbnails.get(size, {}).get("url")
        if url:
            return str(url)
    return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _channel_info(item: dict[str, Any]) -> ChannelInfo:
    snippet = item.get("snippet", {})
    custom_url = snippet.get("customUrl")
    return ChannelInfo(
        channel_id=item["id"],
        title=snippet.get("title", ""),
        handle=custom_url.lstrip("@") if custom_url else None,
        avatar_url=_best_thumbnail(snippet.get("thumbnails", {})),
    )


def _video_info(item: dict[str, Any]) -> VideoInfo:
    snippet = item.get("snippet", {})
    details = item.get("contentDetails", {})
    return VideoInfo(
        video_id=item["id"],
        title=snippet.get("title", ""),
        description=snippet.get("description", ""),
        published_at=_parse_datetime(snippet.get("publishedAt")),
        duration_seconds=parse_duration(details.get("duration")),
        tags=list(snippet.get("tags", [])),
        category_id=snippet.get("categoryId"),
        live_broadcast_content=snippet.get("liveBroadcastContent", "none"),
        thumbnail_url=_best_thumbnail(snippet.get("thumbnails", {})),
    )


class YouTubeApi:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        client: httpx.AsyncClient,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.client = client
        self._budget_lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self.settings.youtube_api_key is not None

    async def _call(
        self, method: str, endpoint: str, params: dict[str, str], user_id: int | None = None
    ) -> dict[str, Any]:
        if self.settings.youtube_api_key is None:
            raise ApiUnavailable
        async with self._budget_lock:
            with self.session_factory() as db:
                if not quota.can_spend(db, self.settings, method):
                    raise QuotaExhausted
                # Booked before the call: YouTube charges failed calls, too.
                quota.record(db, method, user_id=user_id)
        try:
            response = await self.client.get(
                f"{API_BASE}/{endpoint}",
                params=params,
                headers={"X-Goog-Api-Key": self.settings.youtube_api_key.get_secret_value()},
                timeout=DEFAULT_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise ApiError(0, f"{type(exc).__name__}: {exc}") from exc
        if response.status_code != 200:
            reason = _error_reason(response)
            if reason in ("quotaExceeded", "dailyLimitExceeded"):
                raise QuotaExhausted
            raise ApiError(response.status_code, reason)
        data: dict[str, Any] = response.json()
        return data

    async def _channels(
        self, params: dict[str, str], user_id: int | None = None
    ) -> list[ChannelInfo]:
        data = await self._call("channels.list", "channels", {"part": "snippet", **params}, user_id)
        return [_channel_info(item) for item in data.get("items", [])]

    async def channel_by_handle(
        self, handle: str, user_id: int | None = None
    ) -> ChannelInfo | None:
        found = await self._channels({"forHandle": "@" + handle.lstrip("@")}, user_id)
        return found[0] if found else None

    async def channel_by_username(
        self, username: str, user_id: int | None = None
    ) -> ChannelInfo | None:
        found = await self._channels({"forUsername": username}, user_id)
        return found[0] if found else None

    async def channels(
        self, channel_ids: list[str], user_id: int | None = None
    ) -> list[ChannelInfo]:
        if not channel_ids:
            return []
        if len(channel_ids) > MAX_IDS_PER_CALL:
            raise ValueError("at most 50 channel ids per call")
        return await self._channels({"id": ",".join(channel_ids)}, user_id)

    async def videos(self, video_ids: list[str]) -> list[VideoInfo]:
        """Details for up to 50 videos in one call (1 unit). Unknown IDs are omitted."""
        if not video_ids:
            return []
        if len(video_ids) > MAX_IDS_PER_CALL:
            raise ValueError("at most 50 video ids per call")
        data = await self._call(
            "videos.list",
            "videos",
            {"part": "snippet,contentDetails", "id": ",".join(video_ids), "maxResults": "50"},
        )
        return [_video_info(item) for item in data.get("items", [])]


def _error_reason(response: httpx.Response) -> str:
    try:
        errors = response.json()["error"]["errors"]
        return str(errors[0]["reason"])
    except (ValueError, KeyError, IndexError, TypeError):
        return f"HTTP {response.status_code}"
