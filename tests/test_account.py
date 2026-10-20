from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import User
from tests.conftest import PASSWORD, login, new_client, post


def change(client: TestClient, current: str, new: str, repeat: str | None = None) -> str:
    data = {
        "current_password": current,
        "new_password": new,
        "new_password_repeat": new if repeat is None else repeat,
    }
    return post(client, "/account/password", data).text


def test_account_page(alice_client: TestClient) -> None:
    page = alice_client.get("/account")
    assert page.status_code == 200
    assert "alice" in page.text


def test_change_own_password_keeps_session_and_ends_others(
    app: FastAPI, alice_client: TestClient, alice: User
) -> None:
    other_browser = new_client(app)
    login(other_browser, "alice")

    assert "wurde geändert" in change(alice_client, PASSWORD, "new-password-42")

    assert alice_client.get("/", follow_redirects=False).status_code == 200
    assert other_browser.get("/", follow_redirects=False).status_code == 303
    assert login(new_client(app), "alice", "new-password-42").status_code == 303


def test_change_password_errors(alice_client: TestClient) -> None:
    assert "aktuelle Passwort ist falsch" in change(alice_client, "wrong", "new-password-42")
    assert "stimmen nicht überein" in change(alice_client, PASSWORD, "new-password-42", "other")
    assert "mindestens 10 Zeichen" in change(alice_client, PASSWORD, "short")
