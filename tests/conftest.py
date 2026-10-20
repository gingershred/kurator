from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.auth import passwords
from app.config import Settings
from app.db import create_db_engine, create_session_factory

ROOT = Path(__file__).resolve().parent.parent
TEST_SECRET = "test-secret-key-0123456789abcdef"


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
