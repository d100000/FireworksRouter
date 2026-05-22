from __future__ import annotations

import hashlib
import secrets

USER_TOKEN_PREFIX = "sk-fwr-"


def generate_user_token() -> str:
    return f"{USER_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
