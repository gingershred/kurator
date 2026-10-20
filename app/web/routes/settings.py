from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import FilterRule, FilterRuleType, User
from app.playback import downloads
from app.playback.embed import PREFERENCES
from app.services import filters
from app.services import settings as user_settings
from app.web.deps import get_db, get_settings_dep, require_user
from app.web.labels import rule_label
from app.web.templating import flash, render

router = APIRouter(prefix="/settings")

TOGGLES = ("show_livestreams", "show_premieres")


def _back() -> RedirectResponse:
    return RedirectResponse("/settings", status.HTTP_303_SEE_OTHER)


def _own_rule(db: Session, user: User, rule_id: int) -> FilterRule:
    rule = filters.get_rule(db, user, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return rule


@router.get("")
def settings_page(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    titles = filters.channel_titles(db, user)
    rules = [
        (rule, rule_label(rule.type, rule.value, titles)) for rule in filters.list_rules(db, user)
    ]
    context = {
        "values": user_settings.get_user_settings(db, user.id),
        "toggles": TOGGLES,
        "rules": rules,
        "rule_types": [t.value for t in FilterRuleType],
        "channels": titles,
        "downloads_allowed": downloads.user_allowed(db, settings, user),
        "playback_modes": PREFERENCES,
    }
    return render(request, "settings/index.html", context)


@router.post("")
async def save_settings(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    form = await request.form()
    for key in TOGGLES:
        user_settings.set_user_setting(db, user.id, key, form.get(key) == "on")
    mode = form.get("playback_mode")
    if isinstance(mode, str) and mode in PREFERENCES and downloads.user_allowed(db, settings, user):
        user_settings.set_user_setting(db, user.id, "playback_mode", mode)
    flash(request, "settings.saved")
    return _back()


@router.post("/rules")
def add_rule(
    request: Request,
    type: str = Form(...),
    value: str = Form(""),
    channel: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    raw = channel if type == FilterRuleType.CHANNEL.value else value
    try:
        filters.create_rule(db, user, type, raw)
    except filters.RuleError as exc:
        flash(request, exc.key, kind="error")
    else:
        flash(request, "settings.rule_added")
    return _back()


@router.post("/rules/{rule_id}/toggle")
def toggle_rule(
    request: Request,
    rule_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    rule = _own_rule(db, user, rule_id)
    filters.set_active(db, rule, not rule.is_active)
    return _back()


@router.post("/rules/{rule_id}/delete")
def delete_rule(
    request: Request,
    rule_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    filters.delete_rule(db, _own_rule(db, user, rule_id))
    flash(request, "settings.rule_deleted")
    return _back()
