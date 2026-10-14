"""Shared plumbing for every web router: app services, the current user, and page rendering.

Routers never build their own DB or LLM. They read them from `request.app.state` through
`services(request)`, so tests can inject fakes in one place (`create_app(db=..., llm=...)`).

The accounts work replaces `current_user` with real sessions. Until then Finley runs as a single
local user, which is what a self-hosted install wants anyway.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import Settings
from ..db import DB
from ..scoring import score_band

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["fromjson"] = lambda s: json.loads(s or "{}") if isinstance(s, str) else (s or {})
templates.env.filters["money"] = lambda v: f"${(v or 0):,.2f}"
templates.env.filters["pct"] = lambda v: "–" if v is None else f"{v:g}%"
templates.env.globals["band"] = score_band

# Sidebar navigation. Each router sets `active` to one of these keys.
NAV = [
    {"key": "dashboard", "label": "Dashboard", "href": "/app", "icon": "home"},
    {"key": "find", "label": "Find leads", "href": "/app/find", "icon": "search"},
    {"key": "campaigns", "label": "Campaigns", "href": "/app/campaigns", "icon": "send"},
    {"key": "review", "label": "Review", "href": "/app/review", "icon": "inbox"},
]
NAV_BOTTOM = [
    {"key": "settings", "label": "Settings", "href": "/app/settings", "icon": "settings"},
]


@dataclass
class Services:
    settings: Settings
    db: DB
    llm: Any = None      # injected client (tests); None means build one per request from the user's key
    fetch: Any = None    # injected website fetcher (tests); None means the real one


def services(request: Request) -> Services:
    return request.app.state.services


def current_user(request: Request) -> dict | None:
    """The signed-in user as a dict with at least `id`, `name`, `email`, or None if signed out.

    Accounts plug in here: set `request.state.user` in middleware (or replace this function).
    Without accounts, every visitor is the single local user.
    """
    user = getattr(request.state, "user", None)
    if user is not None:
        return user
    if getattr(request.app.state, "accounts_enabled", False):
        return None
    return {"id": None, "name": "You", "email": ""}


def render(request: Request, template: str, active: str = "", status_code: int = 200,
           **ctx: Any) -> HTMLResponse:
    """Render a page with the shared context every layout needs."""
    base = {
        "user": current_user(request),
        "active": active,
        "nav": NAV,
        "nav_bottom": NAV_BOTTOM,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
    }
    return templates.TemplateResponse(request, template, {**base, **ctx}, status_code=status_code)


def redirect(url: str, msg: str = "", err: str = "") -> RedirectResponse:
    from urllib.parse import urlencode

    q = urlencode({k: v for k, v in (("msg", msg), ("err", err)) if v})
    return RedirectResponse(url + (("&" if "?" in url else "?") + q if q else ""), status_code=303)


def api_key_for(request: Request) -> str:
    """The Anthropic key to use for this request: the user's own (from settings), else the server's.

    Accounts store the user's key and expose it as `user["anthropic_api_key"]` (decrypted).
    """
    import os

    user = current_user(request) or {}
    return (user.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")).strip()


def llm_for(request: Request, demo: bool = False):
    """An LLM client for this request, or None when there is no key and demo mode is off."""
    from ..llm import FakeLLM, make_llm

    svc = services(request)
    if svc.llm is not None:
        return svc.llm
    if demo:
        return FakeLLM()
    key = api_key_for(request)
    return make_llm(api_key=key) if key else None


def user_id(request: Request):
    return (current_user(request) or {}).get("id")


def owned_campaign(request: Request, cid: int):
    """The campaign if it belongs to the current user (or there are no accounts), else 404."""
    from fastapi import HTTPException

    c = services(request).db.one("SELECT * FROM campaigns WHERE id = ?", cid)
    uid = user_id(request)
    if c is None or (uid is not None and c["user_id"] != uid):
        raise HTTPException(404)
    return c


def campaign_scope(request: Request) -> tuple[str, tuple]:
    """SQL fragment limiting `campaigns c` to the current user's rows."""
    uid = user_id(request)
    return ("c.user_id = ?", (uid,)) if uid is not None else ("1 = 1", ())
