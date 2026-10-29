"""站内搜索词统计：写入一条日志 + 读出三类榜单。

## 为什么值得做

内容站最便宜的选题来源就是「用户搜了什么、搜不到什么」：
零结果词直接等于「缺哪篇内容」的清单，比拍脑袋定选题准得多。

## 三条硬约束

1. **写日志绝不能影响搜索本身**：`record_search` 内部用 SAVEPOINT 包住 INSERT，
   失败只回滚这一点，外层事务（搜索用的查询）完全不受影响。
   直接在同一个事务里裸 INSERT 的问题是：一旦失败，SQLAlchemy 会把整个事务标记为
   不可用，**接下来的 SELECT 全部报错** —— 埋点把主功能打挂，是本末倒置。
2. **表可能还不存在**（老库没跑迁移）：所有读取失败都返回 `available=False` 的空报告，
   让前端显示「尚未采集」，而不是抛 500 让整页打不开。
3. **不存用户标识**：只做热度/零结果/模块分布，不做按人下钻；搜索词含隐私意图，
   能少存就少存。

保留期 90 天，由 `scheduler/tasks.py::cleanup_search_logs` 清理。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.degrade import degrade_guard
from app.models.search_log import SearchLog

# 关键词清洗：去掉控制字符与首尾空白，按列宽截断。
# 不做归一化/分词 —— 运营要看到用户真实的输入形态（选标题时要照着用户的说法写）。
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

# 保留期：90 天。搜索日志只服务「近期选题」，留太久既占盘又没有额外价值
SEARCH_LOG_RETENTION_DAYS = 90
MAX_KEYWORD_LEN = 64


def normalize_keyword(keyword: str | None) -> str:
    """清洗搜索词；无有效内容返回空串（调用方据此跳过记录）"""
    if not keyword:
        return ""
    cleaned = _CONTROL_CHARS.sub("", str(keyword)).strip()
    return cleaned[:MAX_KEYWORD_LEN]


async def record_search(
    db: AsyncSession, module: str, keyword: str | None, result_count: int
) -> None:
    """记录一次搜索（**绝不抛异常**）。

    :param module: 内容模块键（tutorial/resource/node/plugin/tool）
    :param result_count: 本次命中条数；0 = 零结果（这是最有价值的一类）
    """
    cleaned = normalize_keyword(keyword)
    if not cleaned:
        return
    # SAVEPOINT：失败只回滚这一条 INSERT，不影响外层事务里正在进行的搜索查询。
    # 埋点失败绝不能影响搜索 —— 异常由 degrade_guard 记录后继续。
    with degrade_guard("search_log.record", detail="搜索词埋点写入失败（不影响搜索本身）"):
        async with db.begin_nested():
            db.add(
                SearchLog(
                    keyword=cleaned,
                    module=module[:20],
                    result_count=max(0, int(result_count or 0)),
                )
            )
            await db.flush()


def _utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def get_keyword_report(db: AsyncSession, days: int = 30, limit: int = 20) -> dict:
    """近 N 天的搜索词报告：热搜榜 / 零结果榜 / 模块分布。

    任一查询失败（含「表还没建」）→ 该项为空列表且 `available=False`，
    **不抛异常**：这是运营看的报表，不该因为没跑迁移就整页 500。
    """
    window = max(1, min(365, days))
    top_n = max(1, min(100, limit))
    since = _utc_naive_now() - timedelta(days=window)
    params = {"since": since, "limit": top_n}

    top_sql = """
        SELECT keyword, COUNT(*) AS n,
               SUM(CASE WHEN result_count = 0 THEN 1 ELSE 0 END) AS zero_n,
               ROUND(AVG(result_count), 1) AS avg_r
        FROM search_logs
        WHERE created_at >= :since
        GROUP BY keyword
        ORDER BY n DESC, keyword ASC
        LIMIT :limit
    """
    zero_sql = """
        SELECT keyword, COUNT(*) AS n, MAX(created_at) AS last_at
        FROM search_logs
        WHERE created_at >= :since AND result_count = 0
        GROUP BY keyword
        ORDER BY n DESC, keyword ASC
        LIMIT :limit
    """
    module_sql = """
        SELECT module, COUNT(*) AS n
        FROM search_logs
        WHERE created_at >= :since
        GROUP BY module
        ORDER BY n DESC
    """
    totals_sql = """
        SELECT COUNT(*) AS total,
               COUNT(DISTINCT keyword) AS unique_keywords,
               SUM(CASE WHEN result_count = 0 THEN 1 ELSE 0 END) AS zero_total
        FROM search_logs
        WHERE created_at >= :since
    """

    available = True

    async def _rows(sql: str) -> list:
        """失败返回空列表并把 available 置 False（表还没建 / DB 抖动都退化为「无数据」）"""
        nonlocal available
        rows = None
        with degrade_guard("search_log.report", detail="搜索词报表查询失败（可能是迁移未执行）"):
            rows = (await db.execute(text(sql), params)).all()
        if rows is None:
            available = False
            return []
        return rows

    top_rows = await _rows(top_sql)
    zero_rows = await _rows(zero_sql)
    module_rows = await _rows(module_sql)
    total_row = (await _rows(totals_sql) or [None])[0]

    total = int(total_row[0] or 0) if total_row else 0
    unique_keywords = int(total_row[1] or 0) if total_row else 0
    zero_total = int(total_row[2] or 0) if total_row else 0

    return {
        "days": window,
        "available": available,
        "total": total,
        "unique_keywords": unique_keywords,
        "zero_result_total": zero_total,
        # 零结果占比：内容覆盖度的反向指标，>30% 基本说明「站内找不到用户要的东西」
        "zero_result_rate": round(zero_total / total, 4) if total else None,
        "top": [
            {
                "keyword": row[0],
                "count": int(row[1] or 0),
                "zero_count": int(row[2] or 0),
                "avg_results": float(row[3]) if row[3] is not None else None,
            }
            for row in top_rows
        ],
        "zero_result": [
            {
                "keyword": row[0],
                "count": int(row[1] or 0),
                "last_at": row[2].isoformat() if row[2] else None,
            }
            for row in zero_rows
        ],
        "modules": [{"module": row[0], "count": int(row[1] or 0)} for row in module_rows],
    }


async def cleanup_old(db: AsyncSession, keep_days: int = 90) -> int:
    """删除超过保留期的搜索日志，返回删除条数（不 commit，由调用方提交）"""
    days = max(1, int(keep_days))
    cutoff = _utc_naive_now() - timedelta(days=days)
    deleted = 0
    # 清理失败不影响其它定时任务：保持 0 由 degrade_guard 记录
    with degrade_guard("search_log.cleanup", detail="搜索日志清理失败"):
        result = await db.execute(
            text("DELETE FROM search_logs WHERE created_at < :cutoff"), {"cutoff": cutoff}
        )
        deleted = int(result.rowcount or 0)
        if deleted:
            logger.info(f"搜索日志清理：删除 {deleted} 条（保留 {days} 天）")
    return deleted
