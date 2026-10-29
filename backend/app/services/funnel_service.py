"""转化漏斗与留存：回答「注册进来的人，走到哪一步流失了」。

## 为什么是「从既有表推导」而不是「新建埋点表」

商业模式是**站外变现**：注册 → 免费 3 小时 → 知识星球认证 → VIP。这条链路此前只有
`conversion_rate` 一个结果值，中间四段完全不可见，等于无法优化。

而这条链路的每一步**都已有事实表**：

| 阶段 | 事实来源 | 说明 |
|---|---|---|
| 注册 | `users.created_at` | 队列起点 |
| 有内容消费 | `downloads` / `learning_records` / `tool_usage_logs` | 三者任一即算「用过」 |
| 免费时长耗尽 | `COALESCE(free_started_at, created_at) + FREE_TIME_LIMIT` | 与 `timer_service` 同一口径 |
| 提交认证 | `auth_requests.created_at` | 变现动作的入口 |
| 认证通过 | `auth_requests.status = 'approved'` | 真正变成 VIP |

因此**不需要新建 events 表、不需要在前端埋点、不需要任何写入路径** —— 纯只读聚合。
代价是「无法回答『哪篇文章带来了转化』」（那需要内容级埋点，属下一阶段）；
收益是**零风险**：不会给用户请求增加任何写操作，也不会因为埋点缺失让漏斗失真。

## 口径约定（改之前先读）

- 队列 = **窗口内注册的用户**（`created_at` 落在窗口内），后续阶段只统计这批人；
  因此各阶段人数**单调递减**，转化率才有意义。不按队列统计的「全站事件数相除」是错的
  （分母混进了窗口外注册的人）。
- 时间一律 UTC naive（全库约定）：SQL 里用 `UTC_TIMESTAMP()` 与库里存的值同源。
- 「有内容消费」「留存活跃」都只算**真实使用行为**，不含浏览计数
  （`view_count` 是聚合值，没有 per-user 记录，混进来会算不准）。
- 单个阶段查询失败 → 该阶段 `count=None`（前端显示「—」），**不影响其它阶段**。
  编造一个 0 比显示「未知」危险得多：0 会被当成「没人走到这一步」。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.degrade import degrade_guard
from app.redis import log_redis_degraded, redis_client

# 结果缓存：漏斗是聚合查询，看板刷新频率不高，缓存 5 分钟足够
_CACHE_KEY_PREFIX = "ops:funnel:"
_CACHE_TTL_SECONDS = 300

# 漏斗阶段定义：顺序即展示顺序。`key` 稳定，前端按它渲染，不要随意改名。
FUNNEL_STAGES: tuple[tuple[str, str], ...] = (
    ("registered", "注册"),
    ("consumed", "有内容消费"),
    ("free_exhausted", "免费时长耗尽"),
    ("applied", "提交认证"),
    ("approved", "认证通过"),
)

# 「有内容消费」与「留存活跃」共用的行为子查询：三者任一即算活跃。
# 刻意用相关子查询而不是 UNION：数据量小（单机站），可读性优先，
# 且 EXISTS 命中即短路，比 UNION 去重更省。
_ACTIVITY_EXISTS = """
      (EXISTS (SELECT 1 FROM downloads x
                WHERE x.user_id = u.id AND {range_downloads})
       OR EXISTS (SELECT 1 FROM learning_records x
                WHERE x.user_id = u.id AND {range_learning})
       OR EXISTS (SELECT 1 FROM tool_usage_logs x
                WHERE x.user_id = u.id AND {range_tool}))
