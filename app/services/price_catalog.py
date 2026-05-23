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


# ============================= JSON 导入 / 导出 =============================


@dataclass
class ImportResult:
    received: int            # 收到几条
    created: int             # 新建
    updated: int             # 覆盖更新
    skipped: int             # 跳过（已存在）
    errors: list[str]        # 失败原因


def _is_litellm_format(data) -> bool:
    """检测 JSON 是否是 LiteLLM 格式（{key: {input_cost_per_token, ...}}）。"""
    if not isinstance(data, dict):
        return False
    if not data:
        return False
    # 取第一个 value，是 dict 且含 input_cost_per_token / litellm_provider 任一字段
    first = next(iter(data.values()))
    if not isinstance(first, dict):
        return False
    return any(k in first for k in ("input_cost_per_token", "output_cost_per_token", "litellm_provider"))


def _normalize_native_item(item: dict) -> dict:
    """原生格式条目预处理 + 校验。返回标准化字段或抛 ValueError。"""
    pattern = (item.get("pattern") or "").strip().lower()
    if not pattern:
        raise ValueError("missing 'pattern'")
    match_type = (item.get("match_type") or "contains").lower()
    if match_type not in ("exact", "contains", "prefix"):
        raise ValueError(f"invalid match_type: {match_type}")
    unit = (item.get("unit") or "per_token").lower()
    if unit not in ("per_token", "per_image", "per_step", "per_request"):
        raise ValueError(f"invalid unit: {unit}")
    return {
        "pattern": pattern,
        "match_type": match_type,
        "input_per_1m": float(item.get("input_per_1m") or 0),
        "output_per_1m": float(item.get("output_per_1m") or 0),
        "cached_input_per_1m": float(item.get("cached_input_per_1m") or 0),
        "per_image_usd": float(item.get("per_image_usd") or 0),
        "per_step_usd": float(item.get("per_step_usd") or 0),
        "unit": unit,
        "priority": int(item.get("priority") or 20),
        "enabled": bool(item.get("enabled", True)),
        "note": item.get("note") or None,
    }


def _normalize_litellm_item(key: str, meta: dict) -> dict | None:
    """LiteLLM 格式条目转标准。返回 None 表示跳过（非 Fireworks）。"""
    # 只取 Fireworks 相关
    is_fireworks = "fireworks" in key.lower() or (meta.get("litellm_provider") == "fireworks_ai")
    if not is_fireworks:
        return None
    pattern = key.rsplit("/", 1)[-1].lower().strip()
    if not pattern or pattern in ("fireworks_ai", "default"):
        return None
    inp = float(meta.get("input_cost_per_token") or 0) * 1_000_000
    out = float(meta.get("output_cost_per_token") or 0) * 1_000_000
    cached = float(meta.get("cache_read_input_token_cost") or 0) * 1_000_000
    return {
        "pattern": pattern,
        "match_type": "contains",
        "input_per_1m": inp,
        "output_per_1m": out,
        "cached_input_per_1m": cached,
        "per_image_usd": 0.0,
        "per_step_usd": 0.0,
        "unit": "per_token",
        "priority": 5,
        "enabled": True,
        "note": f"Imported from LiteLLM-style JSON ({key})",
    }


