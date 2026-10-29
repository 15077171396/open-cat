"""内容级浏览归因的契约测试。

钉住五条**破了不报错、只会让归因失真或打挂详情页**的契约：

1. **写入绝不影响详情页**：`INSERT IGNORE` + SAVEPOINT + `degrade_guard`，失败只留日志。
2. **只记登录用户**：游客没有稳定标识，记了也无法归因。
3. **按天去重**：同一人同一天看同一篇只记一条（唯一键兜底，重复写入被 IGNORE）。
4. **归因窗口有上限**：「看内容 → 提交认证」必须在 7 天内，否则「随便看过」也会算贡献。
5. **表不存在不能 500**：报表返回 `available=False`，页面提示「先跑迁移」。
"""

import unittest
from pathlib import Path

from app.core import request_context
from app.models.content_view import ContentView
from app.services import content_view_service as cvs

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "backend" / "app"


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _StubSession:
    """按 SQL 片段返回预设行；`fail` 为 True 时全部抛错（模拟表不存在）"""

    def __init__(self, rows_by_marker: dict | None = None, fail: bool = False):
        self.rows = rows_by_marker or {}
        self.fail = fail
        self.executed: list[tuple[str, dict]] = []
        self.nested = 0

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append((sql, params or {}))
        if self.fail:
            raise RuntimeError("Table 'content_views' doesn't exist")
        for marker, rows in self.rows.items():
            if marker in sql:
                return _Result(rows)
        return _Result([])

    def begin_nested(self):
        session = self

        class _Ctx:
            async def __aenter__(self):
                session.nested += 1
                return session

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _Ctx()


