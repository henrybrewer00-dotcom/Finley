import pytest
from fastapi.testclient import TestClient

from finley import accounts
from finley.accounts import AccountError
from finley.app import create_app
from finley.db import DB, now
from finley.vault import Vault


@pytest.fixture
def vault():
    return Vault("test-secret")


def client_for(db, settings, llm, vault, email, password="correct horse"):
    c = TestClient(create_app(settings, db, llm, fetch=lambda url: {}, vault=vault))
    r = c.post("/signup", data={"email": email, "password": password, "name": "Alex"})
    assert r.status_code == 200, r.text
    return c


def test_passwords_and_sessions(db):
    uid = accounts.create_user(db, "Alex@Example.com", "correct horse", "Alex")
    assert accounts.authenticate(db, "alex@example.com", "correct horse") == uid
    assert accounts.authenticate(db, "alex@example.com", "wrong password") is None
    assert accounts.authenticate(db, "nobody@example.com", "correct horse") is None
    with pytest.raises(AccountError, match="already exists"):
        accounts.create_user(db, "alex@example.com", "another one")
    with pytest.raises(AccountError, match="8 characters"):
        accounts.create_user(db, "sam@example.com", "short")
    row = db.one("SELECT password_hash FROM users WHERE id = ?", uid)
    assert "correct horse" not in row["password_hash"]

    token = accounts.start_session(db, uid)
    assert accounts.session_user(db, token)["email"] == "alex@example.com"
    assert db.one("SELECT token_hash FROM sessions")["token_hash"] != token
    accounts.end_session(db, token)
    assert accounts.session_user(db, token) is None


def test_first_account_adopts_cli_campaigns(db):
    cid = db.insert("campaigns", {"name": "CLI", "offer": "x", "icp": "y", "created_at": now()})
    uid = accounts.create_user(db, "alex@example.com", "correct horse")
    assert db.campaign_user(cid) == uid
    db.insert("campaigns", {"name": "CLI 2", "offer": "x", "icp": "y", "created_at": now()})
    accounts.create_user(db, "sam@example.com", "correct horse")
    assert db.one("SELECT COUNT(*) AS n FROM campaigns WHERE user_id IS NULL")["n"] == 1


def test_secrets_are_encrypted_and_never_shown(db, vault, settings):
    uid = accounts.create_user(db, "alex@example.com", "correct horse")
    accounts.save_settings(db, vault, uid, {"anthropic_key": "sk-ant-api03-abcdefghijklmnop",
                                            "smtp_password": "app-password", "smtp_host": "smtp.gmail.com",
                                            "smtp_user": "alex@example.com", "daily_cap": "40"})
    row = dict(db.one("SELECT * FROM user_settings WHERE user_id = ?", uid))
    assert "sk-ant" not in str(row) and "app-password" not in str(row)
    shown = accounts.get_settings(db, vault, uid)
    assert shown["has_anthropic_key"] and shown["has_smtp_password"]
    assert "sk-ant-api03-abcdefghijklmnop" not in str(shown)
    assert accounts.anthropic_key(db, vault, uid) == "sk-ant-api03-abcdefghijklmnop"

    run = accounts.runtime_settings(settings, db, vault, uid)
    assert (run.smtp_host, run.smtp_password, run.daily_cap_per_inbox) == ("smtp.gmail.com", "app-password", 40)

    # A blank secret keeps the saved one; clear_ removes it.
    accounts.save_settings(db, vault, uid, {"anthropic_key": "", "from_name": "Alex"})
    assert accounts.anthropic_key(db, vault, uid)
    accounts.save_settings(db, vault, uid, {"clear_anthropic_key": "1"})
    assert accounts.anthropic_key(db, vault, uid) == ""
    # A different server secret can't read what was saved.
    assert accounts.runtime_settings(settings, db, Vault("other"), uid).smtp_password == ""

    with pytest.raises(AccountError, match="Anthropic"):
        accounts.save_settings(db, vault, uid, {"anthropic_key": "not-a-key"})
    with pytest.raises(AccountError, match="can't store"):
        accounts.save_settings(db, Vault(None), uid, {"anthropic_key": "sk-ant-api03-abcdefghijklmnop"})


def test_pages_need_login(db, settings, llm, vault):
    c = TestClient(create_app(settings, db, llm, vault=vault))
    for path in ("/app", "/app/settings", "/app/campaigns/1", "/app/find", "/app/review"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), path
    assert c.get("/api/me").status_code == 401
    assert c.get("/").status_code == 200  # the landing page is public
    assert c.post("/app/campaigns", data={"name": "x", "offer": "y", "icp": "z"},
                  follow_redirects=False).headers["location"].startswith("/login")
    assert "Log in" in c.get("/login").text and "Create your account" in c.get("/signup").text


