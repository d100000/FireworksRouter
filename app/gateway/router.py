from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import select

from app.api.deps import APIError, ApiKeyDep, SessionDep
from app.config import get_settings
from app.crypto import decrypt_key
from app.db import session_scope
from app.gateway.proxy import forward
from app.models import Model, ModelStatus
from app.services import models as models_svc
from app.services import scheduler

_settings = get_settings()

router = APIRouter(prefix="/v1", tags=["gateway"])


async def _parse_body(request: Request) -> dict[str, Any]:
    try:
        raw = await request.body()
        if not raw:
            return {}
        return json.loads(raw)
    except json.JSONDecodeError:
        raise APIError(400, "Invalid JSON body", "invalid_request_error") from None


def _enforce_acl(api_key, body: dict[str, Any]) -> None:
    requested = body.get("model")
    if not requested:
        raise APIError(400, "Missing required parameter: model", "invalid_request_error", "missing_parameter")
    allowed = api_key.allowed_models
    if allowed and requested not in allowed:
        raise APIError(403, f"Model '{requested}' not allowed for this API key", "permission_error", "model_not_allowed")


def _enforce_max_tokens(api_key, body: dict[str, Any]) -> None:
    cap = api_key.max_tokens_per_request
    if cap <= 0:
        return
    requested = body.get("max_tokens") or body.get("max_completion_tokens")
    if isinstance(requested, int) and requested > cap:
        raise APIError(400, f"max_tokens={requested} exceeds cap {cap}", "invalid_request_error")


def _enforce_stream(api_key, body: dict[str, Any]) -> None:
    if body.get("stream") and not api_key.stream_enabled:
        raise APIError(403, "Streaming is disabled for this API key", "permission_error", "stream_disabled")


async def _resolve_and_check(session, api_key, body: dict[str, Any]):
    requested = body.get("model") or ""
    resolved = await models_svc.resolve(session, requested)
    if resolved.record is not None and resolved.record.status != ModelStatus.active:
        raise APIError(404, f"Model '{requested}' is disabled", "not_found_error", "model_not_found")
    return resolved


def _model_to_openai_dict(m: Model) -> dict:
    return {
        "id": m.public_name,
        "object": "model",
        "created": int(m.created_at.timestamp()) if m.created_at else 0,
        "owned_by": "fireworks",
        "category": m.category.value,
        "context_length": m.context_length,
        "supports_chat": m.category.value == "chat",
        "supports_tools": m.supports_tools,
        "supports_image_input": m.supports_vision,
    }


def _filter_by_acl(rows: list[Model], api_key) -> list[Model]:
    allowed = set(api_key.allowed_models) if api_key and api_key.allowed_models else None
    if allowed is None:
        return rows
    return [m for m in rows if m.public_name in allowed]


@router.post("/chat/completions")
async def chat_completions(request: Request, api_key: ApiKeyDep, session: SessionDep):
    body = await _parse_body(request)
    _enforce_acl(api_key, body)
    _enforce_max_tokens(api_key, body)
    _enforce_stream(api_key, body)
    resolved = await _resolve_and_check(session, api_key, body)
    return await forward(
        request=request, api_key=api_key, body=body,
        resolved=resolved, endpoint_path="/chat/completions",
    )


@router.post("/completions")
async def completions(request: Request, api_key: ApiKeyDep, session: SessionDep):
    body = await _parse_body(request)
    _enforce_acl(api_key, body)
    _enforce_max_tokens(api_key, body)
    _enforce_stream(api_key, body)
    resolved = await _resolve_and_check(session, api_key, body)
    return await forward(
        request=request, api_key=api_key, body=body,
        resolved=resolved, endpoint_path="/completions",
    )


