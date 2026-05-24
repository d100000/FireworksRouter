"""Anthropic Claude 协议兼容：POST /v1/messages（以及 /v1/message 别名）

实现策略：
- 接受 x-api-key 或 Authorization: Bearer 鉴权头
- Anthropic Messages 请求体翻译为 OpenAI chat.completions
- 转发至 Fireworks 上游
- 响应翻译回 Anthropic Messages 格式（含 streaming event 类型）

为什么不直接透传上游 /inference/v1/messages？
Fireworks 上游不提供 Anthropic 兼容端点（实测 404），只能协议翻译。

翻译矩阵（请求方向，Anthropic → OpenAI）：

  顶层字段
    model                         → model
    system: str|list[block]       → messages[0] = {role:system, content}
    messages                      → messages（content blocks 展开/翻译）
    max_tokens                    → max_tokens
    temperature/top_p/top_k       → 同名
    stop_sequences                → stop
    stream                        → stream
    tools                         → tools（input_schema → parameters，包装 type=function）
    tool_choice                   → tool_choice（"auto/any/tool/none" 映射）
    metadata/cache_control 等     → 丢弃（Anthropic 专属）

  content block 类型
    text                          → text（剥 cache_control）
    image                         → image_url（base64/url 两种 source 都支持）
    tool_use（在 assistant 中）   → 折叠到 message.tool_calls 数组
    tool_result（在 user 中）     → 拆出独立的 role=tool 消息

翻译矩阵（响应方向，OpenAI → Anthropic）：

    text content                  → [{type:text, text:...}]
    tool_calls                    → [{type:tool_use, id, name, input}]
    finish_reason=tool_calls      → stop_reason=tool_use
    usage.prompt_tokens           → usage.input_tokens
    usage.completion_tokens       → usage.output_tokens
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import SessionDep
from app.gateway import errors as gw_errors
from app.gateway.proxy import forward
from app.models import ApiKey, ApiKeyStatus
from app.services import models as models_svc
from app.utils.tokens import hash_token

router = APIRouter(tags=["anthropic"])


# Anthropic 专属字段，OpenAI 上游会报 "Extra inputs are not permitted"
_ANTHROPIC_ONLY_BLOCK_FIELDS = {"cache_control", "cache_creation"}

# Anthropic 错误类型映射（OpenAI 错误类型 / HTTP 状态 → Anthropic error.type）
# 参考 https://docs.anthropic.com/en/api/errors
_ANTHROPIC_ERROR_TYPE_BY_STATUS = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    413: "request_too_large",
    422: "invalid_request_error",
    429: "rate_limit_error",
    500: "api_error",
    502: "api_error",
    503: "overloaded_error",
    504: "api_error",
}


def _to_anthropic_error_response(openai_err_resp):
    """把 OpenAI 错误响应 (`{"error": {message, type, code}}`) 包成 Anthropic 形态
    (`{"type": "error", "error": {type, message}}`)。

    Anthropic SDK 会按 `error.type` 抛特定异常（AuthenticationError 等），
    不正确的 type 会让客户端只能看到 generic "API Error"。
    """
    from fastapi.responses import JSONResponse

    try:
        payload = json.loads(openai_err_resp.body) if isinstance(openai_err_resp, JSONResponse) else {}
    except (json.JSONDecodeError, ValueError):
        payload = {}

    err = payload.get("error") or {}
    message = err.get("message") or "Unknown error"
    status = openai_err_resp.status_code if hasattr(openai_err_resp, "status_code") else 500
    err_type = _ANTHROPIC_ERROR_TYPE_BY_STATUS.get(status, "api_error")

    # 只保留对 Anthropic 客户端有意义的头（Retry-After、X-Fwr-Request-Id）
    safe_headers = {}
    if hasattr(openai_err_resp, "headers"):
        for k in ("retry-after", "x-fwr-request-id"):
            v = openai_err_resp.headers.get(k)
            if v:
                safe_headers[k] = v

    return JSONResponse(
        status_code=status,
        content={"type": "error", "error": {"type": err_type, "message": message}},
        headers=safe_headers,
    )


async def _resolve_api_key(
    session,
    *,
    authorization: str | None,
    x_api_key: str | None,
) -> ApiKey:
    """Anthropic SDK 用 x-api-key；OpenAI 习惯 Authorization Bearer。两个都接受。"""
    raw: str | None = None
    if x_api_key:
        raw = x_api_key.strip()
    elif authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            raw = parts[1].strip()
    if not raw:
        raise gw_errors.AuthenticationError("Missing x-api-key or Authorization header")

    from sqlalchemy import select
    h = hash_token(raw)
    stmt = select(ApiKey).where(ApiKey.token_hash == h)
    record = (await session.execute(stmt)).scalar_one_or_none()
    if record is None:
        raise gw_errors.AuthenticationError("Invalid api key")
    if record.status != ApiKeyStatus.active or not record.is_usable:
        raise gw_errors.AuthenticationError("API key disabled or out of quota")
    return record


# --------------------------------------------------------------------------- #
# 请求翻译：Anthropic → OpenAI
# --------------------------------------------------------------------------- #


def _strip_anthropic_fields(d: dict[str, Any]) -> dict[str, Any]:
    """剥掉 Anthropic 专属字段（cache_control / cache_creation）。"""
    return {k: v for k, v in d.items() if k not in _ANTHROPIC_ONLY_BLOCK_FIELDS}


def _translate_system(system: Any) -> list[dict[str, Any]]:
    """system 字段 → [{role:system, content:...}] (0 或 1 条)。"""
    if isinstance(system, str) and system.strip():
        return [{"role": "system", "content": system}]
    if isinstance(system, list):
        text_parts: list[str] = []
        for b in system:
            if not isinstance(b, dict):
                continue
            clean = _strip_anthropic_fields(b)
            if clean.get("type") == "text":
                text_parts.append(clean.get("text", "") or "")
        joined = "\n".join(s for s in text_parts if s)
        if joined:
            return [{"role": "system", "content": joined}]
    return []


def _translate_image_block(block: dict[str, Any]) -> dict[str, Any] | None:
    """Anthropic image block → OpenAI image_url block。

    Anthropic source 有两种：
      {type:base64, media_type:..., data:...}
      {type:url, url:...}
    """
    src = block.get("source")
    if not isinstance(src, dict):
        return None
    stype = src.get("type")
    if stype == "base64":
        media = src.get("media_type") or "image/png"
        data = src.get("data") or ""
        return {"type": "image_url", "image_url": {"url": f"data:{media};base64,{data}"}}
    if stype == "url":
        url = src.get("url")
        if url:
            return {"type": "image_url", "image_url": {"url": url}}
    return None


def _stringify_tool_result_content(content: Any) -> str:
    """tool_result.content 可以是 string 或 list of blocks（含 text/image）。

    OpenAI tool 消息 content 必须是 string，所以做扁平化。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, dict):
                clean = _strip_anthropic_fields(b)
                if clean.get("type") == "text":
                    parts.append(clean.get("text", "") or "")
                elif clean.get("type") == "image":
                    parts.append("[image]")  # OpenAI tool 消息不支持图片，降级
                else:
                    parts.append(json.dumps(clean, ensure_ascii=False))
            else:
                parts.append(str(b))
        return "\n".join(p for p in parts if p)
    if content is None:
        return ""
    return str(content)


