"""
crypto.py
=========
At-rest encryption for connector OAuth tokens. Tokens are the keys to a
customer's Notion/Slack/etc. — they must not sit in the database as plaintext.

Scheme: values are stored with an explicit prefix so the format is unambiguous
and migratable:
    "enc:<fernet>"   encrypted with CONNECTOR_ENCRYPTION_KEY
    "plain:<value>"  stored unencrypted (only when no key is configured)

Set the key in production:
    CONNECTOR_ENCRYPTION_KEY=$(python -c "from crypto import generate_key; print(generate_key())")

If the key is unset, tokens are stored as "plain:" with a loud warning — fine
for local dev, NOT acceptable in production.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("krab.crypto")

_FERNET = None
_WARNED = False


def _fernet():
    global _FERNET
    if _FERNET is not None:
        return _FERNET
    key = os.getenv("CONNECTOR_ENCRYPTION_KEY")
    if not key:
        return None
    from cryptography.fernet import Fernet

    _FERNET = Fernet(key.encode() if isinstance(key, str) else key)
    return _FERNET


def generate_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def encrypt(plaintext: Optional[str]) -> Optional[str]:
    if plaintext is None:
        return None
    if plaintext == "":
        return ""
    f = _fernet()
    if f is None:
        global _WARNED
        if not _WARNED:
            logger.warning(
                "CONNECTOR_ENCRYPTION_KEY not set — storing tokens in PLAINTEXT. "
                "Set it before going to production."
            )
            _WARNED = True
        return "plain:" + plaintext
    return "enc:" + f.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: Optional[str]) -> str:
    if not ciphertext:
        return ""
    if ciphertext.startswith("plain:"):
        return ciphertext[len("plain:") :]
    if ciphertext.startswith("enc:"):
        f = _fernet()
        if f is None:
            raise RuntimeError(
                "Found an encrypted token but CONNECTOR_ENCRYPTION_KEY is not set."
            )
        return f.decrypt(ciphertext[len("enc:") :].encode()).decode()
    # Legacy / unprefixed value: treat as plaintext.
    return ciphertext
