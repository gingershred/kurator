"""Parsing of channel inputs (IDs, handles, URLs) and derived playlist IDs.

Only official building blocks are used: channel IDs (UC…), handles, legacy usernames and the
long-form uploads playlist `UULF<channel id without UC>`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qs, unquote, urlparse

CHANNEL_ID_RE = re.compile(r"UC[0-9A-Za-z_-]{22}")
VIDEO_ID_RE = re.compile(r"[0-9A-Za-z_-]{11}")
# Handles are 3-30 characters: letters (any script), digits, underscore, hyphen, period.
HANDLE_RE = re.compile(r"[\w.\-·]{3,30}")
USERNAME_RE = re.compile(r"[A-Za-z0-9]{1,100}")

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}


class ChannelRefKind(StrEnum):
    ID = "id"
    HANDLE = "handle"
    USERNAME = "username"


@dataclass(frozen=True)
class ChannelRef:
    kind: ChannelRefKind
    value: str


class ChannelInputError(ValueError):
    """The input is not a usable channel reference. `key` is an i18n message key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def is_channel_id(value: str) -> bool:
    return CHANNEL_ID_RE.fullmatch(value) is not None


def is_video_id(value: str) -> bool:
    return VIDEO_ID_RE.fullmatch(value) is not None


def uploads_playlist_id(channel_id: str) -> str:
    """Long-form uploads playlist of a channel (no Shorts, no live streams)."""
    if not is_channel_id(channel_id):
        raise ValueError(f"not a channel id: {channel_id!r}")
    return "UULF" + channel_id[2:]


def _handle(value: str) -> ChannelRef:
    value = unquote(value).lstrip("@")
    if not HANDLE_RE.fullmatch(value):
        raise ChannelInputError("error.channel_input_invalid")
    return ChannelRef(ChannelRefKind.HANDLE, value)


def _from_url(text: str) -> ChannelRef:
    if "://" not in text:
        text = "https://" + text
    url = urlparse(text)
    host = (url.hostname or "").lower()
    if host == "youtu.be":
        raise ChannelInputError("error.channel_input_video_url")
    if host not in YOUTUBE_HOSTS:
        raise ChannelInputError("error.channel_input_invalid")
    parts = [p for p in url.path.split("/") if p]
    if not parts:
        raise ChannelInputError("error.channel_input_invalid")
    first = parts[0]
    if first.startswith("@"):
        return _handle(first)
    if first == "channel" and len(parts) > 1 and is_channel_id(parts[1]):
        return ChannelRef(ChannelRefKind.ID, parts[1])
    if first == "user" and len(parts) > 1 and USERNAME_RE.fullmatch(parts[1]):
        return ChannelRef(ChannelRefKind.USERNAME, parts[1])
    if first == "c":
        raise ChannelInputError("error.channel_input_custom_url")
    if first in ("watch", "shorts", "live") or "v" in parse_qs(url.query):
        raise ChannelInputError("error.channel_input_video_url")
    raise ChannelInputError("error.channel_input_invalid")


def parse_channel_input(text: str) -> ChannelRef:
    """Turn user input (ID, @handle, handle or channel URL) into a channel reference."""
    text = text.strip()
    if not text:
        raise ChannelInputError("error.channel_input_invalid")
    if is_channel_id(text):
        return ChannelRef(ChannelRefKind.ID, text)
    if text.startswith("@"):
        return _handle(text)
    if "/" in text or text.lower().startswith(("http", "www.", "youtube.")):
        return _from_url(text)
    return _handle(text)
