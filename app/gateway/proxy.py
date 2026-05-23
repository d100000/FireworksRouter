from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import get_settings
from app.crypto import decrypt_key
from app.db import session_scope
from app.models import ApiKey, Model, RequestLog, UpstreamKey
from app.gateway import errors as gw_errors
from app.services import cooldown, scheduler
from app.services.models import ResolvedModel
from app.utils.logger import logger

settings = get_settings()


@dataclass
class GatewayUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0


_STRIP_REQUEST_HEADERS = {
    "host", "authorization", "content-length", "accept-encoding", "connection",
    "transfer-encoding", "expect", "proxy-connection", "x-forwarded-for", "x-forwarded-proto",
    "x-real-ip",
    # Anthropic SDK 的 headers — 不能透传给 Fireworks，会让 Fireworks 当成另一个凭据用导致 401
    "x-api-key", "anthropic-version", "anthropic-beta", "anthropic-dangerous-direct-browser-access",
    # Gemini SDK headers
    "x-goog-api-key", "x-goog-api-client",
}

_STRIP_RESPONSE_HEADERS = {
    "content-length", "content-encoding", "transfer-encoding", "connection",
    "keep-alive", "server", "date",
}


def _build_upstream_headers(request: Request, api_key_plain: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in request.headers.items():
        if k.lower() in _STRIP_REQUEST_HEADERS:
            continue
        out[k] = v
    out["Authorization"] = f"Bearer {api_key_plain}"
    out["Accept-Encoding"] = "identity"
    return out


def _build_response_headers(upstream: httpx.Response) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in upstream.headers.items():
        if k.lower() in _STRIP_RESPONSE_HEADERS:
            continue
        out[k] = v
    return out


def _parse_sse_usage(line_bytes: bytes) -> GatewayUsage | None:
    if not line_bytes.startswith(b"data:"):
        return None
    payload = line_bytes[5:].strip()
    if not payload or payload == b"[DONE]":
        return None
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return None
    usage = obj.get("usage") if isinstance(obj, dict) else None
    if not isinstance(usage, dict):
        return None
    return GatewayUsage(
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        cached_tokens=int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
    )


def _friendlify_upstream_error(msg: str, status: int) -> str:
    """已知的上游报错模式 → 给客户端更可操作的提示。

    命中规则：
      - Regex lookahead/lookbehind/backreferences not supported
      - Error resolving schema reference '#/$defs/...'（Fireworks resolver bug）
    """
    if status != 400 or not msg:
        return msg
    low = msg.lower()

    if (
        "regex lookahead" in low
        or "regex lookbehind" in low
        or "lookahead (?=" in low
        or "lookbehind (?<" in low
        or "regex backreferences" in low
        or "backreferences not supported" in low
    ):
        return (
            f"{msg}\n\n[gateway hint] "
            "Fireworks 上游 RE2 正则引擎不支持 lookahead/lookbehind/backreference。"
            "网关已尝试自动剥除 tools[].input_schema.properties.*.pattern 中的此类正则，"
            "但仍有未覆盖的位置 — 请检查你的工具 schema（含嵌套 oneOf/anyOf/items 等），"
            "把含 (?=...)/(?!...)/(?<=...)/(?<!...)/\\N 的 pattern 简化或移除。"
        )

    if (
        "error resolving schema reference" in low
        or "schema reference '#/" in low
        or "'noneType' object has no attribute 'lookup'".lower() in low  # 上游 resolver bug 的具体异常签名
    ):
        return (
            f"{msg}\n\n[gateway hint] "
            "Fireworks 上游 schema resolver 解析 $ref/$defs 时出错（上游 bug）。"
            "网关已尝试把 tool input_schema 里 #/$defs/* 和 #/definitions/* 的 $ref 全部内联展开，"
            "但仍有未覆盖的位置 — 检查工具 schema 是否有：① 外部 $ref（http://...）；"
            "② $ref 指向 $defs 之外的位置（如 #/properties/X）；③ 客户端在 SDK 层 escape 后形态变化。"
            "把这些 $ref 改成内联结构或简化掉即可。"
        )

    return msg


def _ensure_stream_usage(body: dict[str, Any]) -> dict[str, Any]:
    if not body.get("stream"):
        return body
    opts = dict(body.get("stream_options") or {})
    opts.setdefault("include_usage", True)
    body["stream_options"] = opts
    return body


async def _persist_log(
    *,
    request_id: str,
    api_key: ApiKey | None,
    upstream_key: UpstreamKey | None,
    model_record: Model | None,
    public_model: str,
    upstream_model: str,
    endpoint: str,
    stream: bool,
    usage: GatewayUsage,
    status_code: int,
    error_code: str | None,
    error_message: str | None,
    retry_count: int,
    ttft_ms: int,
    latency_ms: int,
    client_ip: str | None,
    user_agent: str | None,
) -> None:
    """请求结束后落库：累计计数 + 计费扣额度 + 写 RequestLog + 异步上报 metrics_queue。"""
    from app.services import metrics as metrics_svc

    async with session_scope() as session:
        upstream_key_id = upstream_key.id if upstream_key else None
        upstream_key_preview = upstream_key.key_preview if upstream_key else None
        api_key_id = api_key.id if api_key else None
        api_key_label = api_key.label if api_key else None
        api_key_preview = api_key.token_preview if api_key else None

        db_model = await session.get(Model, model_record.id) if model_record else None
        raw_cost = 0.0
        if db_model is not None:
            raw_cost = db_model.compute_cost(
                usage.prompt_tokens, usage.completion_tokens, usage.cached_tokens
            )
        # rate_multiplier 不再来自 user.group，简化为 1.0；后续可作为系统配置
        rate_multiplier = 1.0
        billed_cost = raw_cost * rate_multiplier

        if upstream_key_id is not None:
            db_key = await session.get(UpstreamKey, upstream_key_id)
            if db_key is not None:
                db_key.total_input_tokens += usage.prompt_tokens
                db_key.total_output_tokens += usage.completion_tokens
                db_key.total_cost_usd += raw_cost
                if db_key.balance_usd >= raw_cost:
                    db_key.balance_usd -= raw_cost
                if 200 <= status_code < 400:
                    db_key.last_success_at = datetime.now(timezone.utc)
                    db_key.last_error_message = None
                    db_key.consecutive_failures = 0
                elif status_code >= 400:
                    db_key.last_failed_at = datetime.now(timezone.utc)
                    if error_message:
                        db_key.last_error_message = error_message[:500]
                    db_key.consecutive_failures += 1

        if api_key is not None:
            db_tok = await session.get(ApiKey, api_key.id)
            if db_tok is not None:
                db_tok.total_requests += 1
                db_tok.total_input_tokens += usage.prompt_tokens
                db_tok.total_output_tokens += usage.completion_tokens
                db_tok.last_used_at = datetime.now(timezone.utc)
                if not db_tok.unlimited_quota:
                    db_tok.remaining_quota_usd = max(0.0, db_tok.remaining_quota_usd - billed_cost)
                    db_tok.used_quota_usd += billed_cost

        session.add(
            RequestLog(
                request_id=request_id,
                api_key_id=api_key_id, api_key_label=api_key_label,
                api_key_preview=api_key_preview,
                upstream_key_id=upstream_key_id, upstream_key_preview=upstream_key_preview,
                model_id=db_model.id if db_model else None,
                public_model=public_model, upstream_model=upstream_model,
                endpoint=endpoint, stream=stream,
                prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
                cached_tokens=usage.cached_tokens,
                total_tokens=usage.total_tokens or (usage.prompt_tokens + usage.completion_tokens),
                raw_cost_usd=raw_cost, billed_cost_usd=billed_cost,
                rate_multiplier=rate_multiplier,
                status_code=status_code, error_code=error_code, error_message=error_message,
                retry_count=retry_count, ttft_ms=ttft_ms, latency_ms=latency_ms,
                client_ip=client_ip, user_agent=user_agent,
            )
        )

    # 异步上报到 metrics_queue（不阻塞，错误吞掉）
    try:
        await metrics_svc.enqueue(
            metrics_svc.MetricEvent(
                upstream_key_id=upstream_key_id,
                success=(200 <= status_code < 400),
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cost_usd=billed_cost,
                latency_ms=latency_ms,
                timestamp=datetime.now(timezone.utc),
            )
        )
    except Exception:  # noqa: BLE001
        pass


async def _try_single(
    *,
    request_id: str,
    api_key_plain: str,
    upstream_url: str,
    request_headers: dict[str, str],
    request_body: bytes,
    timeout: float,
    is_stream: bool,
) -> tuple[httpx.Response, httpx.AsyncClient] | None:
    # 注：每请求新建 client 是为了让 stream 生命周期跟着请求走（流式时 client 必须
    # 等到上游响应完成才能关）。如果需要更高吞吐，可以引入一个全局 connection pool
    # （httpx.AsyncClient 内部已经做了 keep-alive；只要进程级别复用 transport 就行）。
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=10.0),
        proxy=settings.proxy_url,
        limits=httpx.Limits(max_keepalive_connections=50, max_connections=200),
    )
    try:
        req = client.build_request(
            "POST", upstream_url, headers=request_headers, content=request_body
        )
        resp = await client.send(req, stream=is_stream)
        return resp, client
    except Exception as e:  # noqa: BLE001
        await client.aclose()
        logger.warning("[{}] upstream connection error: {}", request_id, e)
        return None


