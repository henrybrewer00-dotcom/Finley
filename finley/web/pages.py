"""The signed-in app: dashboard, campaigns, the review queue, and the per-lead run API."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from ..db import now
from ..formats import FORMATS
from ..metrics import REPLY_KINDS, campaign_metrics
from ..pipeline import process_lead
from ..scoring import parse_domains
from ..sender import OUTCOMES, SendError, record_outcome, send_email
from ..sources.csv_import import import_csv
from .deps import (api_key_for, llm_for, owned_campaign, owned_email, profile, redirect, render,
                   require_user, safe_next, services, user_settings)

router = APIRouter()

TABS = [("overview", "Overview"), ("leads", "Leads"), ("emails", "Emails"), ("results", "Results")]


def _campaigns(request: Request) -> list:
    uid = require_user(request)["id"]
    return services(request).db.all(
        "SELECT c.*, "
        "(SELECT COUNT(*) FROM leads l WHERE l.campaign_id = c.id) AS n_leads, "
        "(SELECT COUNT(*) FROM leads l WHERE l.campaign_id = c.id AND l.status = 'new') AS n_new, "
        "(SELECT COUNT(*) FROM emails e WHERE e.campaign_id = c.id) AS n_drafted, "
        "(SELECT COUNT(*) FROM emails e WHERE e.campaign_id = c.id AND e.status IN ('draft','needs_review')) AS n_review, "
        "(SELECT COUNT(*) FROM emails e WHERE e.campaign_id = c.id AND e.status = 'sent') AS n_sent "
        "FROM campaigns c WHERE c.user_id = ? ORDER BY c.id DESC", uid)


def overview(request: Request) -> dict:
    """Account-wide numbers for the dashboard."""
    db = services(request).db
    camps = _campaigns(request)
    ids = [c["id"] for c in camps]
    marks = ",".join("?" * len(ids)) or "NULL"
    one = lambda sql: db.one(sql.format(ids=marks), *ids)[0] or 0  # noqa: E731
    sent = int(one("SELECT COUNT(*) FROM emails WHERE campaign_id IN ({ids}) AND status = 'sent'"))
    kinds = "','".join(REPLY_KINDS)
    replies = int(one(f"SELECT COUNT(DISTINCT email_id) FROM events WHERE campaign_id IN ({{ids}}) AND kind IN ('{kinds}')"))
    meetings = int(one("SELECT COUNT(*) FROM events WHERE campaign_id IN ({ids}) AND kind = 'meeting_booked'"))
    cost = float(one("SELECT COALESCE(SUM(cost_usd), 0) FROM events WHERE campaign_id IN ({ids})"))

    # Activity for the last 14 days: emails drafted and sent per day.
    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    drafted_by_day = {d: 0 for d in days}
    sent_by_day = {d: 0 for d in days}
    for r in db.all(f"SELECT created_at, sent_at FROM emails WHERE campaign_id IN ({marks})", *ids):
        if (r["created_at"] or "")[:10] in drafted_by_day:
            drafted_by_day[r["created_at"][:10]] += 1
        if (r["sent_at"] or "")[:10] in sent_by_day:
            sent_by_day[r["sent_at"][:10]] += 1
    peak = max([1, *drafted_by_day.values(), *sent_by_day.values()])
    activity = [{"day": d, "label": datetime.fromisoformat(d).strftime("%b %-d"),
                 "drafted": drafted_by_day[d], "sent": sent_by_day[d],
                 "h_drafted": round(100 * drafted_by_day[d] / peak), "h_sent": round(100 * sent_by_day[d] / peak)}
                for d in days]
    return {
        "campaigns": camps,
        "leads": sum(c["n_leads"] for c in camps),
        "drafted": sum(c["n_drafted"] for c in camps),
        "to_review": sum(c["n_review"] for c in camps),
        "sent": sent, "replies": replies, "meetings": meetings,
        "reply_rate": round(100 * replies / sent, 1) if sent else None,
        "cost": round(cost, 2),
        "cost_per_meeting": round(cost / meetings, 2) if meetings else None,
        "activity": activity,
    }


def setup_steps(request: Request, ov: dict) -> list[dict]:
    """The getting-started checklist on the dashboard."""
    p = profile(request)
    return [
        {"done": bool(api_key_for(request)), "label": "Add your Anthropic API key",
         "hint": "Finley runs on your own key, so you pay cents per lead.", "href": "/app/settings#ai"},
        {"done": bool(p.get("company_description")), "label": "Describe your business",
         "hint": "Finley uses it to write about you accurately.", "href": "/app/settings#business"},
        {"done": ov["leads"] > 0, "label": "Find your first leads",
         "hint": "Describe your ideal customer in plain English.", "href": "/app/find"},
        {"done": ov["drafted"] > 0, "label": "Write your first emails",
         "hint": "Finley researches each lead and writes in a proven format.", "href": "/app/campaigns"},
        {"done": bool(p.get("smtp_host") and p.get("has_smtp_password")), "label": "Connect your inbox",
         "hint": "Send from your own Gmail or Outlook address.", "href": "/app/settings#sending"},
    ]


# --- pages ---------------------------------------------------------------------------------------

@router.get("/app")
def dashboard(request: Request):
    require_user(request)
    ov = overview(request)
    steps = setup_steps(request, ov)
    return render(request, "app/dashboard.html", active="dashboard", ov=ov, steps=steps,
                  steps_done=sum(s["done"] for s in steps))


@router.get("/app/campaigns")
def campaigns(request: Request):
    require_user(request)
    return render(request, "app/campaigns.html", active="campaigns", campaigns=_campaigns(request))


@router.get("/app/campaigns/new")
def new_campaign(request: Request, lead_list: str = ""):
    require_user(request)
    p = profile(request)
    return render(request, "app/campaign_new.html", active="campaigns",
                  profile={"offer": p.get("company_description", "")}, lead_list=lead_list)


@router.post("/app/campaigns")
def create_campaign(request: Request, name: str = Form(...), offer: str = Form(...), icp: str = Form(...),
                    proof: str = Form(""), voice_samples: str = Form("")):
    require_user(request)
    cid = services(request).db.insert("campaigns", {
        "user_id": require_user(request)["id"], "name": name.strip(), "offer": offer.strip(), "icp": icp.strip(),
        "proof": proof.strip(), "voice_samples": voice_samples.strip(), "created_at": now()})
    return redirect(f"/app/campaigns/{cid}", msg="Campaign created. Add leads to get started.")


@router.get("/app/campaigns/{cid}")
def campaign(request: Request, cid: int, tab: str = "overview"):
    c = owned_campaign(request, cid)
    db = services(request).db
    leads = db.all("SELECT * FROM leads WHERE campaign_id = ? ORDER BY score DESC NULLS LAST, id LIMIT 1000", cid)
    emails = db.all("SELECT e.*, l.email AS to_email, l.first_name, l.last_name, l.company, l.title, l.score "
                    "FROM emails e JOIN leads l ON l.id = e.lead_id WHERE e.campaign_id = ? ORDER BY e.id DESC", cid)
    s = user_settings(request)
    p = profile(request)
    return render(request, "app/campaign.html", active="campaigns", c=c, tab=tab, tabs=TABS,
                  leads=leads, emails=emails, m=campaign_metrics(db, cid), formats=FORMATS,
                  outcomes=OUTCOMES, can_send=bool(s.smtp_host and s.from_email),
                  new_leads=sum(1 for l in leads if l["status"] == "new"),
                  has_key=llm_for(request) is not None, prefs=p,
                  can_store_secrets=services(request).vault.enabled)


@router.post("/app/campaigns/{cid}/edit")
def edit_campaign(request: Request, cid: int, name: str = Form(...), offer: str = Form(...), icp: str = Form(...),
                  proof: str = Form(""), voice_samples: str = Form("")):
    owned_campaign(request, cid)
    services(request).db.update("campaigns", cid, {"name": name, "offer": offer, "icp": icp, "proof": proof,
                                                   "voice_samples": voice_samples})
    return redirect(f"/app/campaigns/{cid}?tab=overview", msg="Campaign saved.")


@router.post("/app/campaigns/{cid}/import")
async def import_leads(request: Request, cid: int, file: UploadFile = File(...)):
    owned_campaign(request, cid)
    text = (await file.read()).decode("utf-8-sig", "ignore")
    try:
        counts = import_csv(services(request).db, cid, text, source=f"csv:{file.filename}")
    except ValueError as e:
        return redirect(f"/app/campaigns/{cid}?tab=leads", err=str(e))
    summary = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items())
    return redirect(f"/app/campaigns/{cid}?tab=leads", msg=f"Import done: {summary}.")


@router.get("/app/campaigns/{cid}/export.csv")
def export_leads(request: Request, cid: int):
    owned_campaign(request, cid)
    rows = services(request).db.all("SELECT * FROM leads WHERE campaign_id = ? ORDER BY id", cid)
    cols = ["first_name", "last_name", "title", "company", "email", "website", "location", "score", "status", "source"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([r[k] for k in cols])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="finley-campaign-{cid}.csv"'})


# --- running: the browser walks the queue one lead per request -----------------------------------

@router.post("/app/campaigns/{cid}/run")
def run(request: Request, cid: int, limit: int = Form(25), min_score: float | None = Form(None),
        exclude_domains: str = Form(""), demo: bool = Form(False), api_key: str = Form("")):
    """No-JavaScript fallback: process up to `limit` leads in this request."""
    from ..pipeline import run_campaign

    owned_campaign(request, cid)
    llm = llm_for(request, demo=demo, api_key=api_key)
    if llm is None:
        return redirect(f"/app/campaigns/{cid}", err="Add your Anthropic API key in Settings first.")
    prefs = profile(request)
    fetch = services(request).fetch
    counts = run_campaign(services(request).db, llm, user_settings(request), cid, limit=limit,
                          min_score=float(prefs.get("min_score") or 50) if min_score is None else min_score,
                          exclude_domains=",".join(x for x in (prefs.get("exclude_domains"), exclude_domains) if x),
                          **({"fetch": fetch} if fetch else {}))
    return redirect(f"/app/campaigns/{cid}?tab=emails",
                    msg=", ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items()) + ".")


@router.get("/api/campaigns/{cid}/queue")
def queue(request: Request, cid: int, limit: int = 25):
    owned_campaign(request, cid)
    rows = services(request).db.all(
        "SELECT id, first_name, last_name, company FROM leads WHERE campaign_id = ? AND status = 'new' "
        "ORDER BY id LIMIT ?", cid, max(1, min(limit, 500)))
    return {"leads": [dict(r) for r in rows]}


@router.post("/api/leads/{lid}/process")
def process(request: Request, lid: int, min_score: float = Form(50), exclude_domains: str = Form(""),
            demo: bool = Form(False), api_key: str = Form("")):
    db = services(request).db
    lead = db.one("SELECT * FROM leads WHERE id = ?", lid)
    if lead is None:
        raise HTTPException(404)
    c = owned_campaign(request, lead["campaign_id"])
    llm = llm_for(request, demo=demo, api_key=api_key)
    if llm is None:
        return JSONResponse({"result": "errors", "error": "Add your Anthropic API key in Settings first."},
                            status_code=400)
    if lead["status"] != "new":
        return {"result": "skipped"}
    fetch = services(request).fetch
    saved = profile(request).get("exclude_domains") or ""  # customers and competitors from Settings
    return process_lead(db, llm, user_settings(request), c, lead, min_score=min_score,
                        excluded=parse_domains(f"{saved},{exclude_domains}"), **({"fetch": fetch} if fetch else {}))


# --- emails --------------------------------------------------------------------------------------

def _back(request: Request, e, msg: str = "", fallback_tab: str = "emails"):
    nxt = request.query_params.get("next")
    if nxt:
        return redirect(safe_next(nxt), msg=msg)
    return redirect(f"/app/campaigns/{e['campaign_id']}?tab={fallback_tab}", msg=msg)


@router.post("/app/emails/{eid}/edit")
def edit_email(request: Request, eid: int, subject: str = Form(...), body: str = Form(...)):
    e = owned_email(request, eid)
    db = services(request).db
    db.update("emails", eid, {"subject": subject, "body": body})
    db.log_event("email_edited", campaign_id=e["campaign_id"], email_id=eid)
    return _back(request, e, "Saved.")


@router.post("/app/emails/{eid}/status")
def set_status(request: Request, eid: int, status: str = Form(...)):
    e = owned_email(request, eid)
    if status not in ("approved", "rejected", "draft"):
        raise HTTPException(400)
    db = services(request).db
    db.update("emails", eid, {"status": status})
    db.log_event(f"email_{status}", campaign_id=e["campaign_id"], email_id=eid)
    return _back(request, e, {"approved": "Approved.", "rejected": "Rejected.", "draft": "Moved back to drafts."}[status])


@router.post("/app/emails/{eid}/outcome")
def outcome(request: Request, eid: int, outcome: str = Form(...)):
    e = owned_email(request, eid)
    record_outcome(services(request).db, eid, outcome)
    return _back(request, e, f"Logged: {outcome.replace('_', ' ')}.")


@router.post("/app/campaigns/{cid}/approve-all")
def approve_all(request: Request, cid: int, min_judge: float = Form(4.0)):
    owned_campaign(request, cid)
    db = services(request).db
    rows = db.all("SELECT id FROM emails WHERE campaign_id = ? AND status = 'draft' AND judge_score >= ?", cid, min_judge)
    for r in rows:
        db.update("emails", r["id"], {"status": "approved"})
    return redirect(f"/app/campaigns/{cid}?tab=emails", msg=f"Approved {len(rows)} drafts.")


@router.post("/app/campaigns/{cid}/send")
def send_approved(request: Request, cid: int):
    owned_campaign(request, cid)
    db, s = services(request).db, user_settings(request)
    sent, errors = 0, []
    for r in db.all("SELECT id FROM emails WHERE campaign_id = ? AND status = 'approved'", cid):
        try:
            send_email(db, s, r["id"])
            sent += 1
        except SendError as e:
            errors.append(str(e))
            if "Daily cap" in str(e) or "sending inbox" in str(e):
                break
    if errors:
        return redirect(f"/app/campaigns/{cid}?tab=emails", msg=f"Sent {sent}.", err=errors[-1])
    return redirect(f"/app/campaigns/{cid}?tab=emails", msg=f"Sent {sent}.")


@router.get("/app/review")
def review(request: Request, id: int | None = None):
    require_user(request)
    db = services(request).db
    queue = db.all(
        "SELECT e.*, l.email AS to_email, l.first_name, l.last_name, l.company, l.title, l.website, l.score, "
        "l.score_json, l.research_json, c.name AS campaign_name FROM emails e "
        "JOIN leads l ON l.id = e.lead_id JOIN campaigns c ON c.id = e.campaign_id "
        "WHERE c.user_id = ? AND e.status IN ('draft', 'needs_review', 'approved') "
        "ORDER BY CASE e.status WHEN 'needs_review' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END, e.id",
        require_user(request)["id"])
    current = next((e for e in queue if e["id"] == id), None) or (queue[0] if queue else None)
    nxt = None
    if current is not None:
        idx = [e["id"] for e in queue].index(current["id"])
        nxt = queue[idx + 1]["id"] if idx + 1 < len(queue) else None
    return render(request, "app/review.html", active="review", queue=queue, e=current, next_id=nxt,
                  formats=FORMATS)


@router.get("/app/campaigns/{cid}/metrics.json")
def metrics_json(request: Request, cid: int):
    owned_campaign(request, cid)
    return campaign_metrics(services(request).db, cid)


# Old single-page routes, kept so bookmarks still work.
@router.get("/campaigns/{cid}")
def old_campaign(cid: int):
    return RedirectResponse(f"/app/campaigns/{cid}", status_code=301)
