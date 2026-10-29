"""SEO / 后台任务的边界（缺口台账 P2-43①②）。

这三处都是「量一大才炸、且不报错」的形态，所以守卫按可失败断言写：

1. **URL 收集必须带上限**：6 条查询原先全都没有 `LIMIT` —— 一次批量导入或分类改名
   （`_sync_content_category` 抬升整张内容表的 `updated_at`）就把全部存量页当成
   「最近更新」，当天烧光百度共享配额、并把 IndexNow 拖成几十分钟串行 POST。
   截断还必须配 `ORDER BY`，否则 MySQL 取哪 500 条不确定（同 P2-8 的分片教训）。
2. **进程内缓存必须有上界**：每片 sitemap 是整份 XML（≤45,000 条 `<url>`，可达数 MB），
   分片号合法域到 1000；原先只增不删，抓取一轮就能把几百片常驻进程 —— 直接顶穿
   384MB/worker 预算。
3. **过期重建必须单飞**：TTL 到点时爬虫是**成批**来的，无锁等于让每个爬虫各跑一遍
   全量重建，抓取放大反而打在数据库上。
"""

import asyncio
import re
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import select

from app.api import llms_txt as llms_api
from app.api import sitemap as sitemap_api
from app.models.tutorial import Tutorial
from scheduler import tasks


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _RecordingSession:
    """只记录语句、返回预置行 —— 不解释 SQL，避免把断言绑在仿真器能力上。"""

    def __init__(self, sink, rows_per_query):
        self._sink = sink
        self._rows = rows_per_query
        self._i = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        rows = self._rows[self._i] if self._i < len(self._rows) else []
        self._i += 1
        self._sink.append(stmt)
        return _Result(rows)


def _is_bounded(stmt) -> bool:
    """语句是否既带上限、又带稳定截断序（updated_at DESC + id DESC 两个排序键）"""
    return (
        getattr(stmt, "_limit", None) is not None
        and len(getattr(stmt, "_order_by_clauses", ())) >= 2
    )


def _collect(session_factory):
    with mock.patch.object(tasks, "async_session", session_factory):
        return asyncio.run(tasks.collect_recent_urls())


class CollectUrlBoundsTests(unittest.TestCase):
    def test_predicate_rejects_an_unbounded_statement(self):
        """反向对照：谓词本身必须能判红一条没有 LIMIT / 没有排序的语句。

        少了这条，上面的守卫会因为「属性名写错」之类的原因恒绿 —— 而恒绿的守卫
        比没有守卫更糟（第三轮报告：守卫必须自证有牙）。
        """
        self.assertFalse(_is_bounded(select(Tutorial.id).where(Tutorial.is_published == True)))  # noqa: E712
        self.assertFalse(
            _is_bounded(select(Tutorial.id).order_by(Tutorial.updated_at.desc()))
        )  # 只有 1 个排序键：同秒并列时截断集合仍不确定
        self.assertTrue(
            _is_bounded(
                select(Tutorial.id)
                .order_by(Tutorial.updated_at.desc(), Tutorial.id.desc())
                .limit(500)
            )
        )

    def test_every_query_is_limited_and_ordered(self):
        seen = []
        _collect(lambda: _RecordingSession(seen, [[]] * 2))

        self.assertEqual(len(seen), 2, "板块查询数量变了，本守卫的覆盖面需同步")
        for i, stmt in enumerate(seen):
            self.assertTrue(_is_bounded(stmt), f"第 {i + 1} 条收集查询无界或截断序不稳定")

    def test_limit_matches_the_documented_cap(self):
        seen = []
        _collect(lambda: _RecordingSession(seen, [[]] * 2))
        for stmt in seen:
            self.assertEqual(stmt._limit, tasks._MAX_PUSH_URLS_PER_TYPE)

    def test_duplicate_urls_are_collapsed(self):
        """同一 URL 重复提交既浪费当日共享配额，也会让推送通道被判低质。"""
        # 两个板块各喂两份相同值：不去重的话返回 4 条
        rows = [[5, 5], [(5, 7), (5, 7)]]
        seen = []
        urls = _collect(lambda: _RecordingSession(seen, rows))

        self.assertEqual(len(urls), 2, f"去重后应剩 2 条，实际 {len(urls)} 条：{urls}")
        self.assertEqual(len(set(urls)), 2)
        self.assertTrue(any(u.endswith("/tutorials/5") for u in urls))
        self.assertTrue(any(u.endswith("/tutorials/5/c/7") for u in urls))