async def forward(
    *,
    request: Request,
    api_key: ApiKey,
    body: dict[str, Any],
    resolved: ResolvedModel,
    endpoint_path: str,
    allow_stream: bool = True,
) -> JSONResponse | StreamingResponse:
    """统一的转发入口：流式与非流式都走这里。

    重写后采用 cooldown.apply_error 决策器：
    - 按 HTTP 状态码差异化退避（429/401/402/403/404/5xx/timeout 各异）
    - 失败时根据 cool_whole_key / cool_key_model 决定影响范围
    - max_retry_credentials 限制一次请求最多尝试几把 Key
    """
    request_id = f"fwr-{uuid.uuid4().hex[:24]}"
    is_stream = bool(body.get("stream")) and allow_stream
    if not allow_stream:
        body.pop("stream", None)

    # 改写 body 里的 model 字段为真实 fireworks_path
    body["model"] = resolved.upstream_path
    body = _ensure_stream_usage(body) if is_stream else body
    payload_bytes = json.dumps(body).encode("utf-8")

    upstream_url = f"{settings.fireworks_inference_base_url}{endpoint_path}"
    timeout = float(settings.gateway_default_timeout_s)
    model_id = resolved.record.id if resolved.record else None

    started = time.perf_counter()
    tried_ids: set[int] = set()
    last_decision: cooldown.Decision | None = None
    last_error_status = 0
    last_error_body: str | None = None
    chosen_key: UpstreamKey | None = None
    upstream_response: httpx.Response | None = None
    upstream_client: httpx.AsyncClient | None = None

    max_attempts = max(1, min(settings.gateway_max_retry, settings.gateway_max_retry_credentials))

    for attempt in range(max_attempts):
        async with session_scope() as session:
            try:
                candidate = await scheduler.pick(
                    session, exclude_ids=tried_ids,
                    requested_model_id=model_id,
                    request_body=body, api_key_id=api_key.id,
                )
            except scheduler.NoAvailableUpstream:
                if attempt == 0 and chosen_key is None:
                    exc = gw_errors.ServiceUnavailableError(
                        "当前无可用上游 Key（全部在冷却或被禁用）",
                        request_id=request_id,
                        retry_after=30,
                    )
                    return gw_errors.to_error_response(exc)
                break
            tried_ids.add(candidate.id)
            chosen_key = candidate
            chosen_api_key_plain = decrypt_key(candidate.key_encrypted)
            upstream_id = candidate.id

        headers = _build_upstream_headers(request, chosen_api_key_plain)
        attempt_result = await _try_single(
            request_id=request_id, api_key_plain=chosen_api_key_plain,
            upstream_url=upstream_url, request_headers=headers,
            request_body=payload_bytes, timeout=timeout, is_stream=is_stream,
        )

        if attempt_result is None:
            # 网络异常 → 走 timeout 决策
            decision = await cooldown.apply_error(
                upstream_id, model_id, http_status=0, error_message="network_error_or_timeout",
            )
            last_decision = decision
            last_error_status = 0
            last_error_body = "connect_error"
            if decision.retryable:
                continue
            break

        resp, client = attempt_result

        # 成功
        if 200 <= resp.status_code < 400:
            upstream_response = resp
            upstream_client = client
            await cooldown.apply_success(upstream_id, model_id)
            break

        # 失败 → 读 body + 决策
        try:
            last_error_body = (await resp.aread()).decode("utf-8", errors="replace")[:2000]
        except Exception:  # noqa: BLE001
            last_error_body = None
        last_error_status = resp.status_code
        await resp.aclose()
        await client.aclose()

        decision = await cooldown.apply_error(
            upstream_id, model_id,
            http_status=resp.status_code,
            error_message=last_error_body,
        )
        last_decision = decision

        logger.warning(
            "[{}] upstream key={} HTTP {} → {} (retryable={})",
            request_id, candidate.key_preview, resp.status_code,
            decision.reason, decision.retryable,
        )

        if not decision.retryable:
            break

    if upstream_response is None or upstream_client is None:
        # 全部失败：按错误类型映射成对应的 OpenAI 错误
        upstream_msg = gw_errors.parse_upstream_error(
            last_error_body,
            default_message=f"all upstream keys exhausted (last_status={last_error_status})",
        )

        # 友好化已知的上游错误模式 — 客户端拿到的消息更可操作
        upstream_msg = _friendlify_upstream_error(upstream_msg, last_error_status)

        # 客户端错误（400/422）直接透传上游响应；不要包装成"切换失败"
        if last_error_status in (400, 422):
            exc_class = gw_errors.InvalidRequestError
        elif last_error_status == 0:
            # 全是网络错误
            exc_class = gw_errors.ServiceUnavailableError
            upstream_msg = "上游 Fireworks 暂时无法连接，请稍后重试"
        else:
            # 按最后一次失败的错误码分类
            exc_class = gw_errors.classify_upstream_status(last_error_status, last_error_body)
            # 如果是连切多把都失败，且原因是认证/额度等关键词 → 升级为 ServiceUnavailable
            if (
                len(tried_ids) >= 2
                and exc_class in (gw_errors.AuthenticationError, gw_errors.InsufficientQuotaError)
            ):
                exc_class = gw_errors.ServiceUnavailableError
                upstream_msg = f"已切换 {len(tried_ids)} 把上游 Key 仍失败：{upstream_msg}"

        exc = exc_class(
            upstream_msg,
            upstream_status=last_error_status,
            upstream_body=last_error_body,
            request_id=request_id,
            retry_after=30 if exc_class in (gw_errors.RateLimitError, gw_errors.ServiceUnavailableError, gw_errors.OverloadedError) else None,
        )

        await _persist_log(
            request_id=request_id, api_key=api_key, upstream_key=chosen_key,
            model_record=resolved.record,
            public_model=resolved.public_name, upstream_model=resolved.upstream_path,
            endpoint=endpoint_path, stream=is_stream, usage=GatewayUsage(),
            status_code=last_error_status, error_code=exc.error_type,
            error_message=(last_error_body or upstream_msg)[:500],
            retry_count=len(tried_ids), ttft_ms=0,
            latency_ms=int((time.perf_counter() - started) * 1000),
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        return gw_errors.to_error_response(exc)

    # 非流式响应处理
    if not is_stream:
        try:
            body_bytes = await upstream_response.aread()
        finally:
            await upstream_response.aclose()
            await upstream_client.aclose()

        usage = GatewayUsage()
        try:
            parsed = json.loads(body_bytes)
            u = parsed.get("usage") if isinstance(parsed, dict) else None
            if isinstance(u, dict):
                usage.prompt_tokens = int(u.get("prompt_tokens") or 0)
                usage.completion_tokens = int(u.get("completion_tokens") or 0)
                usage.cached_tokens = int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
                usage.total_tokens = int(u.get("total_tokens") or 0)
            if isinstance(parsed, dict) and parsed.get("model"):
                parsed["model"] = resolved.public_name
                body_bytes = json.dumps(parsed).encode("utf-8")
        except Exception:  # noqa: BLE001
            pass

        await _persist_log(
            request_id=request_id, api_key=api_key, upstream_key=chosen_key,
            model_record=resolved.record,
            public_model=resolved.public_name, upstream_model=resolved.upstream_path,
            endpoint=endpoint_path, stream=False, usage=usage,
            status_code=upstream_response.status_code,
            error_code=None, error_message=None,
            retry_count=len(tried_ids) - 1, ttft_ms=0,
            latency_ms=int((time.perf_counter() - started) * 1000),
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        response_headers = _build_response_headers(upstream_response)
        response_headers["X-Fwr-Request-Id"] = request_id
        if chosen_key is not None:
            response_headers["X-Fwr-Upstream-Preview"] = chosen_key.key_preview
        return JSONResponse(
            content=json.loads(body_bytes) if body_bytes else {},
            status_code=upstream_response.status_code,
            headers=response_headers,
        )

    # 流式响应
    async def stream_generator() -> AsyncIterator[bytes]:
        ttft_recorded = False
        ttft_ms = 0
        usage = GatewayUsage()
        try:
            async for line in upstream_response.aiter_lines():
                if not ttft_recorded:
                    ttft_ms = int((time.perf_counter() - started) * 1000)
                    ttft_recorded = True
                line_bytes = line.encode("utf-8") if isinstance(line, str) else line
                if line_bytes:
                    parsed = _parse_sse_usage(line_bytes)
                    if parsed is not None:
                        usage = parsed
                yield line_bytes + b"\n"
                if await request.is_disconnected():
                    logger.info("[{}] client disconnected, cancelling upstream", request_id)
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("[{}] stream forward error: {}", request_id, e)
            err_chunk = (
                f'data: {{"error":{{"message":"{type(e).__name__}: {e}","type":"upstream_error"}}}}\n\n'
            ).encode("utf-8")
            yield err_chunk
        finally:
            try:
                await upstream_response.aclose()
            except Exception:  # noqa: BLE001
                pass
            try:
                await upstream_client.aclose()
            except Exception:  # noqa: BLE001
                pass
            await _persist_log(
                request_id=request_id, api_key=api_key, upstream_key=chosen_key,
                model_record=resolved.record,
                public_model=resolved.public_name, upstream_model=resolved.upstream_path,
                endpoint=endpoint_path, stream=True, usage=usage,
                status_code=upstream_response.status_code,
                error_code=None, error_message=None,
                retry_count=max(0, len(tried_ids) - 1), ttft_ms=ttft_ms,
                latency_ms=int((time.perf_counter() - started) * 1000),
                client_ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
            )

    headers = _build_response_headers(upstream_response)
    headers["X-Fwr-Request-Id"] = request_id
    if chosen_key is not None:
        headers["X-Fwr-Upstream-Preview"] = chosen_key.key_preview
    headers.setdefault("Content-Type", "text/event-stream; charset=utf-8")
    headers["Cache-Control"] = "no-cache"
    headers["X-Accel-Buffering"] = "no"
    return StreamingResponse(
        stream_generator(),
        status_code=upstream_response.status_code,
        headers=headers,
        media_type="text/event-stream",
    )
