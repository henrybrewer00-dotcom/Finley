"""Public pages: the landing page."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from ..formats import FORMATS
from .deps import render, services

router = APIRouter()


@router.get("/")
def landing(request: Request):
    return render(request, "site/landing.html", n_formats=len(FORMATS))


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nAllow: /\nDisallow: /app\nDisallow: /api\n"


@router.get("/healthz")
def health(request: Request):
    """Liveness plus whether accounts persist and saved keys can be encrypted (no secrets shown)."""
    svc = services(request)
    return {"ok": True, "database": "postgres" if svc.db.is_postgres else "sqlite",
            "persistent": not svc.db.is_ephemeral, "secrets": svc.vault.enabled}
