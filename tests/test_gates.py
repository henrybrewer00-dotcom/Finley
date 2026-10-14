from finley import gates
from finley.formats import FORMATS

LEAD = {"first_name": "Sam"}
RESEARCH = {"facts": [{"id": "f1", "text": "24/7 emergency service", "source": "website:/"}]}
GOOD = {
    "subject": "after-hours calls",
    "body": "Hi Sam,\n\nSaw that you run 24/7 emergency service. Who answers when a call comes in at 2am?\n\nAlex",
    "claims": [{"text": "you run 24/7 emergency service", "fact_id": "f1"}],
}


def test_good_email_passes():
    r = gates.check(GOOD, FORMATS["quick_question"], LEAD, RESEARCH)
    assert r.passed, r.failures


def test_unbacked_claim_fails():
    email = {**GOOD, "claims": [{"text": "you run 24/7 emergency service", "fact_id": "f9"}]}
    r = gates.check(email, FORMATS["quick_question"], LEAD, RESEARCH)
    assert any("not backed" in f for f in r.failures)


def test_claim_missing_from_body_fails():
    email = {**GOOD, "claims": [{"text": "you have twelve vans on the road", "fact_id": "f1"}]}
    r = gates.check(email, FORMATS["quick_question"], LEAD, RESEARCH)
    assert any("appear in the body" in f for f in r.failures)


def test_spam_links_placeholders_and_subject():
    email = {
        "subject": "Re: URGENT OFFER",
        "body": "Hi {first_name}, I hope this email finds you well! Click here: https://x.co !",
        "claims": [],
    }
    r = gates.check(email, FORMATS["quick_question"], LEAD, RESEARCH)
    text = " ".join(r.failures)
    for expected in ("spammy", "placeholder", "links", "Re:/Fwd:", "exclamation", "first name"):
        assert expected in text, expected


def test_length_window():
    long_body = "Hi Sam,\n\n" + "We help plumbers book more jobs. " * 20
    r = gates.check({"subject": "jobs", "body": long_body, "claims": []}, FORMATS["quick_question"], LEAD, RESEARCH)
    assert any("words" in f for f in r.failures)


def test_reading_grade_simple_text_is_low():
    assert gates.reading_grade("Who picks up the phone at night? We can help.") < 6


def test_judge_score():
    assert gates.judge_score({"relevance": 4, "specificity": 4, "clarity": 5, "tone": 3}) == 4.0
