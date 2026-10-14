"""Finley's web app: landing page, accounts, lead finding, campaigns, review and settings.

Run with `finley serve`. Everyone signs in with their own account; each account sees only its own
campaigns, leads and settings. Set FINLEY_SIGNUPS=closed to allow only the first account (a private
self-hosted install). Routes live in finley/web/; this file wires them together.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .db import DB
from .vault import Vault, vault_from_env
from .web import account, finder, pages, public
from .web.deps import LoginRequired, Services

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, db: DB | None = None, llm=None, fetch=None,
               vault: Vault | None = None, sourcing_fetch=None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = db or DB(settings.db_path)
    app = FastAPI(title="Finley", docs_url=None, redoc_url=None)
    app.state.services = Services(
        settings=settings, db=db, vault=vault or vault_from_env(settings.db_path), llm=llm, fetch=fetch,
        sourcing_fetch=sourcing_fetch, signups_open=os.environ.get("FINLEY_SIGNUPS", "open").lower() != "closed")

    @app.exception_handler(LoginRequired)
    def to_login(request: Request, exc: LoginRequired):
        if request.url.path.startswith("/api/"):
            from fastapi.responses import JSONResponse

            return JSONResponse({"error": "Log in first."}, status_code=401)
        return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    for module in (public, account, finder, pages):
        app.include_router(module.router)
    return app
