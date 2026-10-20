import pytest

from app.youtube.ids import (
    ChannelInputError,
    ChannelRef,
    ChannelRefKind,
    is_video_id,
    parse_channel_input,
    uploads_playlist_id,
)

CID = "UCsXVk37bltHxD1rDPwtNM8Q"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (CID, ChannelRef(ChannelRefKind.ID, CID)),
        (f"  {CID}  ", ChannelRef(ChannelRefKind.ID, CID)),
        (f"https://www.youtube.com/channel/{CID}", ChannelRef(ChannelRefKind.ID, CID)),
        (f"youtube.com/channel/{CID}/videos", ChannelRef(ChannelRefKind.ID, CID)),
        (f"https://m.youtube.com/channel/{CID}?si=x", ChannelRef(ChannelRefKind.ID, CID)),
        ("@kurzgesagt", ChannelRef(ChannelRefKind.HANDLE, "kurzgesagt")),
        ("kurzgesagt", ChannelRef(ChannelRefKind.HANDLE, "kurzgesagt")),
        ("https://www.youtube.com/@kurzgesagt", ChannelRef(ChannelRefKind.HANDLE, "kurzgesagt")),
        (
            "https://youtube.com/@Some.Name-1/videos",
            ChannelRef(ChannelRefKind.HANDLE, "Some.Name-1"),
        ),
        ("https://www.youtube.com/@%C3%BCber", ChannelRef(ChannelRefKind.HANDLE, "über")),
        (
            "https://www.youtube.com/user/LinusTechTips",
            ChannelRef(ChannelRefKind.USERNAME, "LinusTechTips"),
        ),
    ],
)
def test_valid_inputs(text: str, expected: ChannelRef) -> None:
    assert parse_channel_input(text) == expected


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("", "error.channel_input_invalid"),
        ("ab", "error.channel_input_invalid"),
        ("@a b c", "error.channel_input_invalid"),
        ("https://example.com/@someone", "error.channel_input_invalid"),
        ("https://www.youtube.com/c/SomeCustomName", "error.channel_input_custom_url"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "error.channel_input_video_url"),
        ("https://youtu.be/dQw4w9WgXcQ", "error.channel_input_video_url"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "error.channel_input_video_url"),
        ("https://www.youtube.com/channel/UCtooshort", "error.channel_input_invalid"),
        ("https://www.youtube.com/", "error.channel_input_invalid"),
    ],
)
def test_invalid_inputs(text: str, key: str) -> None:
    with pytest.raises(ChannelInputError) as exc:
        parse_channel_input(text)
    assert exc.value.key == key


def test_uploads_playlist_id() -> None:
    assert uploads_playlist_id(CID) == "UULF" + CID[2:]
    with pytest.raises(ValueError):
        uploads_playlist_id("UUxyz")


def test_video_id() -> None:
    assert is_video_id("dQw4w9WgXcQ")
    assert not is_video_id("dQw4w9WgXc")
    assert not is_video_id("dQw4w9WgXc!")
