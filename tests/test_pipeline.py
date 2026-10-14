import json

from finley.bench import load_cases, run_bench
from finley.llm import FakeLLM
from finley.metrics import campaign_metrics
from finley.pipeline import run_campaign
from finley.sources.csv_import import import_csv


def no_fetch(url):
    return {"/": "Rivera Plumbing. 24/7 emergency service across Austin."}


def test_end_to_end_dry_run(db, llm, settings, campaign_id, csv_text):
    import_csv(db, campaign_id, csv_text)
    counts = run_campaign(db, llm, settings, campaign_id, min_score=50, fetch=no_fetch)
    assert counts == {"processed": 3, "filtered": 1, "below_threshold": 0, "drafted": 2, "errors": 0}
    emails = db.all("SELECT * FROM emails")
    assert len(emails) == 2
    e = emails[0]
    assert e["format_key"] == "quick_question" and e["status"] == "draft" and e["judge_score"] >= 4
    gate = json.loads(e["gate_json"])
    assert gate["passed"] and gate["attempts"] == 1
    tasks = [t for t, _ in llm.calls]
    assert tasks.count("write") == 2 and tasks.count("judge") == 4  # two variants per lead
    m = campaign_metrics(db, campaign_id)
    assert m["leads"] == 3 and m["drafted"] == 2 and m["filtered"] == 1


def test_rewrite_loop_on_gate_failure(db, settings, campaign_id, csv_text):
    drafts = iter([
        {"subject": "Re: hi", "body": "Hi Sam, click here https://x.co", "claims": []},
        None,  # second attempt falls back to the good default
    ])

    def writer(user):
        d = next(drafts)
        return d if d else FakeLLM().responders["write"](user)

    llm = FakeLLM({"write": writer})
    import_csv(db, campaign_id, csv_text)
    run_campaign(db, llm, settings, campaign_id, limit=1, fetch=no_fetch)
    e = db.one("SELECT * FROM emails")
    assert json.loads(e["gate_json"])["attempts"] == 2
    second_prompt = [u for t, u in llm.calls if t == "write"][1]
    assert "failed review" in second_prompt and "Re:/Fwd:" in second_prompt


def test_unfixable_email_is_flagged_for_review(db, settings, campaign_id, csv_text):
    bad = {"subject": "Re: hi", "body": "Hi Sam, click here https://x.co", "claims": []}
    llm = FakeLLM({"write": lambda u: bad})
    import_csv(db, campaign_id, csv_text)
    run_campaign(db, llm, settings, campaign_id, limit=1, fetch=no_fetch)
    e = db.one("SELECT * FROM emails")
    assert e["status"] == "needs_review"
    assert json.loads(e["gate_json"])["attempts"] == settings.max_rewrites + 1


def test_disqualified_lead_gets_no_email(db, settings, campaign_id, csv_text):
    def dq(user):
        r = FakeLLM().responders["score"](user)
        return {**r, "disqualified": True, "disqualify_reason": "existing customer"}

    import_csv(db, campaign_id, csv_text)
    counts = run_campaign(db, FakeLLM({"score": dq}), settings, campaign_id, fetch=no_fetch)
    assert counts["drafted"] == 0 and counts["filtered"] == 3


def test_bench_dry_run(tmp_path, settings):
    assert len(load_cases()) >= 30
    summary = run_bench(FakeLLM(), settings, limit=3, out_dir=str(tmp_path))
    assert summary["cases"] == 3 and summary["unbacked_claim_rate"] == 0
    assert "by_format" in summary and "failure_kinds" in summary and "first_try_rate" in summary
    assert (tmp_path / summary["report"].split("/")[-1]).exists()