def _translate_user_message(content: Any) -> list[dict[str, Any]]:
    """user 角色消息 → OpenAI 消息列表。

    Anthropic 允许在 user.content 里同时有 text + image + tool_result 块。
    OpenAI 要求 tool_result 走独立的 role=tool 消息，所以一条 user 可能拆成多条。

    返回顺序：先 role=tool 消息（每个 tool_result 一条），再剩下的 user 消息。

    Claude Code/Anthropic SDK 的习惯：当一轮对话里 assistant 调用了工具，
    下一轮 user 消息会把工具结果塞进来。OpenAI 协议要求 tool 消息紧跟在
    assistant tool_calls 之后，所以拆出来正好对齐顺序。
    """
    out: list[dict[str, Any]] = []
    if isinstance(content, str):
        out.append({"role": "user", "content": content})
        return out
    if not isinstance(content, list):
        out.append({"role": "user", "content": str(content or "")})
        return out

    # 收集普通内容块（text/image）和工具结果
    tool_msgs: list[dict[str, Any]] = []
    regular_blocks: list[dict[str, Any]] = []
    has_image = False
    text_only_parts: list[str] = []

    for b in content:
        if not isinstance(b, dict):
            text_only_parts.append(str(b))
            continue
        clean = _strip_anthropic_fields(b)
        btype = clean.get("type")
        if btype == "tool_result":
            tool_msgs.append({
                "role": "tool",
                "tool_call_id": clean.get("tool_use_id") or "",
                "content": _stringify_tool_result_content(clean.get("content")),
            })
        elif btype == "text":
            text = clean.get("text", "") or ""
            text_only_parts.append(text)
            regular_blocks.append({"type": "text", "text": text})
        elif btype == "image":
            img = _translate_image_block(clean)
            if img:
                regular_blocks.append(img)
                has_image = True
        # 其它未知类型丢弃

    # 工具结果消息先出（如果有）
    out.extend(tool_msgs)

    # 剩下的内容拼成一条 user 消息
    if regular_blocks:
        if has_image:
            out.append({"role": "user", "content": regular_blocks})
        else:
            joined = "\n".join(s for s in text_only_parts if s)
            if joined:
                out.append({"role": "user", "content": joined})

    return out


