"""Customer accounts: sign-up, passwords, login sessions and per-user settings."""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from .config import Settings
from .db import DB, now
from .vault import Vault

SESSION_COOKIE = "finley_session"
SESSION_DAYS = 30
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
MIN_PASSWORD = 8

# Settings a user can edit, and the type each is stored as. Secrets are handled separately.
PROFILE_FIELDS = {
    "from_name": str, "from_email": str, "sender_title": str, "company_name": str,
    "company_website": str, "company_description": str, "physical_address": str, "signature": str,
    "smtp_host": str, "smtp_port": int, "smtp_user": str, "daily_cap": int,
    "exclude_domains": str, "min_score": float,
}
SECRET_FIELDS = {"anthropic_key": "anthropic_key_enc", "smtp_password": "smtp_password_enc"}


class AccountError(ValueError):
    pass


# --- passwords --------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    test = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    return hmac.compare_digest(test.hex(), digest)


# --- users ------------------------------------------------------------------
def create_user(db: DB, email: str, password: str, name: str = "") -> int:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise AccountError("Enter a valid email address.")
    if len(password) < MIN_PASSWORD:
        raise AccountError(f"Use a password of at least {MIN_PASSWORD} characters.")
    if db.one("SELECT 1 FROM users WHERE email = ?", email):
        raise AccountError("An account with that email already exists. Log in instead.")
    first = db.one("SELECT COUNT(*) AS n FROM users")["n"] == 0
    uid = db.insert("users", {"email": email, "name": name.strip(), "password_hash": hash_password(password),
                              "created_at": now()})
    db.execute("INSERT INTO user_settings (user_id, from_name, from_email, updated_at) VALUES (?, ?, ?, ?)",
               uid, name.strip(), email, now())
    if first:
        # Self-hosters: the first account takes over campaigns made earlier with the CLI.
        db.execute("UPDATE campaigns SET user_id = ? WHERE user_id IS NULL", uid)
    return uid


def authenticate(db: DB, email: str, password: str) -> int | None:
    user = db.one("SELECT id, password_hash FROM users WHERE email = ?", email.strip().lower())
    if user is None:
        hash_password(password)  # same work either way, so timing doesn't reveal which emails exist
        return None
    if not check_password(password, user["password_hash"]):
        return None
    db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", now(), user["id"])
    return int(user["id"])


def change_password(db: DB, user_id: int, current: str, new: str) -> None:
    user = db.one("SELECT password_hash FROM users WHERE id = ?", user_id)
    if not check_password(current, user["password_hash"]):
        raise AccountError("Your current password is wrong.")
    if len(new) < MIN_PASSWORD:
        raise AccountError(f"Use a password of at least {MIN_PASSWORD} characters.")
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", hash_password(new), user_id)


def delete_user(db: DB, user_id: int) -> None:
    """Remove an account and everything in it."""
    with db.tx():
        for c in db.all("SELECT id FROM campaigns WHERE user_id = ?", user_id):
            db.execute("DELETE FROM events WHERE campaign_id = ?", c["id"])
            db.execute("DELETE FROM emails WHERE campaign_id = ?", c["id"])
            db.execute("DELETE FROM leads WHERE campaign_id = ?", c["id"])
        for table in ("campaigns", "suppressions", "sessions", "user_settings"):
            db.execute(f"DELETE FROM {table} WHERE user_id = ?", user_id)
        db.execute("DELETE FROM users WHERE id = ?", user_id)


# --- sessions ---------------------------------------------------------------
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(db: DB, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    db.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
               _token_hash(token), user_id, now(), expires.isoformat(timespec="seconds"))
    return token


def session_user(db: DB, token: str | None):
    if not token:
        return None
    return db.one("SELECT u.id, u.email, u.name, u.created_at FROM sessions s JOIN users u ON u.id = s.user_id "
                  "WHERE s.token_hash = ? AND s.expires_at > ?", _token_hash(token), now())


def end_session(db: DB, token: str | None) -> None:
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", _token_hash(token))


