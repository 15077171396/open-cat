"""站点地图分片的契约测试（缺口台账 P2-8 / 代码审查 D6）。

搜索引擎对**单个** sitemap 文件有硬上限（百度 / Google 均为 5 万条 URL），超限会被
**整份丢弃**（不是截断）。所以数据量上来后必须切成「索引 + 分片」。

本文件钉死四条契约，它们一旦破了**都不会报错**，只会让页面静默不被收录：

1. **顺序稳定**：分片靠 offset/limit 切分，若查询没有 ORDER BY，MySQL 不保证顺序
   → 同一 URL 出现在两片里（浪费配额）或被两片都跳过（该页永不收录）。
2. **不重不漏**：所有分片的 URL 并集 == 全集，且没有重复。
3. **超限才分片**：未超上限时必须仍是单份 urlset —— 无端引入 sitemapindex 会让
   搜索引擎多抓一层，且老站长工具会报「结构变化」。
4. **越界页 404**：失效的分片链接要明确报错，而不是回一份空 urlset
   （空 urlset 会被当成「该片确实为空」，可能触发「内容与索引不符」告警）。
"""

import re
import unittest
from unittest import mock

from app.core.exceptions import AppException
from app.models.node import Node, Plugin
from app.models.resource import Resource
from app.models.tool import Tool
from app.models.tutorial import Tutorial
from app.models.tutorial_node import TutorialNode
from app.services import sitemap_service as ss
from tests.fake_db import MemorySession

LOC_RE = re.compile(r"<loc>([^<]+)</loc>")


def _seed_site(db: MemorySession, *, tutorials=1, chapters_per_tutorial=1, plugins=1,
               nodes=1, resources=1, tools=1, tutorial_published=True, tool_published=True):
    next_id = [1000]

    def nid():
        next_id[0] += 1
        return next_id[0]

    for i in range(tutorials):
        tid = nid()
        db.seed(Tutorial(id=tid, title=f"教程{i}", is_published=tutorial_published))
        for j in range(chapters_per_tutorial):
            db.seed(TutorialNode(id=nid(), tutorial_id=tid, title=f"章节{j}"))
    for i in range(plugins):
        db.seed(Plugin(id=nid(), name=f"扩展{i}"))
    for i in range(nodes):
        db.seed(Node(id=nid(), name=f"节点{i}"))
    for i in range(resources):
        db.seed(Resource(id=nid(), name=f"资源{i}"))
    for i in range(tools):
        db.seed(Tool(id=nid(), name=f"工具{i}", is_published=tool_published))
    return db


def _locs(xml: str) -> list[str]:
    return LOC_RE.findall(xml)


class TestSingleFileMode(unittest.IsolatedAsyncioTestCase):
    async def test_small_site_is_a_plain_urlset(self):
        """未超上限时必须仍是 urlset —— 不无端引入 sitemapindex。"""
        db = _seed_site(MemorySession())
        xml = await ss.build_sitemap(db)
        self.assertIn("<urlset", xml)
        self.assertNotIn("<sitemapindex", xml)
        self.assertTrue(xml.startswith('<?xml version="1.0" encoding="UTF-8"?>'))

    async def test_contains_static_paths_and_all_entities(self):
        db = _seed_site(MemorySession(), tutorials=2, chapters_per_tutorial=1,
                        plugins=1, nodes=1, resources=1, tools=1)
        xml = await ss.build_sitemap(db)
        locs = _locs(xml)
        base = ss.settings.SITE_BASE_URL.rstrip("/")
        for p in ("/", "/tutorials", "/plugins", "/nodes", "/resources", "/tools"):
            self.assertIn(f"{base}{p}", locs)
        self.assertTrue(any("/tutorials/" in u and "/c/" in u for u in locs), "缺章节页")
        self.assertTrue(any(u.endswith(".xml") is False and "/plugins/" in u for u in locs))

    async def test_unpublished_tutorial_and_tool_excluded(self):
        """未发布内容不该出现在 sitemap —— 否则爬虫抓到的是一堆空/404 页面。"""
        db = _seed_site(MemorySession(), tutorial_published=False, tool_published=False)
        xml = await ss.build_sitemap(db)
        locs = _locs(xml)
        self.assertFalse(any("/tutorials/" in u for u in locs))
        self.assertFalse(any("/tools/" in u for u in locs))

    async def test_chapter_of_unpublished_tutorial_excluded(self):
        db = MemorySession()
        db.seed(Tutorial(id=1, title="已发布", is_published=True))
        db.seed(Tutorial(id=2, title="未发布", is_published=False))
        db.seed(TutorialNode(id=11, tutorial_id=1, title="公开章节"))
        db.seed(TutorialNode(id=12, tutorial_id=2, title="私有章节"))
        locs = _locs(await ss.build_sitemap(db))
        self.assertTrue(any(u.endswith("/tutorials/1/c/11") for u in locs))
        self.assertFalse(any(u.endswith("/tutorials/2/c/12") for u in locs))

    async def test_page_one_equals_full_set_in_single_file_mode(self):
        """单文件模式下 /sitemap-1.xml 必须可用且等于全量（口径统一，不出现空分片）。"""
        db = _seed_site(MemorySession())
        full = _locs(await ss.build_sitemap(db))
        page1 = _locs(await ss.build_sitemap(db, page=1))
        self.assertEqual(full, page1)


