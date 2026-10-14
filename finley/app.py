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
ERROR_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Something went wrong · Finley</title><link rel="stylesheet" href="/static/finley.css">
<main class="auth"><div class="card card-pad center stack" style="max-width:420px"><h1>Something went wrong</h1>
<p class="muted">Finley hit an error loading this page. Try again in a moment.</p>
<div><a class="btn btn-primary" href="/app">Back to Finley</a></div></div></main>"""


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

    @app.exception_handler(Exception)
    def server_error(request: Request, exc: Exception):
        import logging
        import traceback

        from fastapi.responses import HTMLResponse, JSONResponse

        # Log the end of the traceback on one line so serverless log viewers don't cut it off.
        tail = "".join(traceback.format_exception(exc))[-1800:].replace("\n", " | ")
        logging.getLogger("finley").error("finley-error %s %s: %s", request.method, request.url.path, tail)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "Something went wrong on our side. Try again in a moment."}, status_code=500)
        return HTMLResponse(ERROR_PAGE, status_code=500)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    for module in (public, account, finder, pages):
        app.include_router(module.router)
    return app
