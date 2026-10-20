from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import User
from app.services import users
from tests.conftest import PASSWORD, csrf_token, login, new_client, post


def test_home_requires_login(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2F"


def test_htmx_request_gets_hx_redirect(client: TestClient) -> None:
    response = client.get("/", headers={"HX-Request": "true"}, follow_redirects=False)
    assert response.status_code == 401
    assert response.headers["HX-Redirect"].startswith("/login")


def test_login_and_logout(client: TestClient, alice: User) -> None:
    response = login(client, "alice")
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    page = client.get("/")
    assert page.status_code == 200
    assert "Willkommen, alice!" in page.text

    response = post(client, "/logout", follow_redirects=False)
    assert response.status_code == 303
    assert client.get("/", follow_redirects=False).status_code == 303


def test_session_cookie_flags(client: TestClient, alice: User) -> None:
    response = login(client, "alice")
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_secure_cookie_flag(settings: Settings, engine: object, alice: User) -> None:
    app = create_app(settings.model_copy(update={"secure_cookies": True}))
    with TestClient(app, base_url="https://testserver") as c:
        assert "secure" in login(c, "alice").headers["set-cookie"].lower()


def test_wrong_password(client: TestClient, alice: User) -> None:
    response = login(client, "alice", "wrong-password")
    assert response.status_code == 401
    assert "falsch" in response.text


def test_login_redirects_to_safe_next_only(client: TestClient, alice: User) -> None:
    token = csrf_token(client)
    for target, expected in [
        ("/account", "/account"),
        ("//evil.example", "/"),
        ("https://evil.example", "/"),
    ]:
        client.cookies.clear()
        token = csrf_token(client)
        response = client.post(
            "/login",
            data={"username": "alice", "password": PASSWORD, "csrf_token": token, "next": target},
            follow_redirects=False,
        )
        assert response.headers["location"] == expected


def test_login_without_csrf_token_rejected(client: TestClient, alice: User) -> None:
    client.get("/login")
    response = client.post("/login", data={"username": "alice", "password": PASSWORD})
    assert response.status_code == 403


def test_logout_without_csrf_token_rejected(alice_client: TestClient) -> None:
    response = alice_client.post("/logout", follow_redirects=False)
    assert response.status_code == 403
    assert alice_client.get("/", follow_redirects=False).status_code == 200


def test_csrf_via_header(alice_client: TestClient) -> None:
    token = csrf_token(alice_client, "/")
    response = alice_client.post("/logout", headers={"X-CSRF-Token": token}, follow_redirects=False)
    assert response.status_code == 303


def test_csrf_token_rotates_on_login(client: TestClient, alice: User) -> None:
    before = csrf_token(client)
    login(client, "alice")
    assert csrf_token(client, "/") != before


def test_rate_limit(client: TestClient, alice: User) -> None:
    for _ in range(5):
        assert login(client, "alice", "wrong-password").status_code == 401
    response = login(client, "alice")  # even the right password is refused now
    assert response.status_code == 429
    assert client.get("/", follow_redirects=False).status_code == 303


def test_deactivated_user_loses_session(
    app: FastAPI, alice_client: TestClient, admin: User, alice: User, db: Session
) -> None:
    users.set_active(db, admin, alice, False)
    assert alice_client.get("/", follow_redirects=False).status_code == 303
    assert login(new_client(app), "alice").status_code == 401


def test_password_reset_invalidates_sessions(
    alice_client: TestClient, alice: User, db: Session
) -> None:
    users.set_password(db, alice, "brand-new-password")
    assert alice_client.get("/", follow_redirects=False).status_code == 303


def test_tampered_cookie_is_ignored(client: TestClient, alice: User) -> None:
    login(client, "alice")
    value = client.cookies.get("kurator_session")
    assert value
    client.cookies.set("kurator_session", value[:-3] + "AAA")
    assert client.get("/", follow_redirects=False).status_code == 303


def test_local_login_disabled_in_oidc_mode(settings: Settings, engine: object, alice: User) -> None:
    oidc_settings = settings.model_copy(
        update={
            "auth_mode": "oidc",
            "oidc_issuer": "https://idp.example",
            "oidc_client_id": "kurator",
        }
    )
    with TestClient(create_app(oidc_settings)) as c:
        page = c.get("/login")
        assert 'name="password"' not in page.text
        assert login_status(c) == 403


def login_status(c: TestClient) -> int:
    token = csrf_token(c)
    return c.post(
        "/login", data={"username": "alice", "password": PASSWORD, "csrf_token": token}
    ).status_code


def test_security_headers(client: TestClient) -> None:
    response = client.get("/login")
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"


def test_footer_links_license_and_source(client: TestClient) -> None:
    page = client.get("/login").text
    assert "GNU AGPLv3" in page
    assert "github.com/gingershred/kurator" in page