# --- settings ---------------------------------------------------------------
def get_settings(db: DB, vault: Vault, user_id: int) -> dict:
    """The user's settings for display. Secrets come back only as whether one is saved."""
    row = db.one("SELECT * FROM user_settings WHERE user_id = ?", user_id)
    data = dict(row) if row else {}
    out = {k: data.get(k, "") if data.get(k) is not None else "" for k in PROFILE_FIELDS}
    for name, column in SECRET_FIELDS.items():
        out[f"has_{name}"] = bool(vault.decrypt(data.get(column) or ""))
    out["anthropic_key_hint"] = _hint(vault.decrypt(data.get("anthropic_key_enc") or ""))
    return out


def save_settings(db: DB, vault: Vault, user_id: int, form: dict) -> None:
    """Save the fields present in `form`. A blank secret field keeps the saved secret;
    `clear_<secret>` removes it."""
    fields: dict = {}
    for key, kind in PROFILE_FIELDS.items():
        if key in form:
            value = str(form[key]).strip()
            try:
                fields[key] = kind(value) if kind is not str else value
            except ValueError:
                raise AccountError(f"{key.replace('_', ' ').capitalize()} must be a number.")
    if fields.get("from_email") and not EMAIL_RE.match(fields["from_email"]):
        raise AccountError("Enter a valid sending email address.")
    if "daily_cap" in fields and not 1 <= fields["daily_cap"] <= 500:
        raise AccountError("Daily cap must be between 1 and 500.")
    if "min_score" in fields and not 0 <= fields["min_score"] <= 100:
        raise AccountError("Minimum score must be between 0 and 100.")
    for name, column in SECRET_FIELDS.items():
        if form.get(f"clear_{name}"):
            fields[column] = ""
        elif str(form.get(name) or "").strip():
            if not vault.enabled:
                raise AccountError("This server can't store secrets yet (FINLEY_SECRET_KEY isn't set). "
                                   "Paste your key on each run instead.")
            value = str(form[name]).strip()
            if name == "anthropic_key" and not value.startswith("sk-ant-"):
                raise AccountError("That doesn't look like an Anthropic API key (they start with sk-ant-).")
            fields[column] = vault.encrypt(value)
    if not fields:
        return
    fields["updated_at"] = now()
    if db.one("SELECT 1 FROM user_settings WHERE user_id = ?", user_id) is None:
        db.execute("INSERT INTO user_settings (user_id) VALUES (?)", user_id)
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE user_settings SET {sets} WHERE user_id = ?", *fields.values(), user_id)


def anthropic_key(db: DB, vault: Vault, user_id: int) -> str:
    row = db.one("SELECT anthropic_key_enc FROM user_settings WHERE user_id = ?", user_id)
    return vault.decrypt(row["anthropic_key_enc"] or "") if row else ""


def runtime_settings(base: Settings, db: DB, vault: Vault, user_id: int) -> Settings:
    """The server's Settings with this user's sender profile and inbox laid over it."""
    row = db.one("SELECT * FROM user_settings WHERE user_id = ?", user_id)
    if row is None:
        return dataclasses.replace(base, smtp_host="", smtp_user="", smtp_password="", from_email="")
    r = dict(row)
    return dataclasses.replace(
        base,
        # Hosted accounts never fall back to the server's own inbox.
        smtp_host=r["smtp_host"] or "", smtp_port=int(r["smtp_port"] or 587),
        smtp_user=r["smtp_user"] or "", smtp_password=vault.decrypt(r["smtp_password_enc"] or ""),
        from_name=r["from_name"] or "", from_email=r["from_email"] or r["smtp_user"] or "",
        physical_address=r["physical_address"] or "", daily_cap_per_inbox=int(r["daily_cap"] or 30),
        extra={**base.extra, "sender": {k: r[k] or "" for k in (
            "from_name", "sender_title", "company_name", "company_website", "company_description", "signature")}},
    )


def _hint(key: str) -> str:
    return f"{key[:7]}…{key[-4:]}" if len(key) > 12 else ""
