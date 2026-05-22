"""管理端单密码登录：bcrypt 校验 + 短期 session JWT。

不再涉及多用户系统，只有"通过管理密码 → 拿到 session token"这一条路。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import get_settings

settings = get_settings()

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

SESSION_KIND = "admin_session"


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _pwd.verify(password, hashed)
    except Exception:  # noqa: BLE001
        return False


def create_session_token() -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "kind": SESSION_KIND,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.session_token_ttl_hours)).timestamp()),
    }
    return jwt.encode(payload, settings.session_token_secret, algorithm="HS256")


class InvalidSession(Exception):
    pass


def verify_session_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.session_token_secret, algorithms=["HS256"])
    except JWTError as e:
        raise InvalidSession(str(e)) from None
    if payload.get("kind") != SESSION_KIND:
        raise InvalidSession("Not an admin session token")
    return payload
