"""Email Bench: run the writer over fixed lead profiles and measure quality and cost.

Each case gives the offer, the lead and the research facts, so the bench tests format choice,
writing and gating without any web fetching. Run it before and after every prompt, model or
format change and compare the summaries.

Metrics:
- pass_rate: share of emails that passed every deterministic gate and the judge
- unbacked_claim_rate: share of emails that made a claim not traceable to research (target 0)
- judge_avg: average 1-5 judge score
- avg_words, avg_attempts, cost_usd
- first_try_rate: share that passed without a rewrite (how good the writer is unaided)
- failure_kinds: which gates fail most often, to aim the next prompt change
- by_format: pass rate and judge average per format

A case may set "step" (2+ for follow-ups) and "earlier" (the emails already sent to that lead).
"""

from __future__ import annotations

import json
import random
import statistics
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .db import DB, now
from .llm import LLMClient
from .writer import write_email

CASES = Path(__file__).resolve().parent.parent / "bench" / "cases.jsonl"


def load_cases(path: Path = CASES) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run_case(llm: LLMClient, settings: Settings, case: dict) -> dict:
    db = DB(":memory:")
    cid = db.insert("campaigns", {"name": case["id"], "offer": case["offer"], "icp": case["icp"],
                                  "proof": case.get("proof", ""), "voice_samples": "", "created_at": now()})
    lead = case["lead"]
    lid = db.insert("leads", {**{k: lead.get(k, "") for k in
                                 ("email", "first_name", "last_name", "title", "company", "website",
                                  "industry", "location")},
                              "campaign_id": cid, "research_json": json.dumps(case["research"]),
                              "extra_json": json.dumps(lead.get("extra", {})),
                              "status": "researched", "created_at": now()})
    campaign = db.one("SELECT * FROM campaigns WHERE id = ?", cid)
    lead_row = db.one("SELECT * FROM leads WHERE id = ?", lid)
    for i, prev in enumerate(case.get("earlier", []), 1):
        db.insert("emails", {"campaign_id": cid, "lead_id": lid, "step": i, "format_key": prev.get("format", ""),
                             "subject": prev["subject"], "body": prev["body"], "status": "sent",
                             "created_at": now()})
    eid = write_email(db, llm, settings, campaign, lead_row, step=case.get("step", 1),
                      rng=random.Random(case["id"]))
    e = db.one("SELECT * FROM emails WHERE id = ?", eid)
    gate = json.loads(e["gate_json"])
    cost = float(db.one("SELECT COALESCE(SUM(cost_usd), 0) FROM events")[0])
    return {
        "id": case["id"], "vertical": case["vertical"], "step": e["step"], "format": e["format_key"],
        "subject": e["subject"], "body": e["body"], "claims": json.loads(e["claims_json"]),
        "passed": e["status"] == "draft", "judge_score": e["judge_score"],
        "unbacked_claims": any("not backed" in f for f in gate["failures"]),
        "failures": gate["failures"], "words": gate["metrics"].get("words"),
        "reading_grade": gate["metrics"].get("reading_grade"), "attempts": gate["attempts"],
        "cost_usd": round(cost, 5), "first_try": gate["attempts"] == 1 and e["status"] == "draft",
        "rewrite_failures": [f for r in gate.get("rewrites", []) for f in (r["failures"] or [])],
        "metrics": gate["metrics"],
    }


def _failure_kind(text: str) -> str:
    """Collapse a gate failure message to a short label for counting."""
    for label, needle in (("unbacked_claim", "not backed"), ("claim_not_in_body", "appear in the body"),
                          ("length", "words; the"), ("ai_tell", "templated"), ("dash", "dashes"),
                          ("self_opener", "Open with them"), ("paragraph", "paragraph is"),
                          ("about_us", "about us"), ("questions", "questions;"), ("spam", "spammy"),
                          ("subject", "ubject"), ("reading_grade", "Reading grade"), ("first_name", "first name"),
                          ("links", "links"), ("placeholder", "placeholder")):
        if needle in text:
            return label
    return "judge"


def summarize(results: list[dict], settings: Settings) -> dict:
    n = len(results) or 1
    judged = [r["judge_score"] for r in results if r["judge_score"] is not None]
    formats: dict[str, int] = {}
    by_format: dict[str, dict] = {}
    kinds: dict[str, int] = {}
    for r in results:
        formats[r["format"]] = formats.get(r["format"], 0) + 1
        g = by_format.setdefault(r["format"], {"n": 0, "passed": 0, "judge": []})
        g["n"] += 1
        g["passed"] += r["passed"]
        if r["judge_score"] is not None:
            g["judge"].append(r["judge_score"])
        for f in r.get("rewrite_failures", []) + r["failures"]:
            k = _failure_kind(f)
            kinds[k] = kinds.get(k, 0) + 1
    return {
        "cases": len(results), "writer_model": settings.writer_model,
        "pass_rate": round(sum(r["passed"] for r in results) / n, 3),
        "unbacked_claim_rate": round(sum(r["unbacked_claims"] for r in results) / n, 3),
        "judge_avg": round(statistics.mean(judged), 2) if judged else None,
        "avg_words": round(statistics.mean(r["words"] or 0 for r in results), 1) if results else None,
        "avg_attempts": round(statistics.mean(r["attempts"] for r in results), 2) if results else None,
        "cost_usd": round(sum(r["cost_usd"] for r in results), 4),
        "first_try_rate": round(sum(r.get("first_try", False) for r in results) / n, 3),
        "formats_used": formats,
        "by_format": {k: {"n": g["n"], "pass_rate": round(g["passed"] / g["n"], 2),
                          "judge_avg": round(statistics.mean(g["judge"]), 2) if g["judge"] else None}
                      for k, g in sorted(by_format.items())},
        "failure_kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
    }


def run_bench(llm: LLMClient, settings: Settings, limit: int = 0, out_dir: str = "bench/results") -> dict:
    cases = load_cases()
    if limit:
        cases = cases[:limit]
    results = [run_case(llm, settings, c) for c in cases]
    summary = summarize(results, settings)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{stamp}.json").write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    md = [f"# Email Bench {stamp}", "", "```json", json.dumps(summary, indent=2), "```", ""]
    for r in results:
        md += [f"## {r['id']} · step {r['step']} · {r['format']} · judge {r['judge_score']} · {'pass' if r['passed'] else 'FAIL'}",
               "", f"**{r['subject']}**", "", r["body"], ""]
        if r["failures"]:
            md += ["Failures: " + "; ".join(r["failures"]), ""]
    (out / f"{stamp}.md").write_text("\n".join(md))
    summary["report"] = str(out / f"{stamp}.md")
    return summary
