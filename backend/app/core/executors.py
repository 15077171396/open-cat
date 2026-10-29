"""专用线程池：把重 I/O + 重 CPU 的同步工作（COS 上传、WebP 转码、HTML→Markdown）与口令哈希隔离，
避免占满事件循环默认线程池（仅 min(32, cpu+4) 线程）时登录 bcrypt 校验无声排队。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")

# 8 = 覆盖一次图片批量转存的并发（TRANSFER_CONCURRENCY=5）并留出余量；
# 再大也没意义：这些任务要么在等 COS（受带宽限制），要么在烧 CPU（本机只有 2 核）。
HEAVY_IO_WORKERS = 8

HEAVY_IO_EXECUTOR = ThreadPoolExecutor(
    max_workers=HEAVY_IO_WORKERS, thread_name_prefix="heavy-io"
)


async def run_heavy(fn: Callable[..., T], *args) -> T:
    """在重活专用池里执行同步函数（用法与 `asyncio.to_thread` 一致）。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(HEAVY_IO_EXECUTOR, fn, *args)


def shutdown_heavy_executor() -> None:
    """进程退出时释放线程（lifespan 关闭钩子调用）。
    `wait=False`：已在等 COS 的任务不该拖住停机流程，容器 termination 预算只有几十秒。"""
    HEAVY_IO_EXECUTOR.shutdown(wait=False)
