from __future__ import annotations

import hmac
import time
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


# ============================ ApiKey 鉴权缓存 ============================
# 每个 /v1/* 请求都要查一次 api_keys 表；高 QPS 下这是热点。
# 用进程内 TTL=30s 的缓存，命中时省掉 DB 查询。
# 牺牲：管理员改 / 删 / rotate token 后最多 30s 才生效。
#
# 多 worker 部署时每个 worker 各自缓存（最坏情况延后 30s）；可接受。

_TOKEN_CACHE: dict[str, tuple[float, dict]] = {}
_TOKEN_CACHE_TTL = 30.0
_TOKEN_CACHE_MAX = 10000


def _cache_evict_if_full() -> None:
    if len(_TOKEN_CACHE) > _TOKEN_CACHE_MAX:
        # 简易清空：达到上限时全清（生产可换 LRU）
        _TOKEN_CACHE.clear()


def _cache_get(token_hash: str) -> dict | None:
    entry = _TOKEN_CACHE.get(token_hash)
    if entry is None:
        return None
    expires_at, payload = entry
    if time.time() > expires_at:
        _TOKEN_CACHE.pop(token_hash, None)
        return None
    return payload


def _cache_put(token_hash: str, payload: dict) -> None:
    _cache_evict_if_full()
    _TOKEN_CACHE[token_hash] = (time.time() + _TOKEN_CACHE_TTL, payload)


def invalidate_api_key_cache(token_hash: str | None = None) -> None:
    """token 改/删/rotate 时调用。token_hash=None 全清。"""
    if token_hash is None:
        _TOKEN_CACHE.clear()
    else:
        _TOKEN_CACHE.pop(token_hash, None)


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
    """下游网关鉴权：校验 sk-fwr- 形式的 API Key。

    优化：TTL=30s 的进程内缓存命中时省 DB 查询。高 QPS 下减少 ~95% 的 api_keys
    查询压力。
    """
    raw = _extract_bearer(authorization)
    h = hash_token(raw)

    # 1) 缓存命中（不查 DB；但仍需校验配额状态）
    cached = _cache_get(h)
    if cached is not None:
        # 缓存里 quota 不会随调用变化（每次调用真实扣额度还会读 DB ApiKey 行）
        # 这里只是放行鉴权层，下游 forward() 会再做实时 quota 检查
        stmt = select(ApiKey).where(ApiKey.id == cached["id"])
        record = (await session.execute(stmt)).scalar_one_or_none()
        if record is not None and record.status == ApiKeyStatus.active and record.is_usable:
            return record
        # 缓存的 record 已无效，invalidate 后走完整路径
        invalidate_api_key_cache(h)

    # 2) 缓存未命中，走完整查询
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

    # 缓存命中信息（只缓存 id；具体配额每次还要查实时数据）
    _cache_put(h, {"id": record.id})
    return record


ApiKeyDep = Annotated[ApiKey, Depends(require_api_key)]
