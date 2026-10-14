"""Find leads from a plain-English description. The search engine itself is finley/sourcing.py.

The page calls POST /api/campaigns/{cid}/find-leads, which works within a time budget on
serverless hosts. The browser calls it again until it has as many leads as were asked for.
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Body, Form, Request
from fastapi.responses import JSONResponse

from ..db import now
from ..sourcing import fake_fetch, find_leads
from .deps import llm_for, owned_campaign, profile, redirect, render, require_user, services

router = APIRouter()


@router.get("/app/find")
def find_page(request: Request, campaign: int | None = None):
    user = require_user(request)
    db = services(request).db
    campaigns = db.all("SELECT id, name, icp FROM campaigns WHERE user_id = ? ORDER BY id DESC", user["id"])
    rows = db.all(
        "SELECT ev.ts, ev.campaign_id, ev.data_json, c.name AS campaign_name FROM events ev "
        "JOIN campaigns c ON c.id = ev.campaign_id WHERE c.user_id = ? AND ev.kind = 'sourcing_finished' "
        "ORDER BY ev.id DESC LIMIT 12", user["id"])
    starts = {}
    for r in db.all("SELECT ev.campaign_id, ev.data_json FROM events ev JOIN campaigns c ON c.id = ev.campaign_id "
                    "WHERE c.user_id = ? AND ev.kind = 'sourcing_started' ORDER BY ev.id", user["id"]):
        starts[r["campaign_id"]] = json.loads(r["data_json"] or "{}").get("description", "")
    searches: list[dict] = []
    for r in rows:
        d = json.loads(r["data_json"] or "{}")
        counts = d.get("counts", {})
        prev = searches[-1] if searches else None
        if prev and prev["campaign_id"] == r["campaign_id"] and prev["ts"][:13] == r["ts"][:13]:
            # The page calls the API in rounds; show one search per campaign per hour.
            for k in ("companies_found", "leads_added"):
                prev["counts"][k] = prev["counts"].get(k, 0) + counts.get(k, 0)
            prev["cost_usd"] += d.get("cost_usd", 0)
            continue
        searches.append({"ts": r["ts"], "campaign_id": r["campaign_id"], "campaign_name": r["campaign_name"],
                         "description": starts.get(r["campaign_id"], ""), "counts": dict(counts),
                         "cost_usd": d.get("cost_usd", 0)})
    selected = next((c for c in campaigns if c["id"] == campaign), None)
    return render(request, "app/find.html", active="find", campaigns=campaigns, campaign=campaign,
                  prompt=selected["icp"] if selected else "", searches=searches,
                  has_key=llm_for(request) is not None, can_store_secrets=services(request).vault.enabled, offer=profile(request).get("company_description", ""))


def short_name(icp: str, limit: int = 44) -> str:
    """A short campaign name from a description: "Plumbing companies in Austin" from the full sentence."""
    import re

    text = re.split(r"\s+(?:with|that|who|which|where|offering)\s+|[,.;(]", icp.strip(), maxsplit=1)[0].strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return (text[:1].upper() + text[1:]) or "New campaign"


@router.post("/api/campaigns")
def create_campaign_api(request: Request, body: dict = Body(default={})):
    """Create a campaign from the Find page: {name, icp, offer}. Returns {id}."""
    user = require_user(request)
    icp = str(body.get("icp") or "").strip()
    if not icp:
        return JSONResponse({"error": "Describe your ideal customer first."}, status_code=400)
    name = str(body.get("name") or "").strip() or short_name(icp)
    offer = str(body.get("offer") or "").strip() or profile(request).get("company_description", "")
    cid = services(request).db.insert("campaigns", {
        "user_id": user["id"], "name": name, "offer": offer, "icp": icp, "proof": "", "voice_samples": "",
        "created_at": now()})
    return {"id": cid}


def _source(request: Request, cid: int, description: str, limit: int, exclude_domains: str, dry_run: bool,
            api_key: str) -> dict:
    owned_campaign(request, cid)
    svc = services(request)
    client = llm_for(request, demo=dry_run, api_key=api_key)
    if client is None:
        raise ValueError("Add your Anthropic API key in Settings, or try a demo search.")
    offline = dry_run and svc.llm is None
    extra = {"fetch": fake_fetch, "mx_check": lambda d: True, "email_finder": lambda d: []} if offline else {}
    if svc.sourcing_fetch and not offline:
        extra["fetch"] = svc.sourcing_fetch
    saved = profile(request).get("exclude_domains") or ""
    # Serverless requests get cut off, so stop starting new work early there; the page calls again.
    budget = 50.0 if os.environ.get("VERCEL") else 0
    from .deps import user_settings

    return find_leads(svc.db, client, user_settings(request), cid, description=description, limit=limit,
                      exclude_domains=",".join(x for x in (saved, exclude_domains) if x),
                      time_budget_s=budget, **extra)


@router.post("/api/campaigns/{cid}/find-leads")
def find_leads_api(request: Request, cid: int, body: dict = Body(default={})):
    """Find leads from a description of the ideal customer. JSON in, JSON out.

    Body: {"description": str (defaults to the campaign ICP), "limit": int = 25,
           "exclude_domains": str, "dry_run": bool, "api_key": str (this request only, never stored)}.
    Returns {"counts", "plan", "cost_usd", "seconds", "leads"}.
    """
    try:
        return _source(request, cid, str(body.get("description") or ""), int(body.get("limit") or 25),
                       str(body.get("exclude_domains") or ""), bool(body.get("dry_run")),
                       str(body.get("api_key") or ""))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.post("/app/campaigns/{cid}/find-leads")
def find_leads_form(request: Request, cid: int, description: str = Form(""), limit: int = Form(25),
                    exclude_domains: str = Form(""), dry_run: bool = Form(False), api_key: str = Form("")):
    """No-JavaScript fallback for the Find page."""
    try:
        r = _source(request, cid, description, limit, exclude_domains, dry_run, api_key)
    except ValueError as e:
        return redirect(f"/app/find?campaign={cid}", err=str(e))
    c = r["counts"]
    return redirect(f"/app/campaigns/{cid}?tab=leads",
                    msg=f"Found {c['companies_found']} companies and added {c['leads_added']} leads "
                        f"(${r['cost_usd']:.2f}). {c['no_email']} had no public email.")
