"""站内搜索词日志的契约测试。

钉住五条**破了不报错、只会悄悄失去意义或把主功能打挂**的契约：

1. **写日志绝不能影响搜索**：失败必须被 SAVEPOINT 隔离，不能把外层事务标记成不可用
   （否则埋点一失败，同一请求里后续的 SELECT 全部报错 —— 埋点打挂主功能）。
2. **只记公开端**：管理端检索是运营自己在找东西，混进来会污染「用户想要什么」。
3. **空词不记**：纯空格/控制字符不产生垃圾行（否则热搜榜会被无意义行占据）。
4. **表不存在也不能 500**：老库没跑迁移时返回 `available=False` 的空报告，
   页面显示「尚未采集」，而不是整页打不开。
5. **不存用户标识**：搜索词含隐私意图，本表只做热度/零结果/模块分布。
"""

import re
import unittest
from pathlib import Path

from app.models.search_log import SearchLog
from app.services import search_log_service as sls

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
    """按 SQL 片段返回预设行的假会话；`fail_on` 命中时抛错"""

    def __init__(self, rows_by_marker: dict[str, list] | None = None, fail: bool = False):
        self.rows = rows_by_marker or {}
        self.fail = fail
        self.added: list = []
        self.sqls: list[str] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sqls.append(sql)
        if self.fail:
            raise RuntimeError("table search_logs doesn't exist")
        for marker, rows in self.rows.items():
            if marker in sql:
                return _Result(rows)
        return _Result([])

    def add(self, obj):
        self.added.append(obj)

    def begin_nested(self):
        session = self

        class _Ctx:
            async def __aenter__(self):
                if session.fail:
                    raise RuntimeError("insert failed")
                return session

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _Ctx()

    async def flush(self):
        if self.fail:
            raise RuntimeError("insert failed")


class NormalizeTests(unittest.TestCase):
    def test_strips_control_chars_and_spaces(self):
        self.assertEqual(sls.normalize_keyword("  comfyui 插件 \n"), "comfyui 插件")

    def test_truncates_to_column_width(self):
        self.assertEqual(len(sls.normalize_keyword("a" * 200)), sls.MAX_KEYWORD_LEN)

    def test_empty_input_returns_empty(self):
        for value in (None, "", "   ", "\x00\x01"):
            self.assertEqual(sls.normalize_keyword(value), "")


class RecordTests(unittest.IsolatedAsyncioTestCase):
    async def test_skips_blank_keyword(self):
        db = _StubSession()
        await sls.record_search(db, "tutorial", "   ", 0)
        self.assertEqual(db.added, [], "空词不应产生日志行")

    async def test_records_row(self):
        db = _StubSession()
        await sls.record_search(db, "tutorial", "comfyui", 3)
        self.assertEqual(len(db.added), 1)
        row = db.added[0]
        self.assertEqual((row.keyword, row.module, row.result_count), ("comfyui", "tutorial", 3))

    async def test_never_raises_when_insert_fails(self):
        """核心契约：埋点失败必须被吞掉（SAVEPOINT 隔离），否则会打挂搜索"""
        db = _StubSession(fail=True)
        await sls.record_search(db, "tutorial", "comfyui", 3)  # 不抛即通过


class ReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_table_returns_unavailable_instead_of_raising(self):
        report = await sls.get_keyword_report(_StubSession(fail=True), days=7, limit=10)
        self.assertFalse(report["available"])
        self.assertEqual(report["total"], 0)
        self.assertEqual(report["top"], [])
        self.assertIsNone(report["zero_result_rate"])

    async def test_report_shape(self):
        # 标记必须足够独特：早期版本用 "result_count = 0" 当标记，
        # 结果连「总计」那条 SQL 也被匹配上（它同样含这个条件），断言全错位
        db = _StubSession(
            {
                "ROUND(AVG(result_count)": [("comfyui", 5, 2, 1.5)],
                "MAX(created_at) AS last_at": [("flux 教程", 2, None)],
                "GROUP BY module": [("tutorial", 7)],
                "COUNT(DISTINCT keyword) AS unique_keywords": [(7, 3, 4)],
            }
        )
        report = await sls.get_keyword_report(db, days=30, limit=20)
        self.assertTrue(report["available"])
        self.assertEqual(report["total"], 7)
        self.assertEqual(report["unique_keywords"], 3)
        self.assertEqual(report["zero_result_total"], 4)
        self.assertAlmostEqual(report["zero_result_rate"], 0.5714)
        self.assertEqual(report["top"][0]["keyword"], "comfyui")
        self.assertEqual(report["zero_result"][0]["keyword"], "flux 教程")
        self.assertEqual(report["modules"], [{"module": "tutorial", "count": 7}])

    async def test_zero_rate_is_none_when_no_searches(self):
        db = _StubSession({"COUNT(DISTINCT keyword) AS unique_keywords": [(0, 0, 0)]})
        report = await sls.get_keyword_report(db, days=7)
        self.assertIsNone(report["zero_result_rate"])


