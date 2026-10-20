import io

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.importers.opml import export_opml, looks_like_opml, parse_opml
from app.importers.takeout import parse_subscriptions_file
from app.models import Subscription, User
from tests.conftest import csrf_token, login, new_client
from tests.factories import add_channel, channel_id, subscribe

# Shapes produced by RSS readers and other YouTube feed tools.
FOREIGN_OPML = f"""<?xml version="1.0" encoding="UTF-8"?>
<opml version="1.0"><head><title>Feeds</title></head><body>
  <outline text="YouTube" title="YouTube">
    <outline type="rss" text="Kanal Eins" title="Kanal Eins"
      xmlUrl="https://www.youtube.com/feeds/videos.xml?channel_id={channel_id(1)}"/>
    <outline type="rss" text="Kanal Zwei"
      xmlUrl="https://www.youtube.com/feeds/videos.xml?playlist_id=UULF{channel_id(2)[2:]}"/>
    <outline type="rss" title="Kanal Drei"
      xmlUrl="https://www.youtube.com/feeds/videos.xml?playlist_id=UU{channel_id(3)[2:]}"/>
    <outline text="Nur Link" htmlUrl="https://www.youtube.com/channel/{channel_id(4)}"/>
    <outline text="Doppelt" xmlUrl="https://www.youtube.com/feeds/videos.xml?channel_id={channel_id(1)}"/>
  </outline>
  <outline type="rss" text="Ein Blog" xmlUrl="https://example.org/feed.xml"/>
</body></opml>"""


def test_parse_foreign_opml() -> None:
    result = parse_opml(FOREIGN_OPML)
    assert result.channels == [
        (channel_id(1), "Kanal Eins"),
        (channel_id(2), "Kanal Zwei"),
        (channel_id(3), "Kanal Drei"),
        (channel_id(4), "Nur Link"),
    ]
    assert result.invalid == 1  # the blog


def test_garbage_and_detection() -> None:
    assert parse_opml(b"<opml><body><outline").invalid == 1
    assert looks_like_opml(b"\xef\xbb\xbf<?xml version='1.0'?><opml/>")
    assert not looks_like_opml(b"Channel Id,Channel Url,Channel Title\n")
    assert parse_subscriptions_file(FOREIGN_OPML.encode()).channels[0][0] == channel_id(1)


def test_export_roundtrip_with_special_characters() -> None:
    channels = [(channel_id(1), 'Wissen & "Technik" <live>'), (channel_id(2), "")]
    xml = export_opml(channels)
    assert f"playlist_id=UULF{channel_id(1)[2:]}" in xml  # long-form feeds, no Shorts
    assert parse_opml(xml).channels == [
        (channel_id(1), 'Wissen & "Technik" <live>'),
        (channel_id(2), channel_id(2)),
    ]


def test_upload_opml(alice_client: TestClient, alice: User, db: Session) -> None:
    token = csrf_token(alice_client, "/channels")
    page = alice_client.post(
        "/channels/import",
        data={"csrf_token": token},
        files={"file": ("feeds.opml", io.BytesIO(FOREIGN_OPML.encode()), "text/x-opml")},
    ).text
    assert "4 neu, 0 bereits abonniert, 1 ungültige Zeilen" in page
    subs = db.scalars(select(Subscription).where(Subscription.user_id == alice.id)).all()
    assert len(subs) == 4 and all(s.source == "import" for s in subs)


def test_export_contains_only_own_subscriptions(
    app: FastAPI, alice: User, bob: User, db: Session
) -> None:
    subscribe(db, alice.id, add_channel(db, 1, title="Alices Kanal"))
    subscribe(db, bob.id, add_channel(db, 2, title="Bobs Kanal"))
    bob_client = new_client(app)
    login(bob_client, "bob")
    response = bob_client.get("/channels/export.opml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/x-opml")
    assert "attachment" in response.headers["content-disposition"]
    assert "Bobs Kanal" in response.text and "Alices Kanal" not in response.text


def test_export_requires_login(client: TestClient) -> None:
    assert client.get("/channels/export.opml", follow_redirects=False).status_code == 303


def test_cli_import_opml(settings: object, alice: User, db: Session, tmp_path: object) -> None:
    from pathlib import Path

    from app import cli

    path = Path(str(tmp_path)) / "feeds.opml"
    path.write_text(FOREIGN_OPML)
    assert cli.main(["import-opml", "alice", str(path)], settings=settings) == 0  # type: ignore[arg-type]
    assert len(db.scalars(select(Subscription)).all()) == 4
