"""Google Takeout `subscriptions.csv` (YouTube → Abos). Column names differ by language,
so rows are read by content: the channel ID (or a /channel/ URL) and the title."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from app.youtube.ids import CHANNEL_ID_RE, is_channel_id

MAX_FILE_BYTES = 2 * 1024 * 1024


@dataclass
class TakeoutRows:
    channels: list[tuple[str, str]] = field(default_factory=list)
    invalid: int = 0


def parse_subscriptions_csv(content: bytes | str) -> TakeoutRows:
    text = content.decode("utf-8-sig", errors="replace") if isinstance(content, bytes) else content
    result = TakeoutRows()
    seen: set[str] = set()
    for row in csv.reader(io.StringIO(text)):
        cells = [c.strip() for c in row]
        if not any(cells):
            continue
        channel_id = next((c for c in cells if is_channel_id(c)), None)
        if channel_id is None:
            match = next((CHANNEL_ID_RE.search(c) for c in cells if "/channel/" in c), None)
            channel_id = match.group(0) if match else None
        if channel_id is None:
            # The header row ("Channel Id,…" / "Kanal-ID,…") or garbage.
            if result.channels or result.invalid or not _looks_like_header(cells):
                result.invalid += 1
            continue
        if channel_id in seen:
            continue
        seen.add(channel_id)
        title = next((c for c in reversed(cells) if c and c != channel_id and "://" not in c), "")
        result.channels.append((channel_id, title))
    return result


def _looks_like_header(cells: list[str]) -> bool:
    joined = " ".join(cells).lower()
    return "id" in joined and ("url" in joined or "title" in joined or "titel" in joined)


def parse_subscriptions_file(content: bytes) -> TakeoutRows:
    """Google Takeout CSV or OPML, detected by content."""
    from app.importers.opml import looks_like_opml, parse_opml

    return parse_opml(content) if looks_like_opml(content) else parse_subscriptions_csv(content)
