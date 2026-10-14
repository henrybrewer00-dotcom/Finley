"""Run a campaign's new leads through filter -> research -> score -> write."""

from __future__ import annotations

import logging

from .config import Settings
from .db import DB
from .llm import LLMClient, RefusalError
from .research import fetch_site_text, research_lead
from .scoring import parse_domains, score_lead
from .writer import write_email

log = logging.getLogger("finley")


def run_campaign(db: DB, llm: LLMClient, settings: Settings, campaign_id: int, *,
                 limit: int = 50, min_score: float = 50.0, exclude_domains: str = "",
                 fetch=fetch_site_text) -> dict[str, int]:
    campaign = db.one("SELECT * FROM campaigns WHERE id = ?", campaign_id)
    if campaign is None:
        raise ValueError(f"No campaign {campaign_id}")
    excluded = parse_domains(exclude_domains)
    counts = {"processed": 0, "filtered": 0, "below_threshold": 0, "drafted": 0, "errors": 0}
    leads = db.all("SELECT * FROM leads WHERE campaign_id = ? AND status = 'new' ORDER BY id LIMIT ?",
                   campaign_id, limit)
    for lead in leads:
        counts["processed"] += 1
        try:
            # Website research is free and Haiku is cheap, so research first; the score uses it.
            research_lead(db, llm, settings, lead, fetch=fetch)
            lead = db.one("SELECT * FROM leads WHERE id = ?", lead["id"])
            result = score_lead(db, llm, settings, campaign, lead, excluded)
            if result.get("filtered") or result.get("disqualified"):
                counts["filtered"] += 1
                continue
            if result["score"] < min_score:
                counts["below_threshold"] += 1
                continue
            lead = db.one("SELECT * FROM leads WHERE id = ?", lead["id"])
            write_email(db, llm, settings, campaign, lead)
            counts["drafted"] += 1
        except RefusalError as e:
            counts["errors"] += 1
            db.log_event("error", campaign_id=campaign_id, lead_id=lead["id"], data={"refusal": str(e)})
        except Exception as e:  # one bad lead never stops the run
            counts["errors"] += 1
            log.exception("lead %s failed", lead["id"])
            db.log_event("error", campaign_id=campaign_id, lead_id=lead["id"], data={"error": repr(e)})
    return counts
