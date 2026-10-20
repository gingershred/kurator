from __future__ import annotations

from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.config import Settings
from app.feed.query import FeedFilters, FeedItem, Length, View, feed_page
from app.models import Channel, Subscription, User, Video
from app.playback import downloads
from app.services import channels, filters, video_state
from app.web.deps import get_db, get_settings_dep, require_user
from app.web.routes.auth import safe_next
from app.web.templating import render

router = APIRouter()


def _filters(request: Request) -> FeedFilters:
    params = request.query_params
    channel = params.get("channel", "")
    try:
        length = Length(params.get("length", ""))
    except ValueError:
        length = Length.ANY
    return FeedFilters(
        include_watched=params.get("all") == "1",
        channel_id=int(channel) if channel.isdigit() else None,
        length=length,
    )


def _query_string(filters_: FeedFilters, **extra: str) -> str:
    params = {
        "all": "1" if filters_.include_watched else "",
        "channel": str(filters_.channel_id or ""),
        "length": filters_.length.value,
        **extra,
    }
    return urlencode({k: v for k, v in params.items() if v})


def _show(request: Request, view: View, user: User, db: Session, settings: Settings) -> Response:
    filters_ = _filters(request)
    cursor = request.query_params.get("cursor")
    page = feed_page(
        db,
        user,
        view,
        filters_,
        cursor=cursor,
        require_enriched=settings.youtube_api_key is not None,
    )
    path = {View.FEED: "/", View.LATER: "/later", View.HIDDEN: "/hidden"}[view]
    next_url = (
        f"{path}?{_query_string(filters_, cursor=page.next_cursor)}" if page.next_cursor else None
    )
    context = {
        "view": view.value,
        "path": path,
        "page": page,
        "next_url": next_url,
        "filters": filters_,
        "lengths": [length.value for length in Length if length != Length.ANY],
        "subscriptions": channels.list_subscriptions(db, user),
        "channel_titles": filters.channel_titles(db, user),
        "api_available": settings.youtube_api_key is not None,
        "download_badges": (
            downloads.badges(db, user, [item.video.id for item in page.items])
            if downloads.user_allowed(db, settings, user)
            else {}
        ),
    }
    if cursor and request.headers.get("HX-Request"):
        return render(request, "feed/_page.html", context)
    return render(request, "feed/index.html", context)


@router.get("/")
def feed(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    return _show(request, View.FEED, user, db, settings)


@router.get("/later")
def later(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    return _show(request, View.LATER, user, db, settings)


@router.get("/hidden")
def hidden(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    return _show(request, View.HIDDEN, user, db, settings)


ACTIONS = {"watched", "unwatched", "hide", "unhide", "later", "unlater", "rate"}


def _local_referer(request: Request) -> str:
    """Path of the referring page, never another host."""
    referer = urlparse(request.headers.get("referer", "/"))
    path = referer.path if referer.path.startswith("/") else "/"
    return safe_next(path + (f"?{referer.query}" if referer.query else ""))


def own_video(db: Session, user: User, youtube_video_id: str) -> Video:
    video = video_state.video_for_user(db, user, youtube_video_id)
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return video


@router.post("/videos/{youtube_video_id}/{action}")
def video_action(
    request: Request,
    youtube_video_id: str,
    action: str,
    rating: int = Form(0),
    view: str = Form("feed"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    if action not in ACTIONS:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    video = own_video(db, user, youtube_video_id)
    match action:
        case "watched" | "unwatched":
            video_state.set_watched(db, user, video, action == "watched")
        case "hide" | "unhide":
            video_state.set_hidden(db, user, video, action == "hide")
        case "later" | "unlater":
            video_state.set_watch_later(db, user, video, action == "later")
        case "rate":
            if rating not in (-1, 0, 1):
                raise HTTPException(status.HTTP_400_BAD_REQUEST)
            video_state.set_rating(db, user, video, rating)
    if not request.headers.get("HX-Request"):
        return RedirectResponse(_local_referer(request), status.HTTP_303_SEE_OTHER)
    channel = db.get(Channel, video.channel_id)
    sub = db.query(Subscription).filter_by(user_id=user.id, channel_id=video.channel_id).first()
    assert channel is not None  # noqa: S101  (FK)
    item = FeedItem(video, channel, sub, video_state.get_state(db, user, video))
    template = "videos/_actions.html" if view == "detail" else "feed/_card.html"
    return render(
        request,
        template,
        {"item": item, "view": view, "channel_titles": filters.channel_titles(db, user)},
    )
