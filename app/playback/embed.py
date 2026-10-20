"""Playback abstraction. Phase 1 knows only the embedded player; phase 2 adds downloads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.models import UserVideoState, Video

NOCOOKIE_HOST = "https://www.youtube-nocookie.com"
IFRAME_API_URL = "https://www.youtube.com/iframe_api"
RESUME_UNTIL = 0.95

# Embedded playback needs the IFrame API loader from youtube.com and frames from
# youtube-nocookie.com; everything else stays on 'self'.
PLAYER_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; "
    "script-src 'self' https://www.youtube.com https://s.ytimg.com; "
    f"frame-src {NOCOOKIE_HOST}; "
    "frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)


class PlaybackMode(StrEnum):
    EMBED = "embed"
    DOWNLOAD = "download"  # phase 2


@dataclass(frozen=True)
class Playback:
    mode: PlaybackMode
    video_id: str
    start_seconds: int


def start_position(video: Video, state: UserVideoState | None) -> int:
    """Resume where the user stopped, unless the video was (almost) finished."""
    if state is None or state.progress_seconds <= 0:
        return 0
    if video.duration_seconds and state.progress_seconds >= video.duration_seconds * RESUME_UNTIL:
        return 0
    return state.progress_seconds


def playback_for(video: Video, state: UserVideoState | None, preferred: str) -> Playback:
    # Only embedded playback exists so far; a "download" preference falls back to it.
    del preferred
    return Playback(PlaybackMode.EMBED, video.youtube_video_id, start_position(video, state))
