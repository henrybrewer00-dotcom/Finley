"""Shared plumbing for every web router: app services, the signed-in user, and page rendering.

Routers never build their own DB or LLM. They read them from `request.app.state.services`, so
tests inject fakes in one place (`create_app(db=..., llm=...)`).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import accounts
from ..accounts import SESSION_COOKIE
from ..config import Settings
from ..db import DB
from ..scoring import score_band
from ..vault import Vault

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["fromjson"] = lambda s: json.loads(s or "{}") if isinstance(s, str) else (s or {})
templates.env.filters["money"] = lambda v: f"${(v or 0):,.2f}"
templates.env.filters["pct"] = lambda v: "–" if v is None else f"{v:g}%"
templates.env.globals["band"] = score_band

# Sidebar navigation. Each page passes `active` with one of these keys.
NAV = [
    {"key": "dashboard", "label": "Dashboard", "href": "/app", "icon": "home"},
    {"key": "find", "label": "Find leads", "href": "/app/find", "icon": "search"},
    {"key": "campaigns", "label": "Campaigns", "href": "/app/campaigns", "icon": "send"},
    {"key": "review", "label": "Review", "href": "/app/review", "icon": "inbox"},
]
NAV_BOTTOM = [{"key": "settings", "label": "Settings", "href": "/app/settings", "icon": "settings"}]


@dataclass
class Services:
    settings: Settings
    db: DB
    vault: Vault
    llm: Any = None              # injected client (tests); None = build one per request from the user's key
    fetch: Any = None            # injected website fetcher for research (tests)
    sourcing_fetch: Any = None   # injected website fetcher for lead finding (tests)
    signups_open: bool = True


class LoginRequired(Exception):
    """Raised by `require_user`; the app turns it into a redirect to /login."""


def services(request: Request) -> Services:
    return request.app.state.services


def current_user(request: Request) -> dict | None:
    """The signed-in user ({id, email, name, created_at}) or None. Cached per request."""
    if not hasattr(request.state, "user"):
        row = accounts.session_user(services(request).db, request.cookies.get(SESSION_COOKIE))
        request.state.user = dict(row) if row else None
    return request.state.user


def require_user(request: Request) -> dict:
    user = current_user(request)
    if user is None:
        raise LoginRequired()
    return user


def user_settings(request: Request) -> Settings:
    """Server settings with the user's sender profile and inbox laid over them."""
    svc = services(request)
    return accounts.runtime_settings(svc.settings, svc.db, svc.vault, require_user(request)["id"])


def profile(request: Request) -> dict:
    """The user's saved settings for display (secrets only as has_* flags)."""
    svc = services(request)
    return accounts.get_settings(svc.db, svc.vault, require_user(request)["id"])


def api_key_for(request: Request) -> str:
    """The user's saved Anthropic key, else the server's (self-hosted installs)."""
    svc = services(request)
    key = accounts.anthropic_key(svc.db, svc.vault, require_user(request)["id"])
    return (key or os.environ.get("ANTHROPIC_API_KEY", "")).strip()


def llm_for(request: Request, demo: bool = False, api_key: str = ""):
    """An LLM client for this request, or None when there's no key and demo mode is off."""
    from ..llm import FakeLLM, make_llm

    svc = services(request)
    if svc.llm is not None:
        return svc.llm
    if demo:
        return FakeLLM()
    key = api_key.strip() or api_key_for(request)
    return make_llm(api_key=key) if key else None


def owned_campaign(request: Request, cid: int):
    c = services(request).db.one("SELECT * FROM campaigns WHERE id = ? AND user_id = ?",
                                 cid, require_user(request)["id"])
    if c is None:
        raise HTTPException(404)
    return c


def owned_email(request: Request, eid: int):
    e = services(request).db.one("SELECT e.* FROM emails e JOIN campaigns c ON c.id = e.campaign_id "
                                 "WHERE e.id = ? AND c.user_id = ?", eid, require_user(request)["id"])
    if e is None:
        raise HTTPException(404)
    return e


def review_count(request: Request) -> int:
    user = current_user(request)
    if not user:
        return 0
    row = services(request).db.one(
        "SELECT COUNT(*) AS n FROM emails e JOIN campaigns c ON c.id = e.campaign_id "
        "WHERE c.user_id = ? AND e.status IN ('draft', 'needs_review')", user["id"])
    return int(row["n"] or 0)


def render(request: Request, template: str, active: str = "", status_code: int = 200,
           **ctx: Any) -> HTMLResponse:
    """Render a page with the shared context every layout needs."""
    svc = services(request)
    base = {
        "user": current_user(request),
        "active": active,
        "nav": NAV,
        "nav_bottom": NAV_BOTTOM,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "ephemeral": svc.db.is_ephemeral,
        "review_count": review_count(request) if template.startswith("app/") else 0,
    }
    return templates.TemplateResponse(request, template, {**base, **ctx}, status_code=status_code)


def redirect(url: str, msg: str = "", err: str = "", anchor: str = "") -> RedirectResponse:
    q = urlencode({k: v for k, v in (("msg", msg), ("err", err)) if v}, quote_via=quote)
    url = url + (("&" if "?" in url else "?") + q if q else "")
    return RedirectResponse(url + (f"#{anchor}" if anchor else ""), status_code=303)


def safe_next(url: str, default: str = "/app") -> str:
    return url if url.startswith("/") and not url.startswith("//") else default
