from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from app.youtube.rss import FEED_URL, FeedError, fetch_uploads, parse_feed

FIXTURE = Path(__file__).parent / "fixtures" / "uulf_feed.xml"
CID = "UC" + "a" * 22


def test_parse_feed() -> None:
    feed = parse_feed(FIXTURE.read_bytes())
    assert feed.channel_id == CID
    assert feed.channel_title == "Testkanal – Wissen & Technik"
    assert [e.video_id for e in feed.entries] == ["vid00000001", "vid00000002"]
    first = feed.entries[0]
    assert first.title == "Wie funktioniert ein Kernkraftwerk?"
    assert first.published_at == datetime(2026, 10, 8, 14, 7, tzinfo=UTC)
    assert "20 Minuten" in first.description
    assert first.thumbnail_url == "https://i4.ytimg.com/vi/vid00000001/hqdefault.jpg"
    assert feed.entries[1].description == ""


def test_malformed_entries_are_skipped() -> None:
    xml = FIXTURE.read_bytes().replace(b"<yt:videoId>vid00000002", b"<yt:videoId>bad")
    assert [e.video_id for e in parse_feed(xml).entries] == ["vid00000001"]


def test_garbage_raises() -> None:
    with pytest.raises(FeedError):
        parse_feed(b"<html>not a feed")


@respx.mock
async def test_fetch_uses_uulf_playlist() -> None:
    route = respx.get(FEED_URL, params={"playlist_id": "UULF" + "a" * 22}).respond(
        content=FIXTURE.read_bytes()
    )
    async with httpx.AsyncClient() as client:
        feed = await fetch_uploads(client, CID)
    assert route.called
    assert route.calls[0].request.headers["user-agent"].startswith("kurator/")
    assert len(feed.entries) == 2


@respx.mock
@pytest.mark.parametrize("status", [404, 500])
async def test_fetch_http_errors(status: int) -> None:
    respx.get(FEED_URL).respond(status_code=status)
    async with httpx.AsyncClient() as client:
        with pytest.raises(FeedError):
            await fetch_uploads(client, CID)


@respx.mock
async def test_fetch_network_error() -> None:
    respx.get(FEED_URL).mock(side_effect=httpx.ConnectTimeout("timeout"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(FeedError, match="ConnectTimeout"):
            await fetch_uploads(client, CID)
