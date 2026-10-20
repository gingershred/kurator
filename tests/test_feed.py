import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.config import Settings
from app.feed.query import FeedFilters, Length, View, decode_cursor, encode_cursor, feed_page
from app.main import create_app
from app.models import User
from app.services import filters, video_state
from app.services import settings as user_settings
from tests.conftest import login, new_client, post
from tests.factories import BASE_TIME, add_channel, add_video, subscribe, video_id

IDS = re.compile(r'data-video="([^"]+)"')


def shown(client: TestClient, path: str = "/") -> list[str]:
    return IDS.findall(client.get(path).text)


@pytest.fixture
def setup(db: Session, alice: User):  # type: ignore[no-untyped-def]
    channel = add_channel(db, 1, title="Wissenskanal")
    subscribe(db, alice.id, channel)
    return channel


def test_shorts_never_appear_anywhere(
    alice_client: TestClient, db: Session, alice: User, setup: object
) -> None:
    short = add_video(db, setup, 1, seconds=45, is_short=True)  # type: ignore[arg-type]
    add_video(db, setup, 2)  # type: ignore[arg-type]
    video_state.set_hidden(db, alice, short, True)
    video_state.set_watch_later(db, alice, short, True)
    for path in ("/", "/?all=1", "/later", "/later?all=1", "/hidden"):
        assert video_id(1) not in shown(alice_client, path), path
    assert shown(alice_client) == [video_id(2)]
    assert alice_client.get(f"/videos/{video_id(1)}").status_code == 404
    assert post(alice_client, f"/videos/{video_id(1)}/watched").status_code == 404


def test_order_and_watched(
    alice_client: TestClient, db: Session, alice: User, setup: object
) -> None:
    add_video(db, setup, 1, age_hours=5)  # type: ignore[arg-type]
    v2 = add_video(db, setup, 2, age_hours=1)  # type: ignore[arg-type]
    add_video(db, setup, 3, age_hours=3)  # type: ignore[arg-type]
    assert shown(alice_client) == [video_id(2), video_id(3), video_id(1)]
    video_state.set_watched(db, alice, v2, True)
    assert shown(alice_client) == [video_id(3), video_id(1)]
    assert shown(alice_client, "/?all=1") == [video_id(2), video_id(3), video_id(1)]


def test_unenriched_videos_wait_for_api_when_key_is_set(
    settings: Settings, engine: object, db: Session, alice: User, setup: object
) -> None:
    add_video(db, setup, 1, enriched=False, seconds=None)  # type: ignore[arg-type]
    add_video(db, setup, 2)  # type: ignore[arg-type]
    without_key = TestClient(create_app(settings))
    login(without_key, "alice")
    assert shown(without_key) == [video_id(2), video_id(1)]
    assert "Ohne YouTube-API-Schlüssel" in without_key.get("/").text

    with_key = TestClient(
        create_app(settings.model_copy(update={"youtube_api_key": SecretStr("k")}))
    )
    login(with_key, "alice")
    assert shown(with_key) == [video_id(2)]


def test_live_and_premieres_hidden_by_default(
    alice_client: TestClient, db: Session, alice: User, setup: object
) -> None:
    add_video(db, setup, 1, is_live=True, live_status="live")  # type: ignore[arg-type]
    add_video(db, setup, 2, is_premiere=True, live_status="upcoming")  # type: ignore[arg-type]
    add_video(db, setup, 3)  # type: ignore[arg-type]
    assert shown(alice_client) == [video_id(3)]
    hidden = alice_client.get("/hidden").text
    assert "Livestream" in hidden and "Premiere" in hidden

    user_settings.set_user_setting(db, alice.id, "show_livestreams", True)
    assert set(shown(alice_client)) == {video_id(1), video_id(3)}
    user_settings.set_user_setting(db, alice.id, "show_premieres", True)
    assert set(shown(alice_client)) == {video_id(1), video_id(2), video_id(3)}


