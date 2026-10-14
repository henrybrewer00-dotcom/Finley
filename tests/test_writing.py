"""Writer quality rules: gates for AI tells, format learning, sequences and format mining."""

import json
import random

from finley import gates
from finley.formats import FORMATS
from finley.llm import FakeLLM
from finley.mine import mine_formats, split_emails
from finley.selection import choose, format_stats
from finley.writer import step_kind, write_email
from finley.db import now

LEAD = {"first_name": "Sam"}
QQ = FORMATS["quick_question"]


def _check(body, subject="after-hours calls", fmt=QQ):
    return " ".join(gates.check({"subject": subject, "body": body, "claims": []}, fmt, LEAD, {}).failures)


def test_ai_tells_and_dashes_fail():
    text = _check("Hi Sam,\n\nI hope you're doing well — we leverage AI. Who answers at 2am?\n\nAlex")
    assert "templated" in text and "dashes" in text


def test_self_centered_opener_fails():
    assert "Open with them" in _check("Hi Sam,\n\nI run a call answering service in Austin. Who answers your phones at 2am?\n\nAlex")
    assert "Open with them" not in _check("Hi Sam,\n\nYour site says you run 24/7 service. Who answers your phones at 2am?\n\nAlex")


def test_too_many_questions_and_weak_subject_fail():
    text = _check("Hi Sam,\n\nWho answers at 2am? Is it you? Do you miss calls?\n\nAlex", subject="quick question")
    assert "questions" in text and "low-reply" in text


def test_us_heavy_email_fails():
    body = "Hi Sam,\n\nYour team is busy. We built our tool so we can help, and our team and I think we fit.\n\nAlex"
    assert "about us" in _check(body, fmt=FORMATS["owner_direct"])


def test_long_paragraph_fails():
    para = " ".join(["Your crews are busy and calls pile up."] * 8)
    assert "paragraph" in _check(f"Hi Sam,\n\n{para}\n\nAlex", fmt=FORMATS["rrr"])


def test_step_kind():
    assert [step_kind(s, 4) for s in (1, 2, 3, 4, 5)] == ["first", "follow_up", "follow_up", "final", "final"]


def test_choose_trusts_picker_without_data_and_learns_with_it():
    ranked = [FORMATS["quick_question"], FORMATS["observation"]]
    assert choose(ranked, {}) == (ranked[0], "picker")
    stats = {"quick_question": (200, 2), "observation": (200, 30)}
    rng = random.Random(1)
    picks = [choose(ranked, stats, rng)[0].key for _ in range(200)]
    assert picks.count("observation") > 180
    assert choose(ranked, stats, rng)[1] == "learned"


def _setup(db, campaign_id):
    lid = db.insert("leads", {"campaign_id": campaign_id, "email": "dana@x.com", "first_name": "Dana",
                              "company": "Ridgeline", "status": "researched", "created_at": now(),
                              "research_json": json.dumps({"facts": [{"id": "f1", "text": "24/7 service",
                                                                      "source": "website:/"}], "trigger": ""})})
    return db.one("SELECT * FROM campaigns WHERE id = ?", campaign_id), db.one("SELECT * FROM leads WHERE id = ?", lid)


def test_format_stats_counts_sent_and_positive(db, campaign_id):
    campaign, lead = _setup(db, campaign_id)
    for i in range(3):
        eid = db.insert("emails", {"campaign_id": campaign_id, "lead_id": lead["id"], "format_key": "pas",
                                   "subject": "s", "body": "b", "status": "sent", "created_at": now()})
        if i == 0:
            db.log_event("reply_positive", campaign_id=campaign_id, lead_id=lead["id"], email_id=eid)
            db.log_event("meeting_booked", campaign_id=campaign_id, lead_id=lead["id"], email_id=eid)
    assert format_stats(db)["pas"] == (3, 1)


def test_follow_up_sees_the_thread_and_avoids_used_formats(db, settings, campaign_id):
    campaign, lead = _setup(db, campaign_id)
    db.insert("emails", {"campaign_id": campaign_id, "lead_id": lead["id"], "step": 1, "format_key": "bump_value",
                         "subject": "after-hours calls", "body": "Hi Dana, first email body.", "status": "sent",
                         "created_at": now()})
    llm = FakeLLM()
    eid = write_email(db, llm, settings, campaign, lead, step=2)
    row = db.one("SELECT * FROM emails WHERE id = ?", eid)
    assert row["step"] == 2 and FORMATS[row["format_key"]].step == "follow_up"
    assert row["format_key"] != "bump_value"
    write_prompt = next(u for t, u in llm.calls if t == "write")
    assert "first email body" in write_prompt and "Earlier emails" in write_prompt
    assert json.loads(row["gate_json"])["pick"]["how"] in {"picker", "learned", "only_option"}


def test_mine_formats_returns_new_valid_formats(settings):
    assert split_emails("Hi A\n---\nHi B\n\n---\n") == ["Hi A", "Hi B"]
    mined = mine_formats(FakeLLM(), settings, "Hi A, it worked.\n---\nHi B, also worked.")
    assert [m["key"] for m in mined] == ["two_line_case"]
    clash = dict(FakeLLM().responders["mine_formats"]("")["formats"][0], key="pas")
    taken = FakeLLM({"mine_formats": lambda u: {"formats": [clash]}})
    assert mine_formats(taken, settings, "Hi A") == []


def test_company_name_does_not_trip_ai_tells():
    r = gates.check({"subject": "after-hours calls", "claims": [],
                     "body": "Hi Sam,\n\nUnlock Locksmiths runs a 24/7 line. Who answers it at 2am?\n\nAlex"},
                    QQ, {"first_name": "Sam", "company": "Unlock Locksmiths"}, {})
    assert r.passed, r.failures
