"""Encryption for secrets users save in settings (their Anthropic key, their SMTP password).

The encryption key comes from FINLEY_SECRET_KEY, which lives in the host's environment and never in
the database, so a copy of the database alone doesn't expose anyone's keys. Self-hosters who don't
set it get a random key generated once and saved next to the SQLite file.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class Vault:
    def __init__(self, secret: str | None):
        self._fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())) if secret else None

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        if not self._fernet:
            raise RuntimeError("Set FINLEY_SECRET_KEY to save secrets.")
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        if not token or not self._fernet:
            return ""
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:  # the secret key changed; the user has to enter it again
            return ""


def vault_from_env(db_path: str) -> Vault:
    secret = os.environ.get("FINLEY_SECRET_KEY", "")
    if secret:
        return Vault(secret)
    if os.environ.get("VERCEL") or db_path.startswith(("postgres://", "postgresql://")):
        # Hosted: never invent a key that would vanish with the instance. Saving secrets stays off.
        return Vault(None)
    if db_path == ":memory:":
        return Vault(secrets.token_urlsafe(32))
    key_file = Path(db_path).with_name(Path(db_path).name + ".secret")
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(32))
        key_file.chmod(0o600)
    return Vault(key_file.read_text().strip())
