"""浏览量计数：Redis 累加 + 定时落库。

**为什么不再每次详情 GET 都写库**：详情页是爬虫与用户都最常打的接口，
`UPDATE ... SET view_count = view_count + 1` 随请求事务提交，等于每次访问产生
一次行更新 + 一次事务提交 + 一份 row 格式 binlog。同一篇内容 50 QPS 就是 50 次/秒
对同一行的更新（InnoDB 行锁串行化），而 MySQL 与应用同机，redo/binlog 写放大直接抢 IO。
预渲染服务为爬虫渲染详情页时也会触发，进一步放大。

**现在的语义**：
- 读路径：`HINCRBY view_pending:{model} {id} 1` 一次 Redis 调用，返回值就是
  「尚未落库的增量」，响应里展示 `DB 值 + 该增量`（所以用户看到的是包含本次的实时值）；
- 落库：调度器每分钟 `flush_pending_views()` 把增量合并成一条 UPDATE 写回；
- Redis 不可用：回退为原来的「直接 UPDATE +1」，行为与改造前完全一致，
  不会因为缓存抖动丢掉计数（代价是那一刻回到写热点）。

**已知取舍**：落库采用「先 HDEL 认领、再 UPDATE 提交」，提交失败时把增量回填。
极端情况下（提交成功但进程在 HDEL 前被杀）会**少计**一次，不会多计 ——
浏览量是展示指标，宁可少算也不能凭空膨胀排序权重。

**为什么不碰 `updated_at`**：这些模型都有 `updated_at` 的 `onupdate=func.now()`，
默认行为下「看一次」也会把它刷成现在 —— 列表的「最新更新」排序就变成「最近被看过」，
热门内容永远排在最前（第五轮审查 PERF-13）。做法是把 `updated_at` **显式写进 SET**
（`updated_at = 表.updated_at`，等价于不变）：SQLAlchemy 只在列未出现在 SET 里时才执行
onupdate，因此这一句既保住了 ORM 的可读性/可测试性，又不会刷新时间戳。
顺带用 `func.coalesce` 兜住历史脏数据里的 NULL（NULL + 1 仍是 NULL）。
"""

import asyncio

from loguru import logger
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tutorial import Tutorial
from app.redis import log_redis_degraded, redis_client

PENDING_PREFIX = "view_pending:"

# 模型键 → 表。键同时出现在 Redis 的 hash 名里，改动会影响存量待落库数据，别随手改名。
_VIEW_TABLES: dict[str, type] = {
    "tutorial": Tutorial,
}


async def _incr_view_count_in_db(db: AsyncSession, table, row_id: int, inc: int) -> None:
    """只把 view_count 加上去，**不碰 updated_at**（原因见模块文档 PERF-13）。

    `updated_at=table.updated_at` 不是冗余：把该列显式写进 SET 才能抑制模型的
    `onupdate=func.now()`，否则每次浏览都会把「最新更新」排序顶上去。
    """
    await db.execute(
        update(table)
        .where(table.id == row_id)
        .values(
            view_count=func.coalesce(table.view_count, 0) + inc,
            updated_at=table.updated_at,
        )
    )


async def register_view(
    db: AsyncSession,
    model_key: str,
    row_id: int,
    current: int | None = None,
) -> int:
    """记一次浏览，返回「展示用」的浏览量。

    :param current: 本次读到的 DB 值（可为 None，历史数据可能是 NULL）
    :return: 应当返回给前端的浏览量（DB 值 + 待落库增量，含本次）
    """
    table = _VIEW_TABLES[model_key]
    # 用户级归因（按天去重，只记登录用户）：与聚合计数互不依赖，
    # 失败只留日志（record_content_view 内部已用 SAVEPOINT + degrade_guard）
    from app.services.content_view_service import record_content_view

    await record_content_view(db, model_key, row_id)
    try:
        # HINCRBY 的返回值就是累加后的增量总数，一次往返同时完成「计数」与「取展示值」
        delta = await redis_client.hincrby(f"{PENDING_PREFIX}{model_key}", str(row_id), 1)
        return (current or 0) + int(delta)
    except Exception as exc:  # noqa: BLE001 Redis 不可用时不能影响详情页打开
        log_redis_degraded(
            "view_counter.register_view",
            f"浏览量累加降级为直接写库（{type(exc).__name__}: {exc}）",
        )
        await _incr_view_count_in_db(db, table, row_id, 1)
        return (current or 0) + 1


# 原子认领脚本：**一次 EVAL 里完成「读全部字段 + 删除全部字段」**。
#
# 为什么不能 hgetall + hdel 两步走（第二轮复审 C-02）：
#   · 两个进程（gunicorn -w 2 启动时各 flush 一次，外加 scheduler 每分钟一次）
#     都可能读到同一批数据，各自 UPDATE 一次 → 浏览量**翻倍**；
#   · 更隐蔽的是「读」与「删」之间发生的 HINCRBY：HDEL 删的是整个字段，
#     那期间新增的浏览会被一起抹掉 → **丢数**。
# Lua 脚本在 Redis 内串行执行，两个问题一起消失。
# 返回值是扁平数组 [field1, value1, field2, value2, ...]。
_CLAIM_SCRIPT = """
local vals = redis.call('HGETALL', KEYS[1])
for i = 1, #vals, 2 do
  redis.call('HDEL', KEYS[1], vals[i])
end
return vals
"""

