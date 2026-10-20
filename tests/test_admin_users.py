from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User
from app.services import users
from tests.conftest import PASSWORD, login, new_client, post


def fresh(db: Session, user: User) -> User:
    db.expire_all()
    found = db.get(User, user.id)
    assert found is not None
    return found


def test_list_users(admin_client: TestClient, alice: User) -> None:
    page = admin_client.get("/admin/users")
    assert page.status_code == 200
    assert "alice" in page.text and "admin" in page.text


def test_admin_creates_second_user_who_can_log_in(app: FastAPI, admin_client: TestClient) -> None:
    response = post(
        admin_client,
        "/admin/users",
        {"username": "carol", "password": "carol-password", "role": "user"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "carol" in admin_client.get("/admin/users").text

    carol = new_client(app)
    assert login(carol, "carol", "carol-password").status_code == 303
    assert "Konto (carol)" in carol.get("/").text
    # Both sessions stay valid independently.
    assert admin_client.get("/admin/users").status_code == 200


def test_create_user_validation_error(admin_client: TestClient, alice: User) -> None:
    response = post(admin_client, "/admin/users", {"username": "alice", "password": PASSWORD})
    assert response.status_code == 400
    assert "bereits vergeben" in response.text


def test_create_oidc_only_user_without_password(admin_client: TestClient, db: Session) -> None:
    post(admin_client, "/admin/users", {"username": "dave", "password": ""})
    dave = users.get_user_by_username(db, "dave")
    assert dave is not None and dave.password_hash is None


def test_change_role(admin_client: TestClient, alice: User, db: Session) -> None:
    post(admin_client, f"/admin/users/{alice.id}/role", {"role": "admin"})
    assert fresh(db, alice).is_admin


def test_invalid_role_rejected(admin_client: TestClient, alice: User) -> None:
    response = post(admin_client, f"/admin/users/{alice.id}/role", {"role": "root"})
    assert response.status_code == 400


def test_reset_password(
    app: FastAPI, admin_client: TestClient, alice: User, alice_client: TestClient
) -> None:
    page = post(admin_client, f"/admin/users/{alice.id}/password", {"password": "reset-password-1"})
    assert "Passwort wurde gesetzt" in page.text
    assert alice_client.get("/", follow_redirects=False).status_code == 303
    assert login(new_client(app), "alice", "reset-password-1").status_code == 303


def test_reset_password_too_short(admin_client: TestClient, alice: User) -> None:
    page = post(admin_client, f"/admin/users/{alice.id}/password", {"password": "short"})
    assert "mindestens 10 Zeichen" in page.text


def test_deactivate_and_activate(
    admin_client: TestClient, alice: User, alice_client: TestClient, db: Session
) -> None:
    post(admin_client, f"/admin/users/{alice.id}/active", {"active": "false"})
    assert not fresh(db, alice).is_active
    assert alice_client.get("/", follow_redirects=False).status_code == 303
    post(admin_client, f"/admin/users/{alice.id}/active", {"active": "true"})
    assert fresh(db, alice).is_active


def test_admin_cannot_deactivate_self(admin_client: TestClient, admin: User, db: Session) -> None:
    page = post(admin_client, f"/admin/users/{admin.id}/active", {"active": "false"})
    assert "nicht selbst deaktivieren" in page.text
    assert fresh(db, admin).is_active


def test_last_admin_cannot_be_demoted(admin_client: TestClient, admin: User, db: Session) -> None:
    page = post(admin_client, f"/admin/users/{admin.id}/role", {"role": "user"})
    assert "letzte aktive Admin" in page.text
    assert fresh(db, admin).is_admin


def test_delete_requires_confirmation(admin_client: TestClient, alice: User, db: Session) -> None:
    page = post(admin_client, f"/admin/users/{alice.id}/delete", {"confirm_username": "bob"})
    assert "stimmt nicht überein" in page.text
    assert users.get_user(db, alice.id) is not None

    page = post(admin_client, f"/admin/users/{alice.id}/delete", {"confirm_username": "ALICE"})
    assert "gelöscht" in page.text
    db.expire_all()
    assert db.scalars(select(User).where(User.username == "alice")).first() is None


def test_unlink_oidc(admin_client: TestClient, db: Session) -> None:
    eve = users.create_user(db, "eve", None, oidc_subject="sub-eve")
    post(admin_client, f"/admin/users/{eve.id}/unlink-oidc")
    assert fresh(db, eve).oidc_subject is None


def test_unknown_user_404(admin_client: TestClient) -> None:
    assert admin_client.get("/admin/users/9999").status_code == 404
