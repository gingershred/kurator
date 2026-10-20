from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import utcnow
from app.migrate import head_revision
from app.services import jobs, status
from app.services.settings import record_worker_heartbeat
from app.youtube import quota


def test_collect(db: Session, settings: Settings, session_factory: sessionmaker[Session]) -> None:
    quota.record(db, "videos.list", units=7)
    quota.record(db, "search.list")
    jobs.enqueue(db, "waiting")
    with jobs.track_run(session_factory, "housekeeping"):
        pass
    record_worker_heartbeat(db, utcnow() - timedelta(minutes=1))

    result = status.collect(db, settings)

    assert result.db_revision == head_revision() and result.db_up_to_date
    assert result.quota_used == 107 and result.quota_search_used == 100
    assert result.quota_budget == 8000
    assert result.queue["queued"] == 1
    assert result.worker_alive
    assert [r.name for r in result.recent_runs] == ["housekeeping"]
    assert not result.youtube_api_key_set
    assert result.db_size_bytes > 0


def test_worker_considered_dead_after_silence(db: Session, settings: Settings) -> None:
    record_worker_heartbeat(db, utcnow() - timedelta(minutes=10))
    assert not status.collect(db, settings).worker_alive


def test_status_page(admin_client: TestClient, db: Session) -> None:
    quota.record(db, "videos.list", units=42)
    page = admin_client.get("/admin/status")
    assert page.status_code == 200
    assert "42 von 8000 Einheiten" in page.text
    assert head_revision() in page.text
    assert "noch kein Lebenszeichen" in page.text


def test_rss_health_and_fallback(
    admin_client: TestClient,
    db: Session,
    settings: Settings,
    session_factory: sessionmaker[Session],
) -> None:
    from app.models import Channel

    with jobs.track_run(session_factory, "poll_feeds") as run:
        run.detail = {
            "poll": {"channels": 10, "ok": 7, "failed": 3, "rss_failed": 5, "fallback": 2}
        }
    quota.record(db, "playlistItems.list", units=2)
    db.add(Channel(youtube_channel_id="UC" + "z" * 22, title="weg", gone_at=utcnow()))
    db.commit()

    result = status.collect(db, settings)
    assert result.rss_degraded and result.fallback_used == 2 and result.fallback_budget == 2000
    assert result.gone_channels == 1

    page = admin_client.get("/admin/status").text
    assert "7 von 10 Kanälen ok, davon 2 per API nachgeladen" in page
    assert "RSS gestört" in page and "API-Schlüssel" in page
    assert "2 von höchstens 2000 Einheiten" in page
    assert "1 Kanal/Kanäle existieren nicht mehr" in page


def test_rss_not_degraded_without_runs(db: Session, settings: Settings) -> None:
    assert not status.collect(db, settings).rss_degraded
