"""数据质量只读巡检：只查不删，把「悄悄坏掉」的数据变成后台可见的一条提示。

为什么需要它：本项目有大量「快照 + 引用」结构（收藏存标题快照、下载存资源名快照、
学习记录存教程标题快照），目标被删后引用行会留下，且**不会有任何报错** ——
用户侧表现是「收藏列表点进去 404」，运营侧表现是「总量对不上」。

因此这里只做三件事：
1. **计数**：找出负积分、过期未降级的 VIP、指向已删目标的孤儿行；
2. **留痕**：结果写进 Redis（`ops:data_quality`），由后台「系统状态」卡展示；
3. **报警**：异常项写 WARNING 日志（Sentry 会以面包屑形式带上）。

⚠️ **绝不删除任何数据**。自动清理需要人工判断（比如「过期未降级」可能只是
调度任务刚跑完还没到下一个周期），误删的代价远高于多留一行脏数据。
真要清理时用 `scripts/cleanup_orphans.py`，那里有人工确认步骤。

⚠️ 单条 SQL 全部带 `LIMIT`/纯计数，避免在 2 核 4G 上把库拖住；
单项失败只影响它自己（返回 count=None），不中断整轮巡检。
"""

from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.degrade import degrade_guard

# 收藏的多态目标 → 真实表名。**只列确定存在的**：
# `workflow` 是历史遗留取值（没有对应表），未知类型一律跳过而不是当成孤儿，
# 否则每轮巡检都会报一批假阳性。
_FAVORITE_TARGET_TABLES: dict[str, str] = {
    "tutorial": "tutorials",
    "resource": "resources",
    "plugin": "plugins",
    "node": "nodes",
}

# 各检查项的说明与严重级别：前端直接展示这些文案，改文案只改这一处
_ISSUE_TEXT: dict[str, tuple[str, str]] = {
    "negative_points": ("error", "存在积分为负的用户（余额与流水不一致）"),
    "vip_expired_stale": ("warn", "VIP 已过期超过 24 小时仍未降级（降级任务可能未执行）"),
    "orphan_favorites": ("warn", "收藏指向已删除的内容（用户点开是 404）"),
    "orphan_downloads": ("warn", "下载记录指向已删除的资源"),
    "orphan_learnings": ("warn", "学习记录指向已删除的教程"),
}


async def _count(db: AsyncSession, sql: str) -> int | None:
    """执行一条计数 SQL；失败返回 None（单项失败不得中断整轮巡检）"""
    count = None
    # 巡检本身不能成为新的故障源：失败保持 None（前端显示「—」），由 degrade_guard 记录
    with degrade_guard("data_quality.count", detail=f"SQL={sql[:80]}"):
        count = int((await db.execute(text(sql))).scalar() or 0)
    return count


async def collect_issues(db: AsyncSession) -> dict:
    """跑一轮巡检，返回 ``{"ts", "checks", "issues", "issue_total"}``。

    全程只读，可在任意时刻安全调用（含生产）。
    """
    checks: dict[str, dict] = {}

    # 1) 负积分：一定异常（发放/扣减算错，或有人手工改库）
    checks["negative_points"] = {
        "count": await _count(db, "SELECT COUNT(*) FROM users WHERE points < 0")
    }

    # 2) VIP 过期未降级：**多留 24 小时宽限**，否则每天 00:00~02:00 之间
    #    必然报一批「正常待降级」的账号，把真问题淹没掉
    checks["vip_expired_stale"] = {
        "count": await _count(
            db,
            "SELECT COUNT(*) FROM users "
            "WHERE role = 'vip' AND deleted_at IS NULL "
            "AND (vip_expire_time IS NULL "
            "     OR vip_expire_time < (UTC_TIMESTAMP() - INTERVAL 1 DAY))",
        )
    }

    # 3) 孤儿收藏：按目标类型分别查（NOT EXISTS 走主键索引，比 LEFT JOIN 去重更省）
    for target_type, table in _FAVORITE_TARGET_TABLES.items():
        checks[f"orphan_favorites:{target_type}"] = {
            "count": await _count(
                db,
                "SELECT COUNT(*) FROM favorites f "
                f"WHERE f.target_type = '{target_type}' "
                f"AND NOT EXISTS (SELECT 1 FROM {table} t WHERE t.id = f.target_id)",
            )
        }

    # 4) 孤儿下载记录 / 学习记录
    checks["orphan_downloads"] = {
        "count": await _count(
            db,
            "SELECT COUNT(*) FROM downloads d "
            "WHERE NOT EXISTS (SELECT 1 FROM resources r WHERE r.id = d.resource_id)",
        )
    }
    checks["orphan_learnings"] = {
        "count": await _count(
            db,
            "SELECT COUNT(*) FROM learning_records l "
            "WHERE NOT EXISTS (SELECT 1 FROM tutorials t WHERE t.id = l.tutorial_id)",
        )
    }

    issues: list[dict] = []
    issue_total = 0
    for code, item in checks.items():
        count = item.get("count")
        if not count:  # None（查询失败）与 0（正常）都不算问题
            continue
        # 子项形如 orphan_favorites:tutorial → 汇总到 orphan_favorites 的文案
        base = code.split(":", 1)[0]
        level, label = _ISSUE_TEXT.get(base, ("warn", base))
        suffix = f"（{code.split(':', 1)[1]}）" if ":" in code else ""
        issues.append({"code": code, "level": level, "count": count, "text": f"{label}{suffix}"})
        issue_total += count

    return {
        "ts": int(time.time()),
        "checks": checks,
        "issues": issues,
        "issue_total": issue_total,
    }