def test_login_logout_and_bad_password(db, settings, llm, vault):
    c = client_for(db, settings, llm, vault, "alex@example.com")
    c.post("/logout")
    assert c.get("/app", follow_redirects=False).status_code == 303
    r = c.post("/login", data={"email": "alex@example.com", "password": "nope nope"})
    assert "match" in r.text and r.status_code == 200 and 'alert-error' in r.text
    r = c.post("/login", data={"email": "alex@example.com", "password": "correct horse", "next": "/app/settings"},
               follow_redirects=False)
    assert r.headers["location"] == "/app/settings"
    r = c.post("/login", data={"email": "alex@example.com", "password": "correct horse",
                               "next": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"] == "/app"


def test_accounts_are_isolated(db, settings, llm, vault, csv_text):
    alex = client_for(db, settings, llm, vault, "alex@example.com")
    sam = client_for(db, settings, llm, vault, "sam@example.com")
    r = alex.post("/app/campaigns", data={"name": "Alex's plumbers", "offer": "o", "icp": "i"}, follow_redirects=False)
    cid = int(r.headers["location"].split("?")[0].split("/")[-1])
    alex.post(f"/app/campaigns/{cid}/import", files={"file": ("l.csv", csv_text, "text/csv")})
    alex.post(f"/app/campaigns/{cid}/run", data={"limit": 10, "min_score": 50})
    eid = db.one("SELECT id FROM emails WHERE campaign_id = ?", cid)["id"]

    assert "Alex's plumbers" not in sam.get("/app").text and "Alex's plumbers" not in sam.get("/app/campaigns").text
    assert sam.get(f"/app/campaigns/{cid}").status_code == 404
    assert sam.get(f"/app/campaigns/{cid}/metrics.json").status_code == 404
    assert sam.post(f"/app/campaigns/{cid}/import", files={"file": ("l.csv", csv_text, "text/csv")}).status_code == 404
    assert sam.post(f"/app/emails/{eid}/status", data={"status": "approved"}).status_code == 404
    assert sam.post(f"/app/emails/{eid}/edit", data={"subject": "x", "body": "y"}).status_code == 404
    assert db.one("SELECT status FROM emails WHERE id = ?", eid)["status"] != "approved"


def test_settings_page_saves_and_applies_to_runs(db, settings, llm, vault, csv_text):
    c = client_for(db, settings, llm, vault, "alex@example.com")
    r = c.post("/app/settings/defaults", data={"exclude_domains": "riveraplumbing.com",
                                  "min_score": "50"})
    assert "Saved." in r.text and "riveraplumbing.com" in r.text
    r = c.post("/app/settings/ai", data={"anthropic_key": "sk-ant-api03-abcdefghijklmnop"})
    assert "sk-ant-…mnop" in r.text and "abcdefghijklmnop" not in r.text
    r = c.post("/app/settings/defaults", data={"min_score": "500"})
    assert "between 0 and 100" in r.text

    r = c.post("/app/campaigns", data={"name": "Plumbers", "offer": "o", "icp": "i"}, follow_redirects=False)
    cid = int(r.headers["location"].split("?")[0].split("/")[-1])
    c.post(f"/app/campaigns/{cid}/import", files={"file": ("l.csv", csv_text, "text/csv")})
    c.post(f"/app/campaigns/{cid}/run", data={"limit": 10})
    rivera = db.one("SELECT status FROM leads WHERE email = 'sam@riveraplumbing.com'")
    assert rivera["status"] == "filtered"  # the saved exclusion applied without retyping it
    assert "Using your saved Anthropic key" in c.get(f"/app/campaigns/{cid}").text


def test_change_password_and_delete_account(db, settings, llm, vault, csv_text):
    c = client_for(db, settings, llm, vault, "alex@example.com")
    r = c.post("/app/settings/password", data={"current": "wrong", "new": "new password"})
    assert "current password is wrong" in r.text
    c.post("/app/settings/password", data={"current": "correct horse", "new": "new password"})
    uid = db.one("SELECT id FROM users")["id"]
    assert accounts.authenticate(db, "alex@example.com", "new password") == uid

    c.post("/app/campaigns", data={"name": "P", "offer": "o", "icp": "i"})
    cid = db.one("SELECT id FROM campaigns")["id"]
    c.post(f"/app/campaigns/{cid}/import", files={"file": ("l.csv", csv_text, "text/csv")})
    c.post(f"/app/campaigns/{cid}/run", data={"limit": 10})
    r = c.post("/app/settings/delete-account", data={"confirm_email": "someone@else.com"})
    assert "Type your email" in r.text
    c.post("/app/settings/delete-account", data={"confirm_email": "alex@example.com"})
    for table in ("users", "campaigns", "leads", "emails", "sessions", "user_settings"):
        assert db.one(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0, table


def test_suppression_is_per_account(db):
    a = accounts.create_user(db, "alex@example.com", "correct horse")
    b = accounts.create_user(db, "sam@example.com", "correct horse")
    db.suppress("Lead@Shop.com", "unsubscribe", a)
    assert db.is_suppressed("lead@shop.com", a)
    assert not db.is_suppressed("lead@shop.com", b)
    db.suppress("lead@shop.com", "bounce", a)  # no duplicate row
    assert db.one("SELECT COUNT(*) AS n FROM suppressions")["n"] == 1


def test_old_sqlite_file_is_migrated(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE campaigns (id INTEGER PRIMARY KEY, name TEXT NOT NULL, offer TEXT NOT NULL, icp TEXT NOT NULL,
            voice_samples TEXT DEFAULT '', proof TEXT DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE suppressions (email TEXT PRIMARY KEY, reason TEXT NOT NULL, ts TEXT NOT NULL);
        INSERT INTO campaigns (name, offer, icp, created_at) VALUES ('old', 'o', 'i', '2026-10-14');
        INSERT INTO suppressions VALUES ('x@y.com', 'bounce', '2026-10-14');
    """)
    old.commit()
    old.close()
    db = DB(str(path))
    assert db.is_suppressed("x@y.com")
    uid = accounts.create_user(db, "alex@example.com", "correct horse")
    assert db.one("SELECT user_id FROM campaigns")["user_id"] == uid
