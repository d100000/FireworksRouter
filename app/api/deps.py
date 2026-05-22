from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import ApiKey, ApiKeyStatus
from app.services.session import InvalidSession, verify_session_token
from app.utils.tokens import hash_token

settings = get_settings()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class APIError(HTTPException):
    """HTTPException 的子类，但 detail 直接是 OpenAI 错误结构 {error: {...}}。

    FastAPI 默认把 HTTPException 的 detail 包成 {"detail": ...} 一层。
    我们想要 {"error": {...}} 直接作为顶层，需要用自定义异常处理器（main.py 中注册）。
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        error_type: str = "invalid_api_key",
        error_code: str | None = None,
        headers: dict | None = None,
    ):
        self.api_error_payload = {"error": {"message": message, "type": error_type}}
        if error_code:
            self.api_error_payload["error"]["code"] = error_code
        super().__init__(status_code=status_code, detail=self.api_error_payload, headers=headers)


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise APIError(
            status.HTTP_401_UNAUTHORIZED,
            "Missing Authorization header",
            "authentication_error",
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise APIError(
            status.HTTP_401_UNAUTHORIZED,
            "Bad Authorization scheme",
            "authentication_error",
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
    raise APIError(
        status.HTTP_401_UNAUTHORIZED,
        "Admin credentials required",
        "authentication_error",
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
        raise APIError(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid api key",
            "authentication_error",
            error_code="invalid_api_key",
        )
    if record.status != ApiKeyStatus.active:
        raise APIError(
            status.HTTP_401_UNAUTHORIZED,
            "API key is disabled",
            "authentication_error",
            error_code="api_key_disabled",
        )
    if not record.is_usable:
        raise APIError(
            status.HTTP_402_PAYMENT_REQUIRED,
            "API key expired or out of quota",
            "insufficient_quota",
            error_code="insufficient_quota",
        )
    return record


ApiKeyDep = Annotated[ApiKey, Depends(require_api_key)]
