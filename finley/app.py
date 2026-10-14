"""Local web dashboard: campaigns, CSV import, pipeline runs, the review queue and metrics.

Run with `finley serve`. It binds to 127.0.0.1 by default; set FINLEY_PASSWORD to require a
password if you expose it.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from .config import Settings
from .db import DB, now
from .formats import FORMATS
from .llm import make_llm
from .metrics import campaign_metrics
from .pipeline import run_campaign
from .scoring import score_band
from .sender import OUTCOMES, SendError, record_outcome, send_email
from .sources.csv_import import import_csv

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["fromjson"] = lambda s: json.loads(s or "{}")
templates.env.globals["band"] = score_band
security = HTTPBasic(auto_error=False)


def create_app(settings: Settings | None = None, db: DB | None = None, llm=None, fetch=None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = db or DB(settings.db_path)
    password = os.environ.get("FINLEY_PASSWORD", "")
    app = FastAPI(title="Finley")

    def auth(creds: HTTPBasicCredentials | None = Depends(security)) -> None:
        if password and not (creds and secrets.compare_digest(creds.password, password)):
            raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})

    def back(cid: int, msg: str = "") -> RedirectResponse:
        return RedirectResponse(f"/campaigns/{cid}" + (f"?msg={msg}" if msg else ""), status_code=303)

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def home(request: Request):
        campaigns = db.all("SELECT c.*, (SELECT COUNT(*) FROM leads l WHERE l.campaign_id = c.id) AS n "
                           "FROM campaigns c ORDER BY id DESC")
        return templates.TemplateResponse(request, "home.html", {"campaigns": campaigns})

    @app.post("/campaigns", dependencies=[Depends(auth)])
    def create_campaign(name: str = Form(...), offer: str = Form(...), icp: str = Form(...),
                        proof: str = Form(""), voice_samples: str = Form("")):
        cid = db.insert("campaigns", {"name": name, "offer": offer, "icp": icp, "proof": proof,
                                      "voice_samples": voice_samples, "created_at": now()})
        return back(cid)

    @app.get("/campaigns/{cid}", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def campaign(request: Request, cid: int, msg: str = ""):
        c = db.one("SELECT * FROM campaigns WHERE id = ?", cid)
        if c is None:
            raise HTTPException(404)
        leads = db.all("SELECT * FROM leads WHERE campaign_id = ? ORDER BY score DESC NULLS LAST, id LIMIT 500", cid)
        emails = db.all("SELECT e.*, l.email AS to_email, l.first_name, l.company FROM emails e "
                        "JOIN leads l ON l.id = e.lead_id WHERE e.campaign_id = ? ORDER BY e.id DESC", cid)
        return templates.TemplateResponse(request, "campaign.html", {
            "c": c, "leads": leads, "emails": emails, "m": campaign_metrics(db, cid), "msg": msg,
            "formats": FORMATS, "outcomes": OUTCOMES, "can_send": bool(settings.smtp_host),
        })

    @app.post("/campaigns/{cid}/import", dependencies=[Depends(auth)])
    async def import_leads(cid: int, file: UploadFile = File(...)):
        text = (await file.read()).decode("utf-8-sig", "ignore")
        try:
            counts = import_csv(db, cid, text, source=f"csv:{file.filename}")
        except ValueError as e:
            return back(cid, str(e))
        return back(cid, ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items()))

    @app.post("/campaigns/{cid}/run", dependencies=[Depends(auth)])
    def run(cid: int, background: BackgroundTasks, limit: int = Form(25), min_score: float = Form(50),
            exclude_domains: str = Form(""), dry_run: bool = Form(False)):
        client = llm or make_llm(dry_run=dry_run)
        background.add_task(run_campaign, db, client, settings, cid, limit=limit, min_score=min_score,
                            exclude_domains=exclude_domains, **({"fetch": fetch} if fetch else {}))
        return back(cid, f"Running up to {limit} leads. Refresh to see drafts.")

    @app.post("/emails/{eid}/edit", dependencies=[Depends(auth)])
    def edit(eid: int, subject: str = Form(...), body: str = Form(...)):
        e = db.one("SELECT * FROM emails WHERE id = ?", eid)
        db.update("emails", eid, {"subject": subject, "body": body})
        db.log_event("email_edited", campaign_id=e["campaign_id"], email_id=eid)
        return back(e["campaign_id"], "Saved.")

    @app.post("/emails/{eid}/status", dependencies=[Depends(auth)])
    def set_status(eid: int, status: str = Form(...)):
        e = db.one("SELECT * FROM emails WHERE id = ?", eid)
        if status not in ("approved", "rejected", "draft"):
            raise HTTPException(400)
        db.update("emails", eid, {"status": status})
        db.log_event(f"email_{status}", campaign_id=e["campaign_id"], email_id=eid)
        return back(e["campaign_id"])

    @app.post("/campaigns/{cid}/approve-all", dependencies=[Depends(auth)])
    def approve_all(cid: int, min_judge: float = Form(4.0)):
        rows = db.all("SELECT id FROM emails WHERE campaign_id = ? AND status = 'draft' AND judge_score >= ?",
                      cid, min_judge)
        for r in rows:
            db.update("emails", r["id"], {"status": "approved"})
        return back(cid, f"Approved {len(rows)} drafts.")

    @app.post("/campaigns/{cid}/send", dependencies=[Depends(auth)])
    def send_approved(cid: int):
        sent, errors = 0, []
        for r in db.all("SELECT id FROM emails WHERE campaign_id = ? AND status = 'approved'", cid):
            try:
                send_email(db, settings, r["id"])
                sent += 1
            except SendError as e:
                errors.append(str(e))
                if "Daily cap" in str(e) or "Set SMTP" in str(e):
                    break
        return back(cid, f"Sent {sent}." + (f" Stopped: {errors[-1]}" if errors else ""))

    @app.post("/emails/{eid}/outcome", dependencies=[Depends(auth)])
    def outcome(eid: int, outcome: str = Form(...)):
        e = db.one("SELECT * FROM emails WHERE id = ?", eid)
        record_outcome(db, eid, outcome)
        return back(e["campaign_id"], f"Logged {outcome.replace('_', ' ')}.")

    @app.get("/campaigns/{cid}/metrics.json", dependencies=[Depends(auth)])
    def metrics_json(cid: int):
        return campaign_metrics(db, cid)

    return app