@router.post("/embeddings")
async def embeddings(request: Request, api_key: ApiKeyDep, session: SessionDep):
    body = await _parse_body(request)
    _enforce_acl(api_key, body)
    resolved = await _resolve_and_check(session, api_key, body)
    return await forward(
        request=request, api_key=api_key, body=body,
        resolved=resolved, endpoint_path="/embeddings", allow_stream=False,
    )


@router.post("/images/generations")
async def images_generations(request: Request, api_key: ApiKeyDep, session: SessionDep):
    body = await _parse_body(request)
    _enforce_acl(api_key, body)
    resolved = await _resolve_and_check(session, api_key, body)
    return await forward(
        request=request, api_key=api_key, body=body,
        resolved=resolved, endpoint_path="/images/generations", allow_stream=False,
    )


@router.post("/rerank")
async def rerank(request: Request, api_key: ApiKeyDep, session: SessionDep):
    body = await _parse_body(request)
    _enforce_acl(api_key, body)
    resolved = await _resolve_and_check(session, api_key, body)
    return await forward(
        request=request, api_key=api_key, body=body,
        resolved=resolved, endpoint_path="/rerank", allow_stream=False,
    )


@router.get("/models")
async def list_models(api_key: ApiKeyDep, session: SessionDep):
    stmt = select(Model).where(Model.status == ModelStatus.active).order_by(Model.sort_order, Model.id)
    rows = list((await session.execute(stmt)).scalars().all())
    rows = _filter_by_acl(rows, api_key)
    return {"object": "list", "data": [_model_to_openai_dict(m) for m in rows]}


@router.get("/models/{model_id:path}")
async def get_model(model_id: str, api_key: ApiKeyDep, session: SessionDep):
    resolved = await models_svc.resolve(session, model_id)
    if resolved.record is None or resolved.record.status != ModelStatus.active:
        raise APIError(404, f"Model '{model_id}' not found", "not_found_error", "model_not_found")
    if not _filter_by_acl([resolved.record], api_key):
        raise APIError(403, "Model not allowed", "permission_error", "model_not_allowed")
    return _model_to_openai_dict(resolved.record)


# ---------------- Audio：multipart/form-data 透传 ----------------


async def _proxy_multipart(request: Request, api_key, endpoint_path: str):
    async with session_scope() as session:
        try:
            picked = await scheduler.pick(session, api_key_id=api_key.id)
        except scheduler.NoAvailableUpstream:
            raise APIError(503, "No available upstream key", "service_unavailable", "no_available_upstream") from None
        plain = decrypt_key(picked.key_encrypted)
        preview = picked.key_preview

    body_bytes = await request.body()
    upstream_url = f"{_settings.fireworks_inference_base_url}{endpoint_path}"
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in {"host", "authorization", "content-length", "accept-encoding"}
    }
    headers["Authorization"] = f"Bearer {plain}"
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(_settings.gateway_default_timeout_s, connect=10.0),
        proxy=_settings.proxy_url,
    ) as client:
        resp = await client.post(upstream_url, headers=headers, content=body_bytes)
    out_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in {"content-length", "content-encoding", "transfer-encoding", "connection", "server", "date"}
    }
    out_headers["X-Fwr-Upstream-Preview"] = preview
    return Response(content=resp.content, status_code=resp.status_code, headers=out_headers)


@router.post("/audio/transcriptions")
async def audio_transcriptions(request: Request, api_key: ApiKeyDep):
    return await _proxy_multipart(request, api_key, "/audio/transcriptions")


@router.post("/audio/translations")
async def audio_translations(request: Request, api_key: ApiKeyDep):
    return await _proxy_multipart(request, api_key, "/audio/translations")


@router.post("/audio/speech")
async def audio_speech(request: Request, api_key: ApiKeyDep, session: SessionDep):
    body = await _parse_body(request)
    _enforce_acl(api_key, body)
    resolved = await _resolve_and_check(session, api_key, body)
    return await forward(
        request=request, api_key=api_key, body=body,
        resolved=resolved, endpoint_path="/audio/speech", allow_stream=False,
    )
