"""Storage: Postgres when a database URL is set (the hosted site), otherwise one SQLite file.

SQLite keeps self-hosting free with no server to run. On serverless hosts such as Vercel the
filesystem is wiped between instances, so set DATABASE_URL to a Postgres database there (Neon's
free tier works and is one click from the Vercel dashboard).

Queries are written once with `?` placeholders; the Postgres backend translates them.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# {pk} becomes the dialect's auto-increment primary key; REAL becomes DOUBLE PRECISION on Postgres.
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id {pk},
    email TEXT NOT NULL UNIQUE,
    name TEXT DEFAULT '',
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,  -- sha256 of the cookie value, so a leaked table can't log anyone in
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_settings (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    anthropic_key_enc TEXT DEFAULT '',   -- encrypted, see finley/vault.py
    -- sender profile
    from_name TEXT DEFAULT '',
    from_email TEXT DEFAULT '',
    sender_title TEXT DEFAULT '',
    company_name TEXT DEFAULT '',
    company_website TEXT DEFAULT '',
    company_description TEXT DEFAULT '',
    physical_address TEXT DEFAULT '',
    signature TEXT DEFAULT '',
    -- sending inbox (SMTP)
    smtp_host TEXT DEFAULT '',
    smtp_port INTEGER DEFAULT 587,
    smtp_user TEXT DEFAULT '',
    smtp_password_enc TEXT DEFAULT '',   -- encrypted
    daily_cap INTEGER DEFAULT 30,
    -- defaults for runs
    exclude_domains TEXT DEFAULT '',
    min_score REAL DEFAULT 50,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS campaigns (
    id {pk},
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,  -- NULL for campaigns made with the CLI
    name TEXT NOT NULL,
    offer TEXT NOT NULL,          -- what we sell, in the user's words
    icp TEXT NOT NULL,            -- ideal customer profile, plain English
    voice_samples TEXT DEFAULT '',-- past emails the user wrote, for tone
    proof TEXT DEFAULT '',        -- case studies / results we may cite
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leads (
    id {pk},
    campaign_id BIGINT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
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
    id {pk},
    lead_id BIGINT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    campaign_id BIGINT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
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
    id {pk},
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,           -- llm_call | lead_imported | email_sent | reply | bounce | ...
    campaign_id BIGINT,
    lead_id BIGINT,
    email_id BIGINT,
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    data_json TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS suppressions (
    id {pk},
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,  -- each account has its own do-not-contact list
    email TEXT NOT NULL,
    reason TEXT NOT NULL,
    ts TEXT NOT NULL
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_events_campaign ON events(campaign_id, kind);
CREATE INDEX IF NOT EXISTS idx_emails_status ON emails(campaign_id, status);
CREATE INDEX IF NOT EXISTS idx_campaigns_user ON campaigns(user_id);
CREATE INDEX IF NOT EXISTS idx_suppressions_email ON suppressions(email);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""

# Columns added after the first release, so older SQLite files pick them up on start.
MIGRATIONS = [("campaigns", "user_id", "BIGINT REFERENCES users(id)")]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def database_url_from_env() -> str:
    """The Postgres URL, if any. Vercel's Neon and Supabase integrations set one of these."""
    for name in ("FINLEY_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL"):
        if os.environ.get(name, "").startswith(("postgres://", "postgresql://")):
            return os.environ[name]
    return ""


