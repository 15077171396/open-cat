"""「继续学习」查询语义的守卫（2026-10-20）。

背景：`/users/learning/continue` 原来只查 `status == "in_progress"`，于是
**学完就永久消失**。而 `status` 是从「阅读位置」推导的（前端按当前章节在目录里的
位置上报 progress，点最后一节就等于 100%），用户只是往后翻看一眼也会被判完成，
之后个人中心「继续学习」里再也看不到这篇 —— 用户视角就是「我明明看了却没记录」。

修法：改成 `ORDER BY CASE WHEN status='in_progress' THEN 0 ELSE 1 END,
last_study_at DESC` —— 未完成的优先，不足 N 条用最近学过的补。

这里钉两件事（离线，不需要数据库）：
1. WHERE 里**不能**再出现 status 过滤（否则「已完成」又被排除）；
2. 必须有 CASE 排序（否则「优先接着学」的语义丢了，只剩最近学过）。
"""

import re
import unittest
from types import SimpleNamespace

from app.services.learning_service import list_continue_learning


class _FakeResult:
    def scalars(self):
        return self

    def all(self):
        return []

    def scalar(self):
        return 0


class _CapturingSession:
    """只记录 SQL，不连库。"""

    def __init__(self):
        self.sql = ""
        #: 一次调用可能发多条语句（count + data），逐条留档
        self.sqls = []

    async def execute(self, stmt):
        self.sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        self.sqls.append(self.sql)
        return _FakeResult()


class ContinueLearningQueryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = _CapturingSession()
        self.user = SimpleNamespace(id=42)
        await list_continue_learning(self.session, self.user, limit=3)
        self.sql = self.session.sql
        # 只取 WHERE 子句本身：SELECT 列表里也会出现 `learning_records.status`
        # （它是返回字段），整段字符串搜索会误报。
        m = re.search(r"\bWHERE\b(.*?)\bORDER BY\b(.*)", self.sql, re.S | re.I)
        self.assertIsNotNone(m, "SQL 里没有 WHERE / ORDER BY，守卫已失效：\n" + self.sql)
        self.where_part = m.group(1)
        self.order_part = m.group(2)

    def test_where_does_not_filter_by_status(self):
        """WHERE 里出现 status 过滤 → 已完成的教程又会被排除（原 bug 复发）。"""
        lowered = self.where_part.lower()
        self.assertNotIn(
            "learning_records.status",
            lowered,
            "WHERE 里又按 status 过滤了 —— 「已学完」的教程会从继续学习里消失：\n" + self.sql,
        )

    def test_orders_in_progress_first(self):
        """必须有 CASE 排序，让「进行中」排在「已完成」前面。"""
        self.assertIn(
            "CASE",
            self.order_part.upper(),
            "ORDER BY 里没有 CASE 优先级 —— 未完成的不会再排前面：\n" + self.sql,
        )

    def test_orders_by_last_study_desc(self):
        """组内仍按最近学习时间倒序（否则「最近看过的」不会出现）。"""
        self.assertIn("last_study_at DESC", self.order_part, self.sql)

    def test_still_excludes_unpublished_tutorials(self):
        """取消发布的教程不能出现（点进去只会 404）—— 这条是老行为，别被改掉。"""
        self.assertIn("is_published", self.where_part.lower(), self.sql)


class _Rows:
    """按查询类型返回不同结果的假 Result。"""

    def __init__(self, rows=(), scalar=0):
        self._rows = list(rows)
        self._scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar


class _ScriptedSession:
    """按 SQL 形状分派：count → 标量；查 tutorials → 存活 id；其余 → 数据行。"""

    def __init__(self, rows=(), alive_ids=()):
        self.rows = list(rows)
        self.alive_ids = list(alive_ids)
        self.sqls = []

    async def execute(self, stmt):
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        self.sqls.append(sql)
        low = sql.lower()
        if "count(" in low:
            return _Rows(scalar=len(self.rows))
        if "from tutorials" in low:
            return _Rows(rows=self.alive_ids)
        # 主查询现在返回 (记录, 教程封面) 两列：封面以 tutorials 为准，记录里的快照只兜底
        return _Rows(rows=[(row, None) for row in self.rows])


