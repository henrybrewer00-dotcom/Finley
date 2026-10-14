from finley.scoring import combine, hard_filter, parse_domains, score_band
from finley.sources.csv_import import import_csv, map_headers


def test_header_mapping():
    m = map_headers(["E-mail", "Full Name", "Company Name", "Domain"])
    assert m == {"email": "E-mail", "full_name": "Full Name", "company": "Company Name", "website": "Domain"}
    m = map_headers(["Email", "First Name", "Job Title"])
    assert m["email"] == "Email" and m["first_name"] == "First Name" and m["title"] == "Job Title"


def test_import_csv(db, campaign_id, csv_text):
    counts = import_csv(db, campaign_id, csv_text)
    assert counts == {"imported": 3, "duplicate": 1, "invalid_email": 1, "suppressed": 0}
    jo = db.one("SELECT * FROM leads WHERE email = 'jo@leeplumbing.com'")
    assert jo["website"] == "https://leeplumbing.com" and jo["title"] == "Owner"
    sam = db.one("SELECT * FROM leads WHERE email = 'sam@riveraplumbing.com'")
    assert sam["website"] == "https://riveraplumbing.com"


def test_import_skips_suppressed(db, campaign_id, csv_text):
    db.suppress("sam@riveraplumbing.com", "unsubscribe")
    counts = import_csv(db, campaign_id, csv_text)
    assert counts["suppressed"] == 2  # the original row and its duplicate


def test_hard_filter(db, campaign_id, csv_text):
    import_csv(db, campaign_id, csv_text)
    noreply = db.one("SELECT * FROM leads WHERE email LIKE 'noreply%'")
    assert "role address" in hard_filter(noreply)
    sam = db.one("SELECT * FROM leads WHERE email LIKE 'sam%'")
    assert hard_filter(sam) is None
    assert "excluded" in hard_filter(sam, {"riveraplumbing.com"})


def test_combine_and_band():
    assert combine({"fit": 5, "need": 5, "timing": 5, "reachability": 5}) == 100.0
    assert combine({"fit": 1, "need": 1, "timing": 1, "reachability": 1}) == 0.0
    assert score_band(80) == "A" and score_band(10) == "D" and score_band(None) == "unscored"


def test_parse_domains():
    assert parse_domains("acme.com, Rival.io  bad") == {"acme.com", "rival.io"}