def _translate_assistant_message(content: Any) -> dict[str, Any]:
    """assistant 角色消息 → 单条 OpenAI 消息。

    Anthropic 允许 assistant 同时有 text + tool_use 块。
    OpenAI: assistant.content 是 text；assistant.tool_calls 是数组。
    """
    msg: dict[str, Any] = {"role": "assistant"}
    if isinstance(content, str):
        msg["content"] = content
        return msg
    if not isinstance(content, list):
        msg["content"] = str(content or "")
        return msg

    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for b in content:
        if not isinstance(b, dict):
            text_parts.append(str(b))
            continue
        clean = _strip_anthropic_fields(b)
        btype = clean.get("type")
        if btype == "text":
            text_parts.append(clean.get("text", "") or "")
        elif btype == "tool_use":
            tool_calls.append({
                "id": clean.get("id") or f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {
                    "name": clean.get("name") or "",
                    "arguments": json.dumps(clean.get("input") or {}, ensure_ascii=False),
                },
            })

    text = "\n".join(s for s in text_parts if s)
    # OpenAI 要求：有 tool_calls 时 content 可以是 None 或空串，但不能缺失
    msg["content"] = text if text else None
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _translate_tools(tools: Any) -> list[dict[str, Any]]:
    """Anthropic tools → OpenAI tools（包装成 type=function）。

    顺便对 input_schema 做完整 JSON Schema 消毒（单次遍历）：
      - 展开 $ref / $defs（Fireworks resolver NoneType bug）
      - 剥 RE2 不支持的 pattern（lookaround / backref / atomic group /
        possessive quantifier / inline comment）
      - 处理循环引用 / 悬空引用
    详见 utils/json_schema.py:sanitize_schema。
    """
    if not isinstance(tools, list):
        return []
    from app.utils.json_schema import aggregate_categories, sanitize_schema
    from app.utils.logger import logger

    out: list[dict[str, Any]] = []
    notes_all: list[str] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        name = t.get("name")
        if not name:
            continue
        raw_schema = t.get("input_schema") or {"type": "object", "properties": {}}
        params, notes = sanitize_schema(raw_schema)
        if notes:
            notes_all.extend(f"tool[{name}] {n}" for n in notes)
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": t.get("description") or "",
                "parameters": params,
            },
        })
    if notes_all:
        # 加 category 维度 + bind 给 metrics 聚合用
        cats = aggregate_categories(notes_all)
        logger.bind(category="schema_sanitize", source="anthropic_tools", categories=cats).warning(
            "anthropic tools: schema sanitized {} item(s) — first 3: {}",
            len(notes_all), notes_all[:3],
        )
    return out


