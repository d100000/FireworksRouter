"""5 分钟桶指标聚合：异步事件队列 → worker 批量 flush 到 key_metric_buckets。"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, desc, func, select

from app.db import session_scope
from app.models import KeyMetricBucket, ProbeHistory, UpstreamKey
from app.utils.logger import logger


@dataclass
class MetricEvent:
    upstream_key_id: int | None
    success: bool
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    timestamp: datetime


_queue: asyncio.Queue[MetricEvent] = asyncio.Queue(maxsize=10000)
_worker_task: asyncio.Task | None = None
_aggregator_task: asyncio.Task | None = None
_cleaner_task: asyncio.Task | None = None

_BUCKET_MINUTES = 5
_FLUSH_INTERVAL_S = 30


def _bucket_start(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    minute = (ts.minute // _BUCKET_MINUTES) * _BUCKET_MINUTES
    return ts.replace(minute=minute, second=0, microsecond=0)


async def enqueue(event: MetricEvent) -> None:
    if event.upstream_key_id is None:
        return
    try:
        _queue.put_nowait(event)
    except asyncio.QueueFull:
        logger.warning("metrics_queue full, dropping event")


async def _flush_worker() -> None:
    """每 30 秒把队列里的事件批量 upsert 到 key_metric_buckets。"""
    while True:
        try:
            await asyncio.sleep(_FLUSH_INTERVAL_S)
            await _drain_and_flush()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("metrics flush worker error: {}", e)


async def _drain_and_flush() -> None:
    # 把队列里目前所有事件取出来
    events: list[MetricEvent] = []
    while not _queue.empty():
        try:
            events.append(_queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    if not events:
        return

    # 按 (upstream_key_id, bucket_start) 聚合
    buckets: dict[tuple[int, datetime], dict] = {}
    for e in events:
        bs = _bucket_start(e.timestamp)
        key = (e.upstream_key_id, bs)
        b = buckets.setdefault(
            key,
            {
                "success": 0, "failed": 0, "prompt_tokens": 0, "completion_tokens": 0,
                "cost_usd": 0.0, "total_latency_ms": 0,
                "max_latency_ms": 0, "min_latency_ms": 0, "_min_init": False,
            },
        )
        if e.success:
            b["success"] += 1
        else:
            b["failed"] += 1
        b["prompt_tokens"] += e.prompt_tokens
        b["completion_tokens"] += e.completion_tokens
        b["cost_usd"] += e.cost_usd
        b["total_latency_ms"] += e.latency_ms
        if e.latency_ms > b["max_latency_ms"]:
            b["max_latency_ms"] = e.latency_ms
        if not b["_min_init"] or e.latency_ms < b["min_latency_ms"]:
            b["min_latency_ms"] = e.latency_ms
            b["_min_init"] = True

    # upsert 到 DB
    async with session_scope() as session:
        for (key_id, bs), agg in buckets.items():
            stmt = select(KeyMetricBucket).where(
                KeyMetricBucket.upstream_key_id == key_id,
                KeyMetricBucket.bucket_start == bs,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing is None:
                session.add(
                    KeyMetricBucket(
                        upstream_key_id=key_id, bucket_start=bs,
                        success=agg["success"], failed=agg["failed"],
                        prompt_tokens=agg["prompt_tokens"], completion_tokens=agg["completion_tokens"],
                        cost_usd=agg["cost_usd"],
                        total_latency_ms=agg["total_latency_ms"],
                        max_latency_ms=agg["max_latency_ms"], min_latency_ms=agg["min_latency_ms"],
                    )
                )
            else:
                existing.success += agg["success"]
                existing.failed += agg["failed"]
                existing.prompt_tokens += agg["prompt_tokens"]
                existing.completion_tokens += agg["completion_tokens"]
                existing.cost_usd += agg["cost_usd"]
                existing.total_latency_ms += agg["total_latency_ms"]
                if agg["max_latency_ms"] > existing.max_latency_ms:
                    existing.max_latency_ms = agg["max_latency_ms"]
                if existing.min_latency_ms == 0 or (agg["_min_init"] and agg["min_latency_ms"] < existing.min_latency_ms):
                    existing.min_latency_ms = agg["min_latency_ms"]

    logger.debug("metrics flush: {} events → {} buckets", len(events), len(buckets))


async def _stability_aggregator() -> None:
    """每分钟聚合 24h 稳定性 + 1h sparkline 物化字段 + 最近探针物化。"""
    while True:
        try:
            await asyncio.sleep(60)
            await _refresh_stability_scores()
            await _refresh_recent_buckets()
            await _refresh_last_probe()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("stability aggregator error: {}", e)


async def _refresh_stability_scores() -> None:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    async with session_scope() as session:
        # 按 upstream_key_id 聚合 24h
        stmt = (
            select(
                KeyMetricBucket.upstream_key_id,
                func.coalesce(func.sum(KeyMetricBucket.success), 0).label("ok"),
                func.coalesce(func.sum(KeyMetricBucket.failed), 0).label("bad"),
            )
            .where(KeyMetricBucket.bucket_start >= since)
            .group_by(KeyMetricBucket.upstream_key_id)
        )
        rows = list((await session.execute(stmt)).all())

        # 拿全部上游 Key
        all_keys = list((await session.execute(select(UpstreamKey))).scalars().all())
        agg_map = {r.upstream_key_id: (int(r.ok), int(r.bad)) for r in rows}

        for k in all_keys:
            ok, bad = agg_map.get(k.id, (0, 0))
            k.success_count_24h = ok
            k.failed_count_24h = bad
            total = ok + bad
            if total == 0:
                # 无流量时保持 1.0（乐观）
                k.stability_score = 1.0
            else:
                rate = ok / total
                # 流量加成因子：流量越大评分越置信
                weight = 1 - math.exp(-total / 10.0)
                k.stability_score = round(rate * (0.5 + 0.5 * weight), 4)


def _bucket_10min(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    minute = (ts.minute // 10) * 10
    return ts.replace(minute=minute, second=0, microsecond=0)


async def _refresh_recent_buckets() -> None:
    """物化最近 1h 的 sparkline 数据到 upstream_keys.recent_buckets_json。

    一条 SQL 拉所有 Key 的最近 1h 桶 → Python 按 10min 重聚合 → 批量回写。
    100 把 Key 实测 < 50ms。
    """
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    async with session_scope() as session:
        # 一条 SQL 拉所有数据
        stmt = (
            select(
                KeyMetricBucket.upstream_key_id,
                KeyMetricBucket.bucket_start,
                KeyMetricBucket.success,
                KeyMetricBucket.failed,
                KeyMetricBucket.total_latency_ms,
            )
            .where(KeyMetricBucket.bucket_start >= since)
            .order_by(KeyMetricBucket.upstream_key_id, KeyMetricBucket.bucket_start)
        )
        rows = list((await session.execute(stmt)).all())

        # Python 内分组 + 按 10min 重聚合（合并 2 个 5min 桶）
        per_key: dict[int, dict[datetime, dict]] = {}
        for r in rows:
            bs10 = _bucket_10min(r.bucket_start)
            agg = per_key.setdefault(r.upstream_key_id, {}).setdefault(
                bs10, {"success": 0, "failed": 0, "total_ms": 0}
            )
            agg["success"] += r.success or 0
            agg["failed"] += r.failed or 0
            agg["total_ms"] += r.total_latency_ms or 0

        # 拉所有 Key（要给无数据的 Key 也写空数组）
        all_keys = list((await session.execute(select(UpstreamKey))).scalars().all())
        now = datetime.now(timezone.utc)

        for k in all_keys:
            buckets_map = per_key.get(k.id, {})
            # 生成 6 个连续 10min 桶（即使无数据也补 0，方便前端 sparkline 不错位）
            anchor = _bucket_10min(now)
            series = []
            for i in range(5, -1, -1):  # 50min前 → 当前
                ts = anchor - timedelta(minutes=i * 10)
                agg = buckets_map.get(ts)
                if agg:
                    total = agg["success"] + agg["failed"]
                    avg_ms = int(agg["total_ms"] / total) if total else 0
                    series.append({
                        "ts": ts.isoformat(),
                        "success": agg["success"],
                        "failed": agg["failed"],
                        "avg_ms": avg_ms,
                    })
                else:
                    series.append({"ts": ts.isoformat(), "success": 0, "failed": 0, "avg_ms": 0})
            k.recent_buckets_json = series
            k.recent_buckets_updated_at = now


async def _refresh_last_probe() -> None:
    """物化每把 Key 最近一次 probe 结果到 upstream_keys.last_probe_*。

    SQLite 兼容方案：用 max(id) 子查询拿每个 key 的最近一条 ProbeHistory。
    """
    async with session_scope() as session:
        # 每个 upstream_key 的最大 probe id
        subq = (
            select(
                ProbeHistory.upstream_key_id,
                func.max(ProbeHistory.id).label("max_id"),
            )
            .group_by(ProbeHistory.upstream_key_id)
            .subquery()
        )
        stmt = (
            select(
                ProbeHistory.upstream_key_id,
                ProbeHistory.success,
                ProbeHistory.latency_ms,
                ProbeHistory.created_at,
            )
            .join(subq, ProbeHistory.id == subq.c.max_id)
        )
        rows = list((await session.execute(stmt)).all())
        latest: dict[int, tuple[bool, int, datetime]] = {
            r.upstream_key_id: ((r.success == "ok"), r.latency_ms or 0, r.created_at)
            for r in rows
        }

        all_keys = list((await session.execute(select(UpstreamKey))).scalars().all())
        for k in all_keys:
            t = latest.get(k.id)
            if t is None:
                continue
            ok, ms, at = t
            k.last_probe_ok = ok
            k.last_probe_ms = ms
            k.last_probe_at = at


async def _cleaner() -> None:
    """每小时清理 24h 之前的桶。"""
    while True:
        try:
            await asyncio.sleep(3600)
            cutoff = datetime.now(timezone.utc) - timedelta(hours=25)
            async with session_scope() as session:
                await session.execute(
                    delete(KeyMetricBucket).where(KeyMetricBucket.bucket_start < cutoff)
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("metrics cleaner error: {}", e)


def start_workers() -> None:
    global _worker_task, _aggregator_task, _cleaner_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_flush_worker(), name="metrics-flush")
    if _aggregator_task is None or _aggregator_task.done():
        _aggregator_task = asyncio.create_task(_stability_aggregator(), name="metrics-stability")
    if _cleaner_task is None or _cleaner_task.done():
        _cleaner_task = asyncio.create_task(_cleaner(), name="metrics-cleaner")
    logger.info("metrics workers started")


def stop_workers() -> None:
    for t in (_worker_task, _aggregator_task, _cleaner_task):
        if t is not None and not t.done():
            t.cancel()