class TestShardedMode(unittest.IsolatedAsyncioTestCase):
    def _patch_limit(self, limit: int):
        return mock.patch.object(ss, "SITEMAP_MAX_URLS_PER_FILE", limit)

    async def test_absurd_page_is_rejected_without_collecting_entries(self):
        """远超上界的页码必须**在收集之前**就 404（P1-43②）

        原实现是「先做 6 次全表收集、之后才校验页码」—— 爬虫遍历
        /sitemap-500000.xml 每次都触发一轮全量扫描，是一条廉价的 DB 放大入口。
        这里用「收集函数一次都没被调用」把这条约束钉死。
        """
        db = MemorySession()
        with mock.patch.object(
            ss, "collect_sitemap_entries", new=mock.AsyncMock()
        ) as collector:
            with self.assertRaises(AppException) as ctx:
                await ss.build_sitemap(db, page=999_999)

        self.assertEqual(ctx.exception.code, 404)
        collector.assert_not_awaited()

    async def test_page_zero_and_negative_are_rejected(self):
        db = MemorySession()
        for bad_page in (0, -1):
            with self.subTest(page=bad_page):
                with self.assertRaises(AppException) as ctx:
                    await ss.build_sitemap(db, page=bad_page)
                self.assertEqual(ctx.exception.code, 404)

    async def test_over_limit_returns_sitemapindex(self):
        # 只留静态栏目页（其余实体全部不 seed）；限 3 条 → 必然多片。
        # ⚠️ 片数由「实际收集到的 URL 数」推导，不写死静态页数量：
        # 原先断言 6 条静态页 = 2 片，新增 /about /contact 后直接红，
        # 而失败原因是用例的前提过期，不是分片逻辑坏了。
        db = _seed_site(
            MemorySession(), tutorials=0, chapters_per_tutorial=0,
            plugins=0, nodes=0, resources=0, tools=0,
        )
        entries = await ss.collect_sitemap_entries(db)
        with self._patch_limit(3):
            # 片数必须在 patch 生效期间算：sitemap_page_count 读的是模块级上限
            expected_pages = ss.sitemap_page_count(len(entries))
            self.assertGreater(expected_pages, 1, "用例前提：静态 URL 数需超过单文件上限 3")
            xml = await ss.build_sitemap(db)
        self.assertIn("<sitemapindex", xml)
        self.assertNotIn("<urlset", xml)
        base = ss.settings.SITE_BASE_URL.rstrip("/")
        self.assertEqual(
            _locs(xml),
            [f"{base}/sitemap-{i}.xml" for i in range(1, expected_pages + 1)],
        )

    async def test_index_lastmod_and_namespace(self):
        """索引文件必须带 sitemapindex 命名空间，否则搜索引擎拒绝解析。"""
        db = _seed_site(
            MemorySession(), tutorials=0, chapters_per_tutorial=0,
            plugins=0, nodes=0, resources=0, tools=0,
        )
        with self._patch_limit(3):
            xml = await ss.build_sitemap(db)
        self.assertIn('xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"', xml)
        self.assertIn("<sitemap><loc>", xml)
        self.assertIn("</lastmod></sitemap>", xml)

    async def test_pages_cover_all_urls_exactly_once(self):
        """核心契约：分片并集 == 全集，且无重复（顺序不稳定就会破在这里）。"""
        db = _seed_site(MemorySession(), tutorials=3, chapters_per_tutorial=2,
                        plugins=2, nodes=2, resources=2, tools=2)
        full = _locs(await ss.build_sitemap(db))
        self.assertGreater(len(full), 5)

        with self._patch_limit(4):
            pages = ss.sitemap_page_count(len(full))
            collected: list[str] = []
            for p in range(1, pages + 1):
                collected += _locs(await ss.build_sitemap(db, page=p))

        self.assertEqual(len(collected), len(set(collected)), "分片间出现重复 URL")
        self.assertEqual(sorted(collected), sorted(full), "分片并集与全集不一致")

    async def test_page_out_of_range_raises_404(self):
        db = _seed_site(MemorySession())
        with self._patch_limit(3):
            with self.assertRaises(AppException) as ctx:
                await ss.build_sitemap(db, page=99)
            self.assertEqual(ctx.exception.code, 404)

    async def test_page_count_at_least_one(self):
        self.assertEqual(ss.sitemap_page_count(0), 1)
        self.assertEqual(ss.sitemap_page_count(1), 1)

    async def test_page_count_rounds_up(self):
        with self._patch_limit(10):
            self.assertEqual(ss.sitemap_page_count(10), 1)
            self.assertEqual(ss.sitemap_page_count(11), 2)
            self.assertEqual(ss.sitemap_page_count(30), 3)


