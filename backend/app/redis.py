"""Redis 连接管理"""

import time

import redis.asyncio as redis
from loguru import logger
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.config import settings

# 异步 Redis 连接池 (用参数构造, 避免密码含特殊字符时 URL 解析失败)
redis_client: redis.Redis = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    password=settings.REDIS_PASSWORD or None,
    db=settings.REDIS_DB,
    decode_responses=True,
    # max_connections 是上限而非并发数：redis-py 异步池不排队，超限直接抛
    # MaxConnectionsError（曾与「Redis 挂了」混在同一个 except，对合法用户返回 429）
    max_connections=200,
    protocol=2,
    # 超时必须显式设置：默认 None = 不限时，故障时会一直挂起
    socket_connect_timeout=2,
    socket_timeout=3,
    # 必须显式关闭 redis-py 5.x 默认的 3 次指数退避重试：「Redis 连不上」是持续性
    # 故障，重试会把单次操作阻塞从 2s 拖到约 25s；所有调用点已自带 try/except 降级
    retry=Retry(NoBackoff(), 0),
)


async def close_redis():
    """关闭 Redis 连接（redis-py 5.x 推荐使用 aclose，close 已弃用）"""
    await redis_client.aclose()


# 降级日志按调用位置限流：Redis 不可用是持续性故障，逐请求 warning 会刷满日志，
# 折中为同一位置每 _DEGRADED_LOG_INTERVAL 秒最多一条
_DEGRADED_LOG_INTERVAL = 60.0
_degraded_log_at: dict[str, float] = {}


def log_redis_degraded(where: str, exc: Exception | str) -> None:
    """记录一次 Redis 降级（同一 where 每分钟最多一条 warning）"""
    now = time.monotonic()
    last = _degraded_log_at.get(where, 0.0)
    if now - last < _DEGRADED_LOG_INTERVAL:
        return
    _degraded_log_at[where] = now
    logger.warning(f"Redis 降级（{where}）：{exc}")
