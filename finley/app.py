"""Web dashboard: accounts, settings, campaigns, CSV import, pipeline runs, the review queue and metrics.

Run with `finley serve`. Everyone signs in with their own account; each account sees only its own
campaigns, leads and settings. Set FINLEY_SIGNUPS=closed to allow only the first account (a private
self-hosted install).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import accounts
from .accounts import SESSION_COOKIE, AccountError
from .config import Settings
from .db import DB, now
from .formats import FORMATS
from .llm import make_llm
from .metrics import campaign_metrics
from .pipeline import run_campaign
from .scoring import score_band
from .sender import OUTCOMES, SendError, record_outcome, send_email
from .sources.csv_import import import_csv
from .vault import Vault, vault_from_env

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["fromjson"] = lambda s: json.loads(s or "{}")
templates.env.globals["band"] = score_band


class LoginRequired(Exception):
    pass


def create_app(settings: Settings | None = None, db: DB | None = None, llm=None, fetch=None,
               vault: Vault | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = db or DB(settings.db_path)
    vault = vault or vault_from_env(settings.db_path)
    signups_open = os.environ.get("FINLEY_SIGNUPS", "open").lower() != "closed"
    app = FastAPI(title="Finley")
    templates.env.globals["ephemeral"] = db.is_ephemeral

    @app.exception_handler(LoginRequired)
    def to_login(request: Request, exc: LoginRequired):
        return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)

    def current_user(request: Request):
        user = accounts.session_user(db, request.cookies.get(SESSION_COOKIE))
        if user is None:
            raise LoginRequired()
        return user

    def render(request: Request, name: str, ctx: dict, user=None):
        return templates.TemplateResponse(request, name, {"user": user, **ctx})

    def own_campaign(cid: int, user):
        c = db.one("SELECT * FROM campaigns WHERE id = ? AND user_id = ?", cid, user["id"])
        if c is None:
            raise HTTPException(404)
        return c

    def own_email(eid: int, user):
        e = db.one("SELECT e.* FROM emails e JOIN campaigns c ON c.id = e.campaign_id "
                   "WHERE e.id = ? AND c.user_id = ?", eid, user["id"])
        if e is None:
            raise HTTPException(404)
        return e

    def user_settings(user) -> Settings:
        return accounts.runtime_settings(settings, db, vault, user["id"])

    def back(cid: int, msg: str = "") -> RedirectResponse:
        return RedirectResponse(f"/campaigns/{cid}" + (f"?msg={quote(msg)}" if msg else ""), status_code=303)

    def logged_in(request: Request, user_id: int, to: str = "/") -> RedirectResponse:
        if not to.startswith("/") or to.startswith("//"):
            to = "/"
        resp = RedirectResponse(to, status_code=303)
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        resp.set_cookie(SESSION_COOKIE, accounts.start_session(db, user_id), max_age=accounts.SESSION_DAYS * 86400,
                        httponly=True, samesite="lax", secure=secure)
        return resp

    # --- accounts ---------------------------------------------------------
    @app.get("/signup", response_class=HTMLResponse)
    def signup_page(request: Request, next: str = "/"):
        closed = not signups_open and db.one("SELECT 1 FROM users") is not None
        return render(request, "signup.html", {"error": "", "closed": closed, "next": next, "form": {}})

    @app.post("/signup")
    def signup(request: Request, email: str = Form(...), password: str = Form(...), name: str = Form(""),
               next: str = Form("/")):
        if not signups_open and db.one("SELECT 1 FROM users") is not None:
            raise HTTPException(403, "Sign-ups are closed on this server.")
        try:
            uid = accounts.create_user(db, email, password, name)
        except AccountError as e:
            return render(request, "signup.html", {"error": str(e), "closed": False, "next": next,
                                                   "form": {"email": email, "name": name}})
        return logged_in(request, uid, "/settings?welcome=1")

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/"):
        return render(request, "login.html", {"error": "", "next": next, "email": "", "signups_open": signups_open})

    @app.post("/login")
    def login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/")):
        uid = accounts.authenticate(db, email, password)
        if uid is None:
            return render(request, "login.html", {"error": "That email and password don't match.", "next": next,
                                                  "email": email, "signups_open": signups_open})
        return logged_in(request, uid, next)

    @app.post("/logout")
    def logout(request: Request):
        accounts.end_session(db, request.cookies.get(SESSION_COOKIE))
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    # --- settings ---------------------------------------------------------
    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, user=Depends(current_user), msg: str = "", error: str = "",
                      welcome: bool = False):
        return render(request, "settings.html", {
            "s": accounts.get_settings(db, vault, user["id"]), "msg": msg, "error": error, "welcome": welcome,
            "can_store_secrets": vault.enabled,
        }, user)

    @app.post("/settings")
    async def save_settings(request: Request, user=Depends(current_user)):
        form = dict(await request.form())
        section = form.pop("section", "")
        try:
            accounts.save_settings(db, vault, user["id"], form)
        except AccountError as e:
            return RedirectResponse(f"/settings?error={quote(str(e))}#{section}", status_code=303)
        return RedirectResponse(f"/settings?msg=Saved.#{section}", status_code=303)

    @app.post("/settings/password")
    def password(user=Depends(current_user), current: str = Form(...), new: str = Form(...)):
        try:
            accounts.change_password(db, user["id"], current, new)
        except AccountError as e:
            return RedirectResponse(f"/settings?error={quote(str(e))}#account", status_code=303)
        return RedirectResponse("/settings?msg=Password changed.#account", status_code=303)

    @app.post("/settings/delete-account")
    def delete_account(user=Depends(current_user), confirm_email: str = Form(...)):
        if confirm_email.strip().lower() != user["email"]:
            return RedirectResponse("/settings?error=Type your email to confirm.#account", status_code=303)
        accounts.delete_user(db, user["id"])
        resp = RedirectResponse("/signup", status_code=303)
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    @app.get("/api/me")
    def me(user=Depends(current_user)):
        s = accounts.get_settings(db, vault, user["id"])
        return {"id": user["id"], "email": user["email"], "name": user["name"], "settings": s}

    # --- campaigns --------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, user=Depends(current_user)):
        campaigns = db.all("SELECT c.*, (SELECT COUNT(*) FROM leads l WHERE l.campaign_id = c.id) AS n "
                           "FROM campaigns c WHERE c.user_id = ? ORDER BY id DESC", user["id"])
        return render(request, "home.html", {"campaigns": campaigns,
                                             "s": accounts.get_settings(db, vault, user["id"])}, user)

    @app.post("/campaigns")
    def create_campaign(user=Depends(current_user), name: str = Form(...), offer: str = Form(...),
                        icp: str = Form(...), proof: str = Form(""), voice_samples: str = Form("")):
        cid = db.insert("campaigns", {"user_id": user["id"], "name": name, "offer": offer, "icp": icp,
                                      "proof": proof, "voice_samples": voice_samples, "created_at": now()})
        return back(cid)

    @app.get("/campaigns/{cid}", response_class=HTMLResponse)
    def campaign(request: Request, cid: int, user=Depends(current_user), msg: str = ""):
        c = own_campaign(cid, user)
        leads = db.all("SELECT * FROM leads WHERE campaign_id = ? ORDER BY score DESC NULLS LAST, id LIMIT 500", cid)
        emails = db.all("SELECT e.*, l.email AS to_email, l.first_name, l.company FROM emails e "
                        "JOIN leads l ON l.id = e.lead_id WHERE e.campaign_id = ? ORDER BY e.id DESC", cid)
        s = accounts.get_settings(db, vault, user["id"])
        return render(request, "campaign.html", {
            "c": c, "leads": leads, "emails": emails, "m": campaign_metrics(db, cid), "msg": msg,
            "formats": FORMATS, "outcomes": OUTCOMES, "can_send": bool(user_settings(user).smtp_host),
            "s": s, "server_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        }, user)

    @app.post("/campaigns/{cid}/import")
    async def import_leads(cid: int, user=Depends(current_user), file: UploadFile = File(...)):
        own_campaign(cid, user)
        text = (await file.read()).decode("utf-8-sig", "ignore")
        try:
            counts = import_csv(db, cid, text, source=f"csv:{file.filename}")
        except ValueError as e:
            return back(cid, str(e))
        return back(cid, ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items()))

    @app.post("/campaigns/{cid}/run")
    def run(cid: int, background: BackgroundTasks, user=Depends(current_user), limit: int = Form(25),
            min_score: float | None = Form(None), exclude_domains: str = Form(""), dry_run: bool = Form(False),
            api_key: str = Form("")):
        own_campaign(cid, user)
        prefs = accounts.get_settings(db, vault, user["id"])
        # A key typed into the form is used for this run only; otherwise the key saved in Settings.
        key = api_key.strip() or accounts.anthropic_key(db, vault, user["id"])
        if not (dry_run or key or os.environ.get("ANTHROPIC_API_KEY") or llm):
            return back(cid, "Add your Anthropic API key in Settings, or tick Dry run.")
        client = llm or make_llm(dry_run=dry_run, api_key=key)
        # Saved exclusions (customers, competitors) always apply; the form can add more.
        excluded = ", ".join(x for x in (prefs["exclude_domains"], exclude_domains) if x)
        kwargs = dict(limit=limit, min_score=prefs["min_score"] if min_score is None else min_score,
                      exclude_domains=excluded, **({"fetch": fetch} if fetch else {}))
        run_settings = user_settings(user)
        if os.environ.get("VERCEL"):
            # Serverless functions stop after the response, so run inline there.
            counts = run_campaign(db, client, run_settings, cid, **kwargs)
            return back(cid, ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items()))
        background.add_task(run_campaign, db, client, run_settings, cid, **kwargs)
        return back(cid, f"Running up to {limit} leads. Refresh to see drafts.")

    @app.post("/emails/{eid}/edit")
    def edit(eid: int, user=Depends(current_user), subject: str = Form(...), body: str = Form(...)):
        e = own_email(eid, user)
        db.update("emails", eid, {"subject": subject, "body": body})
        db.log_event("email_edited", campaign_id=e["campaign_id"], email_id=eid)
        return back(e["campaign_id"], "Saved.")

    @app.post("/emails/{eid}/status")
    def set_status(eid: int, user=Depends(current_user), status: str = Form(...)):
        e = own_email(eid, user)
        if status not in ("approved", "rejected", "draft"):
            raise HTTPException(400)
        db.update("emails", eid, {"status": status})
        db.log_event(f"email_{status}", campaign_id=e["campaign_id"], email_id=eid)
        return back(e["campaign_id"])

    @app.post("/campaigns/{cid}/approve-all")
    def approve_all(cid: int, user=Depends(current_user), min_judge: float = Form(4.0)):
        own_campaign(cid, user)
        rows = db.all("SELECT id FROM emails WHERE campaign_id = ? AND status = 'draft' AND judge_score >= ?",
                      cid, min_judge)
        for r in rows:
            db.update("emails", r["id"], {"status": "approved"})
        return back(cid, f"Approved {len(rows)} drafts.")

    @app.post("/campaigns/{cid}/send")
    def send_approved(cid: int, user=Depends(current_user)):
        own_campaign(cid, user)
        send_settings = user_settings(user)
        sent, errors = 0, []
        for r in db.all("SELECT id FROM emails WHERE campaign_id = ? AND status = 'approved'", cid):
            try:
                send_email(db, send_settings, r["id"])
                sent += 1
            except SendError as e:
                errors.append(str(e))
                if "Daily cap" in str(e) or "sending inbox" in str(e):
                    break
        return back(cid, f"Sent {sent}." + (f" Stopped: {errors[-1]}" if errors else ""))

    @app.post("/emails/{eid}/outcome")
    def outcome(eid: int, user=Depends(current_user), outcome: str = Form(...)):
        e = own_email(eid, user)
        record_outcome(db, eid, outcome)
        return back(e["campaign_id"], f"Logged {outcome.replace('_', ' ')}.")

    @app.get("/campaigns/{cid}/metrics.json")
    def metrics_json(cid: int, user=Depends(current_user)):
        own_campaign(cid, user)
        return campaign_metrics(db, cid)

    return app
