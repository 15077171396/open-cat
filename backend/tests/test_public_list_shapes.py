"""公开列表/统计接口的响应形状一致性（第三轮增量审查 P1-48 ①②）。

为什么值得钉：同一个项目里，`/plugins/featured` 返回 `{"items": [...]}`，而
`/resources/hot` 与 `/resources/latest` 原先返回**裸数组**；`/resources/stats` 用
`type` 键、`/nodes/stats` 却用 `key`。前端（或小程序）每次对接都要先猜形状，
而这两种不一致**不会报错** —— 只是让每个消费方多写一份兼容代码。

已核查这些端点当前**没有消费方**（Web 端不调用；小程序 `utils/api.js` 定义了
helper 但从未被页面调用），所以统一形状是安全的。本文件防止它们再漂回去。
"""

import unittest
from contextlib import asynccontextmanager
from unittest import mock

import httpx

from app.models.node import Node, Plugin
from app.models.resource import Resource
from app.services import node_service, plugin_service, resource_service
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession


def _seed(db: MemorySession) -> MemorySession:
    # is_published 必须显式给：SQLAlchemy 的列默认值在 INSERT 时才生效，
    # 直接构造对象得到的是 None —— 而统计现在按 is_published=True 过滤（C-03），
    # 不写就会被当成草稿，统计为空。真实库里的行永远是 True/False，不会缺这一列。
    db.seed(Plugin(id=1, name="扩展1", is_published=True))
    db.seed(Resource(id=2, name="资源2", is_published=True))
    db.seed(Node(id=3, name="节点3", is_published=True))
    return db


class PublicListShapeTests(unittest.IsolatedAsyncioTestCase):
    """三个「取前 N 条」端点必须同一种形状：`{"items": [...]}`"""

    def setUp(self):
        self.db = _seed(MemorySession())
        # 这三个服务各自持有模块级 redis_client，全部换成 FakeRedis（离线）
        self._patchers = [
            mock.patch.object(module, "redis_client", FakeRedis())
            for module in (plugin_service, resource_service, node_service)
        ]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)

    async def test_featured_plugins_shape(self):
        result = await plugin_service.get_featured_plugins(self.db, limit=12)
        self.assertIsInstance(result, dict)
        self.assertIn("items", result)
        self.assertIsInstance(result["items"], list)

    async def test_hot_resources_shape_matches_featured(self):
        result = await resource_service.get_hot_resources(self.db, limit=8)
        self.assertEqual(set(result), {"items"}, "形状须与 /plugins/featured 一致")
        self.assertIsInstance(result["items"], list)

    async def test_latest_resources_shape_matches_featured(self):
        result = await resource_service.get_latest_resources(self.db, limit=5)
        self.assertEqual(set(result), {"items"}, "形状须与 /plugins/featured 一致")
        self.assertIsInstance(result["items"], list)


class StatsKeyConsistencyTests(unittest.IsolatedAsyncioTestCase):
    """两个 stats 端点的分类键必须同名（现统一为 `type`）"""

    def setUp(self):
        self.db = _seed(MemorySession())
        self._patchers = [
            mock.patch.object(module, "redis_client", FakeRedis())
            for module in (resource_service, node_service)
        ]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)

    async def test_resource_and_node_stats_use_same_key(self):
        res_stats = await resource_service.get_resource_stats(self.db)
        node_stats = await node_service.get_node_stats(self.db)

        # 两个端点都用 "categories" 承载分类计数
        res_keys = {k for item in res_stats.get("categories", []) for k in item}
        node_keys = {k for item in node_stats.get("categories", []) for k in item}
        # 两边都必须包含 type/label/count，且都**不含** key
        for keys, label in ((res_keys, "resources/stats"), (node_keys, "nodes/stats")):
            with self.subTest(endpoint=label):
                self.assertIn("type", keys)
                self.assertIn("label", keys)
                self.assertIn("count", keys)
                self.assertNotIn(
                    "key", keys, f"{label} 仍在用 `key`，与另一侧不一致"
                )


