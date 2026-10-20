"""Command line interface: `python -m app.cli <command>`."""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from app.config import Settings, get_settings
from app.db import create_db_engine, create_session_factory, utcnow
from app.logs import configure_logging
from app.models import Role
from app.services import users
from app.web.i18n import translate


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Passwort: ")
    second = getpass.getpass("Passwort wiederholen: ")
    if first != second:
        raise users.UserError("error.password_mismatch")
    return first


class DataDirError(Exception):
    pass


def _check_data_dir(settings: Settings) -> None:
    data_dir = settings.data_dir
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        raise DataDirError(
            f"Das Datenverzeichnis {data_dir} ist nicht beschreibbar ({exc.strerror}). "
            "Der Container läuft als UID/GID 1000 – siehe docs/INSTALL.md "
            "(z. B. `sudo chown -R 1000:1000 data`)."
        ) from exc


def _prepare(settings: Settings) -> None:
    from app.migrate import run_migrations

    _check_data_dir(settings)
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


def cmd_backup(settings: Settings, _args: argparse.Namespace) -> int:
    from app.migrate import backup_database

    if not settings.db_path.exists():
        print(f"Keine Datenbank unter {settings.db_path} gefunden.", file=sys.stderr)
        return 1
    print(f"Backup: {backup_database(settings, 'manual')}")
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


def cmd_worker(settings: Settings, _args: argparse.Namespace) -> int:
    from app.migrate import wait_for_head
    from app.worker import Worker

    # The web process migrates the database; the worker only waits for it.
    wait_for_head(settings, timeout=300)
    engine = create_db_engine(settings.database_url)
    try:
        Worker(settings, create_session_factory(engine)).run_forever()
    finally:
        engine.dispose()
    return 0


def cmd_healthcheck(settings: Settings, args: argparse.Namespace) -> int:
    """Exit 0 if healthy. Web: /healthz answers. Worker: recent heartbeat in the database."""
    if args.worker:
        from app.services.settings import worker_heartbeat
        from app.services.status import WORKER_ALIVE_WITHIN

        engine = create_db_engine(settings.database_url)
        try:
            with create_session_factory(engine)() as db:
                beat = worker_heartbeat(db)
        finally:
            engine.dispose()
        return 0 if beat is not None and utcnow() - beat <= WORKER_ALIVE_WITHIN else 1

    import urllib.request

    url = f"http://127.0.0.1:{args.port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=4) as response:
            return 0 if response.status == 200 else 1
    except OSError:
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="kurator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="Web-Oberfläche starten (migriert vorher die Datenbank)")
    p.add_argument("--host", default="0.0.0.0")  # noqa: S104  (inside the container)
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("worker", help="Hintergrund-Worker starten")
    p.set_defaults(func=cmd_worker)

    p = sub.add_parser("healthcheck", help="Gesundheitsprüfung für Docker")
    p.add_argument("--worker", action="store_true", help="Worker statt Web prüfen")
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(func=cmd_healthcheck)

    p = sub.add_parser("migrate", help="Datenbank sichern und migrieren")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("backup", help="Datenbank im laufenden Betrieb sichern")
    p.set_defaults(func=cmd_backup)

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


def _config_error(exc: ValidationError) -> str:
    # Never echo input values: they may contain secrets.
    lines = ["Ungültige Konfiguration:"]
    for error in exc.errors(include_input=False, include_url=False):
        location = ".".join(str(part) for part in error["loc"]) or "Einstellungen"
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    args = build_parser().parse_args(argv)
    if settings is None:
        try:
            settings = get_settings()
        except ValidationError as exc:
            print(_config_error(exc), file=sys.stderr)
            return 2
    configure_logging(settings)
    try:
        code: int = args.func(settings, args)
    except users.UserError as exc:
        print(translate(exc.key), file=sys.stderr)
        return 1
    except DataDirError as exc:
        print(exc, file=sys.stderr)
        return 2
    return code


if __name__ == "__main__":
    sys.exit(main())
