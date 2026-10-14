"""Public pages: the landing page."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..formats import FORMATS
from .deps import render

router = APIRouter()


@router.get("/")
def landing(request: Request):
    return render(request, "site/landing.html", n_formats=len(FORMATS))


@router.get("/healthz")
def health():
    return {"ok": True}