class RouteShapeTests(unittest.IsolatedAsyncioTestCase):
    """形状必须**经路由验证**（第四轮全量审查 P0-12 / P2-50①）。

    上方两个类只调 service，而真正把契约序列化出来的是路由上的
    `response_model=ApiResponse[...]` —— 于是 P1-48② 把 service 改成返回
    `{"items": [...]}` 却把路由声明留在 `ApiResponse[list]` 时，**全套用例仍绿**，
    而 `/resources/hot`、`/resources/latest` 对任何调用方都是稳定 500
    （已用 venv 实测：`ApiResponse[list]` 校验 dict → `ValidationError: list_type`）。

    本类用真实 app + 真实路由跑一次 GET，断言 200 与 `data` 的键集合。
    """

    def setUp(self):
        self.db = _seed(MemorySession())
        self._patchers = [
            mock.patch.object(module, "redis_client", FakeRedis())
            for module in (resource_service, plugin_service, node_service)
        ]
        # 路由是匿名 GET，限流中间件会摸 Redis；不换 FakeRedis 会去连真实 Redis
        from app.core import rate_limit as _rate_limit

        self._patchers.append(mock.patch.object(_rate_limit, "redis_client", FakeRedis()))
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)

    @asynccontextmanager
    async def _client(self):
        from app.database import get_db
        from app.main import app

        async def _override_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_db
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test", timeout=10.0
            ) as client:
                yield client
        finally:
            app.dependency_overrides.pop(get_db, None)

    async def test_hot_resources_route_returns_200_with_items(self):
        async with self._client() as client:
            resp = await client.get("/api/v1/resources/hot", params={"limit": 8})
        self.assertEqual(resp.status_code, 200, resp.text[:300])
        self.assertEqual(set(resp.json()["data"]), {"items"})

    async def test_latest_resources_route_returns_200_with_items(self):
        async with self._client() as client:
            resp = await client.get("/api/v1/resources/latest", params={"limit": 5})
        self.assertEqual(resp.status_code, 200, resp.text[:300])
        self.assertEqual(set(resp.json()["data"]), {"items"})

    async def test_plugins_featured_route_matches(self):
        """对照端点：/plugins/featured 一直是 `{"items": [...]}`，两条路径必须同样过路由"""
        async with self._client() as client:
            resp = await client.get("/api/v1/plugins/featured", params={"limit": 12})
        self.assertEqual(resp.status_code, 200, resp.text[:300])
        self.assertEqual(set(resp.json()["data"]), {"items"})


class UnknownResourceTypeTests(unittest.IsolatedAsyncioTestCase):
    """数据库里出现未登记的 resource_type 时，公开统计接口不能 500（P0-14）。

    `admin_resources.py:36` 的 `resource_type` 是无枚举校验的自由字符串，管理端下拉
    又取自分类表 → 运营加一个分类即可写入第 7 种取值。同一规则在
    `node_service.get_node_stats` 里已有容错分支（注释明写「兼容数据库中新增但未录入
    categories 表的分类」），本类把两边对齐钉成断言 —— 复制漂移只会红一次。
    """

    def setUp(self):
        self.db = MemorySession()
        self.db.seed(Resource(id=1, name="正常", resource_type="model", is_published=True))
        self.db.seed(Resource(id=2, name="新分类", resource_type="教程文档", is_published=True))
        # 草稿：统计不能把它算进去（C-03）
        self.db.seed(Resource(id=3, name="草稿", resource_type="model", is_published=False))
        self._patchers = [
            mock.patch.object(module, "redis_client", FakeRedis())
            for module in (resource_service, node_service)
        ]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)

    async def test_resource_stats_tolerates_unknown_type_like_node_stats(self):
        stats = await resource_service.get_resource_stats(self.db)
        types = {item["type"] for item in stats["categories"]}
        self.assertIn("教程文档", types, "未知分类被丢弃 → 真实资源不计入统计")
        # total 只算已发布：两条已发布 + 一条草稿 → 2（第二轮复审 C-03）
        self.assertEqual(stats["total"], 2, "总数应含未登记分类的资源，但**不含草稿**")

    async def test_stats_exclude_drafts(self):
        """统计口径必须与列表一致：草稿不进分类计数（C-03）

        此前 group_by 没有 is_published，首页「资源 N 个」与分类 Tab 计数会把草稿算进去，
        与列表 total 对不上；下架内容后数字还不降。
        """
        stats = await resource_service.get_resource_stats(self.db)
        counts = {item["type"]: item["count"] for item in stats["categories"]}
        self.assertEqual(counts.get("model"), 1, "model 类型只有 1 条已发布（另 1 条是草稿）")

    async def test_node_stats_still_tolerates_unknown_category(self):
        stats = await node_service.get_node_stats(self.db)
        self.assertIsInstance(stats["categories"], list)


if __name__ == "__main__":
    unittest.main()