def _translate_tool_choice(tc: Any) -> Any:
    """Anthropic tool_choice → OpenAI tool_choice。

    Anthropic 形式：
      {"type": "auto"}          → "auto"
      {"type": "any"}           → "required"
      {"type": "tool", "name"}  → {"type":"function","function":{"name":...}}
      {"type": "none"}          → "none"
    """
    if isinstance(tc, str):
        return tc
    if not isinstance(tc, dict):
        return None
    t = tc.get("type")
    if t == "auto":
        return "auto"
    if t == "any":
        return "required"
    if t == "none":
        return "none"
    if t == "tool":
        name = tc.get("name")
        if name:
            return {"type": "function", "function": {"name": name}}
    return None


def _anthropic_to_openai(body: dict[str, Any]) -> dict[str, Any]:
    """Anthropic Messages 请求体 → OpenAI chat.completions 请求体（完整版）。"""
    out: dict[str, Any] = {
        "model": body.get("model"),
        "messages": [],
    }

    # 1. system
    out["messages"].extend(_translate_system(body.get("system")))

    # 2. messages
    for m in body.get("messages") or []:
        if not isinstance(m, dict):
            continue
        # 剥消息层 cache_control
        m = _strip_anthropic_fields(m)
        role = m.get("role")
        content = m.get("content")
        if role == "user":
            out["messages"].extend(_translate_user_message(content))
        elif role == "assistant":
            out["messages"].append(_translate_assistant_message(content))
        elif role == "system":
            # 极少数客户端把 system 放进 messages
            if isinstance(content, str):
                out["messages"].append({"role": "system", "content": content})
            elif isinstance(content, list):
                texts = [_strip_anthropic_fields(b).get("text", "")
                         for b in content if isinstance(b, dict) and _strip_anthropic_fields(b).get("type") == "text"]
                out["messages"].append({"role": "system", "content": "\n".join(t for t in texts if t)})

    # 3. 采样参数
    if "max_tokens" in body:
        out["max_tokens"] = body["max_tokens"]
    if "temperature" in body:
        out["temperature"] = body["temperature"]
    if "top_p" in body:
        out["top_p"] = body["top_p"]
    if "top_k" in body:
        out["top_k"] = body["top_k"]
    if "stop_sequences" in body:
        out["stop"] = body["stop_sequences"]
    if body.get("stream"):
        out["stream"] = True

    # 4. 工具
    tools = _translate_tools(body.get("tools"))
    if tools:
        out["tools"] = tools
    tc = _translate_tool_choice(body.get("tool_choice"))
    if tc is not None:
        out["tool_choice"] = tc

    return out


# --------------------------------------------------------------------------- #
# 响应翻译：OpenAI → Anthropic
# --------------------------------------------------------------------------- #


def _openai_to_anthropic_response(openai_resp: dict[str, Any]) -> dict[str, Any]:
    """OpenAI chat.completions 响应 → Anthropic Messages 响应（支持 tool_use）。"""
    choice = (openai_resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content_blocks: list[dict[str, Any]] = []

    text = msg.get("content")
    if isinstance(text, str) and text:
        content_blocks.append({"type": "text", "text": text})

    # tool_calls → tool_use 块
    for tc in msg.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        except json.JSONDecodeError:
            args = {"_raw": raw_args}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
            "name": fn.get("name") or "",
            "input": args,
        })

    # 至少要有一个块，避免客户端解析报错
    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    usage = openai_resp.get("usage") or {}
    return {
        "id": openai_resp.get("id") or f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": openai_resp.get("model"),
        "content": content_blocks,
        "stop_reason": _map_finish_reason(choice.get("finish_reason")),
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }


