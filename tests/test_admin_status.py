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
