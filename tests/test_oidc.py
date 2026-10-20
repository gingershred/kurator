"""OIDC login against a fake identity provider (all HTTP mocked with respx)."""

import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.auth.oidc import resolve_user
from app.config import Settings
from app.main import create_app
from app.models import User
from app.services import users

ISSUER = "https://idp.example"
CLIENT_ID = "kurator"
KEY = RSAKey.generate_key(2048, parameters={"kid": "test-key"})


@pytest.fixture
def oidc_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "auth_mode": "both",
            "oidc_issuer": ISSUER,
            "oidc_client_id": CLIENT_ID,
            "oidc_client_secret": settings.secret_key,
            "base_url": "http://testserver",
        }
    )


@pytest.fixture
def idp() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{ISSUER}/.well-known/openid-configuration").respond(
            json={
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
                "response_types_supported": ["code"],
                "subject_types_supported": ["public"],
                "id_token_signing_alg_values_supported": ["RS256"],
            }
        )
        router.get(f"{ISSUER}/jwks").respond(json={"keys": [KEY.as_dict(private=False)]})
        yield router


@pytest.fixture
def oidc_client(oidc_settings: Settings, engine: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(oidc_settings)) as c:
        yield c


def id_token(nonce: str, **claims: Any) -> str:
    now = int(time.time())
    payload = {"iss": ISSUER, "aud": CLIENT_ID, "iat": now, "exp": now + 300, "nonce": nonce}
    payload.update(claims)
    return jwt.encode({"alg": "RS256", "kid": "test-key"}, payload, KEY)


def oidc_login(
    client: TestClient, idp: respx.MockRouter, next_url: str = "/", **claims: Any
) -> httpx.Response:
    start = client.get("/auth/oidc/login", params={"next": next_url}, follow_redirects=False)
    assert start.status_code == 302
    location = urlparse(start.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == f"{ISSUER}/authorize"
    query = parse_qs(location.query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == ["http://testserver/auth/oidc/callback"]
    assert query["code_challenge_method"] == ["S256"]

    token = id_token(query["nonce"][0], **claims)
    idp.post(f"{ISSUER}/token").respond(
        json={"access_token": "at", "token_type": "Bearer", "expires_in": 300, "id_token": token}
    )
    return client.get(
        "/auth/oidc/callback",
        params={"code": "auth-code", "state": query["state"][0]},
        follow_redirects=False,
    )


def test_login_page_shows_both_options(oidc_client: TestClient) -> None:
    page = oidc_client.get("/login").text
    assert 'name="password"' in page
    assert "/auth/oidc/login" in page


def test_first_login_links_by_username_then_uses_subject(
    oidc_client: TestClient, idp: respx.MockRouter, alice: User, db: Session
) -> None:
    response = oidc_login(oidc_client, idp, "/account", sub="sub-alice", preferred_username="Alice")
    assert response.status_code == 303
    assert response.headers["location"] == "/account"
    assert "alice" in oidc_client.get("/account").text
    db.refresh(alice)
    assert alice.oidc_subject == "sub-alice"

    # Later logins match by subject, even if the username claim changed.
    oidc_client.cookies.clear()
    response = oidc_login(oidc_client, idp, sub="sub-alice", preferred_username="renamed")
    assert response.status_code == 303


def test_unknown_user_rejected(oidc_client: TestClient, idp: respx.MockRouter) -> None:
    response = oidc_login(oidc_client, idp, sub="sub-x", preferred_username="stranger")
    assert response.status_code == 403
    assert "keinen kurator-Nutzer" in response.text
    assert oidc_client.get("/", follow_redirects=False).status_code == 303


def test_username_already_linked_to_other_subject(
    oidc_client: TestClient, idp: respx.MockRouter, db: Session
) -> None:
    users.create_user(db, "bob", None, oidc_subject="sub-bob")
    response = oidc_login(oidc_client, idp, sub="sub-attacker", preferred_username="bob")
    assert response.status_code == 403


def test_inactive_user_rejected(
    oidc_client: TestClient, idp: respx.MockRouter, admin: User, alice: User, db: Session
) -> None:
    users.set_active(db, admin, alice, False)
    response = oidc_login(oidc_client, idp, sub="sub-alice", preferred_username="alice")
    assert response.status_code == 403


def test_wrong_nonce_rejected(oidc_client: TestClient, idp: respx.MockRouter, alice: User) -> None:
    start = oidc_client.get("/auth/oidc/login", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    idp.post(f"{ISSUER}/token").respond(
        json={
            "access_token": "at",
            "token_type": "Bearer",
            "id_token": id_token("other-nonce", sub="s", preferred_username="alice"),
        }
    )
    response = oidc_client.get(
        "/auth/oidc/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    assert response.status_code in (400, 502)
    assert oidc_client.get("/", follow_redirects=False).status_code == 303


def test_callback_with_bad_state_rejected(oidc_client: TestClient, idp: respx.MockRouter) -> None:
    response = oidc_client.get(
        "/auth/oidc/callback", params={"code": "c", "state": "forged"}, follow_redirects=False
    )
    assert response.status_code == 400


def test_oidc_routes_absent_in_local_mode(client: TestClient) -> None:
    assert client.get("/auth/oidc/login", follow_redirects=False).status_code == 404


def test_oidc_only_user_has_no_password_form(
    oidc_client: TestClient, idp: respx.MockRouter, db: Session
) -> None:
    users.create_user(db, "olga", None)
    assert oidc_login(oidc_client, idp, sub="sub-olga", preferred_username="olga").is_redirect
    page = oidc_client.get("/account").text
    assert "kein lokales Passwort" in page


def test_resolve_user_requires_subject(db: Session, alice: User) -> None:
    assert resolve_user(db, {"preferred_username": "alice"}, "preferred_username") is None
    assert resolve_user(db, {"sub": "s1"}, "preferred_username") is None
    assert resolve_user(db, {"sub": "s1", "email": "alice"}, "email") == alice
