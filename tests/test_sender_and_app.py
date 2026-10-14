import pytest
from fastapi.testclient import TestClient

from finley.app import create_app
from finley.pipeline import run_campaign
from finley.sender import SendError, record_outcome, send_email
from finley.sources.csv_import import import_csv


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=30):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture
def drafted(db, llm, settings, campaign_id, csv_text):
    import_csv(db, campaign_id, csv_text)
    run_campaign(db, llm, settings, campaign_id, fetch=lambda url: {})
    return db.all("SELECT * FROM emails ORDER BY id")


def test_send_requires_approval_and_respects_suppression_and_cap(db, settings, drafted):
    FakeSMTP.sent.clear()
    e1, e2 = drafted
    with pytest.raises(SendError, match="approved"):
        send_email(db, settings, e1["id"], smtp_factory=FakeSMTP)

    db.update("emails", e1["id"], {"status": "approved"})
    send_email(db, settings, e1["id"], smtp_factory=FakeSMTP)
    msg = FakeSMTP.sent[-1]
    assert msg["List-Unsubscribe"].startswith("<mailto:alex@example.com")
    assert "1 Main St" in msg.get_content() and "unsubscribe" in msg.get_content()
    assert db.one("SELECT status FROM emails WHERE id = ?", e1["id"])["status"] == "sent"

    lead2 = db.one("SELECT email FROM leads WHERE id = ?", e2["lead_id"])["email"]
    db.suppress(lead2, "unsubscribe")
    db.update("emails", e2["id"], {"status": "approved"})
    with pytest.raises(SendError, match="do-not-contact"):
        send_email(db, settings, e2["id"], smtp_factory=FakeSMTP)

    settings.daily_cap_per_inbox = 1
    db.update("emails", e2["id"], {"status": "approved"})
    db.conn.execute("DELETE FROM suppressions")
    with pytest.raises(SendError, match="Daily cap"):
        send_email(db, settings, e2["id"], smtp_factory=FakeSMTP)


def test_outcomes_feed_metrics_and_suppression(db, settings, drafted):
    e1 = drafted[0]
    db.update("emails", e1["id"], {"status": "approved"})
    send_email(db, settings, e1["id"], smtp_factory=FakeSMTP)
    record_outcome(db, e1["id"], "meeting_booked")
    from finley.metrics import campaign_metrics
    m = campaign_metrics(db, e1["campaign_id"])
    assert m["sent"] == 1 and m["meetings"] == 1 and m["reply_rate"] == 100.0
    assert m["by_format"][0]["positive"] == 1
    record_outcome(db, drafted[1]["id"], "bounce")
    lead = db.one("SELECT email FROM leads WHERE id = ?", drafted[1]["lead_id"])
    assert db.is_suppressed(lead["email"])


def test_dashboard_flow(db, settings, llm, csv_text):
    client = TestClient(create_app(settings, db, llm, fetch=lambda url: {}))
    r = client.post("/signup", data={"email": "alex@example.com", "password": "correct horse"})
    assert r.status_code == 200 and "Settings" in r.text
    r = client.post("/campaigns", data={"name": "Austin plumbers", "offer": "Call answering",
                                        "icp": "Plumbers in Austin"}, follow_redirects=False)
    assert r.status_code == 303
    cid = int(r.headers["location"].split("/")[-1])
    r = client.post(f"/campaigns/{cid}/import", files={"file": ("leads.csv", csv_text, "text/csv")})
    assert "3 imported" in r.text
    r = client.post(f"/campaigns/{cid}/run", data={"limit": 10, "min_score": 50})
    assert r.status_code == 200
    page = client.get(f"/campaigns/{cid}").text
    assert "Quick question" in page and "Approve" in page
    r = client.post(f"/campaigns/{cid}/approve-all", data={"min_judge": 4})
    assert "Approved 2 drafts" in r.text
    assert client.get("/").status_code == 200
    assert client.get(f"/campaigns/{cid}/metrics.json").json()["drafted"] == 2
