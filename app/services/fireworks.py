from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings

settings = get_settings()


class FireworksError(RuntimeError):
    def __init__(self, status: int, message: str, body: Any = None) -> None:
        super().__init__(f"[Fireworks {status}] {message}")
        self.status = status
        self.body = body


@dataclass
class AccountInfo:
    account_id: str          # 形如 eienqmy8016a-ovbqkbf（不含 "accounts/" 前缀）
    account_name: str        # 形如 accounts/eienqmy8016a-ovbqkbf
    email: str | None
    state: str | None        # READY / CREATING / ...
    suspend_state: str | None  # UNSUSPENDED / DELINQUENT / ...


@dataclass
class QuotaSnapshot:
    monthly_spend_limit_usd: float
    monthly_spend_used_usd: float
    monthly_spend_remaining_usd: float
    serverless_rpm: int
    raw: list[dict[str, Any]]


def _client(timeout: float = 15.0) -> httpx.AsyncClient:
    """Fireworks 管理 API 客户端。

    管理 API（accounts/quotas）一般 < 2s 响应，15s 超时已足够防御异常网络。
    connect 5s 用于快速失败：连不上 Fireworks 控制面的话立即返回，不要拖到 10s。
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=5.0),
        proxy=settings.proxy_url,
        http2=False,
    )


def _strip_account_prefix(name: str) -> str:
    return name.removeprefix("accounts/")


async def list_accounts(api_key: str) -> list[AccountInfo]:
    async with _client() as c:
        resp = await c.get(
            f"{settings.fireworks_admin_base_url}/accounts",
            headers={"Authorization": f"Bearer {api_key}"},
        )
    if resp.status_code != 200:
        raise FireworksError(resp.status_code, "list_accounts failed", resp.text)
    items = resp.json().get("accounts", []) or []
    out: list[AccountInfo] = []
    for it in items:
        full_name = it.get("name", "")
        out.append(
            AccountInfo(
                account_id=_strip_account_prefix(full_name),
                account_name=full_name,
                email=it.get("email"),
                state=it.get("state"),
                suspend_state=it.get("suspendState"),
            )
        )
    return out


async def get_account(api_key: str, account_id: str) -> AccountInfo:
    async with _client() as c:
        resp = await c.get(
            f"{settings.fireworks_admin_base_url}/accounts/{account_id}",
            params={"readMask": "*"},
            headers={"Authorization": f"Bearer {api_key}"},
        )
    if resp.status_code != 200:
        raise FireworksError(resp.status_code, "get_account failed", resp.text)
    it = resp.json()
    full_name = it.get("name", "")
    return AccountInfo(
        account_id=_strip_account_prefix(full_name),
        account_name=full_name,
        email=it.get("email"),
        state=it.get("state"),
        suspend_state=it.get("suspendState"),
    )


async def list_quotas(api_key: str, account_id: str) -> QuotaSnapshot:
    async with _client() as c:
        resp = await c.get(
            f"{settings.fireworks_admin_base_url}/accounts/{account_id}/quotas",
            headers={"Authorization": f"Bearer {api_key}"},
        )
    if resp.status_code != 200:
        raise FireworksError(resp.status_code, "list_quotas failed", resp.text)
    quotas = resp.json().get("quotas", []) or []

    monthly_limit = 0.0
    monthly_used = 0.0
    rpm = 0

    for q in quotas:
        name: str = q.get("name", "")
        try:
            value = float(q.get("value") or 0)
            usage = float(q.get("usage") or 0)
        except (TypeError, ValueError):
            value = 0.0
            usage = 0.0
        if name.endswith("/quotas/monthly-spend-usd"):
            monthly_limit = value
            monthly_used = usage
        elif name.endswith("/quotas/serverless-inference-rpm"):
            rpm = int(value)

    return QuotaSnapshot(
        monthly_spend_limit_usd=monthly_limit,
        monthly_spend_used_usd=monthly_used,
        monthly_spend_remaining_usd=max(0.0, monthly_limit - monthly_used),
        serverless_rpm=rpm,
        raw=quotas,
    )


async def list_models(api_key: str) -> list[dict[str, Any]]:
    async with _client() as c:
        resp = await c.get(
            f"{settings.fireworks_inference_base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
    if resp.status_code != 200:
        raise FireworksError(resp.status_code, "list_models failed", resp.text)
    return resp.json().get("data", []) or []