class Row(dict):
    """A dict that also allows `row[0]`, like sqlite3.Row, so callers work on either backend."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class DB:
    def __init__(self, path: str):
        """`path` is a SQLite file (or ":memory:") or a postgres:// URL."""
        self.path = path
        self.is_postgres = path.startswith(("postgres://", "postgresql://"))
        self._lock = threading.RLock()
        self._connect()
        self.executescript(self._dialect(SCHEMA))
        self._migrate()
        self.executescript(INDEXES)

    # --- connection -------------------------------------------------------
    def _connect(self) -> None:
        if self.is_postgres:
            import psycopg  # only needed for the hosted setup

            self.conn = psycopg.connect(
                _clean_pg_url(self.path), autocommit=True, row_factory=_pg_row,
                # Poolers in transaction mode (Neon, Supabase) don't support prepared statements.
                prepare_threshold=None,
            )
        else:
            self.conn = sqlite3.connect(self.path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")

    @property
    def is_ephemeral(self) -> bool:
        """True when data won't survive a restart (in-memory, or SQLite under /tmp on a serverless host)."""
        return not self.is_postgres and (self.path == ":memory:" or
                                         (bool(os.environ.get("VERCEL")) and self.path.startswith("/tmp")))

    def _dialect(self, sql: str) -> str:
        if self.is_postgres:
            sql = sql.replace("{pk}", "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY")
            return re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql)
        return sql.replace("{pk}", "INTEGER PRIMARY KEY")

    def _sql(self, sql: str) -> str:
        if self.is_postgres:
            return sql.replace("%", "%%").replace("?", "%s")
        return sql

    def _run(self, sql: str, args: tuple = ()):
        with self._lock:
            try:
                return self.conn.execute(self._sql(sql), args)
            except Exception as e:
                # Serverless instances get frozen and the Postgres connection can drop; retry once.
                if self.is_postgres and type(e).__name__ in ("OperationalError", "InterfaceError") \
                        and self.conn.closed:
                    self._connect()
                    return self.conn.execute(self._sql(sql), args)
                raise

    def executescript(self, script: str) -> None:
        with self._lock:
            if self.is_postgres:
                self.conn.execute(script)
            else:
                self.conn.executescript(script)

    def _migrate(self) -> None:
        for table, column, decl in MIGRATIONS:
            if self.is_postgres:
                self._run(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {decl}")
            elif column not in {r["name"] for r in self.all(f"PRAGMA table_info({table})")}:
                self._run(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        if not self.is_postgres and "user_id" not in {r["name"] for r in self.all("PRAGMA table_info(suppressions)")}:
            # The first release keyed suppressions by email alone; rebuild it with a user column.
            with self.tx():
                self.conn.execute("ALTER TABLE suppressions RENAME TO suppressions_v1")
                self.conn.executescript(self._dialect(SCHEMA))
                self.conn.execute("INSERT INTO suppressions (email, reason, ts) "
                                  "SELECT email, reason, ts FROM suppressions_v1")
                self.conn.execute("DROP TABLE suppressions_v1")

    @contextmanager
    def tx(self) -> Iterator[Any]:
        with self._lock:
            if self.is_postgres:
                with self.conn.transaction():
                    yield self.conn
            else:
                with self.conn:
                    yield self.conn

    # --- queries ----------------------------------------------------------
    def execute(self, sql: str, *args: Any) -> int:
        """Run a statement and return the number of rows it changed."""
        return self._run(sql, args).rowcount

    def one(self, sql: str, *args: Any) -> Any:
        return self._run(sql, args).fetchone()

    def all(self, sql: str, *args: Any) -> list[Any]:
        return self._run(sql, args).fetchall()

    def insert(self, table: str, row: dict[str, Any]) -> int:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        sql = f"INSERT INTO {table} ({cols}) VALUES ({marks})"
        if self.is_postgres:
            return int(self.one(sql + " RETURNING id", *row.values())["id"])
        with self.tx():
            return int(self._run(sql, tuple(row.values())).lastrowid)

    def update(self, table: str, row_id: int, fields: dict[str, Any]) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.tx():
            self._run(f"UPDATE {table} SET {sets} WHERE id = ?", (*fields.values(), row_id))

    def json_get(self, column: str, key: str) -> str:
        """SQL for reading one key of a JSON text column."""
        if not re.fullmatch(r"\w+", key):
            raise ValueError(key)
        if self.is_postgres:
            return f"(({column})::jsonb ->> '{key}')"
        return f"json_extract({column}, '$.{key}')"

    # --- events -----------------------------------------------------------
    def log_event(self, kind: str, **kw: Any) -> int:
        data = kw.pop("data", None)
        row = {"ts": now(), "kind": kind, **kw, "data_json": json.dumps(data or {})}
        return self.insert("events", row)

    # --- ownership --------------------------------------------------------
    def campaign_user(self, campaign_id: int) -> int | None:
        row = self.one("SELECT user_id FROM campaigns WHERE id = ?", campaign_id)
        return row["user_id"] if row else None

    # --- suppression (per account; user_id None is the CLI / single-user list) -------------
    def _owner_clause(self, user_id: int | None) -> tuple[str, tuple]:
        return ("user_id IS NULL", ()) if user_id is None else ("user_id = ?", (user_id,))

    def suppress(self, email: str, reason: str, user_id: int | None = None) -> None:
        email = email.strip().lower()
        clause, args = self._owner_clause(user_id)
        with self.tx():
            self._run(f"DELETE FROM suppressions WHERE email = ? AND {clause}", (email, *args))
            self._run("INSERT INTO suppressions (user_id, email, reason, ts) VALUES (?, ?, ?, ?)",
                      (user_id, email, reason, now()))

    def is_suppressed(self, email: str, user_id: int | None = None) -> bool:
        clause, args = self._owner_clause(user_id)
        return self.one(f"SELECT 1 FROM suppressions WHERE email = ? AND {clause}",
                        email.strip().lower(), *args) is not None


def _pg_row(cursor):
    names = [c.name for c in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values))


def _clean_pg_url(url: str) -> str:
    """Drop query params libpq doesn't know (Supabase's Vercel integration adds `supa=...`)."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in ("supa", "pgbouncer")]
    return urlunsplit(parts._replace(query=urlencode(query)))
