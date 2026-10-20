from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.auth import passwords
from app.config import Settings
from app.db import create_db_engine, create_session_factory
from app.main import create_app
from app.models import Role, User
from app.services import users

ROOT = Path(__file__).resolve().parent.parent
TEST_SECRET = "test-secret-key-0123456789abcdef"
PASSWORD = "password-1234"


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Never pick up a developer's .env during tests.
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _fast_password_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        passwords, "_hasher", PasswordHasher(time_cost=1, memory_cost=64, parallelism=1)
    )
    passwords._dummy_hash.cache_clear()


@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.youtube import rss

    monkeypatch.setattr(rss, "RETRY_DELAY", 0)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return Settings(KURATOR_SECRET_KEY=TEST_SECRET, KURATOR_DATA_DIR=data_dir)


def alembic_upgrade(engine: Engine, revision: str = "head") -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, revision)


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    eng = create_db_engine(settings.database_url)
    alembic_upgrade(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


# --- Web fixtures ---------------------------------------------------------------------------

CSRF_RE = re.compile(r'(?:name="csrf_token" value="|"X-CSRF-Token": ")([^"]+)"')


@pytest.fixture
def app(settings: Settings, engine: Engine) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def new_client(app: FastAPI) -> TestClient:
    """A second, independent browser (own cookies)."""
    return TestClient(app)


def csrf_token(client: TestClient, path: str = "/login") -> str:
    match = CSRF_RE.search(client.get(path).text)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


def login(client: TestClient, username: str, password: str = PASSWORD) -> Response:
    token = csrf_token(client)
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=False,
    )


def post(client: TestClient, path: str, data: dict[str, str] | None = None, **kw: Any) -> Response:
    """POST a form with a valid CSRF token taken from the logged-in home page."""
    token = csrf_token(client, "/")
    return client.post(path, data={**(data or {}), "csrf_token": token}, **kw)


@pytest.fixture
def make_user(db: Session) -> Callable[..., User]:
    def _make(username: str, role: Role = Role.USER, password: str | None = PASSWORD) -> User:
        return users.create_user(db, username, password, role)

    return _make


@pytest.fixture
def admin(make_user: Callable[..., User]) -> User:
    return make_user("admin", Role.ADMIN)


@pytest.fixture
def alice(make_user: Callable[..., User]) -> User:
    return make_user("alice")


@pytest.fixture
def bob(make_user: Callable[..., User]) -> User:
    return make_user("bob")


@pytest.fixture
def admin_client(app: FastAPI, admin: User) -> TestClient:
    c = new_client(app)
    assert login(c, "admin").status_code == 303
    return c


@pytest.fixture
def alice_client(app: FastAPI, alice: User) -> TestClient:
    c = new_client(app)
    assert login(c, "alice").status_code == 303
    return c
