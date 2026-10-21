from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Download, User
from app.playback import downloads
from app.web.deps import get_db, get_settings_dep, require_admin
from app.web.templating import flash, render

router = APIRouter(prefix="/admin/downloads", dependencies=[Depends(require_admin)])


def _back() -> RedirectResponse:
    return RedirectResponse("/admin/downloads", status.HTTP_303_SEE_OTHER)


def _download(db: Session, download_id: int) -> Download:
    download = db.get(Download, download_id)
    if download is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return download


@router.get("")
def overview(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    context = {
        "values": downloads.admin_settings(db, settings),
        "qualities": downloads.QUALITIES,
        "used": downloads.storage_used(db),
        "limit": downloads.storage_limit_bytes(db, settings),
        "groups": downloads.admin_overview(db),
        "tooling": downloads.tooling(),
    }
    return render(request, "admin/downloads.html", context)


@router.post("/settings")
def save(
    request: Request,
    enabled: str = Form(""),
    max_quality: int = Form(...),
    max_storage_gb: float = Form(...),
    retention_days: int = Form(...),
    concurrency: int = Form(...),
    fallback_percent: int = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    values = downloads.AdminSettings(
        enabled=enabled == "on",
        max_quality=max_quality,
        max_storage_gb=max_storage_gb,
        retention_days=retention_days,
        concurrency=concurrency,
        fallback_percent=fallback_percent,
    )
    try:
        downloads.save_admin_settings(db, admin, values)
    except downloads.DownloadError as exc:
        flash(request, exc.key, kind="error")
    else:
        flash(request, "settings.saved")
    return _back()


@router.post("/{download_id}/delete")
def delete(
    request: Request,
    download_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    downloads.delete_by_admin(db, settings, _download(db, download_id))
    flash(request, "admin.downloads.deleted")
    return _back()


@router.post("/{download_id}/retry")
def retry(request: Request, download_id: int, db: Session = Depends(get_db)) -> Response:
    downloads.retry(db, _download(db, download_id))
    flash(request, "admin.downloads.retried")
    return _back()
