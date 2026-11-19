"""日志配置 - loguru 控制台与文件 sink（按大小滚动、保留 14 天）
+ 错误日志环形缓冲（ERROR+ 事件推入 Redis，供后台「监控管理 → 错误日志」页读取）"""

import asyncio
import json
import os
import sys
import time

from loguru import logger
from redis.exceptions import RedisError

from app.config import settings
from app.core.request_context import get_request_id

LOG_DIR = os.environ.get("LOG_DIR", "logs")
LOG_FILE = os.path.join(LOG_DIR, "app.log")

ROTATION = os.environ.get("LOG_ROTATION", "10 MB")
# loguru retention 支持整数(保留文件数)或时间字符串(如 "14 days"); 默认保留 14 天
RETENTION = os.environ.get("LOG_RETENTION", "14 days")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG" if settings.is_dev else "INFO")

# ---- 错误日志环形缓冲 ----
# 为什么是 Redis 而不是读文件：多 worker + 容器部署时文件散在各自容器里，
# Redis 是唯一跨进程共享的存储。LPUSH + LTRIM 保留最近 N 条。
# ⚠️ 自激防护：上报失败**绝不打日志**（否则每条失败的日志又触发上报，无限循环），
# 只静默吞掉窄类型异常（RedisError/OSError）；也不用 degrade_open（它会打日志）。
ERROR_LOG_KEY = "ops:errorlog:recent"
ERROR_LOG_MAX = 500
_DEDUP_WINDOW_SECONDS = 5.0
_LAST_PUSH: dict[str, float] = {}


async def _push_error_event(payload: dict) -> None:
    """把一条错误事件推入 Redis 环形缓冲（fire-and-forget，绝不抛出/打日志）。"""
    try:
        # 延迟导入：logging.py 在应用最早期就被 import，不能在模块层引 app.redis
        from app.redis import redis_client

        raw = json.dumps(payload, ensure_ascii=False, default=str)
        await redis_client.lpush(ERROR_LOG_KEY, raw)
        await redis_client.ltrim(ERROR_LOG_KEY, 0, ERROR_LOG_MAX - 1)
    except (RedisError, OSError, RuntimeError, ImportError):
        # Redis 不可用/事件循环已关：静默放弃（运维页显示「不可用」，不影响主流程）
        return


def _error_log_sink(message) -> None:
    """loguru sink：ERROR+ 事件进 Redis 环形缓冲。"""
    record = message["record"]
    extra = record.get("extra") or {}
    # 指纹去重：同一位置 5 秒内重复报错只推第一条（防异常风暴把缓冲刷满）
    fingerprint = f"{record['name']}:{record['function']}:{str(record['message'])[:120]}"
    now = time.monotonic()
    last = _LAST_PUSH.get(fingerprint)
    if last is not None and now - last < _DEDUP_WINDOW_SECONDS:
        return
    if len(_LAST_PUSH) > 512:
        _LAST_PUSH.clear()
    _LAST_PUSH[fingerprint] = now

    payload = {
        # loguru 的 message 是 dict 子类，时间要从 record 里取（message.time 属性不存在）
        "ts": record["time"].isoformat() if record.get("time") else None,
        "level": record["level"].name,
        "message": str(record["message"])[:500],
        "name": record["name"],
        "function": record["function"],
        "line": record["line"],
        "request_id": extra.get("request_id"),
    }
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # 无事件循环（导入期/纯同步脚本）：跳过
    loop.create_task(_push_error_event(payload))


def _inject_request_id(record) -> bool:
    """在 filter 里统一注入请求关联 ID：任何一条日志都自动带上不会漏绑；
    无请求上下文填 -，setdefault 让调用方显式 bind 时以调用方为准。"""
    record["extra"].setdefault("request_id", get_request_id() or "-")
    return True


def setup_logging():
    """配置 loguru sink: 控制台 + 文件（应在应用启动早期调用一次，重复调用先移除旧 sink）"""
    logger.remove()
    # 控制台 sink (彩色, 简洁格式)
    logger.add(
        sys.stderr,
        level=LOG_LEVEL,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{extra[request_id]}</cyan> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        filter=_inject_request_id,
        backtrace=settings.is_dev,
        diagnose=settings.is_dev,
    )

    # 文件 sink (JSON 行, 便于采集; 滚动 + 保留)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        logger.add(
            LOG_FILE,
            level=LOG_LEVEL,
            rotation=ROTATION,
            retention=RETENTION,
            compression="zip",
            enqueue=True,  # 进程安全, 避免多 worker 写冲突
            serialize=True,  # 结构化 JSON 输出, 含 extra 字段 (logger.bind 的内容)
            filter=_inject_request_id,
            backtrace=True,
            # 文件 sink 的 diagnose 恒为 False：diagnose 会把异常帧局部变量渲染进落盘日志
            # 且保留 14 天，明文口令一旦进文件就很难收回；控制台仍跟随 is_dev（不落盘）
            diagnose=False,
            delay=True,
        )
    except OSError:
        # 日志目录不可写时仅退化为控制台输出, 不阻塞启动
        logger.warning(f"无法初始化文件日志 sink: {LOG_FILE} (仅控制台输出)")

    # 错误日志环形缓冲 sink（ERROR+）：失败静默，见模块头注释
    logger.add(_error_log_sink, level="ERROR", filter=_inject_request_id)


# 模块导入时即完成基础配置, 保证早期日志也可被文件 sink 捕获
setup_logging()
