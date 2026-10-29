"""章节导航缓存的守卫（第五轮审查 PERF-05）。

## 背景

`get_node_detail` 只为算「上一章 / 下一章」，原本要把该教程**全部章节**读出来、
建树、再扁平化 —— 500 章的教程每翻一页都是一次 O(N) 查询 + O(N) 内存分配。

改成缓存 id 序列后，有两个必须钉住的性质：

1. **命中缓存时不能再查库** —— 否则「加了缓存但每次都回源」等于白做，
   而且这种退化不会有任何报错，只能靠计数发现；
2. **章节增删改/移动必须让缓存失效** —— 否则「新增一章后，下一章还指向旧位置」，
   用户点了会跳到错误的章节，而且 5 分钟内一直错。

顺序语义也一并钉住：缓存里存的是**树前序**，不是按 `sort` 排序
（改排序会改变「下一章」指向哪一章，属产品行为变更）。
"""

import json
import unittest
from unittest import mock

from app.models.tutorial import Tutorial
from app.models.tutorial_node import TutorialNode
from app.services import tutorial_node_service as svc
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession


def _seed(db: MemorySession) -> None:
    db.seed(Tutorial(id=1, title="教程", is_published=True))
    # 树结构：A(1) → 子 B(2)；然后是 C(3)
    # 前序 = [1, 2, 3]；按 sort 排序会是 [1, 3, 2]（B 的 sort=1 与 C 相同）
    db.seed(TutorialNode(id=1, tutorial_id=1, title="A", parent_id=None, sort=1, content=""))
    db.seed(TutorialNode(id=2, tutorial_id=1, title="B", parent_id=1, sort=1, content=""))
    db.seed(TutorialNode(id=3, tutorial_id=1, title="C", parent_id=None, sort=2, content=""))


class NavCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = MemorySession()
        _seed(self.db)
        self.redis = FakeRedis()
        patcher = mock.patch.object(svc, "redis_client", self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_first_call_queries_db_and_second_uses_cache(self):
        calls = {"n": 0}
        real = svc._load_nodes

        async def counting(*args, **kwargs):
            calls["n"] += 1
            return await real(*args, **kwargs)

        with mock.patch.object(svc, "_load_nodes", counting):
            first = await svc._nav_ids(self.db, 1)
            second = await svc._nav_ids(self.db, 1)

        self.assertEqual(first, [1, 2, 3], "顺序必须是树前序（父→子→下一个父）")
        self.assertEqual(second, first)
        self.assertEqual(calls["n"], 1, "第二次应命中缓存，不得再查库")

    async def test_cache_stores_ids_only(self):
        await svc._nav_ids(self.db, 1)
        raw = await self.redis.get(svc._nav_cache_key(1))
        self.assertEqual(json.loads(raw), [1, 2, 3])

    async def test_invalidate_forces_requery(self):
        await svc._nav_ids(self.db, 1)
        await svc.invalidate_nav_cache(1)
        self.assertIsNone(await self.redis.get(svc._nav_cache_key(1)))

        calls = {"n": 0}
        real = svc._load_nodes

        async def counting(*args, **kwargs):
            calls["n"] += 1
            return await real(*args, **kwargs)

        with mock.patch.object(svc, "_load_nodes", counting):
            await svc._nav_ids(self.db, 1)
        self.assertEqual(calls["n"], 1, "失效后必须重新查库")

    async def test_corrupted_cache_falls_back_to_db(self):
        await self.redis.set(svc._nav_cache_key(1), "not-json")
        self.assertEqual(await svc._nav_ids(self.db, 1), [1, 2, 3])

    async def test_redis_failure_degrades_to_db(self):
        """Redis 整体不可用时，导航必须仍然正确（只是每次都查库）。"""

        class Broken:
            async def get(self, *_a, **_k):
                raise ConnectionError("redis down")

            async def set(self, *_a, **_k):
                raise ConnectionError("redis down")

            async def delete(self, *_a, **_k):
                raise ConnectionError("redis down")

        with mock.patch.object(svc, "redis_client", Broken()):
            self.assertEqual(await svc._nav_ids(self.db, 1), [1, 2, 3])
            # 失效调用不能抛（否则写章节的操作会被缓存故障拖垮）
            await svc.invalidate_nav_cache(1)


class AdminMutationsInvalidateTests(unittest.TestCase):
    """四个写操作都必须调用 `invalidate_nav_cache`（AST 检查，不依赖数据库）。"""

    def test_all_admin_node_mutations_invalidate(self):
        import ast
        import io
        import os

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "app",
            "services",
            "tutorial_node_service.py",
        )
        src = io.open(path, encoding="utf-8").read()
        tree = ast.parse(src)
        targets = (
            "admin_create_node",
            "admin_update_node",
            "admin_delete_node",
            "admin_move_node",
        )
        missing = []
        for node in tree.body:
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if node.name not in targets:
                continue
            seg = ast.get_source_segment(src, node) or ""
            if "invalidate_nav_cache" not in seg:
                missing.append(node.name)
        self.assertEqual(
            missing,
            [],
            f"以下章节写操作没有清导航缓存 —— 新增/删除/移动章节后，"
            f"「下一章」会指向旧位置（最长 5 分钟）：{missing}",
        )


if __name__ == "__main__":
    unittest.main()
