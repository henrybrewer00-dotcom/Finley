"""Settings, read from environment variables (or a .env file in the working directory)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Settings:
    db_path: str = "finley.db"
    # Models. Haiku does the cheap bulk work, Opus writes the emails.
    scorer_model: str = "claude-haiku-5-5"
    research_model: str = "claude-haiku-5-5"
    judge_model: str = "claude-haiku-5-5"
    writer_model: str = "claude-opus-5-5"
    writer_effort: str = "high"
    # Writing gates
    judge_min_score: float = 4.0
    max_rewrites: int = 2
    # Sending
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_name: str = ""
    from_email: str = ""
    physical_address: str = ""
    daily_cap_per_inbox: int = 30
    # Sources
    treg_base_url: str = "https://treg.to"
    treg_token: str = ""
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        env = os.environ
        return cls(
            # Serverless hosts (Vercel) only allow writes under /tmp, and it is wiped between instances.
            db_path=env.get("FINLEY_DB", "/tmp/finley.db" if env.get("VERCEL") else cls.db_path),
            scorer_model=env.get("FINLEY_SCORER_MODEL", cls.scorer_model),
            research_model=env.get("FINLEY_RESEARCH_MODEL", cls.research_model),
            judge_model=env.get("FINLEY_JUDGE_MODEL", cls.judge_model),
            writer_model=env.get("FINLEY_WRITER_MODEL", cls.writer_model),
            writer_effort=env.get("FINLEY_WRITER_EFFORT", cls.writer_effort),
            judge_min_score=float(env.get("FINLEY_JUDGE_MIN_SCORE", cls.judge_min_score)),
            max_rewrites=int(env.get("FINLEY_MAX_REWRITES", cls.max_rewrites)),
            smtp_host=env.get("SMTP_HOST", ""),
            smtp_port=int(env.get("SMTP_PORT", 587)),
            smtp_user=env.get("SMTP_USER", ""),
            smtp_password=env.get("SMTP_PASSWORD", ""),
            from_name=env.get("FROM_NAME", ""),
            from_email=env.get("FROM_EMAIL", env.get("SMTP_USER", "")),
            physical_address=env.get("PHYSICAL_ADDRESS", ""),
            daily_cap_per_inbox=int(env.get("DAILY_CAP_PER_INBOX", cls.daily_cap_per_inbox)),
            treg_base_url=env.get("TREG_BASE_URL", cls.treg_base_url),
            treg_token=env.get("TREG_TOKEN", ""),
        )
