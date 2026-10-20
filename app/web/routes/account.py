from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.auth.sessions import refresh_session
from app.models import User
from app.services import users
from app.web.deps import get_db, require_user
from app.web.i18n import translate
from app.web.templating import flash, render

router = APIRouter(prefix="/account")


@router.get("")
def account(request: Request, user: User = Depends(require_user)) -> Response:
    return render(request, "account/index.html")


@router.post("/password")
def change_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    new_password_repeat: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        if new_password != new_password_repeat:
            raise users.UserError("error.password_mismatch")
        users.change_own_password(db, user, current_password, new_password)
    except users.UserError as exc:
        return render(request, "account/index.html", {"error": translate(exc.key)}, 400)
    # Other sessions of this user are now invalid; keep the current one.
    refresh_session(request.session, user)
    flash(request, "account.password_changed")
    return RedirectResponse("/account", status.HTTP_303_SEE_OTHER)