def _map_finish_reason(reason: str | None) -> str | None:
    return {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "function_call": "tool_use",
        "content_filter": "stop_sequence",
    }.get(reason or "", "end_turn")


# --------------------------------------------------------------------------- #
# 路由处理：/v1/messages（以及 /v1/message 别名）
# --------------------------------------------------------------------------- #


async def _handle_anthropic_messages(
    request: Request,
    session,
    authorization: str | None,
    x_api_key: str | None,
):
    """统一入口：/v1/messages 和 /v1/message 都走这里。

    所有错误响应都包成 Anthropic envelope（`{type:error, error:{type,message}}`），
    避免客户端解析失败。
    """
    # 1. 鉴权
    try:
        api_key = await _resolve_api_key(session, authorization=authorization, x_api_key=x_api_key)
    except gw_errors.AuthenticationError as e:
        return _to_anthropic_error_response(gw_errors.to_error_response(e))

    # 2. 读 body（必须是 JSON object）
    try:
        raw = await request.body()
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return _to_anthropic_error_response(
            gw_errors.to_error_response(gw_errors.InvalidRequestError("Invalid JSON body"))
        )
    if not isinstance(body, dict):
        return _to_anthropic_error_response(
            gw_errors.to_error_response(
                gw_errors.InvalidRequestError("Request body must be a JSON object")
            )
        )

    requested_model = body.get("model")
    if not requested_model:
        return _to_anthropic_error_response(
            gw_errors.to_error_response(
                gw_errors.InvalidRequestError("Missing required parameter: model", param="model")
            )
        )

    # 3. 模型解析
    resolved = await models_svc.resolve(session, requested_model)
    if resolved.record is not None and resolved.record.status.value != "active":
        return _to_anthropic_error_response(
            gw_errors.to_error_response(
                gw_errors.ModelNotFoundError(f"Model '{requested_model}' is disabled")
            )
        )

    # 4. Anthropic → OpenAI 请求体转换
    openai_body = _anthropic_to_openai(body)
    is_stream = bool(openai_body.get("stream"))

    return await _forward_anthropic(
        request=request,
        api_key=api_key,
        openai_body=openai_body,
        resolved=resolved,
        is_stream=is_stream,
    )


@router.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    session: SessionDep,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
    anthropic_version: str | None = Header(default=None, alias="anthropic-version"),
):
    """Claude Code / Anthropic SDK 兼容端点。"""
    return await _handle_anthropic_messages(request, session, authorization, x_api_key)


@router.post("/v1/message")
async def anthropic_messages_singular(
    request: Request,
    session: SessionDep,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
    anthropic_version: str | None = Header(default=None, alias="anthropic-version"),
):
    """`/v1/message`（无 s）别名，容忍客户端笔误。"""
    return await _handle_anthropic_messages(request, session, authorization, x_api_key)


# 从上游响应里只挑选 Anthropic 客户端有意义的头透传，不要带 content-length /
# content-type，否则会让客户端按错的长度截断（OpenAI body 体积 ≠ Anthropic body 体积）
_FORWARD_RESPONSE_HEADERS = {"x-fwr-request-id", "x-fwr-upstream-preview"}


