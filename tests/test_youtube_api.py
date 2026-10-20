from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import User
from app.services.settings import set_global_setting
from app.youtube import quota
from app.youtube.api import (
    API_BASE,
    ApiError,
    ApiUnavailable,
    PlaylistNotFound,
    QuotaExhausted,
    YouTubeApi,
    parse_duration,
)
from tests.factories import api_playlist_item

KEY = "AIza-test-key"


def video_item(video_id: str, duration: str = "PT12M3S", live: str = "none") -> dict[str, object]:
    return {
        "id": video_id,
        "snippet": {
            "title": f"Video {video_id}",
            "description": "Beschreibung",
            "publishedAt": "2026-10-01T10:00:00Z",
            "tags": ["a", "b"],
            "categoryId": "27",
            "liveBroadcastContent": live,
            "thumbnails": {"high": {"url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"}},
        },
        "contentDetails": {"duration": duration},
    }


@pytest.fixture
def api_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"youtube_api_key": SecretStr(KEY)})


@pytest.fixture
async def api(
    api_settings: Settings, session_factory: sessionmaker[Session]
) -> AsyncIterator[YouTubeApi]:
    async with httpx.AsyncClient() as client:
        yield YouTubeApi(api_settings, session_factory, client)


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("PT12M3S", 723),
        ("PT1H", 3600),
        ("PT1H2M3S", 3723),
        ("PT59S", 59),
        ("P0D", 0),
        ("P1DT2H", 93600),
        ("PT0S", 0),
        (None, None),
        ("", None),
        ("PT", None),
        ("garbage", None),
    ],
)
def test_parse_duration(value: str | None, seconds: int | None) -> None:
    assert parse_duration(value) == seconds


@respx.mock
async def test_videos_call_books_quota_and_sends_key_as_header(
    api: YouTubeApi, db: Session
) -> None:
    route = respx.get(f"{API_BASE}/videos").respond(
        json={"items": [video_item("vid00000001"), video_item("vid00000002", "PT45S", "upcoming")]}
    )
    videos = await api.videos(["vid00000001", "vid00000002"])

    request = route.calls[0].request
    assert request.headers["x-goog-api-key"] == KEY
    assert KEY not in str(request.url)
    assert request.url.params["id"] == "vid00000001,vid00000002"
    assert videos[0].duration_seconds == 723
    assert videos[0].tags == ["a", "b"] and videos[0].category_id == "27"
    assert videos[1].duration_seconds == 45
    assert videos[1].live_broadcast_content == "upcoming"
    assert quota.used_today(db) == 1


async def test_no_key_raises(settings: Settings, session_factory: sessionmaker[Session]) -> None:
    async with httpx.AsyncClient() as client:
        api = YouTubeApi(settings, session_factory, client)
        assert not api.available
        with pytest.raises(ApiUnavailable):
            await api.videos(["vid00000001"])


@respx.mock
async def test_budget_exhausted_makes_no_call(api: YouTubeApi, db: Session) -> None:
    route = respx.get(f"{API_BASE}/videos").respond(json={"items": []})
    set_global_setting(db, "quota_daily_budget", 1)
    await api.videos(["vid00000001"])
    with pytest.raises(QuotaExhausted):
        await api.videos(["vid00000001"])
    assert route.call_count == 1
    assert quota.used_today(db) == 1


@respx.mock
async def test_google_quota_error(api: YouTubeApi) -> None:
    respx.get(f"{API_BASE}/videos").respond(
        status_code=403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}}
    )
    with pytest.raises(QuotaExhausted):
        await api.videos(["vid00000001"])


@respx.mock
async def test_other_api_errors(api: YouTubeApi) -> None:
    respx.get(f"{API_BASE}/videos").respond(
        status_code=400, json={"error": {"errors": [{"reason": "keyInvalid"}]}}
    )
    with pytest.raises(ApiError, match="keyInvalid"):
        await api.videos(["vid00000001"])


async def test_too_many_ids(api: YouTubeApi) -> None:
    with pytest.raises(ValueError):
        await api.videos([f"vid{i:08d}" for i in range(51)])


@respx.mock
async def test_channel_by_handle(api: YouTubeApi, db: Session, alice: User) -> None:
    route = respx.get(f"{API_BASE}/channels").respond(
        json={
            "items": [
                {
                    "id": "UC" + "a" * 22,
                    "snippet": {
                        "title": "Kanal",
                        "customUrl": "@kanal",
                        "thumbnails": {"default": {"url": "https://yt3.example/a.jpg"}},
                    },
                }
            ]
        }
    )
    info = await api.channel_by_handle("kanal", user_id=alice.id)
    assert route.calls[0].request.url.params["forHandle"] == "@kanal"
    assert info is not None and info.channel_id == "UC" + "a" * 22
    assert info.handle == "kanal" and info.avatar_url == "https://yt3.example/a.jpg"

    respx.get(f"{API_BASE}/channels").respond(json={})
    assert await api.channel_by_handle("missing") is None
    assert quota.used_today(db) == 2


@respx.mock
async def test_playlist_items(api: YouTubeApi, db: Session) -> None:
    cid = "UC" + "a" * 22
    route = respx.get(f"{API_BASE}/playlistItems").respond(
        json={
            "items": [
                api_playlist_item("vid00000001", cid),
                {  # private video: no videoPublishedAt
                    "snippet": {"title": "Private video", "resourceId": {"videoId": "vid00000002"}},
                    "contentDetails": {"videoId": "vid00000002"},
                },
            ]
        }
    )
    entries = await api.playlist_items("UULF" + "a" * 22)
    assert route.calls[0].request.url.params["playlistId"] == "UULF" + "a" * 22
    assert [e.video_id for e in entries] == ["vid00000001"]
    assert entries[0].channel_id == cid and entries[0].description == "aus der API"
    assert quota.used(db, quota.quota_day(), "playlistItems.list") == 1


@respx.mock
async def test_playlist_not_found(api: YouTubeApi) -> None:
    respx.get(f"{API_BASE}/playlistItems").respond(
        status_code=404, json={"error": {"errors": [{"reason": "playlistNotFound"}]}}
    )
    with pytest.raises(PlaylistNotFound):
        await api.playlist_items("UULF" + "b" * 22)


def test_fallback_interval(db: Session, settings: Settings) -> None:
    assert quota.fallback_budget(db, settings) == 2000
    assert quota.fallback_interval_minutes(db, settings, 100, 30) == 72
    assert quota.fallback_interval_minutes(db, settings, 10, 30) == 30
    set_global_setting(db, "quota_daily_budget", 0)
    assert quota.fallback_interval_minutes(db, settings, 10, 30) == 1440
