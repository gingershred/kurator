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
    LOCAL = "local"  # downloaded video file
    AUDIO = "audio"  # downloaded audio-only file


# Values of the per-user setting "playback_mode".
PREFERENCES = ("embed", "download")


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


def choose_mode(
    requested: str | None, preferred: str, local_available: bool, audio_available: bool
) -> PlaybackMode:
    """Explicit choice first, then the user's preference; embedded playback is always the
    fallback when no downloaded file is available."""
    available = {
        PlaybackMode.EMBED: True,
        PlaybackMode.LOCAL: local_available,
        PlaybackMode.AUDIO: audio_available,
    }
    if requested in available and available[PlaybackMode(requested)]:
        return PlaybackMode(requested)
    if preferred == "download":
        if local_available:
            return PlaybackMode.LOCAL
        if audio_available:
            return PlaybackMode.AUDIO
    return PlaybackMode.EMBED


def playback_for(
    video: Video, state: UserVideoState | None, mode: PlaybackMode = PlaybackMode.EMBED
) -> Playback:
    return Playback(mode, video.youtube_video_id, start_position(video, state))
