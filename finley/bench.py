"""Email Bench: run the writer over fixed lead profiles and measure quality and cost.

Each case gives the offer, the lead and the research facts, so the bench tests format choice,
writing and gating without any web fetching. Run it before and after every prompt, model or
format change and compare the summaries.

Metrics:
- pass_rate: share of emails that passed every deterministic gate and the judge
- unbacked_claim_rate: share of emails that made a claim not traceable to research (target 0)
- judge_avg: average 1-5 judge score
- avg_words, avg_attempts, cost_usd
"""

from __future__ import annotations

import json
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
                              "status": "researched", "created_at": now()})
    campaign = db.one("SELECT * FROM campaigns WHERE id = ?", cid)
    lead_row = db.one("SELECT * FROM leads WHERE id = ?", lid)
    eid = write_email(db, llm, settings, campaign, lead_row)
    e = db.one("SELECT * FROM emails WHERE id = ?", eid)
    gate = json.loads(e["gate_json"])
    cost = float(db.one("SELECT COALESCE(SUM(cost_usd), 0) FROM events")[0])
    return {
        "id": case["id"], "vertical": case["vertical"], "format": e["format_key"],
        "subject": e["subject"], "body": e["body"], "claims": json.loads(e["claims_json"]),
        "passed": e["status"] == "draft", "judge_score": e["judge_score"],
        "unbacked_claims": any("not backed" in f for f in gate["failures"]),
        "failures": gate["failures"], "words": gate["metrics"].get("words"),
        "reading_grade": gate["metrics"].get("reading_grade"), "attempts": gate["attempts"],
        "cost_usd": round(cost, 5),
    }


def summarize(results: list[dict], settings: Settings) -> dict:
    n = len(results) or 1
    judged = [r["judge_score"] for r in results if r["judge_score"] is not None]
    formats: dict[str, int] = {}
    for r in results:
        formats[r["format"]] = formats.get(r["format"], 0) + 1
    return {
        "cases": len(results), "writer_model": settings.writer_model,
        "pass_rate": round(sum(r["passed"] for r in results) / n, 3),
        "unbacked_claim_rate": round(sum(r["unbacked_claims"] for r in results) / n, 3),
        "judge_avg": round(statistics.mean(judged), 2) if judged else None,
        "avg_words": round(statistics.mean(r["words"] or 0 for r in results), 1) if results else None,
        "avg_attempts": round(statistics.mean(r["attempts"] for r in results), 2) if results else None,
        "cost_usd": round(sum(r["cost_usd"] for r in results), 4),
        "formats_used": formats,
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
        md += [f"## {r['id']} · {r['format']} · judge {r['judge_score']} · {'pass' if r['passed'] else 'FAIL'}",
               "", f"**{r['subject']}**", "", r["body"], ""]
        if r["failures"]:
            md += ["Failures: " + "; ".join(r["failures"]), ""]
    (out / f"{stamp}.md").write_text("\n".join(md))
    summary["report"] = str(out / f"{stamp}.md")
    return summary
