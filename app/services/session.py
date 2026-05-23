"""管理端单密码登录：bcrypt 校验 + 短期 session JWT。

不再涉及多用户系统，只有"通过管理密码 → 拿到 session token"这一条路。

直接用 bcrypt 库（不走 passlib）：
- passlib 1.7.4 仍读 bcrypt.__about__.__version__，bcrypt 4.1+ 移除了该属性
  会触发"error reading bcrypt version" 警告，某些环境下直接 ImportError
- 直接用 bcrypt 库更稳，且输出哈希与 passlib 完全兼容（同为 $2b$ 标准格式）
- 主动做 72 字节上限校验（bcrypt 协议硬限），避免新版库静默截断造成的混淆
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.config import get_settings

settings = get_settings()

SESSION_KIND = "admin_session"
DB_SETTING_KEY = "admin.password_hash"
BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    """生成 bcrypt 哈希（cost factor 12）。"""
    pw_bytes = password.encode("utf-8")
    if len(pw_bytes) > BCRYPT_MAX_BYTES:
        raise ValueError(
            f"密码字节长度 {len(pw_bytes)} 超出 bcrypt 上限 {BCRYPT_MAX_BYTES}（中文 ≈ 3 字节）"
        )
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """校验密码。无效 / 超长 / 哈希格式错都返回 False（不抛异常）。"""
    if not password or not hashed:
        return False
    try:
        pw_bytes = password.encode("utf-8")
        # 超过 72 字节直接拒绝（不要静默截断 — 那样会让两个不同密码哈希成同一个）
        if len(pw_bytes) > BCRYPT_MAX_BYTES:
            return False
        return bcrypt.checkpw(pw_bytes, hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # hashed 格式不对（如不是 $2b$ 开头）
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
