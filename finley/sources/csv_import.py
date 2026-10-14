"""Import leads from a CSV export (Apollo, HubSpot, Google Sheets, anything with a header row)."""

from __future__ import annotations

import csv
import io
import json
import re

from ..db import DB, now

# Our field -> header names we accept for it (compared lowercase, spaces/underscores ignored).
ALIASES: dict[str, list[str]] = {
    "email": ["email", "emailaddress", "workemail", "contactemail"],
    "first_name": ["firstname", "first", "givenname"],
    "last_name": ["lastname", "last", "surname", "familyname"],
    "title": ["title", "jobtitle", "position", "role"],
    "company": ["company", "companyname", "organization", "account", "business", "businessname"],
    "website": ["website", "domain", "url", "companywebsite", "companydomain"],
    "industry": ["industry", "category", "vertical"],
    "location": ["location", "city", "state", "country", "address"],
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)


def _norm(header: str) -> str:
    return re.sub(r"[\s_\-]", "", header.strip().lower())


def map_headers(headers: list[str]) -> dict[str, str]:
    """Return {our_field: csv_header} for every header we recognise."""
    mapping: dict[str, str] = {}
    for h in headers:
        n = _norm(h)
        for field, names in ALIASES.items():
            if field not in mapping and n in names:
                mapping[field] = h
    if "first_name" not in mapping:
        for h in headers:
            if _norm(h) in ("name", "fullname", "contactname", "ownername"):
                mapping["full_name"] = h
                break
    return mapping


def import_csv(db: DB, campaign_id: int, text: str, source: str = "csv") -> dict[str, int]:
    reader = csv.DictReader(io.StringIO(text))
    mapping = map_headers(reader.fieldnames or [])
    if "email" not in mapping:
        raise ValueError(f"No email column found. Headers: {reader.fieldnames}")

    counts = {"imported": 0, "duplicate": 0, "invalid_email": 0, "suppressed": 0}
    known = set(mapping.values())
    owner = db.campaign_user(campaign_id)
    for raw in reader:
        email = (raw.get(mapping["email"]) or "").strip().lower()
        if not EMAIL_RE.match(email):
            counts["invalid_email"] += 1
            continue
        if db.is_suppressed(email, owner):
            counts["suppressed"] += 1
            continue
        row = {f: (raw.get(h) or "").strip() for f, h in mapping.items() if f != "full_name"}
        if "full_name" in mapping:
            parts = (raw.get(mapping["full_name"]) or "").strip().split()
            row["first_name"] = parts[0] if parts else ""
            row["last_name"] = " ".join(parts[1:])
        row["email"] = email
        if row.get("website") and not row["website"].startswith("http"):
            row["website"] = "https://" + row["website"]
        extra = {k: v for k, v in raw.items() if k not in known and v}
        exists = db.one("SELECT 1 FROM leads WHERE campaign_id = ? AND email = ?", campaign_id, email)
        if exists:
            counts["duplicate"] += 1
            continue
        lead_id = db.insert("leads", {
            **row, "campaign_id": campaign_id, "source": source,
            "extra_json": json.dumps(extra), "created_at": now(),
        })
        db.log_event("lead_imported", campaign_id=campaign_id, lead_id=lead_id, data={"source": source})
        counts["imported"] += 1
    return counts
