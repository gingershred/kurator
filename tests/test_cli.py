import io
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app import cli
from app.bootstrap import ensure_first_admin
from app.config import Settings
from app.db import create_db_engine, create_session_factory
from app.services import users


def run(settings: Settings, monkeypatch: pytest.MonkeyPatch, args: list[str], stdin: str) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return cli.main(args, settings=settings)


def open_db(settings: Settings) -> Session:
    return create_session_factory(create_db_engine(settings.database_url))()


def test_create_user_and_reset_password(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        run(
            settings,
            monkeypatch,
            ["create-user", "Erika", "--admin", "--password-stdin"],
            "first-password\n",
        )
        == 0
    )
    assert "angelegt" in capsys.readouterr().out
    with open_db(settings) as db:
        user = users.authenticate(db, "erika", "first-password")
        assert user is not None and user.is_admin

    assert (
        run(
            settings,
            monkeypatch,
            ["reset-password", "erika", "--password-stdin"],
            "second-password\n",
        )
        == 0
    )
    with open_db(settings) as db:
        assert users.authenticate(db, "erika", "second-password") is not None


def test_create_user_errors(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        run(settings, monkeypatch, ["create-user", "x y", "--password-stdin"], "long-enough-pw\n")
        == 1
    )
    assert "Benutzernamen" in capsys.readouterr().err
    assert run(settings, monkeypatch, ["create-user", "kurz", "--password-stdin"], "abc\n") == 1
    assert (
        run(
            settings,
            monkeypatch,
            ["reset-password", "ghost", "--password-stdin"],
            "long-enough-pw\n",
        )
        == 1
    )


def test_interactive_password_mismatch(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(["password-one", "password-two"])
    monkeypatch.setattr("getpass.getpass", lambda _prompt: next(answers))
    assert cli.main(["create-user", "erika"], settings=settings) == 1


def test_migrate_command(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["migrate"], settings=settings) == 0
    assert "gebracht" in capsys.readouterr().out
    assert cli.main(["migrate"], settings=settings) == 0
    assert "aktuell" in capsys.readouterr().out


def test_first_admin_from_env(db: Session, settings: Settings) -> None:
    assert ensure_first_admin(db, settings) is None  # no password configured
    env_settings = settings.model_copy(
        update={"admin_username": "root", "admin_password": settings.secret_key}
    )
    admin = ensure_first_admin(db, env_settings)
    assert admin is not None and admin.is_admin and admin.username == "root"
    # Never again once users exist.
    assert ensure_first_admin(db, env_settings) is None
    assert users.count_users(db) == 1


def test_healthcheck_worker(settings: Settings, db: Session) -> None:
    from datetime import timedelta

    from app.db import utcnow
    from app.services.settings import record_worker_heartbeat

    cli.main(["migrate"], settings=settings)
    assert cli.main(["healthcheck", "--worker"], settings=settings) == 1
    record_worker_heartbeat(db, utcnow() - timedelta(seconds=30))
    assert cli.main(["healthcheck", "--worker"], settings=settings) == 0


def test_healthcheck_web_unreachable(settings: Settings) -> None:
    assert cli.main(["healthcheck", "--port", "9"], settings=settings) == 1


def test_unwritable_data_dir(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    bad = settings.model_copy(update={"data_dir": blocker / "data"})
    assert cli.main(["create-user", "x1", "--password-stdin"], settings=bad) == 2
    assert "nicht beschreibbar" in capsys.readouterr().err
