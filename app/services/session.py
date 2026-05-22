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
DB_SETTING_KEY = "admin.password_hash"


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _pwd.verify(password, hashed)
    except Exception:  # noqa: BLE001
        return False


def get_effective_password_hash() -> str:
    """DB 中如果有 admin.password_hash 则用 DB 的（管理员改过密码）；否则用 .env 的初始值。"""
    from app.services import settings as settings_svc
    db_val = settings_svc.get(DB_SETTING_KEY)
    if db_val and isinstance(db_val, str) and db_val.startswith("$2"):
        return db_val
    return settings.admin_password_hash


async def update_admin_password(new_password: str) -> None:
    from app.services import settings as settings_svc
    await settings_svc.set_value(
        DB_SETTING_KEY,
        hash_password(new_password),
        description="Admin login password (bcrypt). Overrides .env ADMIN_PASSWORD_HASH.",
    )


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
