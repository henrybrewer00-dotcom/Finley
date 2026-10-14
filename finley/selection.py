"""Learn which formats win from real replies.

The picker (Haiku) ranks the formats that fit a lead's research. Once formats have enough sends,
Thompson sampling chooses among those candidates by their positive reply rate, so formats that
earn replies get used more while new or unproven ones still get tried.
"""

from __future__ import annotations

import random

from .db import DB
from .formats import Format

POSITIVE = ("reply_positive", "meeting_booked")
MIN_SENDS = 20          # below this many sends in total across candidates, trust the picker's rank
PRIOR_STRENGTH = 20     # how many sends' worth of weight the pooled reply rate gets as a prior


def format_stats(db: DB) -> dict[str, tuple[int, int]]:
    """{format_key: (sent, positive replies)} across every campaign in this database."""
    rows = db.all(
        "SELECT e.format_key AS k, COUNT(DISTINCT e.id) AS sent, "
        "COUNT(DISTINCT CASE WHEN ev.kind IN (?, ?) THEN e.id END) AS pos "
        "FROM emails e LEFT JOIN events ev ON ev.email_id = e.id "
        "WHERE e.status = 'sent' GROUP BY e.format_key", *POSITIVE)
    return {r["k"]: (int(r["sent"]), int(r["pos"])) for r in rows}


def choose(ranked: list[Format], stats: dict[str, tuple[int, int]],
           rng: random.Random | None = None) -> tuple[Format, str]:
    """Pick one format from the picker's ranked candidates. Returns (format, how)."""
    if len(ranked) == 1:
        return ranked[0], "only_option"
    seen = [stats.get(f.key, (0, 0)) for f in ranked]
    total_sent = sum(s for s, _ in seen)
    if total_sent < MIN_SENDS:
        return ranked[0], "picker"
    rng = rng or random.Random()
    pooled = sum(p for _, p in seen) / total_sent
    a0, b0 = 1 + pooled * PRIOR_STRENGTH, 1 + (1 - pooled) * PRIOR_STRENGTH
    draws = [rng.betavariate(a0 + p, b0 + s - p) for s, p in seen]
    best = max(range(len(ranked)), key=lambda i: draws[i])
    return ranked[best], "learned"
