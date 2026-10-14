import json

from fastapi.testclient import TestClient

from finley.app import create_app
from finley.llm import FakeLLM
from finley.pipeline import run_campaign
from finley.sourcing import extract_emails, fake_fetch, find_leads, normalize_domain, osm_businesses, rank_emails

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
    assert client.post(f"/api/campaigns/{campaign_id}/find-leads", json={"dry_run": True}).status_code == 401
    client.post("/signup", data={"email": "alex@example.com", "password": "correct horse"})
    db.execute("UPDATE campaigns SET user_id = (SELECT id FROM users) WHERE id = ?", campaign_id)
    r = client.post(f"/api/campaigns/{campaign_id}/find-leads", json={"dry_run": True, "limit": 5,
                                                                      "description": "Plumbers in Austin"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counts"]["leads_added"] == 2 and len(body["leads"]) == 2
    assert body["plan"]["queries"]

    assert client.post("/api/campaigns/999/find-leads", json={"dry_run": True}).status_code == 404
    r = client.post(f"/app/campaigns/{campaign_id}/find-leads", data={"dry_run": "true"}, follow_redirects=False)
    assert r.status_code == 303 and "Found" in r.headers["location"]


def _local_plan(u):
    return {"queries": ["plumber Austin TX"], "target_titles": ["Owner"], "company_type": "plumber",
            "exclusions": [], "local": {"is_local": True, "area": "Austin, Texas, USA",
                                        "osm_tags": ["craft=plumber", "bad tag; drop"]}}


def test_osm_businesses_parses_overpass():
    calls = []

    def http(url, data=None, timeout=0):
        calls.append((url, data))
        if "nominatim" in url:
            return [{"osm_type": "relation", "osm_id": 113314}]
        return {"elements": [
            {"tags": {"name": "Rivera Plumbing", "craft": "plumber", "website": "https://www.riveraplumbing.com/",
                      "email": "sam@riveraplumbing.com"}},
            {"tags": {"name": "Dup", "craft": "plumber", "website": "riveraplumbing.com"}},
            {"tags": {"name": "On Facebook", "craft": "plumber", "website": "https://facebook.com/x"}},
            {"tags": {"craft": "plumber", "website": "noname.com"}},
        ]}

    out = osm_businesses("Austin, Texas, USA", ["craft=plumber", "x\"]; out;"], http=http)
    assert out == [{"name": "Rivera Plumbing", "domain": "riveraplumbing.com", "emails": ["sam@riveraplumbing.com"],
                    "found_via": "osm", "why_fit": "Listed on OpenStreetMap as craft=plumber in Austin, Texas, USA."}]
    query = calls[1][1].decode()
    assert "3600113314" in query and "out%3B" not in query.split("craft")[0]
    assert osm_businesses("Austin", ["craft=plumber"], http=lambda *a, **k: 1 / 0) == []


def test_local_customers_use_free_listings_before_web_search(db, settings, campaign_id):
    llm = FakeLLM({"source_plan": _local_plan})
    osm = [{"name": f"Plumber {i}", "domain": f"plumber{i}.com", "emails": [], "found_via": "osm",
            "why_fit": "Listed on OpenStreetMap"} for i in range(6)]
    r = find_leads(db, llm, settings, campaign_id, limit=3, osm_lookup=lambda area, tags: osm, **OFFLINE)
    assert r["counts"]["from_openstreetmap"] == 6 and r["counts"]["leads_added"] == 3
    assert not any(t == "source_search" for t, _ in llm.calls)  # enough free leads: no paid searches
    assert db.one("SELECT source FROM leads")["source"] == "found:osm"


def test_osm_email_is_used_when_site_lists_none(db, settings, campaign_id):
    llm = FakeLLM({"source_plan": _local_plan})
    osm = [{"name": "Quiet Co", "domain": "quietco.com", "emails": ["owner@quietco.com"], "found_via": "osm",
            "why_fit": "x"}]
    find_leads(db, llm, settings, campaign_id, limit=1, osm_lookup=lambda a, t: osm,
               fetch=lambda url: {"/": "<p>We fix pipes all over Austin. Call us any time.</p>"},
               mx_check=lambda d: True, email_finder=lambda d: [])
    lead = db.one("SELECT * FROM leads")
    assert lead["email"] == "owner@quietco.com"
    assert json.loads(lead["extra_json"])["email_source"] == "openstreetmap"


def test_fit_check_and_parked_domains_filter_leads(db, settings, campaign_id):
    llm = FakeLLM({"source_contact": lambda u: {
        "email": "sam@riveraplumbing.com", "first_name": "", "last_name": "", "title": "", "company": "",
        "fits": "lonestardrains" not in u, "fit_reason": "A national franchise."}})

    def fetch(url):
        if "riveraplumbing" in url:
            return {"/": "<h1>This domain is for sale!</h1> Buy this domain today. sam@riveraplumbing.com"}
        return fake_fetch(url)

    r = find_leads(db, llm, settings, campaign_id, limit=5, fetch=fetch, mx_check=lambda d: True,
                   email_finder=lambda d: [])
    assert r["counts"]["no_site"] == 1 and r["counts"]["not_a_fit"] == 1 and r["counts"]["leads_added"] == 0


def test_redirected_site_is_saved_under_its_real_domain_once(db, llm, settings, campaign_id):
    def fetch(url):
        pages = fake_fetch("https://riveraplumbing.com")
        return {**pages, "__final_url__": "https://riveraplumbing.com"}

    r = find_leads(db, llm, settings, campaign_id, limit=5, fetch=fetch, mx_check=lambda d: True,
                   email_finder=lambda d: [])
    assert r["counts"]["leads_added"] == 1 and r["counts"]["duplicate"] == 1
    assert {l["website"] for l in db.all("SELECT website FROM leads")} == {"https://riveraplumbing.com"}
