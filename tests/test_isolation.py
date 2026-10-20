"""User B must neither see nor change user A's data through any route."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import User
from app.services import users
from tests.conftest import PASSWORD, login, new_client, post


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