# 单事务批量：待落库超过这个条数就分段提交。
# 全站被爬一遍后可能积压上千条，一次性提交会变成秒级长事务（持有行锁与池连接）。
_FLUSH_BATCH_SIZE = 500


async def _claim_pending(pending_key: str) -> dict[str, str] | None:
    """原子认领一批待落库增量；认领失败返回 None（本轮跳过）。

    优先用 EVAL（Redis 2.6+ 均支持）。个别环境（ACL 禁 EVAL / 测试桩）没有该命令时
    退回 hgetall + hdel，并**校验 HDEL 的实际删除数**：删除数对不上说明另一个进程
    已经先认领了，本轮必须放弃，否则就是重复累加。
    """
    if hasattr(redis_client, "eval"):
        try:
            flat = await redis_client.eval(_CLAIM_SCRIPT, 1, pending_key)
            return {str(flat[i]): str(flat[i + 1]) for i in range(0, len(flat), 2)}
        except Exception as exc:  # noqa: BLE001 降级到两步走，不阻断落库
            log_redis_degraded(
                "view_counter.flush_claim_eval",
                f"EVAL 认领失败，改用 hgetall+hdel（{type(exc).__name__}: {exc}）",
            )

    raw = await redis_client.hgetall(pending_key)
    if not raw:
        return {}
    removed = await redis_client.hdel(pending_key, *raw.keys())
    if int(removed or 0) != len(raw):
        logger.warning(
            "浏览量认领冲突（期望删除 %s 个字段，实际 %s）：另一个进程已先认领，本轮跳过",
            len(raw),
            removed,
        )
        return None
    return raw


async def flush_pending_views(db: AsyncSession) -> int:
    """把各表待落库的浏览量增量合并写回数据库，返回处理的内容条数。

    由调度器每分钟调用一次；应用启动时也会调一次，用于回收上次进程异常退出
    留在 Redis 里的增量（否则那些浏览永远只存在于缓存里）。
    """
    flushed = 0
    for model_key, table in _VIEW_TABLES.items():
        pending_key = f"{PENDING_PREFIX}{model_key}"
        try:
            raw = await _claim_pending(pending_key)
        except Exception as exc:  # noqa: BLE001 读不到/认领失败就留到下一轮
            log_redis_degraded("view_counter.flush_read", f"读取待落库浏览量失败（{model_key}）: {exc}")
            continue
        if raw is None or not raw:
            continue

        # 认领后若写库失败，用 HINCRBY 回填，避免这批浏览凭空消失。
        # rows 是**全部**待写行；committed 记录已成功提交到第几条 —— 失败时只回填
        # 未提交的部分（已提交的再回填就会重复累加）。
        rows: list[tuple[int, int]] = []
        for row_id, delta in raw.items():
            try:
                rid = int(row_id)
                inc = int(delta)
            except (TypeError, ValueError):
                logger.warning(f"浏览量待落库数据格式异常，已丢弃: {model_key} {row_id}={delta}")
                continue
            if inc > 0:
                rows.append((rid, inc))

        committed = 0
        try:
            for start in range(0, len(rows), _FLUSH_BATCH_SIZE):
                chunk = rows[start : start + _FLUSH_BATCH_SIZE]
                for rid, inc in chunk:
                    await _incr_view_count_in_db(db, table, rid, inc)
                await db.commit()
                committed = start + len(chunk)
                flushed += len(chunk)
        except Exception:
            await db.rollback()
            logger.exception(f"浏览量落库失败（{model_key}），未提交的增量回填 Redis 等待下一轮")
            try:
                for rid, inc in rows[committed:]:
                    await redis_client.hincrby(pending_key, str(rid), inc)
            except Exception as exc:  # noqa: BLE001 回填失败只能丢这一批
                log_redis_degraded("view_counter.flush_restore", f"回填待落库浏览量失败（{model_key}）: {exc}")
    if flushed:
        logger.debug(f"浏览量落库完成，共 {flushed} 条内容")
    return flushed


async def flush_loop(interval_seconds: int = 60) -> None:
    """进程内落库循环 —— **仅本地开发用**。

    生产环境由 `scheduler` 容器每分钟跑一次（backend/scheduler/tasks.py::flush_view_counts）；
    本地起 uvicorn 时没有那个容器，不补这个循环的话浏览量只会堆在 Redis 里，
    重启才落库，本地看起来像「浏览量不涨」。
    """
    # 延迟导入：避免 services → database 的模块级循环依赖
    from app.database import async_session

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            async with async_session() as db:
                await flush_pending_views(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 循环不能因单次失败而退出
            logger.exception("浏览量进程内落库失败（下一轮重试）")
