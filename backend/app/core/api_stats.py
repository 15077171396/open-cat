"""接口调用统计（2026-11-19）：小时桶聚合 + 慢请求环形明细。

## 为什么聚合到 Redis 而不是逐条落库

每请求一条 INSERT 会把数据库打成写放大重灾区（2 核 4G 的单机部署最怕这个）。
按「(方法, 路径, 状态桶) → 次数」聚合进 Redis 小时桶 hash，一次请求只多
2~3 个 Redis 命令，且**零 DB 写入**；慢请求（超过阈值的少数）才逐条进环形列表。

## 键与生命周期

- `ops:api:stats:{yyyymmddhh}`：hash，字段 `{method} {path} {2xx|4xx|5xx|other}` → 次数；
  保留当前 + 上一小时（TTL 48h 兜底），页面回看「这一小时 / 上一小时」。
- `ops:api:slow`：list，慢请求明细（LPUSH + LTRIM 200），与错误日志环形缓冲同构。

## 取舍

- **路径归一化**：数字段折叠成 `{{id}}`（雪花 ID / 自增 ID 都是纯数字），
  否则 `/admin/users/123` 每个用户一条聚合，Top 榜全是噪音。
- **观测绝不反噬主流程**：记录失败静默（degrade_guard 收口，不用裸 except）；
  中间件计时用 `perf_counter`，包装 send 只读状态码、不改响应流。
- **开关**：`API_STATS_ENABLED` 可在后台关闭（排障时怀疑统计本身有开销就用它）。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone

from app.config import settings
from app.core.degrade import degrade_guard

# Redis 键（查询侧 api_stats_service 与本模块共用）
STATS_KEY_PREFIX = "ops:api:stats:"
STATS_TTL_SECONDS = 48 * 3600
SLOW_KEY = "ops:api:slow"
SLOW_MAX = 200

# 路径归一化：连续数字段折叠（雪花/自增 ID、分页参数不在路径里无需处理）
_NUMERIC_SEGMENT = re.compile(r"/\d+(?=/|$)")
_PATH_MAX = 120

# fire-and-forget 任务的强引用（防止任务对象被 GC 掉一半）
_pending_tasks: set[asyncio.Task] = set()

_STATUS_BUCKETS = ((500, "5xx"), (400, "4xx"), (200, "2xx"))


def normalize_path(path: str) -> str:
    """数字段折叠成 {id}，并截断（超长路径只出现在恶意请求里，截断即可聚合）。"""
    normalized = _NUMERIC_SEGMENT.sub("/{id}", path)
    return normalized[:_PATH_MAX]


def status_bucket(status: int) -> str:
    for floor, bucket in _STATUS_BUCKETS:
        if status >= floor:
            return bucket
    return "other"


def _bucket_key(now: datetime) -> str:
    return f"{STATS_KEY_PREFIX}{now.strftime('%Y%m%d%H')}"


async def _record(now: datetime, method: str, path: str, bucket: str, duration_ms: int, payload: dict | None) -> None:
    """真正写 Redis 的部分（统计 + 慢明细）。失败静默（观测不反噬）。"""
    from app.redis import redis_client

    with degrade_guard("api_stats.record", detail="接口统计写入失败（不影响请求本身）"):
        pipe = redis_client.pipeline()
        pipe.hincrby(_bucket_key(now), f"{method} {path} {bucket}", 1)
        pipe.expire(_bucket_key(now), STATS_TTL_SECONDS)
        await pipe.execute()

    if payload is not None:
        with degrade_guard("api_stats.slow", detail="慢请求明细写入失败（不影响请求本身）"):
            raw = json.dumps(payload, ensure_ascii=False, default=str)
            pipe = redis_client.pipeline()
            pipe.lpush(SLOW_KEY, raw)
            pipe.ltrim(SLOW_KEY, 0, SLOW_MAX - 1)
            pipe.expire(SLOW_KEY, STATS_TTL_SECONDS)
            await pipe.execute()


def record_api_call(method: str, path: str, status: int, duration_ms: int, *, user_id: str | None = None) -> None:
    """记一次调用（fire-and-forget，绝不抛出/阻塞响应）。"""
    if not settings.API_STATS_ENABLED:
        return

    now = datetime.now(timezone.utc)
    normalized = normalize_path(path)
    bucket = status_bucket(status)
    slow_payload = None
    if duration_ms >= settings.API_SLOW_MS:
        slow_payload = {
            "ts": now.isoformat(),
            "method": method,
            "path": normalized,
            "status": status,
            "duration_ms": duration_ms,
            "user_id": user_id,
        }

    async def _job():
        await _record(now, method, normalized, bucket, duration_ms, slow_payload)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # 无事件循环（脚本/导入期）：跳过
    task = loop.create_task(_job())
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)


class ApiStatsMiddleware:
    """纯 ASGI 计时统计（与 RateLimitMiddleware 同款实现形态）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        # OPTIONS 预检由 CORS 消化，不计入业务统计
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_holder: dict[str, int] = {}

        async def _send_wrapped(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, _send_wrapped)
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            status = status_holder.get("status", 500)
            # 用户维度只区分「带凭据与否」（管理请求统一带 Authorization 头），
            # 不解析 cookie / 不解码 JWT —— 统计不增加可感知开销
            user_id = None
            for key, value in scope.get("headers") or []:
                if key == b"authorization":
                    user_id = "authed"
                    break
            record_api_call(method, scope.get("path", ""), status, duration_ms, user_id=user_id)
