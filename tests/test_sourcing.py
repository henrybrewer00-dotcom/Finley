import json

from fastapi.testclient import TestClient

from finley.app import create_app
from finley.llm import FakeLLM
from finley.pipeline import run_campaign
from finley.sourcing import extract_emails, fake_fetch, find_leads, normalize_domain, rank_emails

OFFLINE = {"fetch": fake_fetch, "mx_check": lambda d: True, "email_finder": lambda d: []}


def test_extract_and_rank_emails():
    page = ('<a href="mailto:Sam@RiveraPlumbing.com">x</a> info@riveraplumbing.com '
            'jo [at] riveraplumbing [dot] com logo@2x.png noreply@riveraplumbing.com '
            'abc@sentry.wixpress.com owner.rivera@gmail.com someone@otherco.com')
    emails = extract_emails(page)
    assert "logo@2x.png" not in emails and "noreply@riveraplumbing.com" not in emails
    assert not any("wixpress" in e for e in emails)
    ranked = rank_emails(emails, "riveraplumbing.com")
    assert ranked[:2] == ["sam@riveraplumbing.com", "jo@riveraplumbing.com"]
    assert ranked.index("info@riveraplumbing.com") < ranked.index("owner.rivera@gmail.com")
    assert "someone@otherco.com" not in ranked


def test_normalize_domain():
    assert normalize_domain("https://www.Rivera-Plumbing.com/about?x=1") == "rivera-plumbing.com"
    assert normalize_domain("lonestardrains.com") == "lonestardrains.com"


def test_find_leads_adds_new_leads_with_provenance(db, llm, settings, campaign_id):
    result = find_leads(db, llm, settings, campaign_id, limit=10, **OFFLINE)
    assert result["counts"]["companies_found"] == 2
    assert result["counts"]["leads_added"] == 2
    leads = db.all("SELECT * FROM leads WHERE campaign_id = ? ORDER BY id", campaign_id)
    assert [l["email"] for l in leads] == ["sam@riveraplumbing.com", "sam@lonestardrains.com"]
    assert leads[0]["source"] == "found:web" and leads[0]["status"] == "new"
    assert leads[0]["first_name"] == "Sam" and leads[0]["website"] == "https://riveraplumbing.com"
    extra = json.loads(leads[0]["extra_json"])
    assert extra["email_source"] == "website" and extra["why_fit"]
    # The description defaulted to the campaign's ICP.
    assert "Owner-operated plumbing" in next(u for t, u in llm.calls if t == "source_plan")

    # Found leads flow straight into the existing pipeline.
    counts = run_campaign(db, llm, settings, campaign_id, fetch=lambda url: {})
    assert counts["drafted"] == 2


def test_find_leads_skips_known_excluded_and_invented_domains(db, settings, campaign_id):
    llm = FakeLLM({"source_pick": lambda u: {"companies": [
        {"name": "Rivera Plumbing", "domain": "riveraplumbing.com", "why_fit": "x"},
        {"name": "Lone Star Drains", "domain": "https://www.lonestardrains.com/", "why_fit": "x"},
        {"name": "Made Up Co", "domain": "madeupco.com", "why_fit": "not in any search result"},
        {"name": "Yelp", "domain": "yelp.com", "why_fit": "directory"},
    ]}})
    find_leads(db, llm, settings, campaign_id, limit=10, **OFFLINE)
    again = find_leads(db, llm, settings, campaign_id, limit=10, exclude_domains="lonestardrains.com", **OFFLINE)
    assert again["counts"]["companies_found"] == 0  # both real ones already exist; the rest are rejected
    emails = {r["email"].split("@")[1] for r in db.all("SELECT email FROM leads")}
    assert emails == {"riveraplumbing.com", "lonestardrains.com"}


def test_contact_pick_cannot_invent_an_email(db, settings, campaign_id):
    llm = FakeLLM({"source_contact": lambda u: {"email": "ceo@riveraplumbing.com", "first_name": "Made",
                                                "last_name": "Up", "title": "CEO", "company": ""}})
    find_leads(db, llm, settings, campaign_id, limit=1, **OFFLINE)
    lead = db.one("SELECT * FROM leads")
    assert lead["email"] == "sam@riveraplumbing.com" and lead["first_name"] == ""


def test_email_finder_used_only_when_site_has_no_email(db, llm, settings, campaign_id):
    asked = []

    def finder(domain):
        asked.append(domain)
        return [{"email": f"pat@{domain}", "first_name": "Pat", "last_name": "Lee", "title": "Owner"}]

    find_leads(db, llm, settings, campaign_id, limit=5, fetch=lambda url: {"/": "<p>No email here</p>"},
               mx_check=lambda d: True, email_finder=finder)
    assert asked == ["riveraplumbing.com", "lonestardrains.com"]
    lead = db.one("SELECT * FROM leads ORDER BY id")
    assert lead["email"] == "pat@riveraplumbing.com"
    assert json.loads(lead["extra_json"])["email_source"] == "email_finder"


def test_no_mail_server_and_suppressed_are_dropped(db, llm, settings, campaign_id):
    db.suppress("sam@riveraplumbing.com", "unsubscribe")
    r = find_leads(db, llm, settings, campaign_id, limit=5, fetch=fake_fetch,
                   mx_check=lambda d: d != "lonestardrains.com", email_finder=lambda d: [])
    assert r["counts"]["suppressed"] == 1 and r["counts"]["no_mail_server"] == 1
    assert r["counts"]["leads_added"] == 0


def test_find_leads_api(db, settings, campaign_id):
    client = TestClient(create_app(settings, db))
    r = client.post(f"/api/campaigns/{campaign_id}/find-leads", json={"dry_run": True, "limit": 5,
                                                                      "description": "Plumbers in Austin"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counts"]["leads_added"] == 2 and len(body["leads"]) == 2
    assert body["plan"]["queries"]

    assert client.post("/api/campaigns/999/find-leads", json={"dry_run": True}).status_code == 404
    r = client.post(f"/campaigns/{campaign_id}/find-leads", data={"dry_run": "true"}, follow_redirects=False)
    assert r.status_code == 303 and "Found" in r.headers["location"]
