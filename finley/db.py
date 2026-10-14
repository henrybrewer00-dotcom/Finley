"""SQLite storage. One file, no server, so self-hosting stays free."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,              -- owner when accounts are on; NULL on a single-user install
    name TEXT NOT NULL,
    offer TEXT NOT NULL,          -- what we sell, in the user's words
    icp TEXT NOT NULL,            -- ideal customer profile, plain English
    voice_samples TEXT DEFAULT '',-- past emails the user wrote, for tone
    proof TEXT DEFAULT '',        -- case studies / results we may cite
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    email TEXT NOT NULL,
    first_name TEXT DEFAULT '',
    last_name TEXT DEFAULT '',
    title TEXT DEFAULT '',
    company TEXT DEFAULT '',
    website TEXT DEFAULT '',
    industry TEXT DEFAULT '',
    location TEXT DEFAULT '',
    extra_json TEXT DEFAULT '{}',
    source TEXT DEFAULT 'csv',
    status TEXT DEFAULT 'new',    -- new | filtered | scored | researched | drafted | contacted | replied
    score REAL,
    score_json TEXT,
    research_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (campaign_id, email)
);
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    step INTEGER DEFAULT 1,
    format_key TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    claims_json TEXT DEFAULT '[]',
    gate_json TEXT DEFAULT '{}',
    judge_score REAL,
    status TEXT DEFAULT 'draft',  -- draft | needs_review | approved | sent | failed | rejected
    message_id TEXT,
    sent_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,           -- llm_call | lead_imported | email_sent | reply | bounce | ...
    campaign_id INTEGER,
    lead_id INTEGER,
    email_id INTEGER,
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    data_json TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS suppressions (
    email TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_campaign ON events(campaign_id, kind);
CREATE INDEX IF NOT EXISTS idx_emails_status ON emails(campaign_id, status);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(campaigns)")}
        if "user_id" not in cols:  # databases created before accounts existed
            self.conn.execute("ALTER TABLE campaigns ADD COLUMN user_id INTEGER")

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.conn:
            yield self.conn

    def one(self, sql: str, *args: Any) -> sqlite3.Row | None:
        return self.conn.execute(sql, args).fetchone()

    def all(self, sql: str, *args: Any) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    def insert(self, table: str, row: dict[str, Any]) -> int:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self.tx() as c:
            cur = c.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))
            return int(cur.lastrowid)

    def update(self, table: str, row_id: int, fields: dict[str, Any]) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*fields.values(), row_id))

    # --- events -----------------------------------------------------------
    def log_event(self, kind: str, **kw: Any) -> int:
        data = kw.pop("data", None)
        row = {"ts": now(), "kind": kind, **kw, "data_json": json.dumps(data or {})}
        return self.insert("events", row)

    # --- suppression ------------------------------------------------------
    def suppress(self, email: str, reason: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO suppressions (email, reason, ts) VALUES (?, ?, ?)",
                (email.strip().lower(), reason, now()),
            )

    def is_suppressed(self, email: str) -> bool:
        return self.one("SELECT 1 FROM suppressions WHERE email = ?", email.strip().lower()) is not None
