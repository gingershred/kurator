from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import Video
from app.youtube import thumbs
from tests.factories import add_channel, add_video, video_id

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


def cached(db: Session) -> dict[str, Video]:
    db.expire_all()
    return {v.youtube_video_id: v for v in db.scalars(select(Video))}


@respx.mock
async def test_cache_pending(
    db: Session, session_factory: sessionmaker[Session], settings: Settings
) -> None:
    channel = add_channel(db, 1)
    add_video(db, channel, 1, thumbnail_url=f"https://i4.ytimg.com/vi/{video_id(1)}/hqdefault.jpg")
    add_video(db, channel, 2, thumbnail_url="https://evil.example/x.jpg")  # not trusted
    add_video(db, channel, 3)  # 404
    add_video(db, channel, 4)  # wrong content type -> retry later
    add_video(db, channel, 5, is_short=True)  # never needed
    respx.get(f"https://i4.ytimg.com/vi/{video_id(1)}/hqdefault.jpg").respond(
        content=JPEG, headers={"content-type": "image/jpeg"}
    )
    respx.get(f"https://i.ytimg.com/vi/{video_id(2)}/hqdefault.jpg").respond(
        content=JPEG, headers={"content-type": "image/jpeg"}
    )
    respx.get(f"https://i.ytimg.com/vi/{video_id(3)}/hqdefault.jpg").respond(status_code=404)
    respx.get(f"https://i.ytimg.com/vi/{video_id(4)}/hqdefault.jpg").respond(
        content=b"<html>", headers={"content-type": "text/html"}
    )

    async with httpx.AsyncClient() as client:
        result = await thumbs.cache_pending(session_factory, settings, client)

    assert (result.cached, result.missing, result.failed) == (2, 1, 1)
    assert thumbs.thumb_path(settings, video_id(1)).read_bytes() == JPEG
    assert thumbs.thumb_path(settings, video_id(2)).exists()
    v = cached(db)
    assert v[video_id(3)].thumb_cached_at is not None  # 404: don't ask again
    assert v[video_id(4)].thumb_cached_at is None
    assert v[video_id(5)].thumb_cached_at is None
    assert not list(thumbs.thumbs_dir(settings).glob("*.tmp"))


@respx.mock
async def test_too_large_thumbnail_rejected(settings: Settings, tmp_path: Path) -> None:
    respx.get("https://i.ytimg.com/big.jpg").respond(
        content=b"x" * (thumbs.MAX_BYTES + 1), headers={"content-type": "image/jpeg"}
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="too large"):
            await thumbs.download(client, "https://i.ytimg.com/big.jpg", tmp_path / "a.jpg")
    assert not (tmp_path / "a.jpg").exists()


def test_prune_orphans(db: Session, settings: Settings) -> None:
    channel = add_channel(db, 1)
    add_video(db, channel, 1)
    thumbs.thumbs_dir(settings).mkdir(parents=True)
    thumbs.thumb_path(settings, video_id(1)).write_bytes(JPEG)
    thumbs.thumb_path(settings, video_id(9)).write_bytes(JPEG)
    assert thumbs.prune_orphans(db, settings) == 1
    assert thumbs.thumb_path(settings, video_id(1)).exists()


def test_thumb_path_rejects_traversal(settings: Settings) -> None:
    with pytest.raises(ValueError):
        thumbs.thumb_path(settings, "../../etc/pa")


def test_route(client: TestClient, alice_client: TestClient, settings: Settings) -> None:
    thumbs.thumbs_dir(settings).mkdir(parents=True)
    thumbs.thumb_path(settings, video_id(1)).write_bytes(JPEG)

    assert client.get(f"/thumbs/{video_id(1)}.jpg", follow_redirects=False).status_code == 303
    response = alice_client.get(f"/thumbs/{video_id(1)}.jpg")
    assert response.status_code == 200 and response.content == JPEG
    assert response.headers["content-type"] == "image/jpeg"
    assert "max-age" in response.headers["cache-control"]

    placeholder = alice_client.get(f"/thumbs/{video_id(2)}.jpg")
    assert placeholder.status_code == 200
    assert placeholder.headers["content-type"].startswith("image/svg+xml")
    assert alice_client.get("/thumbs/bad.jpg").status_code == 404