def test_rules_hide_and_undo(
    alice_client: TestClient, db: Session, alice: User, setup: object
) -> None:
    add_video(db, setup, 1, title="Meine REAKTION auf alles")  # type: ignore[arg-type]
    add_video(db, setup, 2, title="Physik erklärt")  # type: ignore[arg-type]
    add_video(db, setup, 3, seconds=300)  # type: ignore[arg-type]
    keyword = filters.create_rule(db, alice, "keyword_title", "reaktion")
    filters.create_rule(db, alice, "min_duration", "10")
    assert shown(alice_client) == [video_id(2)]
    hidden = alice_client.get("/hidden").text
    assert IDS.findall(hidden) == [video_id(3), video_id(1)]
    assert "Stichwort „reaktion“ im Titel" in hidden and "kürzer als 10 min" in hidden
    assert f"/settings/rules/{keyword.id}/toggle" in hidden

    filters.set_active(db, keyword, False)
    assert shown(alice_client) == [video_id(2), video_id(1)]


def test_muted_and_unsubscribed_channels(
    alice_client: TestClient, db: Session, alice: User, setup: object
) -> None:
    muted = add_channel(db, 2, title="Leise")
    sub = subscribe(db, alice.id, muted, status="muted")
    other = add_channel(db, 3, title="Fremd")
    add_video(db, setup, 1)  # type: ignore[arg-type]
    add_video(db, muted, 2)
    add_video(db, other, 3)
    assert shown(alice_client) == [video_id(1)]
    hidden = alice_client.get("/hidden").text
    assert IDS.findall(hidden) == [video_id(2)]
    assert "Kanal stummgeschaltet" in hidden and f"/channels/{sub.id}/unmute" in hidden
    assert video_id(3) not in hidden


def test_manual_hide_and_unhide(
    alice_client: TestClient, db: Session, alice: User, setup: object
) -> None:
    add_video(db, setup, 1)  # type: ignore[arg-type]
    card = post(alice_client, f"/videos/{video_id(1)}/hide", headers={"HX-Request": "true"})
    assert card.status_code == 200 and "is-hidden" in card.text and "Einblenden" in card.text
    assert shown(alice_client) == []
    assert "manuell ausgeblendet" in alice_client.get("/hidden").text
    post(alice_client, f"/videos/{video_id(1)}/unhide", headers={"HX-Request": "true"})
    assert shown(alice_client) == [video_id(1)]


def test_actions(alice_client: TestClient, db: Session, alice: User, setup: object) -> None:
    video = add_video(db, setup, 1)  # type: ignore[arg-type]
    hx = {"HX-Request": "true"}
    assert "is-watched" in post(alice_client, f"/videos/{video_id(1)}/watched", headers=hx).text
    post(alice_client, f"/videos/{video_id(1)}/later", headers=hx)
    card = post(alice_client, f"/videos/{video_id(1)}/rate", {"rating": "1"}, headers=hx).text
    assert 'aria-pressed="true"' in card
    db.expire_all()
    state = video_state.get_state(db, alice, video)
    assert state is not None and state.watched_at and state.watch_later_at and state.rating == 1

    assert shown(alice_client, "/later") == []  # watched
    assert shown(alice_client, "/later?all=1") == [video_id(1)]
    post(alice_client, f"/videos/{video_id(1)}/unwatched", headers=hx)
    assert shown(alice_client, "/later") == [video_id(1)]

    assert post(alice_client, f"/videos/{video_id(1)}/rate", {"rating": "5"}).status_code == 400
    assert post(alice_client, f"/videos/{video_id(1)}/explode").status_code == 404
    plain = post(
        alice_client,
        f"/videos/{video_id(1)}/unlater",
        follow_redirects=False,
        headers={"referer": "https://evil.example/later?x=1"},
    )
    assert plain.status_code == 303 and plain.headers["location"] == "/later?x=1"


