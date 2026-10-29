"""公开列表缓存（PERF-03）的离线单测。

钉住三件事（这三处最容易写错且线上无任何报错）：

1. **键必须随参数变化**：漏一个参数就会「换个分类还是旧结果」；
2. **键必须稳定**：同一组参数两次调用要命中同一个键，否则缓存永不命中（等于白写）；
3. **写操作要能清掉列表缓存**：`invalidate_nodes()` 之类必须按前缀删掉 `list:nodes:*`，
   否则下架的内容还能在列表里活到 TTL 到期。
"""

import unittest

from app.services import content_cache
from tests.conftest import FakeRedis


class ListCacheKeyTests(unittest.TestCase):
    def test_key_is_stable_for_same_params(self):
        a = content_cache.list_cache_key("nodes", {"page": 1, "sort": "latest", "category": "ComfyUI"})
        b = content_cache.list_cache_key("nodes", {"category": "ComfyUI", "sort": "latest", "page": 1})
        self.assertEqual(a, b, "参数顺序不应影响缓存键")

    def test_key_changes_with_each_param(self):
        base = {"page": 1, "sort": "latest", "category": "ComfyUI"}
        keys = {
            content_cache.list_cache_key("nodes", base),
            content_cache.list_cache_key("nodes", {**base, "page": 2}),
            content_cache.list_cache_key("nodes", {**base, "sort": "hottest"}),
            content_cache.list_cache_key("nodes", {**base, "category": "AI"}),
        }
        self.assertEqual(len(keys), 4, "任何一个参数变化都必须产生新键")

    def test_module_namespace_is_isolated(self):
        params = {"page": 1, "sort": "latest"}
        self.assertNotEqual(
            content_cache.list_cache_key("nodes", params),
            content_cache.list_cache_key("plugins", params),
        )

    def test_empty_values_are_ignored(self):
        """None / 空串视为「未传」：与不传该参数共享同一个键，避免无意义的分裂"""
        self.assertEqual(
            content_cache.list_cache_key("nodes", {"page": 1, "category": None}),
            content_cache.list_cache_key("nodes", {"page": 1, "category": ""}),
        )


class ListCacheRoundTripTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig = content_cache.redis_client
        self.redis = FakeRedis()
        content_cache.redis_client = self.redis

    def tearDown(self):
        content_cache.redis_client = self._orig

    async def test_set_then_get(self):
        key = content_cache.list_cache_key("nodes", {"page": 1})
        payload = {"items": [{"id": 1}], "total": 1, "page": 1, "page_size": 12}
        await content_cache.set_cached_list(key, payload)
        self.assertEqual(await content_cache.get_cached_list(key), payload)

    async def test_corrupted_value_returns_none_and_is_dropped(self):
        key = content_cache.list_cache_key("nodes", {"page": 9})
        await self.redis.set(key, "{not json", ex=60)
        self.assertIsNone(await content_cache.get_cached_list(key))
        self.assertIsNone(await self.redis.get(key), "损坏的值应被删掉，避免每次读都解析失败")

    async def test_redis_down_returns_none(self):
        class _Down:
            def __getattr__(self, _name):
                raise ConnectionError("redis down")

        content_cache.redis_client = _Down()
        key = content_cache.list_cache_key("nodes", {"page": 1})
        self.assertIsNone(await content_cache.get_cached_list(key))
        # 写失败也不能抛（列表接口不该因缓存挂掉）
        await content_cache.set_cached_list(key, {"items": []})

    async def test_invalidate_clears_list_prefix(self):
        """内容写操作必须清掉该模块的列表缓存，否则下架内容会活到 TTL 到期"""
        node_key = content_cache.list_cache_key("nodes", {"page": 1})
        plugin_key = content_cache.list_cache_key("plugins", {"page": 1})
        await content_cache.set_cached_list(node_key, {"items": []})
        await content_cache.set_cached_list(plugin_key, {"items": []})

        await content_cache.invalidate_nodes()

        self.assertIsNone(await self.redis.get(node_key), "节点列表缓存应被清掉")
        self.assertIsNotNone(await self.redis.get(plugin_key), "不应误删其它模块的缓存")


if __name__ == '__main__':
    unittest.main()
