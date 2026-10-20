from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.config import Settings
from app.services import status
from app.web.deps import get_db, get_settings_dep, require_admin
from app.web.templating import render

router = APIRouter(prefix="/admin/status", dependencies=[Depends(require_admin)])


@router.get("")
def status_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    return render(request, "admin/status.html", {"status": status.collect(db, settings)})
