from app.importers.takeout import parse_subscriptions_csv
from tests.factories import channel_id


def csv_lines(header: str, n: int) -> str:
    rows = [header] + [
        f"{channel_id(i)},http://www.youtube.com/channel/{channel_id(i)},Kanal {i}"
        for i in range(n)
    ]
    return "\n".join(rows) + "\n"


def test_english_export() -> None:
    result = parse_subscriptions_csv(csv_lines("Channel Id,Channel Url,Channel Title", 3).encode())
    assert result.channels == [(channel_id(i), f"Kanal {i}") for i in range(3)]
    assert result.invalid == 0


def test_german_export_with_bom_and_150_channels() -> None:
    content = ("﻿" + csv_lines("Kanal-ID,Kanal-URL,Kanaltitel", 150)).encode()
    result = parse_subscriptions_csv(content)
    assert len(result.channels) == 150 and result.invalid == 0


def test_titles_with_commas_duplicates_and_garbage() -> None:
    content = "\n".join(
        [
            "Channel Id,Channel Url,Channel Title",
            f'{channel_id(1)},http://www.youtube.com/channel/{channel_id(1)},"Wissen, kurz"',
            f"{channel_id(1)},http://www.youtube.com/channel/{channel_id(1)},Doppelt",
            "kaputt,zeile,ohne id",
            f",https://www.youtube.com/channel/{channel_id(2)},Nur URL",
            "",
        ]
    )
    result = parse_subscriptions_csv(content)
    assert result.channels == [(channel_id(1), "Wissen, kurz"), (channel_id(2), "Nur URL")]
    assert result.invalid == 1


def test_empty_and_header_only() -> None:
    assert parse_subscriptions_csv(b"").channels == []
    assert parse_subscriptions_csv(b"Channel Id,Channel Url,Channel Title\n").invalid == 0
