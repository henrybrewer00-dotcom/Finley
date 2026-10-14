"""treg (https://github.com/superdesigndev/treg) as a data source.

treg proxies third-party APIs behind one token: `GET {base}/call/{upstream_url}` with an
`X-Treg-Token` header. Whoever owns the token pays: your own provider keys registered in
treg take priority and are not metered by treg.

Licensing note: treg is "Apache 2.0 with additional terms" that forbid re-hosting it as a
competing registry. Finley only *calls* treg; it never bundles or re-hosts it.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from ..db import DB


class TregClient:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0):
        if not token:
            raise ValueError("Set TREG_TOKEN to use treg.")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def call(self, upstream_url: str, params: dict[str, Any] | None = None) -> Any:
        if params:
            upstream_url += ("&" if "?" in upstream_url else "?") + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            f"{self.base_url}/call/{upstream_url}",
            headers={"X-Treg-Token": self.token, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode())

    def find_email(self, domain: str, first_name: str, last_name: str) -> dict | None:
        """Hunter email finder through treg. Returns {"email", "score"} or None."""
        data = self.call("https://api.hunter.io/v2/email-finder", {
            "domain": domain, "first_name": first_name, "last_name": last_name,
        })
        d = (data or {}).get("data") or {}
        return {"email": d["email"], "score": d.get("score")} if d.get("email") else None


def enrich_missing_emails(db: DB, client: TregClient, campaign_id: int, rows: list[dict]) -> list[dict]:
    """Fill in `email` for rows that have a name and a website but no email."""
    out = []
    for row in rows:
        if not row.get("email") and row.get("website") and row.get("first_name"):
            domain = urllib.parse.urlparse(row["website"]).netloc or row["website"]
            found = client.find_email(domain.removeprefix("www."), row["first_name"], row.get("last_name", ""))
            db.log_event("enrichment", campaign_id=campaign_id,
                         data={"provider": "hunter via treg", "domain": domain, "hit": bool(found)})
            if found:
                row = {**row, "email": found["email"]}
        out.append(row)
    return out
