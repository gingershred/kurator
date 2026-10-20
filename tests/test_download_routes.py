from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import utcnow
from app.models import Download, DownloadKind, User, Video
from app.playback import downloads
from app.services.settings import set_global_setting
from tests.conftest import login, new_client, post
from tests.factories import add_channel, add_video, subscribe, video_id

HX = {"HX-Request": "true"}


@pytest.fixture
def world(db: Session, settings: Settings, make_user: Callable[..., User]) -> dict[str, object]:
    set_global_setting(db, "downloads_enabled", True)
    channel = add_channel(db, 1)
    video = add_video(db, channel, 1)
    users = {}
    for name in ("alice", "bob", "carol", "dave"):
        user = make_user(name)
        user.can_download = name != "dave"
        users[name] = user
    db.commit()
    for name in ("alice", "carol", "dave"):  # bob does not follow the channel
        subscribe(db, users[name].id, channel)
    return {"video": video, **users}


def finished(db: Session, settings: Settings, user: User, video: Video) -> Download:
    download = downloads.request_download(db, settings, user, video, DownloadKind.VIDEO)
    root = downloads.media_root(settings)
    (root / "UC1").mkdir(parents=True, exist_ok=True)
    (root / "UC1/v.mp4").write_bytes(bytes(range(256)) * 4)  # 1024 bytes
    (root / "UC1/v.de.vtt").write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nHallo\n")
    download.status, download.file_path, download.size_bytes = "done", "UC1/v.mp4", 1024
    download.subtitles = {"de": "UC1/v.de.vtt"}
    download.finished_at = utcnow()
    db.commit()
    return download


def client_for(app: FastAPI, name: str) -> TestClient:
    client = new_client(app)
    login(client, name)
    return client


def test_request_and_status(app: FastAPI, world: dict[str, object], db: Session) -> None:
    alice = client_for(app, "alice")
    response = post(alice, f"/videos/{video_id(1)}/download/video", headers=HX)
    assert response.status_code == 200 and "wartet" in response.text
    assert 'hx-trigger="every 2s"' in response.text  # keeps polling while queued
    assert "wartet" in alice.get(f"/videos/{video_id(1)}/downloads").text
    removed = post(alice, f"/videos/{video_id(1)}/download/video/remove", headers=HX)
    assert "Herunterladen" in removed.text
    assert post(alice, f"/videos/{video_id(1)}/download/nonsense").status_code == 404


def test_media_with_range_and_subtitles(
    app: FastAPI, world: dict[str, object], db: Session, settings: Settings
) -> None:
    download = finished(db, settings, world["alice"], world["video"])  # type: ignore[arg-type]
    alice = client_for(app, "alice")
    full = alice.get(f"/media/{download.id}")
    assert full.status_code == 200 and len(full.content) == 1024
    assert full.headers["content-type"] == "video/mp4"
    assert full.headers["accept-ranges"] == "bytes"
    part = alice.get(f"/media/{download.id}", headers={"Range": "bytes=100-199"})
    assert part.status_code == 206 and part.content == (bytes(range(256)) * 4)[100:200]
    assert part.headers["content-range"] == "bytes 100-199/1024"
    subs = alice.get(f"/media/{download.id}/subs/de.vtt")
    assert subs.status_code == 200 and subs.text.startswith("WEBVTT")
    assert alice.get(f"/media/{download.id}/subs/fr.vtt").status_code == 404
    db.refresh(download)
    assert download.last_played_at is not None


def test_media_access_rules(
    app: FastAPI, world: dict[str, object], db: Session, settings: Settings
) -> None:
    download = finished(db, settings, world["alice"], world["video"])  # type: ignore[arg-type]
    # Carol did not request it, but the video is in her feed.
    assert client_for(app, "carol").get(f"/media/{download.id}").status_code == 200
    # Bob may download but neither requested it nor follows the channel.
    bob = client_for(app, "bob")
    assert bob.get(f"/media/{download.id}").status_code == 404
    assert bob.get(f"/media/{download.id}/subs/de.vtt").status_code == 404
    # Dave follows the channel but has no download permission.
    assert client_for(app, "dave").get(f"/media/{download.id}").status_code == 404
    # Anonymous users are sent to the login.
    anonymous = new_client(app)
    assert anonymous.get(f"/media/{download.id}", follow_redirects=False).status_code == 303


def test_everything_invisible_when_disabled(
    app: FastAPI, world: dict[str, object], db: Session, settings: Settings
) -> None:
    download = finished(db, settings, world["alice"], world["video"])  # type: ignore[arg-type]
    set_global_setting(db, "downloads_enabled", False)
    alice = client_for(app, "alice")
    for path in (f"/videos/{video_id(1)}/downloads", f"/media/{download.id}"):
        assert alice.get(path).status_code == 404, path
    assert post(alice, f"/videos/{video_id(1)}/download/video").status_code == 404
    page = alice.get(f"/videos/{video_id(1)}").text
    assert "Herunterladen" not in page and "/media/" not in page


def test_user_without_permission_sees_nothing(
    app: FastAPI, world: dict[str, object], db: Session
) -> None:
    dave = client_for(app, "dave")
    assert dave.get(f"/videos/{video_id(1)}/downloads").status_code == 404
    assert post(dave, f"/videos/{video_id(1)}/download/video").status_code == 404
    assert "Herunterladen" not in dave.get(f"/videos/{video_id(1)}").text


def test_bob_cannot_remove_alices_request(
    app: FastAPI, world: dict[str, object], db: Session, settings: Settings
) -> None:
    download = finished(db, settings, world["alice"], world["video"])  # type: ignore[arg-type]
    bob = client_for(app, "bob")
    # Bob can't even address the video (not in his feed) …
    assert post(bob, f"/videos/{video_id(1)}/download/video/remove").status_code == 404
    # … and Carol, who can, only ever removes her own request.
    carol = client_for(app, "carol")
    post(carol, f"/videos/{video_id(1)}/download/video/remove")
    assert downloads.requested_by(db, world["alice"], download)  # type: ignore[arg-type]