def test_channel_and_length_filters(
    alice_client: TestClient, db: Session, alice: User, setup: object
) -> None:
    second = add_channel(db, 2)
    subscribe(db, alice.id, second)
    add_video(db, setup, 1, seconds=600)  # type: ignore[arg-type]
    add_video(db, setup, 2, seconds=1800)  # type: ignore[arg-type]
    add_video(db, second, 3, seconds=4000)
    add_video(db, second, 4, seconds=None)
    assert shown(alice_client, f"/?channel={second.id}") == [video_id(4), video_id(3)]
    assert shown(alice_client, "/?length=short") == [video_id(1)]
    assert shown(alice_client, "/?length=medium") == [video_id(2)]
    assert shown(alice_client, "/?length=long") == [video_id(3)]
    assert len(shown(alice_client, "/?length=bogus&channel=x")) == 4


def test_paging_with_htmx(
    alice_client: TestClient, db: Session, alice: User, setup: object
) -> None:
    for n in range(30):
        add_video(db, setup, n, age_hours=n)  # type: ignore[arg-type]
    filters.create_rule(db, alice, "keyword_title", "Video 3")  # hides 3 and 30..39 -> only 3
    page = alice_client.get("/").text
    first = IDS.findall(page)
    assert len(first) == 24
    next_url = re.search(r'hx-get="(/\?cursor=[^"]+)"', page)
    assert next_url
    rest = alice_client.get(next_url.group(1).replace("&amp;", "&"), headers={"HX-Request": "true"})
    assert "<html" not in rest.text
    second = IDS.findall(rest.text)
    assert len(first) + len(second) == 29
    assert set(first).isdisjoint(second)
    assert "Mehr laden" not in rest.text


def test_cursor_roundtrip() -> None:
    cursor = encode_cursor(BASE_TIME, 42)
    assert decode_cursor(cursor) == (BASE_TIME, 42)
    assert decode_cursor("kaputt!") is None


def test_service_feed_page_hidden_view(db: Session, alice: User, setup: object) -> None:
    video = add_video(db, setup, 1)  # type: ignore[arg-type]
    video_state.set_hidden(db, alice, video, True)
    page = feed_page(db, alice, View.HIDDEN, FeedFilters(length=Length.ANY))
    assert [i.video.id for i in page.items] == [video.id]
    assert page.items[0].reason is not None and page.items[0].reason.kind == "manual"


def test_empty_states(
    client: TestClient, alice_client: TestClient, db: Session, alice: User
) -> None:
    assert "noch keine Kanäle abonniert" in alice_client.get("/").text
    channel = add_channel(db, 1)
    subscribe(db, alice.id, channel)
    assert "Schön ruhig" in alice_client.get("/").text
    assert "leer" in alice_client.get("/later").text
    assert client.get("/later", follow_redirects=False).status_code == 303


def test_user_isolation(app: FastAPI, db: Session, alice: User, bob: User, setup: object) -> None:
    video = add_video(db, setup, 1)  # type: ignore[arg-type]
    other = add_channel(db, 2)
    subscribe(db, bob.id, other)
    add_video(db, other, 2)
    subscribe(db, bob.id, setup)  # type: ignore[arg-type]
    video_state.set_hidden(db, alice, video, True)
    filters.create_rule(db, alice, "keyword_title", "Video")

    bob_client = new_client(app)
    login(bob_client, "bob")
    assert set(shown(bob_client)) == {
        video_id(1),
        video_id(2),
    }  # Alice's state and rules don't apply
    assert shown(bob_client, "/hidden") == []

    alice_client = new_client(app)
    login(alice_client, "alice")
    # Alice cannot act on a video from a channel she does not follow.
    assert post(alice_client, f"/videos/{video_id(2)}/watched").status_code == 404
    post(bob_client, f"/videos/{video_id(1)}/unhide")
    db.expire_all()
    state = video_state.get_state(db, alice, video)
    assert state is not None and state.hidden_at is not None
