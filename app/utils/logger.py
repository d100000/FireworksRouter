"""日志系统：loguru → stdout（所有级别） + DB sink（默认 WARNING+，可调）。

DB sink 采用 asyncio.Queue + 后台 worker 批量 flush 模式，避免每条日志同步 DB。
启动顺序：
  1. setup_logging() — 早期调用，挂 stdout sink，DB sink 先以"暂存模式"启动
     （未启动 worker，事件会进队列等 start_db_sink 后才落库）
  2. start_db_sink() — 在 FastAPI lifespan 起来后调用，启动 flush worker
  3. stop_db_sink() — lifespan shutdown 时调用，flush 完剩余事件再退出
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.config import get_settings


# 队列上限：超过就 drop 老的（防内存膨胀），同时记一条 WARNING（不入队，只 stdout）
_MAX_QUEUE = 5000
# 批量 flush 触发条件
_FLUSH_BATCH_SIZE = 50
_FLUSH_INTERVAL_S = 5.0

# 级别名 → 数字（loguru 内部已有，这里冗余一份给 sink 过滤）
_LEVEL_NO = {
    "TRACE": 5, "DEBUG": 10, "INFO": 20, "SUCCESS": 25,
    "WARNING": 30, "ERROR": 40, "CRITICAL": 50,
}


_db_queue: asyncio.Queue | None = None
_db_worker_task: asyncio.Task | None = None
_min_level_cached: int = _LEVEL_NO["WARNING"]


def _build_event(record) -> dict[str, Any]:
    """从 loguru record 提取一条 system_logs 行需要的字段。"""
    # 异常信息
    extra_obj: dict[str, Any] = {}
    if record["exception"] is not None:
        exc = record["exception"]
        extra_obj["exception"] = {
            "type": exc.type.__name__ if exc.type else None,
            "value": str(exc.value) if exc.value else None,
            "traceback": "".join(traceback.format_exception(exc.type, exc.value, exc.traceback))[:8000],
        }
    # extra dict（用户通过 logger.bind 或 logger.opt(extra=...) 传的）
    if record.get("extra"):
        for k, v in record["extra"].items():
            # request_id 单独提一份，剩下塞 extra
            if k == "request_id":
                continue
            try:
                json.dumps({k: v})  # 验证可 JSON 化
                extra_obj[k] = v
            except (TypeError, ValueError):
                extra_obj[k] = str(v)

    return {
        "timestamp": record["time"].astimezone(timezone.utc) if record["time"].tzinfo else
                     record["time"].replace(tzinfo=timezone.utc),
        "level": record["level"].name,
        "module": record["name"] or "",
        "function": record["function"] or "",
        "line": record["line"] or 0,
        "message": record["message"][:8000],  # 截断防巨型日志撑爆
        "request_id": record["extra"].get("request_id") if record.get("extra") else None,
        "extra": json.dumps(extra_obj, ensure_ascii=False, default=str) if extra_obj else None,
    }


def _db_sink(message) -> None:
    """loguru sink callback：把记录塞进 asyncio queue，由 worker flush 到 DB。

    被 loguru 在所有 logger.info/warning/error 处调用。注意这里**不能** await
    （loguru sink 接受同步 callable 或 async callable，但 enqueue=True 模式下
    它会在独立线程跑 — 用 asyncio queue 跨线程不安全）。

    解决：直接 dict 准备好，用 queue.put_nowait 入队。如果 queue 满就丢，
    打一条 stderr 提醒（不调 logger.warning 防递归）。
    """
    record = message.record
    if _LEVEL_NO.get(record["level"].name, 0) < _min_level_cached:
        return
    if _db_queue is None:
        # worker 还没起来，丢弃（早期日志只去 stdout）
        return
    event = _build_event(record)
    try:
        _db_queue.put_nowait(event)
    except asyncio.QueueFull:
        # 队列爆了：stderr 提醒（不能用 logger，会递归）
        print(f"[system_log] queue full, dropping: {record['level'].name} {record['message'][:80]}",
              file=sys.stderr)


def setup_logging() -> None:
    """挂 stdout sink；DB sink 通过 add 注册但走我们的 _db_sink 函数。

    DB sink 在 worker 启动前会沉默丢弃，启动后 _db_queue 才接收事件。
    """
    settings = get_settings()
    logger.remove()
    # stdout — 所有级别（按 .env LOG_LEVEL 过滤）
    logger.add(
        sys.stdout,
        level=settings.log_level.upper(),
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
            "<level>{level:<7}</level> "
            "<cyan>{name}:{function}:{line}</cyan> "
            "{message}"
        ),
        backtrace=False,
        diagnose=False,
        enqueue=True,
    )
    # DB sink — 自定义函数，level=TRACE 让所有事件都进 _db_sink，
    # 由 _db_sink 内部按 _min_level_cached 二次过滤
    logger.add(
        _db_sink,
        level="TRACE",
        format="{message}",  # 没用，我们自己读 record
        enqueue=False,        # 同步调用 _db_sink；put_nowait 是 O(1) 不阻塞
    )


async def _flush_loop() -> None:
    """从 queue 拿事件，攒批后写 DB。"""
    from app.db import session_scope
    from app.models import SystemLog

    buffer: list[dict[str, Any]] = []
    last_flush = asyncio.get_event_loop().time()

    async def do_flush(events: list[dict[str, Any]]) -> None:
        if not events:
            return
        try:
            async with session_scope() as s:
                for ev in events:
                    s.add(SystemLog(**ev))
        except Exception as e:  # noqa: BLE001
            print(f"[system_log] DB flush failed: {e}", file=sys.stderr)

    while True:
        try:
            # 拿事件，最多等 _FLUSH_INTERVAL_S；超时就 flush 现有 buffer
            timeout = max(0.1, _FLUSH_INTERVAL_S - (asyncio.get_event_loop().time() - last_flush))
            try:
                ev = await asyncio.wait_for(_db_queue.get(), timeout=timeout)
                buffer.append(ev)
            except asyncio.TimeoutError:
                pass

            now = asyncio.get_event_loop().time()
            if buffer and (len(buffer) >= _FLUSH_BATCH_SIZE or (now - last_flush) >= _FLUSH_INTERVAL_S):
                events_to_flush, buffer = buffer, []
                await do_flush(events_to_flush)
                last_flush = now
        except asyncio.CancelledError:
            # 退出前 flush 一次
            if buffer:
                await do_flush(buffer)
            raise
        except Exception as e:  # noqa: BLE001
            print(f"[system_log] flush loop error: {e}", file=sys.stderr)
            # 出错就清掉 buffer 避免无限重试同一批失败的事件
            buffer.clear()
            await asyncio.sleep(1.0)


def refresh_min_level() -> None:
    """从 settings 读取 system_log_min_level，刷新本进程的过滤阈值。

    在 settings 修改后调用即可生效，无需重启。
    """
    global _min_level_cached
    from app.services import settings as settings_svc
    raw = settings_svc.get("system_log_min_level", "WARNING")
    _min_level_cached = _LEVEL_NO.get(str(raw).upper(), _LEVEL_NO["WARNING"])


async def start_db_sink() -> None:
    """启动 DB flush worker；幂等。"""
    global _db_queue, _db_worker_task
    if _db_queue is None:
        _db_queue = asyncio.Queue(maxsize=_MAX_QUEUE)
    if _db_worker_task is None or _db_worker_task.done():
        refresh_min_level()
        _db_worker_task = asyncio.create_task(_flush_loop(), name="system-log-flush")


async def stop_db_sink() -> None:
    """优雅停止：cancel worker 等它 flush 完。"""
    global _db_worker_task
    if _db_worker_task and not _db_worker_task.done():
        _db_worker_task.cancel()
        try:
            await _db_worker_task
        except asyncio.CancelledError:
            pass
    _db_worker_task = None


__all__ = ["logger", "setup_logging", "start_db_sink", "stop_db_sink", "refresh_min_level"]
