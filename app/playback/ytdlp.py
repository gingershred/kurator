"""Thin wrapper around the yt-dlp Python API (the only place kurator talks to yt-dlp).

Tests replace `YoutubeDL` entirely, so no network is ever needed.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadError as YtDlpDownloadError

log = logging.getLogger(__name__)

ProgressCallback = Callable[[int], None]

# Error hints shown in the UI.
HINT_OUTDATED = "outdated"  # YouTube changed something; a newer yt-dlp usually helps
HINT_UNAVAILABLE = "unavailable"  # private, removed, members-only, age-restricted …
HINT_BOT_CHECK = "bot_check"  # YouTube asks to sign in; mostly datacenter/VPN IP addresses

_BOT_CHECK_PATTERNS = ("not a bot", "sign in to confirm")

_UNAVAILABLE_PATTERNS = (
    "private video",
    "video unavailable",
    "has been removed",
    "members-only",
    "join this channel",
    "confirm your age",
    "this live event",
    "premieres in",
)
_OUTDATED_PATTERNS = (
    "http error 403",
    "nsig",
    "signature extraction",
    "unable to extract",
    "requested format is not available",
    "no supported javascript runtime",
    "po token",
)


class DownloadFailed(Exception):
    def __init__(self, message: str, hint: str | None, retryable: bool) -> None:
        super().__init__(message)
        self.hint = hint
        self.retryable = retryable


@dataclass(frozen=True)
class DownloadSpec:
    youtube_video_id: str
    kind: str  # "video" | "audio"
    target_dir: Path
    basename: str
    max_height: int
    subtitle_langs: tuple[str, ...] = ("de", "en")


@dataclass
class DownloadResult:
    file_path: Path
    size_bytes: int
    height: int | None
    format: str
    subtitles: dict[str, Path] = field(default_factory=dict)


def format_selector(kind: str, max_height: int) -> str:
    """Prefer H.264 + AAC in MP4 (plays everywhere, including iOS); fall back to the best
    available formats up to the quality limit."""
    if kind == "audio":
        return "ba[acodec^=mp4a]/ba/b"
    h = int(max_height)
    return "/".join(
        [
            f"bv*[height<={h}][vcodec^=avc1]+ba[acodec^=mp4a]",
            f"b[height<={h}][vcodec^=avc1][acodec^=mp4a]",
            f"bv*[height<={h}]+ba",
            f"b[height<={h}]",
            "b",
        ]
    )


def classify_error(message: str) -> tuple[str | None, bool]:
    """(hint, retryable) for a yt-dlp error message."""
    text = message.lower()
    if any(p in text for p in _BOT_CHECK_PATTERNS):
        return HINT_BOT_CHECK, True
    if any(p in text for p in _UNAVAILABLE_PATTERNS):
        return HINT_UNAVAILABLE, False
    if any(p in text for p in _OUTDATED_PATTERNS):
        return HINT_OUTDATED, True
    return None, True


def build_options(spec: DownloadSpec, progress: ProgressCallback | None = None) -> dict[str, Any]:
    last = {"percent": -1}

    def hook(status: dict[str, Any]) -> None:
        if progress is None or status.get("status") != "downloading":
            return
        total = status.get("total_bytes") or status.get("total_bytes_estimate")
        done = status.get("downloaded_bytes") or 0
        if not total:
            return
        percent = min(99, int(done * 100 / total))
        if percent != last["percent"]:
            last["percent"] = percent
            progress(percent)

    options: dict[str, Any] = {
        "format": format_selector(spec.kind, spec.max_height),
        "outtmpl": str(spec.target_dir / f"{spec.basename}.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "overwrites": True,
        "retries": 3,
        "fragment_retries": 3,
        "progress_hooks": [hook],
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": list(spec.subtitle_langs),
        "subtitlesformat": "vtt/best",
        "postprocessors": [{"key": "FFmpegSubtitlesConvertor", "format": "vtt"}],
    }
    if spec.kind == "audio":
        options["postprocessors"].insert(0, {"key": "FFmpegExtractAudio", "preferredcodec": "m4a"})
    else:
        options["merge_output_format"] = "mp4"
    return options


def _subtitles(spec: DownloadSpec) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in sorted(spec.target_dir.glob(f"{spec.basename}.*.vtt")):
        lang = path.name[len(spec.basename) + 1 : -len(".vtt")]
        if lang and "." not in lang:
            found[lang] = path
    return found


def run_download(spec: DownloadSpec, progress: ProgressCallback | None = None) -> DownloadResult:
    spec.target_dir.mkdir(parents=True, exist_ok=True)
    url = f"https://www.youtube.com/watch?v={spec.youtube_video_id}"
    try:
        with yt_dlp.YoutubeDL(build_options(spec, progress)) as ydl:
            info = ydl.extract_info(url, download=True)
    except YtDlpDownloadError as exc:
        hint, retryable = classify_error(str(exc))
        raise DownloadFailed(str(exc), hint, retryable) from exc

    downloads = info.get("requested_downloads") or []
    path = Path(downloads[0]["filepath"]) if downloads else None
    if path is None or not path.is_file():
        raise DownloadFailed("yt-dlp reported success but no file was written", None, True)
    vcodec, acodec = info.get("vcodec") or "", info.get("acodec") or ""
    return DownloadResult(
        file_path=path,
        size_bytes=path.stat().st_size,
        height=info.get("height") if spec.kind == "video" else None,
        format=f"{path.suffix.lstrip('.')} {vcodec}/{acodec}".strip(),
        subtitles=_subtitles(spec),
    )


def version() -> str:
    return str(yt_dlp.version.__version__)


_VERSION_RE = re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})")


def version_age_days(value: str, today: date | None = None) -> int | None:
    match = _VERSION_RE.match(value)
    if match is None:
        return None
    released = date(*(int(part) for part in match.groups()))
    return ((today or date.today()) - released).days
