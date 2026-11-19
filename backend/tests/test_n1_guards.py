"""N+1 守卫：列表服务单次调用的 SQL 语句数上限（离线跑，基于 MemorySession）。

背景：2026-10-29 对标文档把「列表接口 N+1 = 0」定为性能底线。真实库上的表现是
慢查询与连接池压力；这里用 fake_db 的语句流水（MemorySession.statements）做离线
守卫 —— 一次列表调用（count + 分页 + 关联回填）的语句数必须 ≤ 上限，且**不随
行数增长**（行数变化时语句数恒定，才是真正没有 N+1）。

超限的常见根因与修法：
- 循环内逐行 SELECT 回填关联 → selectinload / 一次 IN 批量取回 + 内存组装；
- 逐行查总数 → 合并为一次聚合查询。

覆盖范围：只守「走 ORM select 的服务」。裸 SQL（text()）服务 fake_db 不支持，
且其语句形态固定（count + page 各一条），N+1 风险低，不在此列。
新模块接入守卫：照抄一个用例，跑红 → 优化 → 收紧上限（棘轮只紧不松）。
"""

import unittest
from datetime import datetime

from app.models.admin_audit_log import AdminAuditLog
from app.models.comment import COMMENT_TARGET_TUTORIAL, ContentComment
from app.models.tutorial import Tutorial
from app.models.user import User
from app.services import admin_audit_service, comment_service
from tests.fake_db import MemorySession

# 棘轮上限：改服务后如果数字变小，请把这里收紧；变大则先修代码再谈。
# 实测（2026-11-20）：评论列表 = count + 分页 + 作者/教程批量回填 = 4；审计列表 = count + 分页 = 2。
COMMENTS_BUDGET = 4
AUDIT_BUDGET = 2


def _assert_budget(testcase, db: MemorySession, budget: int, what: str) -> None:
    used = len(db.statements)
    testcase.assertLessEqual(
        used,
        budget,
        f"{what} 单次调用发出 {used} 条 SQL（上限 {budget}）—— 超限大概率是 N+1。"
        f"语句流水：{db.statements}",
    )


def _seed_comments() -> MemorySession:
    """与 test_admin_comments_filter 同款种子：5 条评论挂在同一篇教程上。"""
    db = MemorySession()
    db.seed(Tutorial(id=100, title="守卫用教程", is_published=True))
    db.seed(User(id=1, account="reader@example.com", password_hash="x", role="user"))
    rows = [
        (1, "10-10 的评论", datetime(2026, 10, 10, 10, 0, 0), None),
        (2, "10-12 的评论", datetime(2026, 10, 12, 8, 0, 0), None),
        (3, "10-14 的评论", datetime(2026, 10, 14, 10, 0, 0), None),
        (4, "已删除的评论", datetime(2026, 10, 12, 9, 0, 0), datetime(2026, 10, 12, 12, 0, 0)),
    ]
    for cid, content, created_at, deleted_at in rows:
        db.seed(
            ContentComment(
                id=cid,
                target_type=COMMENT_TARGET_TUTORIAL,
                target_id=100,
                user_id=1,
                content=content,
                created_at=created_at,
                deleted_at=deleted_at,
            )
        )
    return db


def _seed_audit_logs(count: int = 30) -> MemorySession:
    """审计日志：行数给到超过一页（默认 page_size=20），逼出分页路径。"""
    db = MemorySession()
    for i in range(1, count + 1):
        db.seed(
            AdminAuditLog(
                id=i,
                admin_id=1,
                admin_account=f"admin{i % 3}@example.com",
                action="role_update",
                target_type="role",
                target_id=i,
                summary=f"第 {i} 条审计",
                created_at=datetime(2026, 11, 1, 0, i % 60, 0),
            )
        )
    return db


class N1GuardTests(unittest.IsolatedAsyncioTestCase):
    """每个用例 = 一个列表服务 + 一组「行数不同、筛选不同」的调用，断言语句数恒定且不超限。"""

    async def test_admin_comments_list_budget(self):
        """评论管理列表：三种筛选路径（默认 / 时间区间 / 含已删）语句数一致且 ≤ 上限。"""
        variants = [
            {},
            {"start_at": datetime(2026, 10, 12, 0, 0, 0), "end_at": datetime(2026, 10, 12, 23, 59, 59)},
            {"include_deleted": True, "page": 2, "page_size": 2},
        ]
        seen: list[int] = []
        for kwargs in variants:
            db = _seed_comments()
            data = await comment_service.admin_list_comments(db, **kwargs)
            self.assertGreater(len(data["items"]), 0, f"种子没命中筛选路径：{kwargs}")
            _assert_budget(self, db, COMMENTS_BUDGET, f"admin_list_comments({kwargs})")
            seen.append(len(db.statements))
        self.assertEqual(len(set(seen)), 1, f"语句数应与筛选条件无关（N+1 特征：随条件漂移）：{seen}")

    async def test_audit_logs_list_budget(self):
        """审计日志列表：翻两页 + 关键字/风险筛选，语句数一致且 ≤ 上限。"""
        variants = [
            {},
            {"page": 2, "page_size": 10},
            {"keyword": "admin1", "risk": "danger"},
        ]
        seen: list[int] = []
        for kwargs in variants:
            db = _seed_audit_logs()
            data = await admin_audit_service.list_audit_logs(db, **kwargs)
            self.assertGreater(len(data["items"]), 0, f"种子没命中筛选路径：{kwargs}")
            _assert_budget(self, db, AUDIT_BUDGET, f"list_audit_logs({kwargs})")
            seen.append(len(db.statements))
        self.assertEqual(len(set(seen)), 1, f"语句数应与筛选条件无关（N+1 特征）：{seen}")

    async def test_comments_budget_scales_with_rows_not_growth(self):
        """行数翻倍语句数不涨 —— 这是 N+1 与「固定成本」的分界线。"""
        db_small = _seed_comments()
        await comment_service.admin_list_comments(db_small)
        small = len(db_small.statements)

        db_big = MemorySession()
        db_big.seed(Tutorial(id=200, title="大种子教程", is_published=True))
        db_big.seed(User(id=2, account="reader2@example.com", password_hash="x", role="user"))
        for i in range(1, 41):
            db_big.seed(
                ContentComment(
                    id=i,
                    target_type=COMMENT_TARGET_TUTORIAL,
                    target_id=200,
                    user_id=2,
                    content=f"第 {i} 条",
                    created_at=datetime(2026, 11, 1, 0, 0, 0),
                )
            )
        await comment_service.admin_list_comments(db_big)
        big = len(db_big.statements)

        self.assertEqual(
            small, big,
            f"行数从 4 → 40，语句数 {small} → {big}：每行都在发 SQL，这就是 N+1。流水：{db_big.statements}",
        )


if __name__ == "__main__":
    unittest.main()
