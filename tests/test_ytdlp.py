from datetime import date
from pathlib import Path
from typing import Any, ClassVar

import pytest
from yt_dlp.utils import DownloadError as YtDlpDownloadError

from app.playback import ytdlp
from app.playback.ytdlp import (
    HINT_BOT_CHECK,
    HINT_OUTDATED,
    HINT_UNAVAILABLE,
    DownloadFailed,
    DownloadSpec,
    build_options,
    classify_error,
    format_selector,
    run_download,
    version_age_days,
)


def spec(tmp_path: Path, kind: str = "video") -> DownloadSpec:
    return DownloadSpec("dQw4w9WgXcQ", kind, tmp_path / "UCx", "dQw4w9WgXcQ", 1080)


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL: writes files and reports progress like the real one."""

    instances: ClassVar[list["FakeYDL"]] = []
    error: ClassVar[str | None] = None

    def __init__(self, options: dict[str, Any]) -> None:
        self.options = options
        FakeYDL.instances.append(self)

    def __enter__(self) -> "FakeYDL":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def extract_info(self, url: str, download: bool) -> dict[str, Any]:
        assert download and url.endswith("dQw4w9WgXcQ")
        if FakeYDL.error:
            raise YtDlpDownloadError(FakeYDL.error)
        for hook in self.options["progress_hooks"]:
            hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 200})
            hook({"status": "downloading", "downloaded_bytes": 51, "total_bytes": 200})
            hook({"status": "downloading", "downloaded_bytes": 200, "total_bytes": 200})
            hook({"status": "finished"})
        ext = "m4a" if self.options.get("merge_output_format") is None else "mp4"
        target = Path(self.options["outtmpl"].replace("%(ext)s", ext))
        target.write_bytes(b"x" * 1234)
        target.with_name(target.stem + ".de.vtt").write_text("WEBVTT\n")
        target.with_name(target.stem + ".en.vtt").write_text("WEBVTT\n")
        return {
            "requested_downloads": [{"filepath": str(target)}],
            "height": 720,
            "vcodec": "avc1.64001F",
            "acodec": "mp4a.40.2",
        }


@pytest.fixture(autouse=True)
def fake_ydl(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeYDL.instances = []
    FakeYDL.error = None
    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)


def test_format_selector_prefers_h264_aac_within_limit() -> None:
    selector = format_selector("video", 720)
    first = selector.split("/")[0]
    assert first == "bv*[height<=720][vcodec^=avc1]+ba[acodec^=mp4a]"
    assert "height<=1080" not in selector
    assert format_selector("audio", 720).startswith("ba[acodec^=mp4a]")


def test_build_options(tmp_path: Path) -> None:
    video = build_options(spec(tmp_path))
    assert video["merge_output_format"] == "mp4"
    assert video["subtitleslangs"] == ["de", "en"] and video["writeautomaticsub"]
    assert video["noplaylist"] and video["outtmpl"].endswith("dQw4w9WgXcQ.%(ext)s")
    audio = build_options(spec(tmp_path, "audio"))
    assert "merge_output_format" not in audio
    assert audio["postprocessors"][0] == {"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}


def test_run_download_video(tmp_path: Path) -> None:
    seen: list[int] = []
    result = run_download(spec(tmp_path), seen.append)
    assert seen == [25, 99]  # throttled to changes, never 100 before the file is complete
    assert result.file_path.name == "dQw4w9WgXcQ.mp4" and result.size_bytes == 1234
    assert result.height == 720 and result.format == "mp4 avc1.64001F/mp4a.40.2"
    assert set(result.subtitles) == {"de", "en"}


def test_run_download_audio(tmp_path: Path) -> None:
    result = run_download(spec(tmp_path, "audio"))
    assert result.file_path.suffix == ".m4a" and result.height is None


@pytest.mark.parametrize(
    ("message", "hint", "retryable"),
    [
        (
            "ERROR: [youtube] x: Private video. Sign in if you've been granted access",
            HINT_UNAVAILABLE,
            False,
        ),
        (
            "ERROR: [youtube] x: Video unavailable. This video has been removed",
            HINT_UNAVAILABLE,
            False,
        ),
        (
            "ERROR: [youtube] x: Join this channel to get access to members-only content",
            HINT_UNAVAILABLE,
            False,
        ),
        ("ERROR: unable to download video data: HTTP Error 403: Forbidden", HINT_OUTDATED, True),
        ("ERROR: [youtube] x: Sign in to confirm you’re not a bot", HINT_BOT_CHECK, True),
        ("WARNING: No supported JavaScript runtime could be found", HINT_OUTDATED, True),
        ("ERROR: Requested format is not available", HINT_OUTDATED, True),
        ("ERROR: [Errno 28] No space left on device", None, True),
    ],
)
def test_classify_error(message: str, hint: str | None, retryable: bool) -> None:
    assert classify_error(message) == (hint, retryable)


def test_download_error_is_classified(tmp_path: Path) -> None:
    FakeYDL.error = "ERROR: [youtube] x: Private video"
    with pytest.raises(DownloadFailed) as exc:
        run_download(spec(tmp_path))
    assert exc.value.hint == HINT_UNAVAILABLE and not exc.value.retryable


def test_version_age() -> None:
    assert version_age_days("2026.08.19", date(2026, 10, 20)) == 62
    assert version_age_days("2026.10.20.123456", date(2026, 10, 20)) == 0
    assert version_age_days("unknown") is None
    assert ytdlp.version()