class PrivacyAndScopeTests(unittest.TestCase):
    def test_no_user_identifier_column(self):
        """搜索词含隐私意图：本表刻意不存用户标识"""
        columns = {c.name for c in SearchLog.__table__.columns}
        self.assertEqual(columns, {"id", "keyword", "module", "result_count", "created_at"})

    def test_admin_call_sites_do_not_log(self):
        """管理端检索必须显式关闭记录，否则热搜榜会被运营自己的搜索污染"""
        admin_files = sorted(APP.glob("services/admin_*_service.py"))
        self.assertTrue(admin_files)
        for path in admin_files:
            source = path.read_text(encoding="utf-8")
            if "apply_keyword_filter(" not in source:
                continue
            self.assertIn(
                "log_search=False",
                source,
                f"{path.name} 调用 apply_keyword_filter 时没关掉搜索日志",
            )

    def test_public_call_sites_keep_logging(self):
        """公开端不能误加 log_search=False —— 那样报表永远是空的"""
        for name in ("tutorial_service.py", "resource_service.py", "node_service.py",
                     "plugin_service.py", "tool_service.py"):
            source = (APP / "services" / name).read_text(encoding="utf-8")
            self.assertNotIn("log_search=False", source, f"{name} 关掉了公开端搜索日志")

    def test_module_mapping_comes_from_registry(self):
        """模块键必须由内容类型注册表派生，不能手写第二份清单"""
        source = (APP / "services" / "search.py").read_text(encoding="utf-8")
        self.assertIn("CONTENT_TYPES", source)
        from app.services.search import module_for_model
        from app.models.tutorial import Tutorial

        self.assertEqual(module_for_model(Tutorial), "tutorial")
        self.assertEqual(module_for_model(object), "")


class RouteAndJobTests(unittest.TestCase):
    def test_route_registered_and_reuses_dashboard_permission(self):
        from app.core.rbac import match_route_permission
        from app.main import app

        self.assertIn("/api/v1/admin/search-keywords", app.openapi()["paths"])
        self.assertEqual(
            match_route_permission("GET", "/api/v1/admin/search-keywords"), ["dashboard:view"]
        )

    def test_cleanup_job_registered(self):
        """日志必须有人清理，否则这张表会无界增长（单机磁盘最先满）。

        注册点在 `JOB_FUNCS`（配置驱动的调度器只认这张表），保留期常量在 handler 里用到。
        """
        from scheduler import tasks as scheduler_tasks

        self.assertIn("cleanup_search_logs", scheduler_tasks.JOB_FUNCS)
        tasks = (REPO_ROOT / "backend" / "scheduler" / "tasks.py").read_text(encoding="utf-8")
        self.assertIn("SEARCH_LOG_RETENTION_DAYS", tasks)
        self.assertGreaterEqual(sls.SEARCH_LOG_RETENTION_DAYS, 30)

    def test_migration_matches_model(self):
        """迁移与模型必须一致（列名/表名），否则线上 INSERT 直接报 Unknown column"""
        migration = (
            REPO_ROOT / "backend" / "alembic" / "versions" / "20261027_0051_add_search_logs.py"
        ).read_text(encoding="utf-8")
        self.assertIn("'search_logs'", migration)
        for column in ("keyword", "module", "result_count", "created_at"):
            self.assertIn(f"'{column}'", migration)
        # 只看建表语句，不看注释：注释里明确写了「不存 user_id」这条设计说明
        self.assertNotIn("sa.Column('user_id'", migration)
        # 表名与模型一致
        self.assertEqual(SearchLog.__tablename__, "search_logs")
        self.assertTrue(re.search(r"ix_search_logs_created_at", migration))


if __name__ == "__main__":
    unittest.main()
