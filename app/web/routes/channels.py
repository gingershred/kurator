from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.config import Settings
from app.importers.takeout import MAX_FILE_BYTES, parse_subscriptions_csv
from app.models import Channel, Subscription, User
from app.services import channels
from app.services.ingest import http_client
from app.web.deps import get_db, get_settings_dep, require_user
from app.web.templating import flash, render
from app.youtube.api import YouTubeApi

router = APIRouter(prefix="/channels")


def _back() -> RedirectResponse:
    return RedirectResponse("/channels", status.HTTP_303_SEE_OTHER)


def _own(db: Session, user: User, subscription_id: int) -> Subscription:
    sub = channels.get_subscription(db, user, subscription_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return sub


@router.get("")
def list_channels(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    rows = channels.list_subscriptions(db, user)
    return render(request, "channels/index.html", {"rows": rows})


@router.post("")
async def add_channel(
    request: Request,
    channel: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    session_factory = request.app.state.session_factory
    try:
        async with http_client() as client:
            sub = await channels.add_channel(
                db, YouTubeApi(settings, session_factory, client), user, channel
            )
    except channels.ChannelError as exc:
        flash(request, exc.key, kind="error")
        return _back()
    flash(request, "channels.added", title=_title(db, sub))
    return _back()


def _title(db: Session, sub: Subscription) -> str:
    channel = db.get(Channel, sub.channel_id)
    return (channel.title or channel.youtube_channel_id) if channel else ""


@router.post("/import")
async def import_takeout(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    content = await file.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        flash(request, "error.import_too_large", kind="error")
        return _back()
    rows = parse_subscriptions_csv(content)
    if not rows.channels:
        flash(request, "error.import_empty", kind="error")
        return _back()
    result = channels.import_channels(db, user, rows.channels)
    flash(
        request,
        "channels.imported",
        added=result.added,
        existing=result.existing,
        invalid=rows.invalid,
    )
    return _back()


@router.post("/{subscription_id}/mute")
def mute(
    request: Request,
    subscription_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    channels.set_muted(db, _own(db, user, subscription_id), True)
    return _back()


@router.post("/{subscription_id}/unmute")
def unmute(
    request: Request,
    subscription_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    channels.set_muted(db, _own(db, user, subscription_id), False)
    return _back()


@router.post("/{subscription_id}/remove")
def remove(
    request: Request,
    subscription_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    sub = _own(db, user, subscription_id)
    title = _title(db, sub)
    channels.unsubscribe(db, sub)
    flash(request, "channels.removed", title=title)
    return _back()