class TestOrderingIsStable(unittest.IsolatedAsyncioTestCase):
    """顺序稳定是分片正确性的前提，且「漏写 ORDER BY」在运行期不会报错。"""

    def test_every_entity_query_has_order_by(self):
        for model in (Tutorial, TutorialNode, Plugin, Node, Resource, Tool):
            stmt = ss._entity_query(model, model.id)
            self.assertTrue(
                stmt._order_by_clauses,
                f"{model.__name__} 的 sitemap 查询缺少 ORDER BY（分片会重复/漏项）",
            )

    def test_entity_query_only_loads_requested_columns(self):
        """load_only 生效：全站遍历时多取一个正文字段就是几十 MB 额外内存。"""
        stmt = ss._entity_query(Tutorial, Tutorial.id, Tutorial.updated_at)
        loaded = set(stmt.column_descriptions[0]["expr"].__table__.columns.keys())
        self.assertIn("id", loaded)
        self.assertIn("updated_at", loaded)

    async def test_two_consecutive_builds_are_identical(self):
        db = _seed_site(MemorySession(), tutorials=2, plugins=2)
        self.assertEqual(await ss.build_sitemap(db), await ss.build_sitemap(db))


class TestSitemapCache(unittest.IsolatedAsyncioTestCase):
    """进程内缓存按「页」分键：键写错会让分片互相覆盖（第 2 页返回第 1 页的内容）。"""

    async def asyncSetUp(self):
        from app.api import sitemap as api

        api._sitemap_cache.clear()

    async def test_cache_is_keyed_per_page(self):
        from app.api import sitemap as api

        db = _seed_site(MemorySession(), tutorials=2, plugins=2)
        with mock.patch.object(ss, "SITEMAP_MAX_URLS_PER_FILE", 3):
            p1 = await api._render("page-1", db, 1)
            p2 = await api._render("page-2", db, 2)
        self.assertNotEqual(p1, p2, "不同分片返回了相同内容，缓存键可能未区分页号")

    async def test_second_call_hits_cache(self):
        from app.api import sitemap as api

        db = _seed_site(MemorySession())
        first = await api._render("index", db, None)
        # 缓存命中后，数据变化不影响已缓存内容（TTL 内不再重算）
        db.seed(Plugin(id=7777, name="新扩展"))
        second = await api._render("index", db, None)
        self.assertEqual(first, second)

    async def test_exception_is_not_cached(self):
        """越界页抛 404，不能把「空内容」缓存下来，否则修好分片数后仍返回旧结果。"""
        from app.api import sitemap as api

        db = _seed_site(MemorySession())
        with self.assertRaises(AppException):
            await api._render("page-9", db, 9)
        self.assertNotIn("page-9", api._sitemap_cache)


class TestSitemapRoutes(unittest.TestCase):
    """路由接线守卫：nginx 依赖这两个路径，改名会让 sitemap 整体 404。"""

    def test_routes_registered(self):
        from app.api.sitemap import router

        paths = {r.path for r in router.routes}
        self.assertIn("/sitemap.xml", paths)
        self.assertIn("/sitemap-{page}.xml", paths)

    def test_nginx_has_shard_location(self):
        from pathlib import Path

        nginx = (Path(__file__).resolve().parents[2] / "nginx.conf").read_text(encoding="utf-8")
        self.assertIn("/sitemap-(\\d+)\\.xml", nginx, "nginx 缺少 sitemap 分片 location")
        self.assertIn("/api/v1/sitemap-$1.xml", nginx, "分片 location 未把路径改写给后端")


if __name__ == "__main__":
    unittest.main()
