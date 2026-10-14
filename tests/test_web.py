"""The redesigned web app: every page renders, the run API works lead by lead, and onboarding saves."""

from fastapi.testclient import TestClient

from finley.app import create_app
from finley.vault import Vault


def signed_in(db, settings, llm, csv_text=None):
    c = TestClient(create_app(settings, db, llm, fetch=lambda url: {}, vault=Vault("test-secret")))
    c.post("/signup", data={"email": "alex@example.com", "password": "correct horse", "name": "Alex Rivera"})
    return c


def test_landing_and_auth_pages_render(db, settings, llm):
    c = TestClient(create_app(settings, db, llm, vault=Vault("x")))
    page = c.get("/").text
    assert "Finley finds them" in page and "/signup" in page
    assert c.get("/static/finley.css").status_code == 200 and c.get("/static/finley.js").status_code == 200
    assert "Create your account" in c.get("/signup").text and "Welcome back" in c.get("/login").text


def test_onboarding_saves_profile_and_prefills_campaigns(db, settings, llm):
    c = signed_in(db, settings, llm)
    r = c.post("/app/settings/onboarding", data={"company_name": "Nightline", "company_description": "We answer calls.",
                                                 "anthropic_key": ""})
    assert r.url.path == "/app/find" and "set up" in r.text
    assert "We answer calls." in c.get("/app/campaigns/new").text
    assert "Welcome back, Alex" in c.get("/app").text


def test_run_api_processes_one_lead_per_request_and_review_flow(db, settings, llm, csv_text):
    c = signed_in(db, settings, llm)
    r = c.post("/app/campaigns", data={"name": "Plumbers", "offer": "o", "icp": "i"}, follow_redirects=False)
    cid = int(r.headers["location"].split("?")[0].split("/")[-1])
    c.post(f"/app/campaigns/{cid}/import", files={"file": ("l.csv", csv_text, "text/csv")})
    queue = c.get(f"/api/campaigns/{cid}/queue?limit=10").json()["leads"]
    assert len(queue) == 3
    results = [c.post(f"/api/leads/{l['id']}/process", data={"min_score": 50}).json()["result"] for l in queue]
    assert results.count("drafted") == 2 and results.count("filtered") == 1
    assert c.post(f"/api/leads/{queue[0]['id']}/process", data={}).json()["result"] == "skipped"

    review = c.get("/app/review").text
    assert "Quick question" in review and "Approve" in review and "f1" in review
    eid = db.one("SELECT id FROM emails ORDER BY id")["id"]
    r = c.post(f"/app/emails/{eid}/status?next=/app/review", data={"status": "approved"})
    assert r.url.path == "/app/review" and "Approved." in r.text
    dash = c.get("/app").text
    assert "Emails written" in dash and "Plumbers" in dash
    for tab in ("overview", "leads", "emails", "results"):
        assert c.get(f"/app/campaigns/{cid}?tab={tab}").status_code == 200
    assert c.get(f"/app/campaigns/{cid}/export.csv").text.startswith("first_name")


def test_find_page_creates_campaign_and_searches(db, settings, llm):
    c = signed_in(db, settings, None)
    assert "Find leads" in c.get("/app/find").text
    cid = c.post("/api/campaigns", json={"icp": "Plumbers in Austin", "offer": "Call answering"}).json()["id"]
    r = c.post(f"/api/campaigns/{cid}/find-leads", json={"dry_run": True, "limit": 5, "description": "Plumbers in Austin"})
    assert r.status_code == 200 and r.json()["counts"]["leads_added"] == 2
    assert "Plumbers in Austin" in c.get("/app/find").text  # shows under recent searches
    assert c.post(f"/api/campaigns/{cid}/find-leads", json={"limit": 5}).status_code == 400  # no key, no demo
