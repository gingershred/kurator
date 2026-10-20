"""Every /admin route must be closed to anonymous visitors and to non-admins.

The routes are discovered from all `app.web.routes.admin_*` modules, so new admin pages are
covered automatically.
"""

import importlib
import pkgutil
import re

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.web.routes
from app.models import User
from tests.conftest import csrf_token


def admin_routes(_app: FastAPI) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    routes: list[object] = []
    for module_info in pkgutil.iter_modules(app.web.routes.__path__):
        if module_info.name.startswith("admin_"):
            module = importlib.import_module(f"app.web.routes.{module_info.name}")
            routes.extend(module.router.routes)
    for route in routes:
        if isinstance(route, APIRoute):
            assert route.path.startswith("/admin"), route.path
            path = re.sub(r"\{[^}]+\}", "1", route.path)
            found.extend((method, path) for method in sorted(route.methods or ()))
    return found


def test_admin_routes_exist(app: FastAPI) -> None:
    paths = {path for _method, path in admin_routes(app)}
    assert "/admin/users" in paths


def test_non_admin_gets_403_everywhere(
    app: FastAPI, alice_client: TestClient, admin: User, db: Session
) -> None:
    token = csrf_token(alice_client, "/")
    for method, path in admin_routes(app):
        response = alice_client.request(
            method, path, data={"csrf_token": token}, follow_redirects=False
        )
        assert response.status_code == 403, (method, path)
    db.expire_all()
    # Nothing was changed by the attempts (admin id 1 still admin and active).
    target = db.get(User, admin.id)
    assert target is not None and target.is_admin and target.is_active


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_anonymous_is_sent_to_login(app: FastAPI, client: TestClient, method: str) -> None:
    token = csrf_token(client)
    for route_method, path in admin_routes(app):
        if route_method != method:
            continue
        response = client.request(method, path, data={"csrf_token": token}, follow_redirects=False)
        assert response.status_code == 303, (method, path)
        assert response.headers["location"].startswith("/login")
