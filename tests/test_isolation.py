"""User B must neither see nor change user A's data through any route."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import User
from app.services import users
from tests.conftest import PASSWORD, csrf_token, login, new_client, post


def test_account_page_shows_only_own_data(app: FastAPI, alice: User, bob: User) -> None:
    bob_client = new_client(app)
    login(bob_client, "bob")
    page = bob_client.get("/account").text
    assert "bob" in page
    assert "alice" not in page


def test_password_change_only_affects_own_account(
    app: FastAPI, alice: User, bob: User, db: Session
) -> None:
    bob_client = new_client(app)
    login(bob_client, "bob")
    post(
        bob_client,
        "/account/password",
        {
            "current_password": PASSWORD,
            "new_password": "bobs-new-password",
            "new_password_repeat": "bobs-new-password",
        },
    )
    db.expire_all()
    assert users.authenticate(db, "alice", PASSWORD) is not None
    assert users.authenticate(db, "bob", "bobs-new-password") is not None


def test_sessions_are_independent(app: FastAPI, alice: User, bob: User) -> None:
    alice_client, bob_client = new_client(app), new_client(app)
    login(alice_client, "alice")
    login(bob_client, "bob")
    assert "Konto (alice)" in alice_client.get("/").text
    assert "Konto (bob)" in bob_client.get("/").text
    post(alice_client, "/logout")
    assert bob_client.get("/", follow_redirects=False).status_code == 200


def test_bob_cannot_use_admin_actions_on_alice(
    app: FastAPI, alice: User, bob: User, db: Session
) -> None:
    bob_client = new_client(app)
    login(bob_client, "bob")
    for path, data in [
        (f"/admin/users/{alice.id}/password", {"password": "hijacked-password"}),
        (f"/admin/users/{alice.id}/delete", {"confirm_username": "alice"}),
        (f"/admin/users/{alice.id}/active", {"active": "false"}),
    ]:
        assert post(bob_client, path, data).status_code == 403
    db.expire_all()
    still = users.get_user(db, alice.id)
    assert still is not None and still.is_active
    assert users.authenticate(db, "alice", PASSWORD) is not None


def test_forged_session_of_other_user_rejected(
    alice_client: TestClient, alice: User, bob: User
) -> None:
    # A cookie signed with another key (e.g. crafted by bob) is not accepted.
    import base64
    import json

    from itsdangerous import TimestampSigner

    payload = base64.b64encode(json.dumps({"uid": alice.id, "sv": 1}).encode())
    forged = TimestampSigner("wrong-key-0123456789").sign(payload).decode()
    attacker = TestClient(alice_client.app)
    attacker.cookies.set("kurator_session", forged)
    assert attacker.get("/", follow_redirects=False).status_code == 303


# --- Phase 1: feed data ---------------------------------------------------------------------


def test_bob_sees_and_changes_nothing_of_alice_feed_data(
    app: FastAPI, alice: User, bob: User, db: Session
) -> None:
    from sqlalchemy import select

    from app.models import FilterRule, Subscription, UserVideoState, WatchEvent
    from app.services import filters, video_state
    from app.services import settings as user_settings
    from tests.factories import add_channel, add_video, subscribe, video_id

    secret_channel = add_channel(db, 1, title="Alices Geheimkanal")
    sub = subscribe(db, alice.id, secret_channel)
    video = add_video(db, secret_channel, 1, title="Alices Lieblingsvideo")
    video_state.set_watch_later(db, alice, video, True)
    video_state.set_rating(db, alice, video, 1)
    rule = filters.create_rule(db, alice, "keyword_title", "AlicesStichwort")
    user_settings.set_user_setting(db, alice.id, "show_livestreams", True)
    alice_client = new_client(app)
    login(alice_client, "alice")
    token = csrf_token(alice_client, "/")
    alice_client.post(
        f"/videos/{video_id(1)}/events",
        json={"event": "progress", "position": 120},
        headers={"X-CSRF-Token": token},
    )

    bob_client = new_client(app)
    login(bob_client, "bob")

    # Reading: none of Alice's data shows up on any of Bob's pages.
    for path in ("/", "/?all=1", "/later", "/later?all=1", "/hidden", "/channels", "/settings"):
        page = bob_client.get(path).text
        for secret in ("Alices Geheimkanal", "Alices Lieblingsvideo", "AlicesStichwort"):
            assert secret not in page, (path, secret)
    assert bob_client.get(f"/videos/{video_id(1)}").status_code == 404

    # Writing: every id-based route rejects Alice's objects.
    bob_token = csrf_token(bob_client, "/")
    for path in (
        f"/channels/{sub.id}/mute",
        f"/channels/{sub.id}/unmute",
        f"/channels/{sub.id}/remove",
        f"/settings/rules/{rule.id}/toggle",
        f"/settings/rules/{rule.id}/delete",
        *(f"/videos/{video_id(1)}/{a}" for a in ("watched", "hide", "later", "unlater", "rate")),
    ):
        assert post(bob_client, path).status_code == 404, path
    response = bob_client.post(
        f"/videos/{video_id(1)}/events",
        json={"event": "progress", "position": 999},
        headers={"X-CSRF-Token": bob_token},
    )
    assert response.status_code == 404
    post(bob_client, "/settings", {})

    # Alice's data is untouched.
    db.expire_all()
    [alice_sub] = db.scalars(select(Subscription).where(Subscription.user_id == alice.id)).all()
    assert alice_sub.status == "active"
    assert db.get(FilterRule, rule.id) is not None
    state = db.get(UserVideoState, (alice.id, video.id))
    assert state is not None and state.watch_later_at and state.rating == 1
    assert state.progress_seconds == 120 and state.watched_at is None
    assert db.scalars(select(WatchEvent).where(WatchEvent.user_id == bob.id)).all() == []
    assert user_settings.get_user_setting(db, alice.id, "show_livestreams") is True