async def import_from_json(
    session: AsyncSession,
    data,
    strategy: str = "skip",
) -> ImportResult:
    """从 JSON 数据导入价格条目。

    支持两种格式（自动检测）：
    1. 原生数组：[{pattern, match_type, input_per_1m, ...}, ...]
    2. LiteLLM 字典：{"fireworks_ai/.../model": {input_cost_per_token, ...}}

    strategy:
    - "skip": 已存在同 pattern → 跳过（默认，最安全）
    - "update": 已存在同 pattern → 用新数据覆盖
    - "replace": 先清空所有 source=manual 的条目，再全部当 manual 插入
    """
    errors: list[str] = []
    created = 0
    updated = 0
    skipped = 0

    # 解析格式
    items: list[dict] = []
    if isinstance(data, list):
        # 原生数组
        for idx, raw in enumerate(data):
            try:
                if not isinstance(raw, dict):
                    raise ValueError(f"item must be object, got {type(raw).__name__}")
                items.append(_normalize_native_item(raw))
            except (ValueError, TypeError) as e:
                errors.append(f"item[{idx}]: {e}")
    elif isinstance(data, dict) and _is_litellm_format(data):
        # LiteLLM 字典
        for k, meta in data.items():
            try:
                normalized = _normalize_litellm_item(k, meta)
                if normalized is not None:
                    items.append(normalized)
            except (ValueError, TypeError) as e:
                errors.append(f"'{k}': {e}")
    elif isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
        # 兼容 {items: [...]} 包装
        for idx, raw in enumerate(data["items"]):
            try:
                items.append(_normalize_native_item(raw))
            except (ValueError, TypeError) as e:
                errors.append(f"items[{idx}]: {e}")
    else:
        errors.append("Unrecognized JSON format. Expected array, LiteLLM dict, or {items:[...]}")
        return ImportResult(0, 0, 0, 0, errors)

    received = len(items)

    # replace 模式先清 manual
    if strategy == "replace":
        existing_manual = list((await session.execute(
            select(ModelPriceCatalog).where(ModelPriceCatalog.source == PriceSource.manual)
        )).scalars().all())
        for r in existing_manual:
            await session.delete(r)
        await session.flush()

    # 处理每条
    for n in items:
        existing = (await session.execute(
            select(ModelPriceCatalog).where(ModelPriceCatalog.pattern == n["pattern"])
        )).scalar_one_or_none()

        if existing is None or strategy == "replace":
            session.add(ModelPriceCatalog(
                pattern=n["pattern"],
                match_type=PriceMatchType(n["match_type"]),
                input_per_1m=n["input_per_1m"],
                output_per_1m=n["output_per_1m"],
                cached_input_per_1m=n["cached_input_per_1m"],
                per_image_usd=n["per_image_usd"],
                per_step_usd=n["per_step_usd"],
                unit=PriceUnit(n["unit"]),
                source=PriceSource.manual,
                priority=n["priority"],
                enabled=n["enabled"],
                note=n["note"],
            ))
            created += 1
        elif strategy == "update":
            existing.input_per_1m = n["input_per_1m"]
            existing.output_per_1m = n["output_per_1m"]
            existing.cached_input_per_1m = n["cached_input_per_1m"]
            existing.per_image_usd = n["per_image_usd"]
            existing.per_step_usd = n["per_step_usd"]
            existing.match_type = PriceMatchType(n["match_type"])
            existing.unit = PriceUnit(n["unit"])
            existing.priority = n["priority"]
            existing.enabled = n["enabled"]
            if n["note"]:
                existing.note = n["note"]
            existing.source = PriceSource.manual
            updated += 1
        else:  # skip
            skipped += 1

    return ImportResult(received, created, updated, skipped, errors)


async def export_all_as_json(session: AsyncSession) -> list[dict]:
    """导出所有价格条目为 JSON 数组（原生格式，可直接 import 回来）。"""
    rows = list((await session.execute(
        select(ModelPriceCatalog).order_by(ModelPriceCatalog.priority.desc(), ModelPriceCatalog.pattern)
    )).scalars().all())
    return [
        {
            "pattern": r.pattern,
            "match_type": r.match_type.value,
            "input_per_1m": r.input_per_1m,
            "output_per_1m": r.output_per_1m,
            "cached_input_per_1m": r.cached_input_per_1m,
            "per_image_usd": r.per_image_usd,
            "per_step_usd": r.per_step_usd,
            "unit": r.unit.value,
            "priority": r.priority,
            "enabled": bool(r.enabled),
            "note": r.note,
        }
        for r in rows
    ]


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
