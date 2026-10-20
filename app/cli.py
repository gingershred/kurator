"""Command line interface: `python -m app.cli <command>`."""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
from collections.abc import Sequence

from app.config import Settings, get_settings
from app.db import create_db_engine, create_session_factory
from app.models import Role
from app.services import users
from app.web.i18n import translate

log = logging.getLogger("kurator.cli")


def _configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Passwort: ")
    second = getpass.getpass("Passwort wiederholen: ")
    if first != second:
        raise users.UserError("error.password_mismatch")
    return first


def _prepare(settings: Settings) -> None:
    from app.migrate import run_migrations

    run_migrations(settings)


def cmd_migrate(settings: Settings, _args: argparse.Namespace) -> int:
    from app.migrate import run_migrations

    result = run_migrations(settings)
    if result.upgraded:
        print(f"Datenbank auf Stand {result.head_revision} gebracht.")
        if result.backup:
            print(f"Backup: {result.backup}")
    else:
        print(f"Datenbank ist aktuell ({result.head_revision}).")
    return 0


def cmd_create_user(settings: Settings, args: argparse.Namespace) -> int:
    _prepare(settings)
    password = _read_password(args.password_stdin)
    engine = create_db_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as db:
            role = Role.ADMIN if args.admin else Role.USER
            user = users.create_user(db, args.username, password, role)
            print(f"Nutzer „{user.username}“ ({translate('role.' + user.role)}) angelegt.")
    finally:
        engine.dispose()
    return 0


def cmd_reset_password(settings: Settings, args: argparse.Namespace) -> int:
    _prepare(settings)
    engine = create_db_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as db:
            user = users.get_user_by_username(db, args.username)
            if user is None:
                print(f"Nutzer „{args.username}“ nicht gefunden.", file=sys.stderr)
                return 1
            users.set_password(db, user, _read_password(args.password_stdin))
            print(f"Passwort für „{user.username}“ gesetzt; bestehende Sitzungen beendet.")
    finally:
        engine.dispose()
    return 0


def cmd_serve(settings: Settings, args: argparse.Namespace) -> int:
    import uvicorn

    from app.bootstrap import ensure_first_admin
    from app.main import create_app

    _prepare(settings)
    engine = create_db_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as db:
            ensure_first_admin(db, settings)
    finally:
        engine.dispose()
    uvicorn.run(
        create_app(settings),
        host=args.host,
        port=args.port,
        proxy_headers=True,
        log_level=settings.log_level.lower(),
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="kurator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="Web-Oberfläche starten (migriert vorher die Datenbank)")
    p.add_argument("--host", default="0.0.0.0")  # noqa: S104  (inside the container)
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("migrate", help="Datenbank sichern und migrieren")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("create-user", help="Nutzer anlegen")
    p.add_argument("username")
    p.add_argument("--admin", action="store_true", help="als Admin anlegen")
    p.add_argument("--password-stdin", action="store_true", help="Passwort von stdin lesen")
    p.set_defaults(func=cmd_create_user)

    p = sub.add_parser("reset-password", help="Passwort eines Nutzers neu setzen")
    p.add_argument("username")
    p.add_argument("--password-stdin", action="store_true", help="Passwort von stdin lesen")
    p.set_defaults(func=cmd_reset_password)

    return parser


def main(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = settings or get_settings()
    _configure_logging(settings)
    try:
        code: int = args.func(settings, args)
    except users.UserError as exc:
        print(translate(exc.key), file=sys.stderr)
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
