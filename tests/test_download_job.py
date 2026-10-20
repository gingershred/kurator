import threading
from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import Download, DownloadKind, Job, User, Video
from app.playback import download_job, downloads, ytdlp
from app.services.settings import set_global_setting
from app.worker import Worker
from tests.factories import add_channel, add_video, channel_id, video_id


class FakeDownloader:
    """Replaces ytdlp.run_download: writes files like yt-dlp, or fails as configured."""

    def __init__(self) -> None:
        self.calls: list[ytdlp.DownloadSpec] = []
        self.failures: list[ytdlp.DownloadFailed] = []

    def __call__(
        self, spec: ytdlp.DownloadSpec, progress: ytdlp.ProgressCallback | None = None
    ) -> ytdlp.DownloadResult:
        self.calls.append(spec)
        if self.failures:
            raise self.failures.pop(0)
        if progress:
            progress(42)
        spec.target_dir.mkdir(parents=True, exist_ok=True)
        ext = "m4a" if spec.kind == "audio" else "mp4"
        path = spec.target_dir / f"{spec.basename}.{ext}"
        path.write_bytes(b"x" * 500)
        sub = spec.target_dir / f"{spec.basename}.de.vtt"
        sub.write_text("WEBVTT\n")
        return ytdlp.DownloadResult(
            path, 500, 720 if ext == "mp4" else None, f"{ext} avc1/mp4a", {"de": sub}
        )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeDownloader:
    fake = FakeDownloader()
    monkeypatch.setattr(ytdlp, "run_download", fake)
    return fake


@pytest.fixture
def worker(settings: Settings, session_factory: sessionmaker[Session]) -> Worker:
    return Worker(settings, session_factory)


@pytest.fixture
def setup(db: Session, settings: Settings, admin: User) -> tuple[Video, Download]:
    set_global_setting(db, "downloads_enabled", True)
    video = add_video(db, add_channel(db, 1), 1)
    return video, downloads.request_download(db, settings, admin, video, DownloadKind.VIDEO)


def job_of(db: Session) -> Job:
    db.expire_all()
    return db.query(Job).filter(Job.type == "download").one()


def test_download_succeeds(
    db: Session,
    worker: Worker,
    settings: Settings,
    fake: FakeDownloader,
    setup: tuple[Video, Download],
) -> None:
    _video, download = setup
    assert worker.process_one(download_lane=True)
    db.expire_all()
    assert download.status == "done" and download.progress == 100
    assert download.file_path == f"{channel_id(1)}/{video_id(1)}.mp4"
    assert download.subtitles == {"de": f"{channel_id(1)}/{video_id(1)}.de.vtt"}
    assert download.size_bytes == 500 and download.height == 720
    assert downloads.resolve(settings, download.file_path) is not None
    spec = fake.calls[0]
    assert spec.max_height == 1080 and spec.subtitle_langs == ("de", "en")
    assert job_of(db).status == "done"


def test_audio_basename(
    db: Session,
    worker: Worker,
    settings: Settings,
    fake: FakeDownloader,
    admin: User,
    setup: tuple[Video, Download],
) -> None:
    video, _download = setup
    downloads.request_download(db, settings, admin, video, DownloadKind.AUDIO)
    while worker.process_one(download_lane=True):
        pass
    assert {c.basename for c in fake.calls} == {video_id(1), f"{video_id(1)}.audio"}


def test_retryable_failure_retries_then_fails(
    db: Session, worker: Worker, fake: FakeDownloader, setup: tuple[Video, Download]
) -> None:
    _video, download = setup
    fake.failures = [ytdlp.DownloadFailed("HTTP Error 403", ytdlp.HINT_OUTDATED, True)] * 3
    worker.process_one(download_lane=True)
    db.expire_all()
    assert download.status == "queued" and download.error_hint == "outdated"
    job = job_of(db)
    for _ in range(2):
        job.run_after = job.created_at  # make it due again
        db.commit()
        worker.process_one(download_lane=True)
        job = job_of(db)
    db.expire_all()
    assert download.status == "failed" and job.status == "failed" and len(fake.calls) == 3


def test_unavailable_video_is_not_retried(
    db: Session, worker: Worker, fake: FakeDownloader, setup: tuple[Video, Download]
) -> None:
    _video, download = setup
    fake.failures = [ytdlp.DownloadFailed("Private video", ytdlp.HINT_UNAVAILABLE, False)]
    worker.process_one(download_lane=True)
    db.expire_all()
    assert download.status == "failed" and download.error_hint == "unavailable"
    assert job_of(db).status == "failed"


def test_postponed_when_disabled_or_storage_full(
    db: Session, worker: Worker, fake: FakeDownloader, setup: tuple[Video, Download]
) -> None:
    _video, download = setup
    set_global_setting(db, "downloads_enabled", False)
    worker.process_one(download_lane=True)
    job = job_of(db)
    assert job.status == "queued" and job.attempts == 0 and fake.calls == []

    set_global_setting(db, "downloads_enabled", True)
    set_global_setting(db, "download_max_storage_gb", 0)
    job.run_after = job.created_at
    db.commit()
    worker.process_one(download_lane=True)
    job = job_of(db)
    db.expire_all()
    assert job.status == "queued" and job.attempts == 0
    assert download.status == "queued" and download.error_hint == "storage_full"


def test_orphaned_download_is_dropped(
    db: Session, worker: Worker, fake: FakeDownloader, admin: User, setup: tuple[Video, Download]
) -> None:
    _video, download = setup
    downloads.cancel_request(db, admin, download)
    worker.process_one(download_lane=True)
    db.expire_all()
    assert download.status == "deleted" and fake.calls == []


def test_lanes_are_separate(
    db: Session, worker: Worker, fake: FakeDownloader, setup: tuple[Video, Download]
) -> None:
    assert worker.drain_queue() == 0  # the feed lane never runs downloads
    assert worker.process_one(download_lane=True)


def test_download_tick_respects_concurrency(
    db: Session, worker: Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    calls: list[bool] = []

    def blocking(download_lane: bool = False) -> bool:
        calls.append(download_lane)
        release.wait(5)
        return False

    monkeypatch.setattr(worker, "process_one", blocking)
    set_global_setting(db, "download_concurrency", 2)
    assert worker.download_tick() == 2
    assert worker.download_tick() == 0  # both still busy
    release.set()
    for thread in list(worker._download_threads):
        thread.join(5)
    set_global_setting(db, "download_concurrency", 99)  # clamped to 3
    assert worker.download_tick() == 3
    assert all(calls)


def test_recover_interrupted(db: Session, setup: tuple[Video, Download]) -> None:
    _video, download = setup
    download.status = "running"
    db.commit()
    assert download_job.recover_interrupted(db) == 1
    assert download.status == "queued"


@pytest.fixture(autouse=True)
def _no_threads_left(worker: Worker) -> Iterator[None]:
    yield
    for thread in list(worker._download_threads):
        thread.join(5)
