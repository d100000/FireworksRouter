"""价格目录服务层：CRUD + lookup + 种子 + LiteLLM 同步。

Fireworks 官方 /v1/models 端点不返回价格，fireworks.ai/pricing 页面价格被客户端
JS 动态加载（HTML 里完全没有），所以采用 LiteLLM 社区维护的价格库作为同步源。

价格表与 models 表解耦，可独立维护、多源混合（manual + seed + litellm）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ModelPriceCatalog,
    PriceMatchType,
    PriceSource,
    PriceUnit,
)
from app.services.known_prices import KNOWN_PRICES
from app.utils.logger import logger


# ============================= 查找 =============================


@dataclass
class PriceLookupResult:
    record: ModelPriceCatalog | None
    matched_pattern: str | None = None


async def lookup_price(session: AsyncSession, model_path_or_name: str) -> PriceLookupResult:
    """模糊匹配模型路径或 public_name 命中的价格条目。

    优先级（按 priority 排序，priority 高的先匹配）：
    1) exact match
    2) prefix match
    3) contains match
    """
    if not model_path_or_name:
        return PriceLookupResult(None)
    name = model_path_or_name.rsplit("/", 1)[-1].lower()

    rows = list(
        (
            await session.execute(
                select(ModelPriceCatalog)
                .where(ModelPriceCatalog.enabled.is_(True))
                .order_by(ModelPriceCatalog.priority.desc(), ModelPriceCatalog.id.desc())
            )
        ).scalars().all()
    )

    # 1. exact
    for r in rows:
        if r.match_type == PriceMatchType.exact and r.pattern.lower() == name:
            return PriceLookupResult(r, r.pattern)

    # 2. prefix
    for r in rows:
        if r.match_type == PriceMatchType.prefix and name.startswith(r.pattern.lower()):
            return PriceLookupResult(r, r.pattern)

    # 3. contains（双向）
    for r in rows:
        if r.match_type == PriceMatchType.contains:
            p = r.pattern.lower()
            if p in name or name in p:
                return PriceLookupResult(r, r.pattern)

    return PriceLookupResult(None)


# ============================= 种子数据 =============================


async def seed_initial(session: AsyncSession) -> int:
    """首次启动时把 hardcoded KNOWN_PRICES 灌入 DB（source=seed）。

    幂等：已存在同 pattern + source=seed 的条目则跳过；不影响管理员手动添加的条目。
    """
    inserted = 0
    for pattern, price in KNOWN_PRICES.items():
        existing = (
            await session.execute(
                select(ModelPriceCatalog).where(
                    ModelPriceCatalog.pattern == pattern,
                    ModelPriceCatalog.source == PriceSource.seed,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        # 判定单位
        unit = PriceUnit.per_token
        if any(t in pattern for t in ("flux", "sdxl", "stable-diffusion", "image")):
            unit = PriceUnit.per_image
        elif "embed" in pattern:
            unit = PriceUnit.per_token
        session.add(
            ModelPriceCatalog(
                pattern=pattern,
                match_type=PriceMatchType.contains,
                input_per_1m=price.input_per_1m,
                output_per_1m=price.output_per_1m,
                cached_input_per_1m=price.cached_input_per_1m,
                unit=unit,
                source=PriceSource.seed,
                priority=10,
                note=price.note,
                enabled=True,
            )
        )
        inserted += 1
    if inserted:
        logger.info("price_catalog seeded {} initial entries", inserted)
    return inserted


# ============================= LiteLLM 同步 =============================


LITELLM_PRICES_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)


@dataclass
class LiteLLMSyncResult:
    fetched_total: int          # LiteLLM 全量条目数
    fireworks_matched: int      # 含 fireworks 关键字的条目数
    created: int
    updated: int
    skipped: int
    errors: list[str]


async def sync_from_litellm(
    session: AsyncSession,
    overwrite_existing: bool = False,
) -> LiteLLMSyncResult:
    """从 LiteLLM GitHub raw JSON 拉 Fireworks 价格并 upsert 到 model_price_catalog。

    - overwrite_existing=False（默认）：跳过 source=litellm 之外的条目（不覆盖 seed/manual）
    - overwrite_existing=True：source=litellm 的条目用新数据覆盖；其他 source 仍跳过
    """
    errors: list[str] = []
    fireworks_matched = 0
    created = 0
    updated = 0
    skipped = 0

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            resp = await client.get(LITELLM_PRICES_URL)
            if resp.status_code != 200:
                errors.append(f"GitHub HTTP {resp.status_code}")
                return LiteLLMSyncResult(0, 0, 0, 0, 0, errors)
            data: dict[str, Any] = resp.json()
    except Exception as e:  # noqa: BLE001
        errors.append(f"fetch failed: {e}")
        return LiteLLMSyncResult(0, 0, 0, 0, 0, errors)

    now = datetime.now(timezone.utc)
    for litellm_key, meta in data.items():
        if not isinstance(meta, dict):
            continue
        # 只取 Fireworks 相关条目
        if "fireworks_ai" not in litellm_key.lower() and not litellm_key.startswith(
            "accounts/fireworks/"
        ):
            continue
        provider = meta.get("litellm_provider") or ""
        if provider and provider != "fireworks_ai":
            continue

        fireworks_matched += 1

        # 提取模型尾段作为 pattern
        # 形如 "fireworks_ai/accounts/fireworks/models/glm-5p1" → "glm-5p1"
        # 或 "fireworks_ai/glm-5p1" → "glm-5p1"
        pattern = litellm_key.rsplit("/", 1)[-1].lower()
        if not pattern or pattern in ("fireworks_ai", "default"):
            skipped += 1
            continue

        inp_token = float(meta.get("input_cost_per_token") or 0)
        out_token = float(meta.get("output_cost_per_token") or 0)
        cache_token = float(meta.get("cache_read_input_token_cost") or 0)

        # token 价转 per 1M
        inp_per_1m = inp_token * 1_000_000
        out_per_1m = out_token * 1_000_000
        cache_per_1m = cache_token * 1_000_000

        # 找已有条目
        existing = (
            await session.execute(
                select(ModelPriceCatalog).where(ModelPriceCatalog.pattern == pattern)
            )
        ).scalar_one_or_none()

        if existing is None:
            session.add(
                ModelPriceCatalog(
                    pattern=pattern,
                    match_type=PriceMatchType.contains,
                    input_per_1m=inp_per_1m,
                    output_per_1m=out_per_1m,
                    cached_input_per_1m=cache_per_1m,
                    unit=PriceUnit.per_token,
                    source=PriceSource.litellm,
                    priority=5,
                    note=f"From LiteLLM ({litellm_key})",
                    last_synced_at=now,
                )
            )
            created += 1
        else:
            # 只覆盖 source=litellm 的旧条目；seed / manual 不动
            if existing.source == PriceSource.litellm or overwrite_existing:
                existing.input_per_1m = inp_per_1m
                existing.output_per_1m = out_per_1m
                existing.cached_input_per_1m = cache_per_1m
                existing.source = PriceSource.litellm
                existing.note = f"From LiteLLM ({litellm_key})"
                existing.last_synced_at = now
                updated += 1
            else:
                skipped += 1

    return LiteLLMSyncResult(
        fetched_total=len(data),
        fireworks_matched=fireworks_matched,
        created=created,
        updated=updated,
        skipped=skipped,
        errors=errors,
    )
