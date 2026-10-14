"""Campaign metrics, computed straight from the event log and tables."""

from __future__ import annotations

from collections import defaultdict

from .db import DB
from .scoring import score_band

REPLY_KINDS = ("reply_positive", "reply_neutral", "reply_negative", "meeting_booked")


def campaign_metrics(db: DB, campaign_id: int) -> dict:
    def count(sql: str, *args) -> int:
        return int(db.one(sql, *args)[0] or 0)

    leads = count("SELECT COUNT(*) FROM leads WHERE campaign_id = ?", campaign_id)
    filtered = count("SELECT COUNT(*) FROM leads WHERE campaign_id = ? AND status = 'filtered'", campaign_id)
    drafted = count("SELECT COUNT(*) FROM emails WHERE campaign_id = ?", campaign_id)
    sent = count("SELECT COUNT(*) FROM emails WHERE campaign_id = ? AND status = 'sent'", campaign_id)
    ev = {r["kind"]: r["n"] for r in db.all(
        "SELECT kind, COUNT(*) AS n FROM events WHERE campaign_id = ? GROUP BY kind", campaign_id)}
    replies = sum(ev.get(k, 0) for k in REPLY_KINDS)
    positive = ev.get("reply_positive", 0) + ev.get("meeting_booked", 0)
    meetings = ev.get("meeting_booked", 0)
    cost = float(db.one("SELECT COALESCE(SUM(cost_usd), 0) FROM events WHERE campaign_id = ?", campaign_id)[0])
    cost_by_task = {r["task"]: round(r["usd"], 4) for r in db.all(
        "SELECT json_extract(data_json, '$.task') AS task, SUM(cost_usd) AS usd FROM events "
        "WHERE campaign_id = ? AND kind = 'llm_call' GROUP BY task", campaign_id)}

    def rate(n: int, d: int) -> float | None:
        return round(100 * n / d, 1) if d else None

    return {
        "leads": leads, "filtered": filtered, "drafted": drafted, "sent": sent,
        "bounces": ev.get("bounce", 0), "replies": replies, "positive": positive, "meetings": meetings,
        "bounce_rate": rate(ev.get("bounce", 0), sent), "reply_rate": rate(replies, sent),
        "positive_rate": rate(positive, sent),
        "cost_usd": round(cost, 4), "cost_by_task": cost_by_task,
        "cost_per_lead": round(cost / leads, 5) if leads else None,
        "cost_per_meeting": round(cost / meetings, 2) if meetings else None,
        "by_format": breakdown(db, campaign_id, "format"),
        "by_band": breakdown(db, campaign_id, "band"),
    }


def breakdown(db: DB, campaign_id: int, by: str) -> list[dict]:
    """Sent / replies / positive per email format or per score band."""
    rows = db.all(
        "SELECT e.id, e.format_key, e.status, l.score FROM emails e JOIN leads l ON l.id = e.lead_id "
        "WHERE e.campaign_id = ?", campaign_id)
    outcomes = defaultdict(set)
    for r in db.all("SELECT email_id, kind FROM events WHERE campaign_id = ? AND email_id IS NOT NULL",
                    campaign_id):
        outcomes[r["email_id"]].add(r["kind"])
    groups: dict[str, dict] = defaultdict(lambda: {"drafted": 0, "sent": 0, "replies": 0, "positive": 0})
    for r in rows:
        key = r["format_key"] if by == "format" else score_band(r["score"])
        g = groups[key]
        g["drafted"] += 1
        g["sent"] += r["status"] == "sent"
        kinds = outcomes[r["id"]]
        g["replies"] += bool(kinds & set(REPLY_KINDS))
        g["positive"] += bool(kinds & {"reply_positive", "meeting_booked"})
    return [{"key": k, **v, "reply_rate": round(100 * v["replies"] / v["sent"], 1) if v["sent"] else None}
            for k, v in sorted(groups.items())]
