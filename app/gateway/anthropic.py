"""Anthropic Claude 协议兼容：POST /v1/messages

实现策略：
- 接受 x-api-key 或 Authorization: Bearer 鉴权头
- Anthropic Messages 请求体翻译为 OpenAI chat.completions
- 转发至 Fireworks 上游
- 响应翻译回 Anthropic Messages 格式（含 streaming event 类型）

为什么不直接透传上游 /inference/v1/messages？
Fireworks 上游不提供 Anthropic 兼容端点（实测 404），只能协议翻译。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import SessionDep, require_api_key
from app.gateway import errors as gw_errors
from app.gateway.proxy import forward
from app.models import ApiKey, ApiKeyStatus
from app.services import models as models_svc
from app.utils.tokens import hash_token

router = APIRouter(tags=["anthropic"])


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


def _anthropic_to_openai(body: dict[str, Any]) -> dict[str, Any]:
    """Anthropic Messages 请求体 → OpenAI chat.completions 请求体。"""
    out: dict[str, Any] = {
        "model": body.get("model"),
        "messages": [],
    }

    # system → 顶层字段移到 messages[0]
    system = body.get("system")
    if isinstance(system, str) and system:
        out["messages"].append({"role": "system", "content": system})
    elif isinstance(system, list):
        # Anthropic 支持 list of {type, text} content blocks
        text = "\n".join(b.get("text", "") for b in system if isinstance(b, dict) and b.get("type") == "text")
        if text:
            out["messages"].append({"role": "system", "content": text})

    # messages: Anthropic 的 content 可以是 string 或 list of blocks
    for m in body.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str):
            out["messages"].append({"role": role, "content": content})
        elif isinstance(content, list):
            # 多模态 / 工具调用 content blocks，简化为只取 text
            text_parts: list[str] = []
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    text_parts.append(b.get("text", ""))
            out["messages"].append({"role": role, "content": "\n".join(text_parts)})
        else:
            out["messages"].append({"role": role, "content": str(content or "")})

    # 参数映射
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

    return out


def _openai_to_anthropic_response(openai_resp: dict[str, Any]) -> dict[str, Any]:
    """OpenAI chat.completions 响应 → Anthropic Messages 响应。"""
    choice = (openai_resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content_text = msg.get("content") or ""

    usage = openai_resp.get("usage") or {}
    return {
        "id": openai_resp.get("id") or f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": openai_resp.get("model"),
        "content": [{"type": "text", "text": content_text}],
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


@router.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    session: SessionDep,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
    anthropic_version: str | None = Header(default=None, alias="anthropic-version"),
):
    """Claude Code / Anthropic SDK 兼容端点。"""
    # 1. 鉴权
    try:
        api_key = await _resolve_api_key(session, authorization=authorization, x_api_key=x_api_key)
    except gw_errors.AuthenticationError as e:
        return gw_errors.to_error_response(e)

    # 2. 读 body
    try:
        raw = await request.body()
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return gw_errors.to_error_response(
            gw_errors.InvalidRequestError("Invalid JSON body")
        )

    requested_model = body.get("model")
    if not requested_model:
        return gw_errors.to_error_response(
            gw_errors.InvalidRequestError("Missing required parameter: model", param="model")
        )

    # 3. 模型解析（同 OpenAI 流程）
    resolved = await models_svc.resolve(session, requested_model)
    if resolved.record is not None and resolved.record.status.value != "active":
        return gw_errors.to_error_response(
            gw_errors.ModelNotFoundError(f"Model '{requested_model}' is disabled")
        )

    # 4. Anthropic → OpenAI 请求体转换
    openai_body = _anthropic_to_openai(body)
    is_stream = bool(openai_body.get("stream"))

    # 5. 用现有 forward() 转发；但 forward 期望 OpenAI 格式 body，所以我们要包装 request
    # 直接构造一个新的虚拟请求复用 forward 不便，改为手动构造转发
    return await _forward_anthropic(
        request=request,
        api_key=api_key,
        anthropic_body=body,
        openai_body=openai_body,
        resolved=resolved,
        is_stream=is_stream,
    )


async def _forward_anthropic(
    request: Request,
    api_key: ApiKey,
    anthropic_body: dict,
    openai_body: dict,
    resolved,
    is_stream: bool,
):
    """利用 forward() 转发，但把响应转回 Anthropic 格式。"""
    # 复用 forward 时需要它把 body 当成 OpenAI 格式
    # 简化：直接修改 request 内部 body cache，然后调 forward
    # 但 Request._body 是 private — 干净做法是再实现一遍精简版
    from app.gateway.proxy import forward
    from starlette.requests import Request as StarletteRequest

    # Monkey-patch request 的 body() 返回 openai_body 的 JSON
    new_body_bytes = json.dumps(openai_body).encode("utf-8")

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

    # 关闭 OpenAI 路径下的流式（我们要自己重新拼 Anthropic 流）
    openai_body_no_stream = dict(openai_body)
    openai_body_no_stream["stream"] = False
    fake._body_bytes = json.dumps(openai_body_no_stream).encode("utf-8")  # type: ignore[attr-defined]

    resp = await forward(
        request=fake,  # type: ignore[arg-type]
        api_key=api_key,
        body=openai_body_no_stream,
        resolved=resolved,
        endpoint_path="/chat/completions",
        allow_stream=False,
    )

    # 非 2xx：原样透传错误（已经是 OpenAI 错误结构）
    if not isinstance(resp, JSONResponse):
        return resp
    if resp.status_code >= 400:
        return resp

    # 解析 OpenAI 响应 → 转换为 Anthropic
    body_dict = json.loads(resp.body)
    anthropic_resp = _openai_to_anthropic_response(body_dict)

    if not is_stream:
        return JSONResponse(content=anthropic_resp, status_code=200, headers=dict(resp.headers))

    # 流式：把整段拆成 Anthropic SSE events
    async def _anthropic_stream():
        msg_id = anthropic_resp["id"]
        # event: message_start
        yield f"event: message_start\ndata: {json.dumps({'type':'message_start','message':{**anthropic_resp,'content':[],'usage':{'input_tokens':anthropic_resp['usage']['input_tokens'],'output_tokens':0}}})}\n\n".encode()
        # event: content_block_start
        yield f"event: content_block_start\ndata: {json.dumps({'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}})}\n\n".encode()
        # 拆 content 为单帧（演示简化 — 真正流式要逐 token）
        text = anthropic_resp["content"][0]["text"]
        if text:
            yield f"event: content_block_delta\ndata: {json.dumps({'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':text}})}\n\n".encode()
        yield f"event: content_block_stop\ndata: {json.dumps({'type':'content_block_stop','index':0})}\n\n".encode()
        yield f"event: message_delta\ndata: {json.dumps({'type':'message_delta','delta':{'stop_reason':anthropic_resp['stop_reason']},'usage':anthropic_resp['usage']})}\n\n".encode()
        yield b"event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"

    return StreamingResponse(
        _anthropic_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
