import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import Channel, Subscription, User, UserVideoState, Video, WatchEvent
from app.playback.embed import start_position
from app.services import video_state
from tests.conftest import csrf_token, login, new_client, post
from tests.factories import add_channel, add_video, subscribe, video_id


@pytest.fixture
def video(db: Session, alice: User) -> Video:
    channel = add_channel(db, 1, title="Wissenskanal")
    subscribe(db, alice.id, channel)
    return add_video(
        db, channel, 1, title="Ein langes Video", seconds=1000, description="Zeile 1\nZeile 2"
    )


def event(client: TestClient, vid: str, name: str, position: int, token: str | None = None) -> int:
    headers = {"X-CSRF-Token": token if token is not None else csrf_token(client, "/")}
    return client.post(
        f"/videos/{vid}/events", json={"event": name, "position": position}, headers=headers
    ).status_code


def state(db: Session, user: User, video: Video) -> UserVideoState:
    db.expire_all()
    found = video_state.get_state(db, user, video)
    assert found is not None
    return found


def test_detail_page(alice_client: TestClient, video: Video) -> None:
    response = alice_client.get(f"/videos/{video_id(1)}")
    assert response.status_code == 200
    page = response.text
    assert 'id="player"' in page and f'data-video="{video_id(1)}"' in page
    assert 'data-host="https://www.youtube-nocookie.com"' in page
    assert f"https://www.youtube.com/watch?v={video_id(1)}" in page
    assert "Zeile 1\nZeile 2" in page
    csp = response.headers["content-security-policy"]
    assert "frame-src https://www.youtube-nocookie.com" in csp
    assert "https://www.youtube.com" in csp
    # Other pages keep the strict policy.
    assert "youtube" not in alice_client.get("/").headers["content-security-policy"]


def test_progress_and_auto_watched(
    alice_client: TestClient, db: Session, alice: User, video: Video
) -> None:
    assert event(alice_client, video_id(1), "play", 0) == 200
    assert event(alice_client, video_id(1), "progress", 300) == 200
    assert state(db, alice, video).progress_seconds == 300
    assert state(db, alice, video).watched_at is None
    assert "weiter bei 5:00" in alice_client.get(f"/videos/{video_id(1)}").text

    assert event(alice_client, video_id(1), "progress", 900) == 200  # 90 %
    assert state(db, alice, video).watched_at is not None
    events = db.scalars(select(WatchEvent).where(WatchEvent.user_id == alice.id)).all()
    assert [(e.event, e.position_seconds) for e in events] == [
        ("play", 0),
        ("progress", 300),
        ("progress", 900),
    ]


def test_ended_marks_watched_and_positions_are_clamped(
    alice_client: TestClient, db: Session, alice: User, video: Video
) -> None:
    event(alice_client, video_id(1), "progress", 999999)
    assert state(db, alice, video).progress_seconds == 1000
    video_state.set_watched(db, alice, video, False)
    event(alice_client, video_id(1), "ended", 10)
    assert state(db, alice, video).watched_at is not None


def test_invalid_events(alice_client: TestClient, video: Video) -> None:
    assert event(alice_client, video_id(1), "explode", 1) == 422
    assert event(alice_client, video_id(1), "progress", -5) == 422
    assert event(alice_client, video_id(1), "progress", 5, token="wrong") == 403


def test_progress_survives_restart(
    settings: Settings, engine: object, db: Session, alice: User, video: Video
) -> None:
    with TestClient(create_app(settings)) as first:
        login(first, "alice")
        event(first, video_id(1), "progress", 420)
    with TestClient(create_app(settings)) as second:  # "new container", same /data
        login(second, "alice")
        assert "weiter bei 7:00" in second.get(f"/videos/{video_id(1)}").text


def test_actions_on_detail_page(alice_client: TestClient, video: Video) -> None:
    response = post(
        alice_client,
        f"/videos/{video_id(1)}/watched",
        {"view": "detail"},
        headers={"HX-Request": "true"},
    )
    assert 'class="detail-actions"' in response.text and "thumb" not in response.text


def test_start_position() -> None:
    video = Video(duration_seconds=1000)
    assert start_position(video, None) == 0
    assert start_position(video, UserVideoState(progress_seconds=0)) == 0
    assert start_position(video, UserVideoState(progress_seconds=500)) == 500
    assert start_position(video, UserVideoState(progress_seconds=960)) == 0
    assert start_position(Video(duration_seconds=None), UserVideoState(progress_seconds=500)) == 500


def test_isolation(app: FastAPI, db: Session, alice: User, bob: User, video: Video) -> None:
    bob_client = new_client(app)
    login(bob_client, "bob")
    assert bob_client.get(f"/videos/{video_id(1)}").status_code == 404
    assert event(bob_client, video_id(1), "progress", 500) == 404
    assert video_state.get_state(db, bob, video) is None
    assert db.scalars(select(WatchEvent)).all() == []

    # Once Bob follows the channel he gets his own, independent state.
    channel = db.get(Channel, video.channel_id)
    assert channel is not None
    db.add(Subscription(user_id=bob.id, channel_id=channel.id))
    db.commit()
    alice_client = new_client(app)
    login(alice_client, "alice")
    event(alice_client, video_id(1), "progress", 100)
    assert event(bob_client, video_id(1), "progress", 600) == 200
    assert state(db, alice, video).progress_seconds == 100
    assert state(db, bob, video).progress_seconds == 600
