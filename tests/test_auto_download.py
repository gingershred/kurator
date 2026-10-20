from collections.abc import Callable
from datetime import timedelta

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import utcnow
from app.models import Channel, Download, DownloadRequest, Subscription, User
from app.playback import downloads
from app.services.settings import set_global_setting
from tests.conftest import login, new_client, post
from tests.factories import BASE_TIME, add_channel, add_video, subscribe


@pytest.fixture
def setup(db: Session, make_user: Callable[..., User]) -> tuple[User, Subscription]:
    set_global_setting(db, "downloads_enabled", True)
    alice = make_user("alice")
    alice.can_download = True
    db.commit()
    return alice, subscribe(db, alice.id, add_channel(db, 1))


def requests(db: Session) -> list[DownloadRequest]:
    return list(db.scalars(select(DownloadRequest)))


def test_only_new_long_videos_are_queued(
    db: Session, settings: Settings, setup: tuple[User, Subscription]
) -> None:
    alice, sub = setup
    channel = sub.channel_id
    downloads.set_auto_download(db, settings, alice, sub, True)
    since = sub.auto_download_since
    assert since is not None
    sub.auto_download_since = BASE_TIME
    db.commit()
    ch = db.get(Channel, channel)
    assert ch is not None
    add_video(db, ch, 1, age_hours=5)  # before auto-download was enabled
    new = add_video(db, ch, 2, age_hours=-1)
    add_video(db, ch, 3, age_hours=-2, is_short=True)
    add_video(db, ch, 4, age_hours=-3, live_status="upcoming", is_premiere=True)
    add_video(db, ch, 5, age_hours=-4, enriched=False)

    assert downloads.queue_auto_downloads(db, settings, require_enriched=True) == 1
    [req] = requests(db)
    download = db.get(Download, req.download_id)
    assert download is not None and download.video_id == new.id and req.source == "auto"
    # Idempotent: nothing new on the next run.
    assert downloads.queue_auto_downloads(db, settings, require_enriched=True) == 0
    # Without an API key, unenriched videos count as long-form (UULF only).
    assert downloads.queue_auto_downloads(db, settings, require_enriched=False) == 1


def test_nothing_without_permission_or_when_disabled_or_muted(
    db: Session, settings: Settings, setup: tuple[User, Subscription]
) -> None:
    alice, sub = setup
    downloads.set_auto_download(db, settings, alice, sub, True)
    sub.auto_download_since = utcnow() - timedelta(days=1)
    db.commit()
    ch = db.get(Channel, sub.channel_id)
    assert ch is not None
    add_video(db, ch, 1, age_hours=-1)

    set_global_setting(db, "downloads_enabled", False)
    assert downloads.queue_auto_downloads(db, settings, require_enriched=True) == 0
    set_global_setting(db, "downloads_enabled", True)
    sub.status = "muted"
    db.commit()
    assert downloads.queue_auto_downloads(db, settings, require_enriched=True) == 0
    sub.status = "active"
    alice.can_download = False
    db.commit()
    assert downloads.queue_auto_downloads(db, settings, require_enriched=True) == 0
    with pytest.raises(downloads.DownloadError):
        downloads.set_auto_download(db, settings, alice, sub, True)


def test_toggle_route_and_isolation(
    app: FastAPI, db: Session, setup: tuple[User, Subscription], make_user: Callable[..., User]
) -> None:
    _alice, sub = setup
    client = new_client(app)
    login(client, "alice")
    page = post(client, f"/channels/{sub.id}/auto-download/on").text
    assert "automatisch heruntergeladen" in page and "⬇ automatisch" in page
    db.refresh(sub)
    assert sub.auto_download_since is not None
    post(client, f"/channels/{sub.id}/auto-download/off")
    db.refresh(sub)
    assert sub.auto_download_since is None
    assert post(client, f"/channels/{sub.id}/auto-download/maybe").status_code == 404

    bob = make_user("bob")
    bob.can_download = True
    db.commit()
    bob_client = new_client(app)
    login(bob_client, "bob")
    assert post(bob_client, f"/channels/{sub.id}/auto-download/on").status_code == 404
    db.refresh(sub)
    assert sub.auto_download_since is None

    plain = make_user("plain")
    plain_sub = subscribe(db, plain.id, add_channel(db, 2))
    plain_client = new_client(app)
    login(plain_client, "plain")
    assert "Auto-Download" not in plain_client.get("/channels").text
    assert post(plain_client, f"/channels/{plain_sub.id}/auto-download/on").status_code == 404