class UpdatedAtIndexGuardTests(unittest.TestCase):
    """`updated_at` 扫描侧的索引半边（缺口台账 P2-43）。

    代码侧加了 `ORDER BY updated_at DESC, id DESC` + `LIMIT 500` 只解决
    「推爆配额 / 串行几十分钟」；**没有该列索引时 MySQL 仍要先扫全表再 filesort**，
    LIMIT 只是把尾部行丢掉。所以「有没有索引」必须被钉住，而不是等某次
    内容量上来后靠人想起这条 🟡。
    """

    # collect_recent_urls 实际扫的表（对过 scheduler/tasks.py 的查询）
    SCANNED = {
        "tutorials": "app.models.tutorial",
        "tutorial_nodes": "app.models.tutorial_node",
    }

    def _models(self):
        import app.models  # noqa: F401  触发全部模型注册
        from app.database import Base

        return {m.class_.__tablename__: m.class_ for m in Base.registry.mappers}

    def _has_updated_at_index(self, model) -> bool:
        return any(
            [c.name for c in idx.columns] == ["updated_at"]
            for idx in model.__table__.indexes
        )

    def test_every_scanned_table_has_an_updated_at_index(self):
        models = self._models()
        missing = [t for t in self.SCANNED if not self._has_updated_at_index(models[t])]
        self.assertEqual(
            missing,
            [],
            f"以下表的 updated_at 无索引，SEO 增量推送仍是全表 + filesort: {missing}",
        )

    def test_index_names_match_sqlalchemy_defaults(self):
        """索引名必须等于 `ix_<表>_updated_at`，否则 autogenerate 会反向 DROP（同 0035/0043 的坑）。"""
        models = self._models()
        wrong = []
        for table in self.SCANNED:
            names = [
                idx.name for idx in models[table].__table__.indexes
                if [c.name for c in idx.columns] == ["updated_at"]
            ]
            if names != [f"ix_{table}_updated_at"]:
                wrong.append(f"{table}: {names}")
        self.assertEqual(wrong, [], "索引名与模型默认命名不一致: " + "; ".join(wrong))

    def test_a_migration_actually_creates_each_index(self):
        """模型声明了不等于库里有：必须有迁移把它建出来（🟡→✅ 的那半边）。

        注意要扫**全部**版本文件 —— `nodes` 的这条来自 0035，其余 5 张来自 0044；
        只盯一个新迁移会把已有覆盖误判成缺失。
        """
        versions_dir = Path(__file__).resolve().parents[1] / "alembic" / "versions"
        sources = {p.name: p.read_text(encoding="utf-8") for p in versions_dir.glob("*.py")}

        def created_by(path_text: str) -> set[str]:
            found = set(re.findall(r"ix_([a-z_]+)_updated_at", path_text))
            # 循环式写法：`_TABLES = ("a", "b")` + f"ix_{table}_updated_at"
            if "_TABLES" in path_text and "updated_at" in path_text:
                block = path_text[path_text.index("_TABLES"):]
                block = block[: block.index("]")] if "]" in block else block
                found |= set(re.findall(r'"([a-z_]+)"', block))
            return found

        covered: set[str] = set()
        for name, text in sources.items():
            if "create_index" in text:
                covered |= created_by(text)
        missing = sorted(set(self.SCANNED) - covered)
        self.assertEqual(
            missing,
            [],
            f"以下表的 updated_at 索引只写在模型里、没有任何迁移创建它: {missing}",
        )

    def test_predicate_is_not_vacuous(self):
        """反向对照：一张确实没索引的表必须被判为「无」，否则上面两条守卫会恒绿。"""
        import app.models  # noqa: F401
        from app.database import Base

        models = {m.class_.__tablename__: m.class_ for m in Base.registry.mappers}
        for table in ("feedbacks", "announcements"):
            self.assertFalse(
                self._has_updated_at_index(models[table]),
                f"{table}.updated_at 若有索引，本守卫的反向对照就失效了（需同步换表）",
            )


class SitemapCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        sitemap_api._sitemap_cache.clear()

    async def asyncTearDown(self):
        sitemap_api._sitemap_cache.clear()

    async def test_concurrent_misses_rebuild_only_once(self):
        calls = []

        async def slow_build(db, page=None):
            calls.append(page)
            await asyncio.sleep(0.02)  # 让其它协程有机会越过「未命中」检查
            return "<urlset/>"

        with mock.patch.object(sitemap_api, "build_sitemap", slow_build):
            results = await asyncio.gather(
                *[sitemap_api._render("index", None, None) for _ in range(5)]
            )

        self.assertEqual(len(calls), 1, f"单飞失效：并发 miss 触发了 {len(calls)} 次全量重建")
        self.assertEqual(set(results), {"<urlset/>"})

    async def test_cache_is_capped(self):
        async def build(db, page=None):
            return f"<urlset page={page}/>"

        with mock.patch.object(sitemap_api, "build_sitemap", build), mock.patch.object(
            sitemap_api, "_MAX_CACHED_SITEMAP_PAGES", 3
        ):
            for i in range(7):
                await sitemap_api._render(f"page-{i}", None, i)

        self.assertLessEqual(len(sitemap_api._sitemap_cache), 3)

    async def test_expired_entries_are_dropped_on_write(self):
        async def build(db, page=None):
            return "<urlset/>"

        # 两条早已过期的残留（模拟「抓取过一批后来不再存在的分片」）
        sitemap_api._sitemap_cache["page-900"] = (0.0, "x" * 10_000)
        sitemap_api._sitemap_cache["page-901"] = (0.0, "x" * 10_000)

        with mock.patch.object(sitemap_api, "build_sitemap", build):
            await sitemap_api._render("index", None, None)

        self.assertNotIn("page-900", sitemap_api._sitemap_cache)
        self.assertNotIn("page-901", sitemap_api._sitemap_cache)


class LlmsTxtSingleFlightTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        llms_api._llms_cache = (0.0, "")

    async def asyncTearDown(self):
        llms_api._llms_cache = (0.0, "")

    async def test_concurrent_crawlers_rebuild_once(self):
        calls = []

        async def slow_build(db):
            calls.append(1)
            await asyncio.sleep(0.02)
            return "# llms"

        with mock.patch.object(llms_api, "build_llms_txt", slow_build):
            responses = await asyncio.gather(
                *[llms_api.llms_txt(None) for _ in range(4)]
            )

        self.assertEqual(len(calls), 1, f"单飞失效：{len(calls)} 个爬虫各跑了一遍全量重建")
        self.assertEqual({r.body for r in responses}, {b"# llms"})


if __name__ == "__main__":
    unittest.main()