class ContextVarTests(unittest.TestCase):
    def test_default_is_none(self):
        self.assertIsNone(request_context.get_current_user_id())

    def test_set_and_read(self):
        request_context.set_current_user_id(42)
        try:
            self.assertEqual(request_context.get_current_user_id(), 42)
        finally:
            request_context.set_current_user_id(None)

    def test_auth_dependencies_populate_context(self):
        """鉴权依赖必须写入上下文，否则归因永远是空的（而不会有任何报错）"""
        source = (APP / "dependencies.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("set_current_user_id("), 2, "两个鉴权依赖都要写入")


class RecordTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        # IsolatedAsyncioTestCase 的异步清理钩子叫 asyncTearDown；
        # 写成 `async def tearDown` 不会被 await，且会产生 unraisable 警告
        request_context.set_current_user_id(None)

    async def test_skips_anonymous(self):
        db = _StubSession()
        await cvs.record_content_view(db, "tutorial", 1)
        self.assertEqual(db.executed, [], "游客不应产生归因记录")

    async def test_skips_empty_module(self):
        db = _StubSession()
        await cvs.record_content_view(db, "", 1, user_id=7)
        self.assertEqual(db.executed, [])

    async def test_writes_with_insert_ignore(self):
        db = _StubSession()
        await cvs.record_content_view(db, "tutorial", 9, user_id=7)
        self.assertEqual(len(db.executed), 1)
        sql, params = db.executed[0]
        self.assertIn("INSERT IGNORE", sql, "必须用 INSERT IGNORE，否则同日重复浏览会报唯一键冲突")
        self.assertEqual(params["user_id"], 7)
        self.assertEqual(params["target_type"], "tutorial")
        self.assertEqual(params["target_id"], 9)
        self.assertIn("view_date", params)
        self.assertEqual(db.nested, 1, "写入必须包在 SAVEPOINT 里，失败不能污染详情页事务")

    async def test_uses_context_user_when_not_passed(self):
        db = _StubSession()
        request_context.set_current_user_id(11)
        await cvs.record_content_view(db, "node", 3)
        self.assertEqual(db.executed[0][1]["user_id"], 11)

    async def test_never_raises_when_table_missing(self):
        db = _StubSession(fail=True)
        await cvs.record_content_view(db, "tutorial", 1, user_id=7)  # 不抛即通过

    async def test_view_counter_records_attribution(self):
        """详情页的浏览计数必须顺带落归因（否则整张表永远是空的）"""
        source = (APP / "services" / "view_counter.py").read_text(encoding="utf-8")
        self.assertIn("record_content_view", source)


class ReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_table_returns_unavailable(self):
        report = await cvs.get_attribution_report(_StubSession(fail=True), days=30, limit=10)
        self.assertFalse(report["available"])
        self.assertEqual(report["items"], [])
        self.assertEqual(report["total_views"], 0)

    async def test_report_shape_and_rate(self):
        db = _StubSession(
            {
                "COUNT(DISTINCT CASE": [("tutorial", 4, 10, 3)],
                "SELECT id, title FROM tutorials": [(4, "AI 视频生成完全指南")],
                "COUNT(DISTINCT user_id) AS viewers": [(25, 8)],
            }
        )
        report = await cvs.get_attribution_report(db, days=30, limit=10)
        self.assertTrue(report["available"])
        self.assertEqual(report["total_views"], 25)
        self.assertEqual(report["total_viewers"], 8)
        item = report["items"][0]
        self.assertEqual(item["title"], "AI 视频生成完全指南")
        self.assertEqual(item["viewers"], 10)
        self.assertEqual(item["converted"], 3)
        self.assertEqual(item["rate"], 0.3)

    async def test_attribution_window_is_bounded(self):
        """窗口必须有上限：无限窗口会把「随便看过」也算成贡献"""
        self.assertGreaterEqual(cvs.ATTRIBUTION_WINDOW_DAYS, 1)
        self.assertLessEqual(cvs.ATTRIBUTION_WINDOW_DAYS, 30)

    async def test_zero_viewers_gives_null_rate(self):
        db = _StubSession(
            {
                "COUNT(DISTINCT CASE": [("tutorial", 1, 0, 0)],
                "SELECT id, title FROM tutorials": [],
                "COUNT(DISTINCT user_id) AS viewers": [(0, 0)],
            }
        )
        report = await cvs.get_attribution_report(db, days=7, limit=5)
        self.assertIsNone(report["items"][0]["rate"])


class SchemaAndRouteTests(unittest.TestCase):
    def test_model_columns_and_unique_key(self):
        columns = {c.name for c in ContentView.__table__.columns}
        self.assertEqual(columns, {"id", "user_id", "target_type", "target_id", "view_date", "created_at"})
        unique = [
            c for c in ContentView.__table__.constraints
            if type(c).__name__ == "UniqueConstraint"
        ]
        self.assertTrue(unique, "必须有唯一键做按天去重")
        names = {col.name for col in unique[0].columns}
        self.assertEqual(names, {"user_id", "target_type", "target_id", "view_date"})

    def test_migration_matches_model(self):
        migration = (
            REPO_ROOT / "backend" / "alembic" / "versions" / "20261027_0054_add_content_views.py"
        ).read_text(encoding="utf-8")
        self.assertIn("'content_views'", migration)
        for column in ("user_id", "target_type", "target_id", "view_date"):
            self.assertIn(f"'{column}'", migration)
        self.assertIn("uq_content_view_daily", migration)

    def test_route_registered_and_reuses_dashboard_permission(self):
        from app.core.rbac import match_route_permission
        from app.main import app

        self.assertIn("/api/v1/admin/content-attribution", app.openapi()["paths"])
        self.assertEqual(
            match_route_permission("GET", "/api/v1/admin/content-attribution"), ["dashboard:view"]
        )

    def test_cleanup_job_registered(self):
        """注册在 JOB_FUNCS（配置驱动的调度器只认这张表，写 add_job 字面量已经不算注册）"""
        from scheduler import tasks as scheduler_tasks

        self.assertIn("cleanup_content_views", scheduler_tasks.JOB_FUNCS)
        self.assertGreaterEqual(cvs.CONTENT_VIEW_RETENTION_DAYS, 30)


if __name__ == "__main__":
    unittest.main()
