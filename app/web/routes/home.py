from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.models import User
from app.web.deps import require_user
from app.web.templating import render

router = APIRouter()


@router.get("/")
def home(request: Request, user: User = Depends(require_user)) -> Response:
    return render(request, "home.html")
