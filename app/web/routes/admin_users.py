from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.models import Role, User
from app.services import users
from app.web.deps import get_db, require_admin
from app.web.i18n import translate
from app.web.templating import flash, render

router = APIRouter(prefix="/admin/users", dependencies=[Depends(require_admin)])


def _target(db: Session, user_id: int) -> User:
    user = users.get_user(db, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return user


def _back(user_id: int | None = None) -> RedirectResponse:
    path = "/admin/users" if user_id is None else f"/admin/users/{user_id}"
    return RedirectResponse(path, status.HTTP_303_SEE_OTHER)


def _parse_role(value: str) -> Role:
    try:
        return Role(value)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST) from exc


@router.get("")
def list_users(request: Request, db: Session = Depends(get_db)) -> Response:
    return render(request, "admin/users.html", {"users": users.list_users(db)})


@router.post("")
def create_user(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    role: str = Form(Role.USER.value),
    db: Session = Depends(get_db),
) -> Response:
    try:
        user = users.create_user(db, username, password or None, _parse_role(role))
    except users.UserError as exc:
        context = {
            "users": users.list_users(db),
            "error": translate(exc.key),
            "form": {"username": username, "role": role},
        }
        return render(request, "admin/users.html", context, 400)
    flash(request, "admin.users.created_ok", username=user.username)
    return _back()


@router.get("/{user_id}")
def edit_user(request: Request, user_id: int, db: Session = Depends(get_db)) -> Response:
    return render(request, "admin/user_form.html", {"user": _target(db, user_id)})


def _apply(
    request: Request, user_id: int, ok_key: str, action: Callable[[], None]
) -> RedirectResponse:
    """Run a user-service action and report the outcome as a flash message."""
    try:
        action()
    except users.UserError as exc:
        flash(request, exc.key, kind="error")
    else:
        flash(request, ok_key)
    return _back(user_id)


@router.post("/{user_id}/role")
def change_role(
    request: Request,
    user_id: int,
    role: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    user, new_role = _target(db, user_id), _parse_role(role)
    return _apply(
        request,
        user_id,
        "admin.users.role_saved",
        lambda: users.set_role(db, admin, user, new_role),
    )


@router.post("/{user_id}/password")
def reset_password(
    request: Request,
    user_id: int,
    password: str = Form(""),
    db: Session = Depends(get_db),
) -> Response:
    user = _target(db, user_id)
    return _apply(
        request,
        user_id,
        "admin.users.password_reset_ok",
        lambda: users.set_password(db, user, password),
    )


@router.post("/{user_id}/active")
def set_active(
    request: Request,
    user_id: int,
    active: bool = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    user = _target(db, user_id)
    key = "admin.users.activated" if active else "admin.users.deactivated"
    return _apply(request, user_id, key, lambda: users.set_active(db, admin, user, active))


@router.post("/{user_id}/unlink-oidc")
def unlink_oidc(request: Request, user_id: int, db: Session = Depends(get_db)) -> Response:
    user = _target(db, user_id)
    return _apply(
        request, user_id, "admin.users.oidc_unlinked", lambda: users.unlink_oidc(db, user)
    )


@router.post("/{user_id}/delete")
def delete_user(
    request: Request,
    user_id: int,
    confirm_username: str = Form(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    user = _target(db, user_id)
    if confirm_username.strip().lower() != user.username.lower():
        flash(request, "error.delete_confirm_mismatch", kind="error")
        return _back(user_id)
    username = user.username
    try:
        users.delete_user(db, admin, user)
    except users.UserError as exc:
        flash(request, exc.key, kind="error")
        return _back(user_id)
    flash(request, "admin.users.deleted", username=username)
    return _back()