async def _forward_anthropic(
    request: Request,
    api_key: ApiKey,
    openai_body: dict,
    resolved,
    is_stream: bool,
):
    """利用 forward() 转发，但把响应转回 Anthropic 格式。"""

    # Monkey-patch request 的 body() 返回 openai_body 的 JSON
    # 关闭 OpenAI 路径下的流式（我们要自己重新拼 Anthropic 流）
    openai_body_no_stream = dict(openai_body)
    openai_body_no_stream.pop("stream", None)
    new_body_bytes = json.dumps(openai_body_no_stream).encode("utf-8")

    class FakeRequest:
        def __init__(self, original: Request, body_bytes: bytes):
            self._original = original
            self._body_bytes = body_bytes
            self.headers = original.headers
            self.client = original.client
            self.url = original.url
            self.method = original.method
            self.scope = original.scope

        async def body(self) -> bytes:
            return self._body_bytes

        async def is_disconnected(self) -> bool:
            return await self._original.is_disconnected()

    fake = FakeRequest(request, new_body_bytes)

    resp = await forward(
        request=fake,  # type: ignore[arg-type]
        api_key=api_key,
        body=openai_body_no_stream,
        resolved=resolved,
        endpoint_path="/chat/completions",
        allow_stream=False,
    )

    if not isinstance(resp, JSONResponse):
        # 不应该走到（allow_stream=False），保险起见原样透传
        return resp
    if resp.status_code >= 400:
        # 错误响应包装成 Anthropic envelope
        return _to_anthropic_error_response(resp)

    # 只挑选少量请求追踪头透传，丢弃上游的 content-length/content-type
    safe_headers = {k: v for k, v in resp.headers.items() if k.lower() in _FORWARD_RESPONSE_HEADERS}

    # 解析 OpenAI 响应 → 转换为 Anthropic
    try:
        body_dict = json.loads(resp.body) if resp.body else {}
    except json.JSONDecodeError:
        return _to_anthropic_error_response(
            gw_errors.to_error_response(
                gw_errors.UpstreamError("Failed to parse upstream response as JSON")
            )
        )
    anthropic_resp = _openai_to_anthropic_response(body_dict)

    if not is_stream:
        return JSONResponse(content=anthropic_resp, status_code=200, headers=safe_headers)

    # 流式：把整段拆成 Anthropic SSE events
    stream_headers = dict(safe_headers)
    stream_headers["Cache-Control"] = "no-cache"
    stream_headers["X-Accel-Buffering"] = "no"
    return StreamingResponse(
        _anthropic_stream_events(anthropic_resp),
        media_type="text/event-stream",
        headers=stream_headers,
    )


async def _anthropic_stream_events(anthropic_resp: dict[str, Any]):
    """把完整 Anthropic 响应拆成 SSE events 流。

    事件序列：
      message_start
      content_block_start / content_block_delta+ / content_block_stop  (per block)
      message_delta（含 stop_reason）
      message_stop
    """
    # message_start
    start_msg = {
        **anthropic_resp,
        "content": [],
        "usage": {
            "input_tokens": anthropic_resp["usage"]["input_tokens"],
            "output_tokens": 0,
        },
    }
    yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': start_msg})}\n\n".encode()

    # 逐 block 输出
    for idx, block in enumerate(anthropic_resp.get("content") or []):
        btype = block.get("type")
        if btype == "text":
            # content_block_start with empty text
            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': idx, 'content_block': {'type': 'text', 'text': ''}})}\n\n".encode()
            text = block.get("text", "") or ""
            if text:
                yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': idx, 'delta': {'type': 'text_delta', 'text': text}})}\n\n".encode()
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': idx})}\n\n".encode()
        elif btype == "tool_use":
            tool_start = {
                "type": "content_block_start",
                "index": idx,
                "content_block": {
                    "type": "tool_use",
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "input": {},
                },
            }
            yield f"event: content_block_start\ndata: {json.dumps(tool_start)}\n\n".encode()
            args_json = json.dumps(block.get("input") or {}, ensure_ascii=False)
            if args_json and args_json != "{}":
                delta = {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "input_json_delta", "partial_json": args_json},
                }
                yield f"event: content_block_delta\ndata: {json.dumps(delta)}\n\n".encode()
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': idx})}\n\n".encode()

    # message_delta
    delta_msg = {
        "type": "message_delta",
        "delta": {"stop_reason": anthropic_resp["stop_reason"], "stop_sequence": None},
        "usage": anthropic_resp["usage"],
    }
    yield f"event: message_delta\ndata: {json.dumps(delta_msg)}\n\n".encode()

    # message_stop
    yield b"event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"
