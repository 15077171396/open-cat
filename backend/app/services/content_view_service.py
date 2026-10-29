"""内容级浏览归因：写入 + 报表。

## 回答什么问题

漏斗（`/admin/funnel`）回答「哪一段在流失」；本模块回答**「哪篇内容带来了转化」**——
即「看过它的登录用户里，有多少人后来申请了认证」。

## 为什么不能复用 `view_count`

`tutorials.view_count` 是**聚合计数**，没有用户维度，无法与「谁申请了认证」关联。
本表按 `(user_id, target_type, target_id, view_date)` 唯一，**同一人同一天看同一篇只记一条**：
既控制体量，也让「反复刷同一页」不会放大归因权重。

## 三条硬约束

1. **写入绝不影响详情页**：`INSERT IGNORE` + SAVEPOINT + `degrade_guard`，
   埋点失败只留一行日志，页面照常返回。
2. **只记登录用户**：游客没有稳定标识（刻意不引入指纹），记了也无法归因。
   用户 id 从 `request_context` 的 contextvar 取，**不透传到 service 签名**里。
3. **表可能还不存在**（老库没跑迁移）：报表返回 `available=False`，页面提示「先跑迁移」。

保留期 180 天（比搜索词长：归因要看「申请认证前看过什么」，而认证决策周期通常以周计）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.degrade import degrade_guard
from app.core.request_context import get_current_user_id
from app.database import generate_id
from app.models.content_view import ContentView  # noqa: F401  确保模型被注册到 Base.metadata

# 保留期（天）
CONTENT_VIEW_RETENTION_DAYS = 180

# 归因窗口：看内容 → 提交认证，允许的最大间隔。
# 取 7 天：更短会漏掉「考虑几天才付费」的人，更长会把「随便看过」也算成贡献。
ATTRIBUTION_WINDOW_DAYS = 7

# 内容类型 → (表名, 标题列)。与 `core/content_types` 的模型保持一致；
# 这里用裸 SQL 是为了让「一次查多张表」不用为每种类型写一份 ORM 查询。
_TITLE_SOURCES: dict[str, tuple[str, str]] = {
    "tutorial": ("tutorials", "title"),
    "resource": ("resources", "name"),
    "node": ("nodes", "name"),
    "plugin": ("plugins", "name"),
}

_INSERT_SQL = text(
    "INSERT IGNORE INTO content_views "
    "(id, user_id, target_type, target_id, view_date, created_at) "
    "VALUES (:id, :user_id, :target_type, :target_id, :view_date, UTC_TIMESTAMP())"
)


def _utc_today() -> date:
    """UTC 日期（全库 naive-UTC 约定：用本地日期会让「今天的浏览」在凌晨错位一天）"""
    return datetime.now(timezone.utc).date()


async def record_content_view(
    db: AsyncSession, module: str, row_id: int, user_id: int | None = None
) -> None:
    """记一条用户级浏览（按天去重，**绝不抛异常**）。

    :param user_id: 不传则从请求上下文取；仍为空（游客）则直接跳过
    """
    uid = user_id if user_id is not None else get_current_user_id()
    if not uid or not module:
        return
    with degrade_guard("content_view.record", detail="归因埋点写入失败（不影响详情页）"):
        # SAVEPOINT：唯一键冲突 / 表缺失都只回滚这一条，不影响详情页的事务
        async with db.begin_nested():
            await db.execute(
                _INSERT_SQL,
                {
                    "id": generate_id(),
                    "user_id": uid,
                    "target_type": module[:20],
                    "target_id": row_id,
                    "view_date": _utc_today(),
                },
            )


async def _titles(db: AsyncSession, rows: list[tuple[str, int]]) -> dict[tuple[str, int], str]:
    """批量取标题（按类型分组各查一次，避免 N+1）"""
    result: dict[tuple[str, int], str] = {}
    by_type: dict[str, list[int]] = {}
    for module, target_id in rows:
        by_type.setdefault(module, []).append(target_id)
    for module, ids in by_type.items():
        source = _TITLE_SOURCES.get(module)
        if not source:
            continue
        table, column = source
        with degrade_guard("content_view.titles", detail=f"取 {module} 标题失败，标题留空"):
            id_list = ", ".join(str(int(i)) for i in ids[:200])
            for row in (
                await db.execute(text(f"SELECT id, {column} FROM {table} WHERE id IN ({id_list})"))
            ).all():
                result[(module, int(row[0]))] = str(row[1] or "")
    return result


async def get_attribution_report(db: AsyncSession, days: int = 30, limit: int = 10) -> dict:
    """近 N 天的内容归因榜：看过 → 7 天内提交认证的人数与转化率。"""
    window = max(1, min(180, days))
    top_n = max(1, min(50, limit))
    since = _utc_today() - timedelta(days=window)
    params = {
        "since": since,
        "limit": top_n,
        "attr_days": ATTRIBUTION_WINDOW_DAYS,
    }

    # 核心聚合：按内容分组，统计「看过的人」与「其中 N 天内申请了认证的人」。
    # 用 EXISTS 相关子查询而不是 JOIN + DISTINCT：一个人可能有多条认证申请记录，
    # JOIN 会把 viewers 放大（去重后能修，但 COUNT(DISTINCT CASE ...) 更好读也更快）。
    sql = text(
        """
        SELECT cv.target_type, cv.target_id,
               COUNT(DISTINCT cv.user_id) AS viewers,
               COUNT(DISTINCT CASE WHEN EXISTS (
                   SELECT 1 FROM auth_requests a
                   WHERE a.user_id = cv.user_id
                     AND a.created_at >= cv.created_at
                     AND a.created_at <= cv.created_at + INTERVAL :attr_days DAY
               ) THEN cv.user_id END) AS converted
        FROM content_views cv
        WHERE cv.view_date >= :since
        GROUP BY cv.target_type, cv.target_id
        ORDER BY converted DESC, viewers DESC, cv.target_id DESC
        LIMIT :limit
        """
    )
    totals_sql = text(
        """
        SELECT COUNT(*) AS views, COUNT(DISTINCT user_id) AS viewers
        FROM content_views WHERE view_date >= :since
        """
    )

    available = True
    rows: list = []
    totals = None
    with degrade_guard("content_view.report", detail="归因报表查询失败（可能是迁移未执行）"):
        rows = (await db.execute(sql, params)).all()
        totals = (await db.execute(totals_sql, {"since": since})).first()
    if rows == [] and totals is None:
        # 两条都失败 → 表大概率不存在：明确告诉前端「还没采集」，而不是显示空榜
        available = False

    titles = await _titles(db, [(str(r[0]), int(r[1])) for r in rows]) if rows else {}

    items = []
    for row in rows:
        module, target_id = str(row[0]), int(row[1])
        viewers = int(row[2] or 0)
        converted = int(row[3] or 0)
        items.append(
            {
                "target_type": module,
                "target_id": target_id,
                "title": titles.get((module, target_id), ""),
                "viewers": viewers,
                "converted": converted,
                "rate": round(converted / viewers, 4) if viewers else None,
            }
        )

    return {
        "days": window,
        "available": available,
        "attribution_window_days": ATTRIBUTION_WINDOW_DAYS,
        "total_views": int(totals[0] or 0) if totals else 0,
        "total_viewers": int(totals[1] or 0) if totals else 0,
        "items": items,
        "ts": int(datetime.now(timezone.utc).timestamp()),
    }


async def cleanup_old(db: AsyncSession, keep_days: int = CONTENT_VIEW_RETENTION_DAYS) -> int:
    """删除超过保留期的浏览记录，返回删除条数（不 commit）"""
    days = max(1, int(keep_days))
    cutoff = _utc_today() - timedelta(days=days)
    deleted = 0
    with degrade_guard("content_view.cleanup", detail="归因记录清理失败（不影响其它任务）"):
        result = await db.execute(
            text("DELETE FROM content_views WHERE view_date < :cutoff"), {"cutoff": cutoff}
        )
        deleted = int(result.rowcount or 0)
        if deleted:
            logger.info(f"内容浏览归因清理：删除 {deleted} 条（保留 {days} 天）")
    return deleted
