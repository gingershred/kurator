import json
from collections.abc import Callable
from datetime import timedelta

import httpx
import pytest
import respx
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import utcnow
from app.models import Channel, User, Video
from app.services import ingest
from app.services.settings import set_global_setting
from app.youtube import quota
from app.youtube.api import API_BASE, YouTubeApi
from app.youtube.rss import FEED_URL
from tests.factories import (
    BASE_TIME,
    add_channel,
    api_playlist_item,
    api_video,
    channel_id,
    feed_xml,
    rss_router,
    subscribe,
    video_id,
)


@pytest.fixture
def api_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"youtube_api_key": SecretStr("AIza-test")})


def videos_by_id(db: Session) -> dict[str, Video]:
    db.expire_all()
    return {v.youtube_video_id: v for v in db.scalars(select(Video))}


def api_handler(
    items: dict[str, dict[str, object]],
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        ids = request.url.params["id"].split(",")
        return httpx.Response(200, json={"items": [items[i] for i in ids if i in items]})

    return handler


def test_channels_to_poll_only_active(db: Session, alice: User, bob: User) -> None:
    active = add_channel(db, 1)
    muted = add_channel(db, 2)
    add_channel(db, 3)  # nobody subscribed
    shared = add_channel(db, 4)
    subscribe(db, alice.id, active)
    subscribe(db, alice.id, muted, status="muted")
    subscribe(db, alice.id, shared)
    subscribe(db, bob.id, shared)
    assert ingest.channels_to_poll(db) == [active.id, shared.id]


@respx.mock
async def test_poll_stores_new_videos_once(
    db: Session, session_factory: sessionmaker[Session], alice: User
) -> None:
    channel = add_channel(db, 1, title="")
    subscribe(db, alice.id, channel)
    cid = channel_id(1)
    feeds = {cid: feed_xml(cid, [(video_id(1), "Erstes", BASE_TIME)], title="Echter Name")}
    respx.get(FEED_URL).mock(side_effect=rss_router(feeds))

    async with httpx.AsyncClient() as client:
        result = await ingest.poll_channels(session_factory, client, [channel.id])
        assert (result.ok, result.failed, result.new_videos) == (1, 0, 1)

        feeds[cid] = feed_xml(
            cid,
            [(video_id(2), "Zweites", BASE_TIME), (video_id(1), "Erstes (neu)", BASE_TIME)],
            title="Echter Name",
        )
        result = await ingest.poll_channels(session_factory, client, [channel.id])
        assert result.new_videos == 1

    videos = videos_by_id(db)
    assert set(videos) == {video_id(1), video_id(2)}
    assert videos[video_id(1)].title == "Erstes (neu)"
    assert videos[video_id(1)].duration_seconds is None and videos[video_id(1)].enriched_at is None
    db.refresh(channel)
    assert channel.title == "Echter Name" and channel.last_success_at is not None


@respx.mock
async def test_broken_feed_does_not_stop_run(
    db: Session, session_factory: sessionmaker[Session]
) -> None:
    good, broken = add_channel(db, 1), add_channel(db, 2)
    feeds = {channel_id(1): feed_xml(channel_id(1), [(video_id(1), "Gut", BASE_TIME)])}
    respx.get(FEED_URL).mock(side_effect=rss_router(feeds))

    async with httpx.AsyncClient() as client:
        result = await ingest.poll_channels(session_factory, client, [broken.id, good.id])
        assert (result.ok, result.failed, result.new_videos) == (1, 1, 1)
        await ingest.poll_channels(session_factory, client, [broken.id])

    db.refresh(broken)
    assert broken.error_count == 2 and "not found" in (broken.last_error or "")
    assert broken.last_success_at is None

    feeds[channel_id(2)] = feed_xml(channel_id(2), [])
    async with httpx.AsyncClient() as client:
        await ingest.poll_channels(session_factory, client, [broken.id])
    db.refresh(broken)
    assert broken.error_count == 0 and broken.last_error is None


@respx.mock
async def test_enrich_detects_shorts_live_and_premieres(
    db: Session, session_factory: sessionmaker[Session], api_settings: Settings
) -> None:
    channel = add_channel(db, 1)
    for n in range(1, 7):
        db.add(Video(youtube_video_id=video_id(n), channel_id=channel.id, published_at=BASE_TIME))
    db.commit()
    items = {
        video_id(1): api_video(video_id(1), 45),  # Short
        video_id(2): api_video(video_id(2), 180),  # exactly the limit -> Short
        video_id(3): api_video(video_id(3), 181),
        video_id(4): api_video(video_id(4), None, live="upcoming"),  # P0D but upcoming
        video_id(5): api_video(video_id(5), None, live="live"),
        # video 6 missing from the response (deleted/private)
    }
    route = respx.get(f"{API_BASE}/videos").mock(side_effect=api_handler(items))

    async with httpx.AsyncClient() as client:
        api = YouTubeApi(api_settings, session_factory, client)
        result = await ingest.enrich_pending(session_factory, api, api_settings)

    assert route.call_count == 1
    assert (result.enriched, result.shorts, result.calls) == (5, 2, 1)
    v = videos_by_id(db)
    assert v[video_id(1)].is_short and v[video_id(2)].is_short
    assert not v[video_id(3)].is_short and v[video_id(3)].duration_seconds == 181
    assert v[video_id(3)].tags == ["tag"] and v[video_id(3)].category_id == "27"
    assert v[video_id(4)].is_premiere and not v[video_id(4)].is_short
    assert v[video_id(4)].live_status == "upcoming"
    assert v[video_id(5)].is_live and v[video_id(5)].live_status == "live"
    assert v[video_id(6)].enriched_at is not None and v[video_id(6)].duration_seconds is None


@respx.mock
async def test_upcoming_is_rechecked_and_released(
    db: Session, session_factory: sessionmaker[Session], api_settings: Settings
) -> None:
    channel = add_channel(db, 1)
    db.add(
        Video(
            youtube_video_id=video_id(1),
            channel_id=channel.id,
            published_at=BASE_TIME,
            live_status="upcoming",
            is_premiere=True,
            enriched_at=utcnow() - timedelta(minutes=10),
        )
    )
    db.commit()
    items = {video_id(1): api_video(video_id(1), 1500)}
    route = respx.get(f"{API_BASE}/videos").mock(side_effect=api_handler(items))

    async with httpx.AsyncClient() as client:
        api = YouTubeApi(api_settings, session_factory, client)
        await ingest.enrich_pending(session_factory, api, api_settings)
        assert route.call_count == 0  # re-check only after 30 minutes

        video = db.scalars(select(Video)).one()
        video.enriched_at = utcnow() - timedelta(minutes=31)
        db.commit()
        await ingest.enrich_pending(session_factory, api, api_settings)

    v = videos_by_id(db)[video_id(1)]
    assert v.live_status == "completed" and not v.is_premiere and not v.is_short
    assert v.duration_seconds == 1500


async def test_enrich_without_key_is_skipped(
    db: Session, session_factory: sessionmaker[Session], settings: Settings
) -> None:
    channel = add_channel(db, 1)
    db.add(Video(youtube_video_id=video_id(1), channel_id=channel.id, published_at=BASE_TIME))
    db.commit()
    async with httpx.AsyncClient() as client:
        api = YouTubeApi(settings, session_factory, client)
        result = await ingest.enrich_pending(session_factory, api, settings)
    assert result.stopped == "no_api_key" and result.calls == 0


@respx.mock
async def test_enrich_stops_when_budget_is_used_up(
    db: Session, session_factory: sessionmaker[Session], api_settings: Settings
) -> None:
    channel = add_channel(db, 1)
    for n in range(120):
        db.add(Video(youtube_video_id=video_id(n), channel_id=channel.id, published_at=BASE_TIME))
    db.commit()
    set_global_setting(db, "quota_daily_budget", 2)
    items = {video_id(n): api_video(video_id(n)) for n in range(120)}
    route = respx.get(f"{API_BASE}/videos").mock(side_effect=api_handler(items))

    async with httpx.AsyncClient() as client:
        api = YouTubeApi(api_settings, session_factory, client)
        result = await ingest.enrich_pending(session_factory, api, api_settings)

    assert route.call_count == 2
    assert result.stopped == "quota" and result.enriched == 100
    assert quota.used_today(db) == 2


@respx.mock
async def test_simulated_day_with_100_channels_stays_far_below_1000_units(
    db: Session, session_factory: sessionmaker[Session], api_settings: Settings, alice: User
) -> None:
    channels = [add_channel(db, n) for n in range(100)]
    for channel in channels:
        subscribe(db, alice.id, channel)
    feeds: dict[str, list[tuple[str, str, object]]] = {c.youtube_channel_id: [] for c in channels}

    def rss(request: httpx.Request) -> httpx.Response:
        cid = "UC" + request.url.params["playlist_id"][4:]
        return httpx.Response(200, content=feed_xml(cid, feeds[cid][-15:]))  # type: ignore[arg-type]

    def videos(request: httpx.Request) -> httpx.Response:
        ids = request.url.params["id"].split(",")

        def live(vid: str) -> str:
            return "upcoming" if int(vid[1:]) % 20 == 0 else "none"

        return httpx.Response(
            200, content=json.dumps({"items": [api_video(i, 1200, live(i)) for i in ids]})
        )

    respx.get(FEED_URL).mock(side_effect=rss)
    respx.get(f"{API_BASE}/videos").mock(side_effect=videos)
    respx.get(host__regex=r".*\.ytimg\.com").respond(
        content=b"jpeg", headers={"content-type": "image/jpeg"}
    )

    n = 0
    for poll in range(48):  # every 30 minutes
        for _ in range(3 if poll % 3 else 4):  # ~150 new videos per day
            cid = channels[n % 100].youtube_channel_id
            feeds[cid].append((video_id(n), f"Video {n}", BASE_TIME + timedelta(minutes=n)))
            n += 1
        await ingest.poll_and_enrich(session_factory, api_settings)
        # Pretend 30 minutes pass so upcoming videos are re-checked on the next run.
        db.execute(
            update(Video)
            .where(Video.live_status == "upcoming")
            .values(enriched_at=utcnow() - timedelta(minutes=31))
        )
        db.commit()

    assert db.query(Video).count() == n
    used = quota.used_today(db)
    assert used < 1000
    assert used <= 48 * 2  # at most a couple of videos.list calls per poll
    assert db.query(Channel).filter(Channel.error_count > 0).count() == 0


@respx.mock
async def test_concurrent_duplicate_insert_is_not_a_channel_error(
    db: Session, session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = add_channel(db, 1)
    feeds = {channel_id(1): feed_xml(channel_id(1), [(video_id(1), "Eins", BASE_TIME)])}
    respx.get(FEED_URL).mock(side_effect=rss_router(feeds))

    def racing_store(*_args: object) -> int:
        from sqlalchemy.exc import IntegrityError

        raise IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed"))

    monkeypatch.setattr(ingest, "store_feed", racing_store)
    async with httpx.AsyncClient() as client:
        result = await ingest.poll_channels(session_factory, client, [channel.id])
    db.refresh(channel)
    assert (result.ok, result.failed) == (1, 0) and channel.error_count == 0


# --- RSS fallback via playlistItems.list ----------------------------------------------------


def fallback_for(api: YouTubeApi, api_settings: Settings, minutes: int = 72) -> ingest.Fallback:
    return ingest.Fallback(api, api_settings, timedelta(minutes=minutes))


@respx.mock
async def test_rss_failure_falls_back_to_api(
    db: Session, session_factory: sessionmaker[Session], api_settings: Settings
) -> None:
    channel = add_channel(db, 1)
    rss = respx.get(FEED_URL).respond(status_code=500)
    playlist = respx.get(f"{API_BASE}/playlistItems").respond(
        json={"items": [api_playlist_item(video_id(1), channel_id(1))]}
    )
    async with httpx.AsyncClient() as client:
        api = YouTubeApi(api_settings, session_factory, client)
        fb = fallback_for(api, api_settings)
        result = await ingest.poll_channels(session_factory, client, [channel.id], fallback=fb)
        assert (result.ok, result.failed, result.rss_failed, result.fallback) == (1, 0, 1, 1)
        assert rss.call_count == 2  # one retry
        assert playlist.calls[0].request.url.params["playlistId"] == "UULF" + channel_id(1)[2:]

        # Right after a fallback the channel is not due again: RSS error is recorded instead.
        result = await ingest.poll_channels(session_factory, client, [channel.id], fallback=fb)
        assert (result.failed, result.fallback) == (1, 0)
    assert playlist.call_count == 1
    assert [v.youtube_video_id for v in videos_by_id(db).values()] == [video_id(1)]
    db.refresh(channel)
    assert channel.last_fallback_at is not None
    assert "postponed" in (channel.last_error or "")
    assert quota.used(db, quota.quota_day(), "playlistItems.list") == 1


@respx.mock
async def test_no_fallback_without_api_key(
    db: Session, session_factory: sessionmaker[Session], settings: Settings
) -> None:
    channel = add_channel(db, 1)
    respx.get(FEED_URL).respond(status_code=500)
    playlist = respx.get(f"{API_BASE}/playlistItems").respond(json={"items": []})
    async with httpx.AsyncClient() as client:
        api = YouTubeApi(settings, session_factory, client)
        result = await ingest.poll_channels(
            session_factory, client, [channel.id], fallback=fallback_for(api, settings)
        )
    assert result.failed == 1 and playlist.call_count == 0
    db.refresh(channel)
    assert channel.error_count == 1 and channel.last_fallback_at is None


@respx.mock
async def test_fallback_respects_its_budget_share(
    db: Session, session_factory: sessionmaker[Session], api_settings: Settings
) -> None:
    channels = [add_channel(db, n) for n in range(5)]
    set_global_setting(db, "quota_daily_budget", 12)  # fallback share: 3 units
    respx.get(FEED_URL).respond(status_code=503)
    playlist = respx.get(f"{API_BASE}/playlistItems").respond(json={"items": []})
    async with httpx.AsyncClient() as client:
        api = YouTubeApi(api_settings, session_factory, client)
        result = await ingest.poll_channels(
            session_factory,
            client,
            [c.id for c in channels],
            concurrency=1,
            fallback=fallback_for(api, api_settings),
        )
    assert playlist.call_count == 3
    assert (result.fallback, result.failed) == (3, 2)


@respx.mock
async def test_full_rss_outage_for_a_day_stays_within_fallback_share(
    db: Session, session_factory: sessionmaker[Session], api_settings: Settings, alice: User
) -> None:
    channels = [add_channel(db, n) for n in range(100)]
    for channel in channels:
        subscribe(db, alice.id, channel)
    respx.get(FEED_URL).respond(status_code=500)
    respx.get(f"{API_BASE}/playlistItems").respond(json={"items": []})

    for _poll in range(48):  # every 30 minutes
        await ingest.poll_and_enrich(session_factory, api_settings)
        # Pretend 30 minutes pass.
        db.expire_all()
        for channel in db.scalars(select(Channel).where(Channel.last_fallback_at.is_not(None))):
            assert channel.last_fallback_at is not None
            channel.last_fallback_at -= timedelta(minutes=30)
        db.commit()

    used = quota.used(db, quota.quota_day(), "playlistItems.list")
    assert 0 < used <= quota.fallback_budget(db, api_settings)  # 2000
    assert quota.used_today(db) == used  # nothing else was spent


@respx.mock
async def test_many_channels_with_real_suspension_do_not_exhaust_the_pool(
    db: Session, session_factory: sessionmaker[Session], alice: User
) -> None:
    """Regression: sessions must not be held across awaits (pool size is 15)."""
    import asyncio

    channels = [add_channel(db, n) for n in range(60)]

    async def slow_feed(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.01)  # a real network wait lets all coroutines interleave
        cid = "UC" + request.url.params["playlist_id"][4:]
        return httpx.Response(200, content=feed_xml(cid, []))

    respx.get(FEED_URL).mock(side_effect=slow_feed)
    async with httpx.AsyncClient() as client:
        result = await asyncio.wait_for(
            ingest.poll_channels(session_factory, client, [c.id for c in channels]), timeout=20
        )
    assert (result.ok, result.failed) == (60, 0)
