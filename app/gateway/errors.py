"""统一错误处理：参考 OpenAI / one-api / new-api 的实践。

设计原则：
- 输出严格遵守 OpenAI 错误体 {"error": {message, type, code, param}}
- 上游 raw 错误用 parse_upstream_error 兜底解析 7 种字段位置
- 状态码按错误类型分类（不一刀切 500/502）
- 429 必须带 Retry-After header
- "无可用 Key" 用 503（SDK 会自动重试），不用 500
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fastapi.responses import JSONResponse


# ============================= 错误体结构 =============================


@dataclass
class OpenAIError:
    """OpenAI 官方错误结构。"""
    message: str
    type: str
    code: str | None = None
    param: str | None = None

    def to_dict(self) -> dict:
        out = {"message": self.message, "type": self.type}
        if self.code is not None:
            out["code"] = self.code
        if self.param is not None:
            out["param"] = self.param
        return out


# ============================= 异常类继承体系 =============================


class GatewayException(Exception):
    """所有网关异常的基类。"""
    http_status: int = 500
    error_type: str = "api_error"
    error_code: str | None = None
    retry_after: int | None = None

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        param: str | None = None,
        upstream_status: int = 0,
        upstream_body: str | None = None,
        request_id: str | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.error_code
        self.param = param
        self.upstream_status = upstream_status
        self.upstream_body = upstream_body
        self.request_id = request_id
        if retry_after is not None:
            self.retry_after = retry_after

    def to_openai_error(self) -> OpenAIError:
        return OpenAIError(
            message=self.message,
            type=self.error_type,
            code=self.code,
            param=self.param,
        )

    def extra(self) -> dict[str, Any]:
        out = {}
        if self.upstream_status:
            out["upstream_status"] = self.upstream_status
        if self.request_id:
            out["request_id"] = self.request_id
        return out


class InvalidRequestError(GatewayException):
    http_status = 400
    error_type = "invalid_request_error"


class AuthenticationError(GatewayException):
    http_status = 401
    error_type = "authentication_error"
    error_code = "invalid_api_key"


class PermissionError_(GatewayException):
    http_status = 403
    error_type = "permission_error"


class ModelNotFoundError(GatewayException):
    http_status = 404
    error_type = "not_found_error"
    error_code = "model_not_found"


class RequestTooLargeError(GatewayException):
    http_status = 413
    error_type = "request_too_large"


class RateLimitError(GatewayException):
    http_status = 429
    error_type = "rate_limit_error"
    error_code = "rate_limit_exceeded"


class InsufficientQuotaError(GatewayException):
    """余额不足 / 配额耗尽 — OpenAI 官方用 429。"""
    http_status = 429
    error_type = "insufficient_quota"
    error_code = "insufficient_quota"


class UpstreamError(GatewayException):
    """上游服务端错误（5xx）。"""
    http_status = 502
    error_type = "upstream_error"


class OverloadedError(GatewayException):
    """上游过载。"""
    http_status = 503
    error_type = "overloaded_error"


class ServiceUnavailableError(GatewayException):
    """所有候选 Key 都不可用 — 用 503 让 SDK 自动重试。"""
    http_status = 503
    error_type = "service_unavailable"
    error_code = "no_available_upstream"


class TimeoutError_(GatewayException):
    http_status = 504
    error_type = "timeout_error"


# ============================= 上游 raw 错误兜底解析 =============================


_DEACTIVATE_KEYWORDS = (
    "invalid api key",
    "incorrect api key",
    "invalid_api_key",
    "api key not found",
    "key not found",
    "account_deactivated",
    "account deactivated",
    "suspended",
    "banned",
    "revoked",
    "expired",
    "insufficient_quota",
    "insufficient quota",
    "quota exceeded",
    "billing_hard_limit_reached",
    "you exceeded your current quota",
)


def parse_upstream_error(body: str | bytes | None, default_message: str = "") -> str:
    """从上游错误响应里按 one-api 的 GeneralErrorResponse 兜底逻辑取 message。

    优先级：error.message → message → msg → err → error_msg → header.message → response.error.message。
    完全无法解析 → 返回 default_message 或 body 前 500 字。
    """
    if body is None:
        return default_message
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return default_message
    body = body.strip()
    if not body:
        return default_message
    try:
        obj = json.loads(body)
    except Exception:  # noqa: BLE001
        return body[:500] or default_message

    if not isinstance(obj, dict):
        return body[:500] or default_message

    # error.message
    err = obj.get("error")
    if isinstance(err, dict):
        msg = err.get("message")
        if msg:
            return str(msg)
    elif isinstance(err, str) and err:
        return err

    # 平铺字段
    for k in ("message", "msg", "err", "error_msg", "detail"):
        v = obj.get(k)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict):
            mm = v.get("message")
            if mm:
                return str(mm)

    # header.message
    header = obj.get("header")
    if isinstance(header, dict):
        v = header.get("message")
        if v:
            return str(v)

    # response.error.message
    resp = obj.get("response")
    if isinstance(resp, dict):
        re = resp.get("error")
        if isinstance(re, dict) and re.get("message"):
            return str(re["message"])

    return default_message or body[:500]


def should_disable_key(body: str | bytes | None) -> bool:
    """根据上游响应内容判断是否应该立即禁用 Key（参考 new-api 关键词匹配）。"""
    if body is None:
        return False
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return False
    lower = body.lower()
    return any(kw in lower for kw in _DEACTIVATE_KEYWORDS)


# ============================= 序列化 =============================


def to_error_response(
    exc: GatewayException,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """把异常序列化为 OpenAI 兼容的错误响应。"""
    payload = {"error": exc.to_openai_error().to_dict()}
    extra = exc.extra()
    if extra:
        payload["error"]["details"] = extra

    resp_headers = dict(headers or {})
    if exc.retry_after is not None:
        resp_headers["Retry-After"] = str(exc.retry_after)
    if exc.request_id:
        resp_headers["X-Fwr-Request-Id"] = exc.request_id

    return JSONResponse(
        status_code=exc.http_status,
        content=payload,
        headers=resp_headers,
    )


def classify_upstream_status(http_status: int, body: str | bytes | None) -> type[GatewayException]:
    """根据上游 HTTP 状态码 + body 关键词，返回对应的异常类。"""
    if http_status in (401,):
        return AuthenticationError
    if http_status == 402:
        return InsufficientQuotaError
    if http_status == 403:
        if should_disable_key(body):
            return AuthenticationError  # 实际是凭据问题
        return PermissionError_
    if http_status == 404:
        return ModelNotFoundError
    if http_status == 413:
        return RequestTooLargeError
    if http_status == 408:
        return TimeoutError_
    if http_status == 429:
        return InsufficientQuotaError if should_disable_key(body) else RateLimitError
    if http_status == 503:
        return OverloadedError
    if 500 <= http_status < 600:
        return UpstreamError
    if 400 <= http_status < 500:
        return InvalidRequestError
    return UpstreamError
