"""Send approved emails from the user's own inbox over SMTP (Gmail, Outlook, any provider).

Plain text only, a one-click List-Unsubscribe header, a per-inbox daily cap, and a
suppression check right before every send.
"""

from __future__ import annotations

import smtplib
import sqlite3
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .config import Settings
from .db import DB, now


class SendError(RuntimeError):
    pass


def sent_today(db: DB, from_email: str) -> int:
    day = datetime.now(timezone.utc).date().isoformat()
    row = db.one("SELECT COUNT(*) AS n FROM events WHERE kind = 'email_sent' AND ts >= ? "
                 "AND json_extract(data_json, '$.from') = ?", day, from_email)
    return int(row["n"]) if row else 0


def build_message(settings: Settings, email: sqlite3.Row, lead: sqlite3.Row) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((settings.from_name, settings.from_email))
    msg["To"] = lead["email"]
    msg["Subject"] = email["subject"]
    msg["Message-ID"] = make_msgid(domain=settings.from_email.split("@")[-1])
    unsubscribe = f"mailto:{settings.from_email}?subject=unsubscribe"
    msg["List-Unsubscribe"] = f"<{unsubscribe}>"
    footer = "\n\n--\nNot relevant? Reply \"unsubscribe\" and I won't email again."
    if settings.physical_address:
        footer += f"\n{settings.physical_address}"
    msg.set_content(email["body"].rstrip() + footer)
    return msg


def send_email(db: DB, settings: Settings, email_id: int, smtp_factory=smtplib.SMTP) -> str:
    email = db.one("SELECT * FROM emails WHERE id = ?", email_id)
    if email is None:
        raise SendError(f"No email {email_id}")
    if email["status"] != "approved":
        raise SendError("Only approved emails can be sent.")
    lead = db.one("SELECT * FROM leads WHERE id = ?", email["lead_id"])
    if db.is_suppressed(lead["email"]):
        db.update("emails", email_id, {"status": "rejected"})
        raise SendError(f"{lead['email']} is on the do-not-contact list.")
    if not (settings.smtp_host and settings.from_email):
        raise SendError("Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD and FROM_EMAIL to send.")
    if sent_today(db, settings.from_email) >= settings.daily_cap_per_inbox:
        raise SendError(f"Daily cap of {settings.daily_cap_per_inbox} reached for {settings.from_email}.")

    msg = build_message(settings, email, lead)
    try:
        with smtp_factory(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        db.update("emails", email_id, {"status": "failed"})
        db.log_event("send_failed", campaign_id=email["campaign_id"], lead_id=lead["id"],
                     email_id=email_id, data={"error": repr(e)})
        raise SendError(str(e)) from e

    db.update("emails", email_id, {"status": "sent", "message_id": msg["Message-ID"], "sent_at": now()})
    db.update("leads", lead["id"], {"status": "contacted"})
    db.log_event("email_sent", campaign_id=email["campaign_id"], lead_id=lead["id"], email_id=email_id,
                 data={"from": settings.from_email, "format": email["format_key"]})
    return msg["Message-ID"]


OUTCOMES = ("reply_positive", "reply_neutral", "reply_negative", "meeting_booked", "bounce", "unsubscribe")


def record_outcome(db: DB, email_id: int, outcome: str) -> None:
    """Log what happened after a send. Bounces and unsubscribes join the suppression list."""
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    email = db.one("SELECT * FROM emails WHERE id = ?", email_id)
    lead = db.one("SELECT * FROM leads WHERE id = ?", email["lead_id"])
    db.log_event(outcome, campaign_id=email["campaign_id"], lead_id=lead["id"], email_id=email_id,
                 data={"format": email["format_key"]})
    if outcome in ("bounce", "unsubscribe"):
        db.suppress(lead["email"], outcome)
    if outcome.startswith("reply") or outcome == "meeting_booked":
        db.update("leads", lead["id"], {"status": "replied"})
