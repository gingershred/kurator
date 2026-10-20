from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import utcnow
from app.models import Channel, Download, DownloadKind, User, Video
from app.playback import downloads
from app.playback.embed import PlaybackMode, choose_mode
from app.services import settings as user_settings
from app.services.settings import set_global_setting
from tests.conftest import login, new_client, post
from tests.factories import add_channel, add_video, subscribe, video_id


@pytest.mark.parametrize(
    ("requested", "preferred", "local", "audio", "expected"),
    [
        (None, "embed", True, True, PlaybackMode.EMBED),
        (None, "download", True, True, PlaybackMode.LOCAL),
        (None, "download", False, True, PlaybackMode.AUDIO),
        (None, "download", False, False, PlaybackMode.EMBED),  # embed is always the fallback
        ("audio", "embed", True, True, PlaybackMode.AUDIO),
        ("local", "embed", False, True, PlaybackMode.EMBED),  # not available
        ("bogus", "download", True, False, PlaybackMode.LOCAL),
    ],
)
def test_choose_mode(
    requested: str | None, preferred: str, local: bool, audio: bool, expected: PlaybackMode
) -> None:
    assert choose_mode(requested, preferred, local, audio) == expected


@pytest.fixture
def setup(db: Session, settings: Settings, make_user: Callable[..., User]) -> dict[str, object]:
    set_global_setting(db, "downloads_enabled", True)
    channel = add_channel(db, 1)
    video = add_video(db, channel, 1, seconds=600)
    alice, plain = make_user("alice"), make_user("plain")
    alice.can_download = True
    db.commit()
    subscribe(db, alice.id, channel)
    subscribe(db, plain.id, channel)
    return {"video": video, "alice": alice, "plain": plain}


def done(db: Session, settings: Settings, user: User, video: Video, kind: DownloadKind) -> Download:
    download = downloads.request_download(db, settings, user, video, kind)
    root = downloads.media_root(settings) / "UC1"
    root.mkdir(parents=True, exist_ok=True)
    name = f"{kind.value}.mp4"
    (root / name).write_bytes(b"x" * 10)
    (root / f"{kind.value}.de.vtt").write_text("WEBVTT\n")
    download.status, download.file_path, download.size_bytes = "done", f"UC1/{name}", 10
    download.subtitles = {"de": f"UC1/{kind.value}.de.vtt"}
    download.finished_at = utcnow()
    db.commit()
    return download


def client_for(app: FastAPI, name: str) -> TestClient:
    client = new_client(app)
    login(client, name)
    return client


def test_video_page_playback_choice(
    app: FastAPI, db: Session, settings: Settings, setup: dict[str, object]
) -> None:
    video, alice = setup["video"], setup["alice"]
    local = done(db, settings, alice, video, DownloadKind.VIDEO)  # type: ignore[arg-type]
    client = client_for(app, "alice")

    page = client.get(f"/videos/{video_id(1)}").text
    assert 'id="player"' in page  # preference is "embed"
    assert 'href="?play=local"' in page and "Nur Audio herunterladen" in page

    page = client.get(f"/videos/{video_id(1)}?play=local").text
    assert '<video id="local-player"' in page and f'src="/media/{local.id}"' in page
    assert f'src="/media/{local.id}/subs/de.vtt"' in page and 'label="Deutsch"' in page

    user_settings.set_user_setting(db, alice.id, "playback_mode", "download")  # type: ignore[attr-defined]
    assert '<video id="local-player"' in client.get(f"/videos/{video_id(1)}").text
    assert 'id="player"' in client.get(f"/videos/{video_id(1)}?play=embed").text


def test_audio_only(
    app: FastAPI, db: Session, settings: Settings, setup: dict[str, object]
) -> None:
    audio = done(db, settings, setup["alice"], setup["video"], DownloadKind.AUDIO)  # type: ignore[arg-type]
    page = client_for(app, "alice").get(f"/videos/{video_id(1)}?play=audio").text
    assert '<audio id="local-player"' in page and f'src="/media/{audio.id}"' in page


def test_no_choice_without_permission(
    app: FastAPI, db: Session, settings: Settings, setup: dict[str, object]
) -> None:
    done(db, settings, setup["alice"], setup["video"], DownloadKind.VIDEO)  # type: ignore[arg-type]
    page = client_for(app, "plain").get(f"/videos/{video_id(1)}?play=local").text
    assert 'id="player"' in page and "local-player" not in page and "?play=" not in page


def test_playback_preference_setting(app: FastAPI, db: Session, setup: dict[str, object]) -> None:
    alice, plain = setup["alice"], setup["plain"]
    client = client_for(app, "alice")
    assert "Standard-Wiedergabe" in client.get("/settings").text
    post(client, "/settings", {"playback_mode": "download"})
    db.expire_all()
    assert user_settings.get_user_setting(db, alice.id, "playback_mode") == "download"  # type: ignore[attr-defined]
    post(client, "/settings", {"playback_mode": "evil"})
    db.expire_all()
    assert user_settings.get_user_setting(db, alice.id, "playback_mode") == "download"  # type: ignore[attr-defined]

    other = client_for(app, "plain")
    assert "Standard-Wiedergabe" not in other.get("/settings").text
    post(other, "/settings", {"playback_mode": "download"})
    db.expire_all()
    assert user_settings.get_user_setting(db, plain.id, "playback_mode") == "embed"  # type: ignore[attr-defined]


def test_feed_badge_only_for_own_downloads(
    app: FastAPI,
    db: Session,
    settings: Settings,
    setup: dict[str, object],
    make_user: Callable[..., User],
) -> None:
    video = setup["video"]
    done(db, settings, setup["alice"], video, DownloadKind.VIDEO)  # type: ignore[arg-type]
    assert "⬇ lokal" in client_for(app, "alice").get("/").text
    bob = make_user("bob")
    bob.can_download = True
    db.commit()
    channel = db.get(Channel, video.channel_id)  # type: ignore[attr-defined]
    assert channel is not None
    subscribe(db, bob.id, channel)
    page = client_for(app, "bob").get("/").text
    assert video_id(1) in page and "⬇" not in page  # Bob did not request it