"""


def _utc_naive_now() -> datetime:
    """与库内存储同口径的当前时间（naive UTC）"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _rate(numerator: int | None, denominator: int | None) -> float | None:
    """转化率（0~1，保留 4 位）。分母为 0 或未知时返回 None，**不返回 0** ——
    「没有样本」和「转化率为零」是两件事。"""
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def build_stages(counts: list[int | None]) -> list[dict]:
    """把各阶段人数整理成带两级转化率的阶段列表。

    - `rate_from_start`：相对队列起点（注册）的累计转化率
    - `rate_from_prev`：相对上一阶段的阶段间转化率（**定位流失点的关键**）

    某阶段人数缺失（None）时，两个比率都置 None，且**后续阶段的 from_prev 也置 None**
    —— 中间断了一环，后面再算比率就是拿错的分母。
    """
    start = counts[0] if counts else None
    stages: list[dict] = []
    prev: int | None = start
    broken = False
    for index, (key, label) in enumerate(FUNNEL_STAGES):
        count = counts[index] if index < len(counts) else None
        if index > 0 and (count is None or prev is None):
            broken = True
        stages.append(
            {
                "key": key,
                "label": label,
                "count": count,
                "rate_from_start": _rate(count, start),
                "rate_from_prev": None if index == 0 or broken else _rate(count, prev),
            }
        )
        prev = count
    return stages


async def get_conversion_funnel(db: AsyncSession, days: int = 30) -> dict:
    """近 N 天注册用户的**嵌套**转化漏斗。

    ⚠️ 必须是嵌套口径（后一阶段只数「走完了前面所有阶段」的人），否则各阶段互相独立，
    会出现「免费耗尽 1 人 > 有内容消费 0 人」这种自相矛盾的数字，阶段间转化率直接失真。
    实现上把四个布尔标记算成派生列，再用乘法求和（MySQL 的 `SUM(a*b*c)`），
    **一条 SQL 出全部阶段**，不把用户行拉回 Python（用户量涨起来也不会拖内存）。
    """
    now = _utc_naive_now()
    since = now - timedelta(days=max(1, days))
    params = {
        "since": since,
        "until": now,
        "free_limit": max(0, settings.FREE_TIME_LIMIT),
    }

    activity = _ACTIVITY_EXISTS.format(
        range_downloads="x.created_at >= u.created_at",
        range_learning="x.last_study_at >= u.created_at",
        range_tool="x.created_at >= u.created_at",
    )

    # 派生表 t：每个注册用户四个 0/1 标记
    sql = f"""
        SELECT
            COUNT(*)                                            AS registered,
            COALESCE(SUM(t.consumed), 0)                        AS consumed,
            COALESCE(SUM(t.consumed * t.exhausted), 0)          AS free_exhausted,
            COALESCE(SUM(t.consumed * t.exhausted * t.applied), 0)  AS applied,
            COALESCE(SUM(t.consumed * t.exhausted * t.applied * t.approved), 0) AS approved
        FROM (
            SELECT
                {activity} AS consumed,
                (COALESCE(u.free_started_at, u.created_at)
                    <= UTC_TIMESTAMP() - INTERVAL :free_limit SECOND) AS exhausted,
                (EXISTS (SELECT 1 FROM auth_requests a WHERE a.user_id = u.id)) AS applied,
                (EXISTS (SELECT 1 FROM auth_requests a
                          WHERE a.user_id = u.id AND a.status = 'approved')) AS approved
            FROM users u
            WHERE u.created_at >= :since AND u.created_at < :until
        ) t
    """

    counts: list[int | None] = [None] * len(FUNNEL_STAGES)
    row = None
    # 整块聚合失败 → 全部阶段标「未知」，**绝不编 0**（0 会被读成「没人走到这一步」）
    with degrade_guard("funnel.conversion", detail="漏斗聚合失败，各阶段标「未知」"):
        row = (await db.execute(text(sql), params)).first()
    if row is not None:
        counts = [int(value or 0) for value in row]

    registered = counts[0]
    applied, approved = counts[3], counts[4]
    return {
        "days": max(1, days),
        "since": since.date().isoformat(),
        "stages": build_stages(counts),
        # 通过率单独给：运营最常问的一句就是「申请了的人有多少通过」
        "approval_rate": _rate(approved, applied),
        # 队列整体付费转化（注册→VIP）：商业模式的核心指标
        "paid_rate": _rate(approved, registered),
        "ts": int(time.time()),
    }


