import os

import pytest

from finley.config import Settings
from finley.db import DB, now
from finley.llm import FakeLLM


@pytest.fixture
def settings():
    return Settings(db_path=":memory:", from_name="Alex", from_email="alex@example.com",
                    smtp_host="smtp.example.com", smtp_user="alex@example.com", smtp_password="x",
                    physical_address="1 Main St, Austin, TX")


# Set FINLEY_TEST_DATABASE_URL to a scratch Postgres database to run every DB test on Postgres too.
PG_URL = os.environ.get("FINLEY_TEST_DATABASE_URL", "")


@pytest.fixture(params=["sqlite"] + (["postgres"] if PG_URL else []))
def db(request):
    if request.param == "sqlite":
        yield DB(":memory:")
        return
    import psycopg

    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    d = DB(PG_URL)
    yield d
    d.conn.close()


@pytest.fixture
def llm():
    return FakeLLM()


@pytest.fixture
def campaign_id(db):
    return db.insert("campaigns", {
        "name": "Test", "offer": "We answer after-hours calls for plumbers.",
        "icp": "Owner-operated plumbing companies in Texas.", "proof": "", "voice_samples": "",
        "created_at": now(),
    })


CSV = """First Name,Last Name,Email,Company,Website,Job Title
Sam,Rivera,sam@riveraplumbing.com,Rivera Plumbing,riveraplumbing.com,Owner
Jo,Lee,JO@leeplumbing.com,Lee Plumbing,https://leeplumbing.com,Owner
Bad,Row,not-an-email,Nowhere,,
Dup,Row,sam@riveraplumbing.com,Rivera Plumbing,,
No,Reply,noreply@bigco.com,BigCo,,
"""


@pytest.fixture
def csv_text():
    return CSV
