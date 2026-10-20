import io

import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import Channel, Job, Subscription, User
from app.youtube import quota
from app.youtube.api import API_BASE
from tests.conftest import csrf_token, login, new_client, post
from tests.factories import add_channel, channel_id, subscribe


def subs(db: Session, user: User) -> list[Subscription]:
    db.expire_all()
    return list(db.scalars(select(Subscription).where(Subscription.user_id == user.id)))


def upload(client: TestClient, content: bytes) -> str:
    token = csrf_token(client, "/channels")
    return client.post(
        "/channels/import",
        data={"csrf_token": token},
        files={"file": ("subscriptions.csv", io.BytesIO(content), "text/csv")},
    ).text


def test_channels_page(alice_client: TestClient) -> None:
    page = alice_client.get("/channels")
    assert page.status_code == 200
    assert "noch keine Kanäle" in page.text
    assert "Ohne YouTube-API-Schlüssel" in page.text


def test_add_by_channel_id_without_api(alice_client: TestClient, alice: User, db: Session) -> None:
    page = post(
        alice_client, "/channels", {"channel": f"https://www.youtube.com/channel/{channel_id(1)}"}
    ).text
    assert "abonniert" in page
    [sub] = subs(db, alice)
    assert sub.source == "manual" and sub.status == "active"
    job = db.scalars(select(Job)).one()
    assert job.type == "fetch_channel" and job.payload == {"channel_ids": [sub.channel_id]}

    page = post(alice_client, "/channels", {"channel": channel_id(1)}).text
    assert "bereits abonniert" in page


def test_handle_needs_api_key(alice_client: TestClient, alice: User, db: Session) -> None:
    page = post(alice_client, "/channels", {"channel": "@kurzgesagt"}).text
    assert "nur mit YouTube-API-Schlüssel" in page
    assert subs(db, alice) == []


def test_invalid_input(alice_client: TestClient, alice: User, db: Session) -> None:
    page = post(alice_client, "/channels", {"channel": "https://www.youtube.com/c/Foo"}).text
    assert "/c/" in page
    assert subs(db, alice) == []


@respx.mock
def test_add_by_handle_with_api(
    settings: Settings, engine: object, alice: User, db: Session
) -> None:
    app = create_app(settings.model_copy(update={"youtube_api_key": SecretStr("AIza-x")}))
    respx.get(f"{API_BASE}/channels").respond(
        json={
            "items": [
                {
                    "id": channel_id(7),
                    "snippet": {"title": "Kurzgesagt", "customUrl": "@kurzgesagt"},
                }
            ]
        }
    )
    with TestClient(app) as client:
        login(client, "alice")
        page = post(client, "/channels", {"channel": "@kurzgesagt"}).text
    assert "„Kurzgesagt“ wurde abonniert" in page
    channel = db.scalars(select(Channel)).one()
    assert channel.youtube_channel_id == channel_id(7) and channel.handle == "kurzgesagt"
    assert quota.used_today(db) == 1


@respx.mock
def test_unknown_handle(settings: Settings, engine: object, alice: User, db: Session) -> None:
    app = create_app(settings.model_copy(update={"youtube_api_key": SecretStr("AIza-x")}))
    respx.get(f"{API_BASE}/channels").respond(json={"items": []})
    with TestClient(app) as client:
        login(client, "alice")
        assert "gibt es auf YouTube nicht" in post(client, "/channels", {"channel": "@nope"}).text


def test_import_150_channels(alice_client: TestClient, alice: User, db: Session) -> None:
    rows = ["Kanal-ID,Kanal-URL,Kanaltitel"] + [
        f"{channel_id(i)},http://www.youtube.com/channel/{channel_id(i)},Kanal {i}"
        for i in range(150)
    ]
    page = upload(alice_client, "\n".join(rows).encode())
    assert "150 neu, 0 bereits abonniert" in page
    assert len(subs(db, alice)) == 150
    assert all(s.source == "import" for s in subs(db, alice))
    jobs = db.scalars(select(Job)).all()
    assert [len(j.payload["channel_ids"]) for j in jobs] == [50, 50, 50]
    assert quota.used_today(db) == 0

    page = upload(alice_client, "\n".join(rows[:11]).encode())
    assert "0 neu, 10 bereits abonniert" in page


def test_import_errors(alice_client: TestClient) -> None:
    assert "keine Kanäle gefunden" in upload(alice_client, b"nur,muell\n")
    assert "zu groß" in upload(alice_client, b"x" * (2 * 1024 * 1024 + 10))


def test_channels_are_shared(
    app: FastAPI, alice_client: TestClient, alice: User, bob: User, db: Session
) -> None:
    bob_client = new_client(app)
    login(bob_client, "bob")
    post(alice_client, "/channels", {"channel": channel_id(1)})
    post(bob_client, "/channels", {"channel": channel_id(1)})
    assert db.query(Channel).count() == 1
    assert len(subs(db, alice)) == len(subs(db, bob)) == 1


def test_mute_unmute_remove(alice_client: TestClient, alice: User, db: Session) -> None:
    channel = add_channel(db, 1, title="Testkanal")
    sub = subscribe(db, alice.id, channel)
    post(alice_client, f"/channels/{sub.id}/mute")
    assert subs(db, alice)[0].status == "muted"
    assert "stummgeschaltet" in alice_client.get("/channels").text
    post(alice_client, f"/channels/{sub.id}/unmute")
    assert subs(db, alice)[0].status == "active"
    page = post(alice_client, f"/channels/{sub.id}/remove").text
    assert "„Testkanal“ wurde entfernt" in page
    assert subs(db, alice) == []
    assert db.query(Channel).count() == 1  # shared channel stays


def test_user_isolation(app: FastAPI, alice: User, bob: User, db: Session) -> None:
    channel = add_channel(db, 1, title="Alices Kanal")
    sub = subscribe(db, alice.id, channel)
    bob_client = new_client(app)
    login(bob_client, "bob")
    assert "Alices Kanal" not in bob_client.get("/channels").text
    for action in ("mute", "unmute", "remove"):
        assert post(bob_client, f"/channels/{sub.id}/{action}").status_code == 404
    [still] = subs(db, alice)
    assert still.status == "active"


def test_cli_import(
    settings: Settings,
    alice: User,
    db: Session,
    tmp_path: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pathlib import Path

    from app import cli

    path = Path(str(tmp_path)) / "subscriptions.csv"
    path.write_text("Channel Id,Channel Url,Channel Title\n" + f"{channel_id(1)},x,Eins\n")
    assert cli.main(["import-takeout", "alice", str(path)], settings=settings) == 0
    assert "1 neu" in capsys.readouterr().out
    assert len(subs(db, alice)) == 1
    assert cli.main(["import-takeout", "ghost", str(path)], settings=settings) == 1
