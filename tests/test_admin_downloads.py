from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import utcnow
from app.models import DownloadKind, Job, User
from app.playback import downloads
from app.services.settings import get_global_setting, set_global_setting
from tests.conftest import login, new_client, post
from tests.factories import add_channel, add_video, subscribe, video_id

FORM = {
    "enabled": "on",
    "max_quality": "720",
    "max_storage_gb": "20",
    "retention_days": "7",
    "concurrency": "2",
    "fallback_percent": "10",
}


def test_page_and_settings(admin_client: TestClient, db: Session, settings: Settings) -> None:
    page = admin_client.get("/admin/downloads").text
    assert "Downloads aktiviert" in page and "yt-dlp" in page and "Speicherlimit" in page
    post(admin_client, "/admin/downloads/settings", FORM)
    db.expire_all()
    values = downloads.admin_settings(db, settings)
    assert values == downloads.AdminSettings(True, 720, 20.0, 7, 2, 10)
    assert get_global_setting(db, settings, "rss_fallback_share") == 0.1

    bad = post(admin_client, "/admin/downloads/settings", {**FORM, "max_quality": "999"}).text
    assert "Bitte die Werte prüfen" in bad
    assert (
        post(admin_client, "/admin/downloads/settings", {**FORM, "concurrency": "9"}).status_code
        == 200
    )
    db.expire_all()
    assert downloads.admin_settings(db, settings).concurrency == 2


def test_retry_and_delete(
    admin_client: TestClient, admin: User, db: Session, settings: Settings
) -> None:
    set_global_setting(db, "downloads_enabled", True)
    video = add_video(db, add_channel(db, 1), 1)
    failed = downloads.request_download(db, settings, admin, video, DownloadKind.VIDEO)
    failed.status, failed.error, failed.error_hint = "failed", "403", "outdated"
    done = downloads.request_download(db, settings, admin, video, DownloadKind.AUDIO)
    root = downloads.media_root(settings) / "UC1"
    root.mkdir(parents=True)
    (root / "a.m4a").write_bytes(b"x" * 10)
    done.status, done.file_path, done.size_bytes, done.finished_at = (
        "done",
        "UC1/a.m4a",
        10,
        utcnow(),
    )
    db.commit()

    page = admin_client.get("/admin/downloads").text
    assert "Fehlgeschlagen (1)" in page and "Heruntergeladen (1)" in page
    assert "aktuelles kurator-Image" in page  # error hint

    post(admin_client, f"/admin/downloads/{failed.id}/retry")
    post(admin_client, f"/admin/downloads/{done.id}/delete")
    db.expire_all()
    assert failed.status == "queued" and failed.error is None
    assert len(db.scalars(select(Job).where(Job.type == "download")).all()) == 3
    assert done.status == "deleted" and not (root / "a.m4a").exists()
    assert admin_client.post("/admin/downloads/999/delete").status_code in (403, 404)


def test_admin_grants_download_permission(
    admin_client: TestClient, alice: User, db: Session
) -> None:
    assert "Darf herunterladen" in admin_client.get("/admin/users").text
    post(admin_client, f"/admin/users/{alice.id}/can-download", {"allowed": "true"})
    db.refresh(alice)
    assert alice.can_download
    post(admin_client, f"/admin/users/{alice.id}/can-download", {"allowed": "false"})
    db.refresh(alice)
    assert not alice.can_download


def test_downloads_completely_invisible_when_disabled(
    app: FastAPI, db: Session, settings: Settings, make_user: Callable[..., User]
) -> None:
    """Acceptance: with DOWNLOADS_ENABLED=false nothing of the feature shows up for users."""
    alice = make_user("alice")
    alice.can_download = True  # even with permission
    db.commit()
    channel = add_channel(db, 1)
    subscribe(db, alice.id, channel)
    add_video(db, channel, 1)
    client = new_client(app)
    login(client, "alice")
    for path in ("/", f"/videos/{video_id(1)}", "/channels", "/settings"):
        page = client.get(path).text.lower()
        for word in ("herunterladen", "download", "/media/", "standard-wiedergabe"):
            assert word not in page, (path, word)
    assert "Downloads" not in client.get("/").text  # no admin nav for users either


def test_ytdlp_age_warning_threshold() -> None:
    assert not downloads.Tooling("2026.08.19", 62, True, True).ytdlp_outdated
    assert downloads.Tooling("2026.01.01", 130, True, True).ytdlp_outdated
    assert not downloads.Tooling("unknown", None, True, True).ytdlp_outdated