class _Record:
    """LearningRecord 的最小替身。"""

    def __init__(self, tutorial_id=10, status="completed"):
        self.tutorial_id = tutorial_id
        self.title = "某教程"
        self.current_chapter = "第 1 节"
        self.progress = 100
        self.status = status
        self.last_study_at = None
        self.cover = None


class LearningListKeepsUnavailableTests(unittest.IsolatedAsyncioTestCase):
    """学习记录**保留**「已下架」条目，但必须标 `available=False`（与收藏同口径）。

    产品口径（`services/content_availability.py` 模块文档）：
    - 收藏 = 用户主动保存的资产 → 标「已下架」，不消失；
    - 学习记录 = 用户自己的学习轨迹 → **同样保留**，标「已下架」并禁用跳转。

    一开始按「下载历史」的做法直接过滤，结果该用户 11 条记录只剩 1 条 ——
    一次下架让 10 条历史凭空消失，比看到「已下架」更让人困惑。用户选择保留历史。

    这里钉三件事：
    1. 列表的主查询（count / data）**不得**按发布态过滤（否则历史又消失）；
       为取封面而做的 LEFT OUTER JOIN 是允许的，它不丢行 —— INNER JOIN 不行。
    2. 统计也不得过滤（否则「共 1 门」vs 列表 11 条自相矛盾）；
    3. 必须真的去算 `available`：教程已删除/下架 → False，仍可见 → True。
    """

    async def test_main_queries_do_not_filter_unpublished(self):
        from app.services.learning_service import list_learning_records

        session = _ScriptedSession(rows=[_Record()])
        await list_learning_records(session, SimpleNamespace(id=42), 1, 10)
        main = [s for s in session.sqls if "from tutorials" not in s.lower()]
        self.assertGreaterEqual(len(main), 2, "列表应至少发 count + data 两条语句")
        for sql in main:
            low = sql.lower()
            if "join tutorials" in low:
                # LEFT OUTER JOIN 只为取教程封面，不会丢行，是允许的；
                # 但 INNER JOIN 或按发布态过滤都会让「已下架」的历史消失。
                self.assertIn(
                    "left outer join",
                    low,
                    "主查询用 INNER JOIN 教程表会丢掉已下架的历史：\n" + sql,
                )
                self.assertNotIn(
                    "is_published",
                    low,
                    "主查询按发布态过滤了 —— 已下架的历史会消失：\n" + sql,
                )

    async def test_stats_does_not_filter_unpublished(self):
        from app.services.learning_service import get_learning_stats

        session = _ScriptedSession()
        await get_learning_stats(session, SimpleNamespace(id=42))
        self.assertTrue(session.sqls, "统计没有发查询")
        for sql in session.sqls:
            self.assertNotIn(
                "join tutorials",
                sql.lower(),
                "统计又按发布态过滤了 —— 会和列表条数对不上：\n" + sql,
            )

    async def test_marks_missing_tutorial_as_unavailable(self):
        from app.services.learning_service import list_learning_records

        # 可用性探测返回空 → 教程已删除/下架
        session = _ScriptedSession(rows=[_Record(tutorial_id=10)], alive_ids=[])
        result = await list_learning_records(session, SimpleNamespace(id=42), 1, 10)
        self.assertEqual(len(result["items"]), 1)
        self.assertIs(
            result["items"][0]["available"], False, "已下架的教程没被标出来，前端无法禁用跳转"
        )

    async def test_marks_alive_tutorial_as_available(self):
        from app.services.learning_service import list_learning_records

        session = _ScriptedSession(rows=[_Record(tutorial_id=10)], alive_ids=[10])
        result = await list_learning_records(session, SimpleNamespace(id=42), 1, 10)
        self.assertIs(result["items"][0]["available"], True, "正常教程被误标成已下架")

    async def test_does_query_availability_in_one_shot(self):
        """可用性必须批量判定（一条 IN 查询），不能逐条查（N+1）。"""
        from app.services.learning_service import list_learning_records

        rows = [_Record(tutorial_id=i) for i in range(1, 6)]
        session = _ScriptedSession(rows=rows, alive_ids=[1, 2, 3, 4, 5])
        await list_learning_records(session, SimpleNamespace(id=42), 1, 10)
        probes = [s for s in session.sqls if "from tutorials" in s.lower()]
        self.assertEqual(
            len(probes), 1, f"可用性探测发了 {len(probes)} 条查询，应只有 1 条批量 IN"
        )


if __name__ == "__main__":
    unittest.main()
