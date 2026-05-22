from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import ApiKey, ApiKeyStatus
from app.services.session import InvalidSession, verify_session_token
from app.utils.tokens import hash_token

settings = get_settings()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"message": "Missing Authorization header", "type": "invalid_api_key"}},
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"message": "Bad Authorization scheme", "type": "invalid_api_key"}},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return parts[1].strip()


def _is_session_token(token: str) -> bool:
    try:
        verify_session_token(token)
        return True
    except InvalidSession:
        return False


async def require_admin(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> None:
    """管理鉴权双通道：环境 ADMIN_TOKEN（CLI/CI）或 session JWT（UI 登录）。"""
    token = _extract_bearer(authorization)
    if hmac.compare_digest(token, settings.admin_token):
        return
    if _is_session_token(token):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": {"message": "Admin credentials required", "type": "invalid_api_key"}},
    )


async def require_api_key(
    session: SessionDep,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> ApiKey:
    """下游网关鉴权：校验 sk-fwr- 形式的 API Key。"""
    raw = _extract_bearer(authorization)
    h = hash_token(raw)
    stmt = select(ApiKey).where(ApiKey.token_hash == h)
    record = (await session.execute(stmt)).scalar_one_or_none()
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"message": "Invalid api key", "type": "invalid_api_key"}},
        )
    if record.status != ApiKeyStatus.active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"message": "API key is disabled", "type": "invalid_api_key"}},
        )
    if not record.is_usable:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"error": {"message": "API key expired or out of quota", "type": "insufficient_quota"}},
        )
    return record


ApiKeyDep = Annotated[ApiKey, Depends(require_api_key)]