async def get_retention(db: AsyncSession, days: int = 14) -> dict:
    """按注册日分队列的 D1 / D7 留存（活跃 = 有下载/学习/工具行为）。

    ⚠️ 刻意**不叫「次日留存」**：我们没有 per-user 的浏览埋点，只能统计「有真实使用行为」，
    叫留存会让运营误以为等同于「登录/访问留存」。字段名用 `d1_active` 把口径写进数据结构里。
    """
    now = _utc_naive_now()
    window = max(1, days)
    since = (now - timedelta(days=window)).replace(hour=0, minute=0, second=0, microsecond=0)
    params = {"since": since}

    cohort_sql = """
        SELECT DATE(u.created_at) AS d, COUNT(*) AS n
        FROM users u
        WHERE u.created_at >= :since
        GROUP BY DATE(u.created_at)
        ORDER BY d
    """
    d1_sql = f"""
        SELECT DATE(u.created_at) AS d, COUNT(*) AS n
        FROM users u
        WHERE u.created_at >= :since
          AND {_ACTIVITY_EXISTS.format(
              range_downloads="x.created_at >= DATE(u.created_at) + INTERVAL 1 DAY AND x.created_at < DATE(u.created_at) + INTERVAL 2 DAY",
              range_learning="x.last_study_at >= DATE(u.created_at) + INTERVAL 1 DAY AND x.last_study_at < DATE(u.created_at) + INTERVAL 2 DAY",
              range_tool="x.created_at >= DATE(u.created_at) + INTERVAL 1 DAY AND x.created_at < DATE(u.created_at) + INTERVAL 2 DAY",
          )}
        GROUP BY DATE(u.created_at)
    """
    d7_sql = f"""
        SELECT DATE(u.created_at) AS d, COUNT(*) AS n
        FROM users u
        WHERE u.created_at >= :since
          AND {_ACTIVITY_EXISTS.format(
              range_downloads="x.created_at >= DATE(u.created_at) + INTERVAL 7 DAY AND x.created_at < DATE(u.created_at) + INTERVAL 8 DAY",
              range_learning="x.last_study_at >= DATE(u.created_at) + INTERVAL 7 DAY AND x.last_study_at < DATE(u.created_at) + INTERVAL 8 DAY",
              range_tool="x.created_at >= DATE(u.created_at) + INTERVAL 7 DAY AND x.created_at < DATE(u.created_at) + INTERVAL 8 DAY",
          )}
        GROUP BY DATE(u.created_at)
    """

    async def _rows(sql: str) -> dict[str, int] | None:
        """失败返回 None（调用方据此把该列显示成「—」），不抛异常"""
        rows = None
        with degrade_guard("funnel.retention", detail="留存队列查询失败，该列显示「—」"):
            result = await db.execute(text(sql), params)
            rows = {str(row[0]): int(row[1] or 0) for row in result.all()}
        return rows

    cohorts = await _rows(cohort_sql)
    d1 = await _rows(d1_sql)
    d7 = await _rows(d7_sql)

    rows: list[dict] = []
    for day, size in (cohorts or {}).items():
        rows.append(
            {
                "date": day,
                "cohort": size,
                # 队列里最后一天的人还没过完 D1/D7 窗口，比率天然偏低 —— 前端需提示
                "d1_active": None if d1 is None else d1.get(day, 0),
                "d7_active": None if d7 is None else d7.get(day, 0),
                "d1_rate": None if d1 is None else _rate(d1.get(day, 0), size),
                "d7_rate": None if d7 is None else _rate(d7.get(day, 0), size),
            }
        )
    return {"days": window, "rows": rows, "ts": int(time.time())}


async def get_funnel_overview(db: AsyncSession, days: int = 30) -> dict:
    """漏斗 + 留存一次取回（看板一个请求就够，避免前端串行两次请求）"""
    key = f"{_CACHE_KEY_PREFIX}{days}"
    try:
        cached = await redis_client.get(key)
        if cached:
            return json.loads(cached)
    except Exception as exc:  # noqa: BLE001 缓存不可用直接实时算
        log_redis_degraded("funnel.get_overview", exc)

    funnel = await get_conversion_funnel(db, days)
    retention = await get_retention(db, min(days, 14))
    payload = {"funnel": funnel, "retention": retention}

    try:
        await redis_client.set(key, json.dumps(payload, ensure_ascii=False), ex=_CACHE_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        log_redis_degraded("funnel.get_overview", exc)
    logger.debug(f"漏斗聚合完成：窗口 {days} 天")
    return payload
