"""OPML import/export of subscriptions (used by RSS readers and other YouTube feed tools)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

from app.importers.takeout import TakeoutRows
from app.youtube.ids import CHANNEL_ID_RE, uploads_playlist_id

_PLAYLIST_RE = re.compile(r"playlist_id=UU(?:LF)?([0-9A-Za-z_-]{22})")
_CHANNEL_PARAM_RE = re.compile(r"channel_id=(UC[0-9A-Za-z_-]{22})")
_CHANNEL_PATH_RE = re.compile(r"/channel/(UC[0-9A-Za-z_-]{22})")


def _channel_id(urls: list[str]) -> str | None:
    for url in urls:
        for pattern in (_CHANNEL_PARAM_RE, _CHANNEL_PATH_RE):
            match = pattern.search(url)
            if match:
                return match.group(1)
        match = _PLAYLIST_RE.search(url)
        if match:
            return "UC" + match.group(1)
    return None


def parse_opml(content: bytes | str) -> TakeoutRows:
    result = TakeoutRows()
    try:
        root = ET.fromstring(content)  # noqa: S314
    except ET.ParseError:
        result.invalid += 1
        return result
    seen: set[str] = set()
    for outline in root.iter("outline"):
        urls = [outline.get(key, "") for key in ("xmlUrl", "htmlUrl", "url")]
        if not any(urls):
            continue  # a folder
        channel_id = _channel_id(urls)
        if channel_id is None or not CHANNEL_ID_RE.fullmatch(channel_id):
            result.invalid += 1
            continue
        if channel_id in seen:
            continue
        seen.add(channel_id)
        title = outline.get("title") or outline.get("text") or ""
        result.channels.append((channel_id, title.strip()))
    return result


def looks_like_opml(content: bytes) -> bool:
    head = content.lstrip(b"\xef\xbb\xbf \t\r\n")[:200].lower()
    return head.startswith(b"<?xml") or head.startswith(b"<opml")


def export_opml(channels: list[tuple[str, str]], title: str = "kurator") -> str:
    """OPML 2.0 with the long-form uploads (UULF) feeds, so other readers get no Shorts either."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        f"  <head><title>{_escape(title)}</title></head>",
        "  <body>",
    ]
    for channel_id, name in channels:
        feed = "https://www.youtube.com/feeds/videos.xml?playlist_id=" + uploads_playlist_id(
            channel_id
        )
        lines.append(
            f'    <outline type="rss" text={quoteattr(name or channel_id)} '
            f"title={quoteattr(name or channel_id)} xmlUrl={quoteattr(feed)} "
            f"htmlUrl={quoteattr('https://www.youtube.com/channel/' + channel_id)}/>"
        )
    lines += ["  </body>", "</opml>", ""]
    return "\n".join(lines)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
