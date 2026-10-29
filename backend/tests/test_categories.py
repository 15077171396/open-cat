"""分类管理模块守卫：模块白名单 / 分类内容计数 / 整组重排。

三件事都是「错了不会报错，只会静默误导运营」的类型，所以各钉一条：

1. **模块白名单三处各写一份**（schema、路由、前端 Tab）。原先 schema 里硬编码
   ``{tutorial, resource, node, plugin}`` 漏了 ``tool`` —— 管理端「在线工具」Tab
   能列出分类、点新增却必然 422。现在白名单以 ``MODULE_TABLE_MAP`` 为唯一事实来源，
   本文件把「每个模块都能建分类」钉住。
2. **内容数量**是运营判断「这个分类能不能删」的依据（删除接口会拦，但运营会先看到
   数字），算错了等于给了假依据。孤儿分类（内容在用、分类表没有的取值）同理：
   它们会让前台按该值筛选时筛不到东西。
3. **整组重排**的入参必须恰好覆盖全部分类：只改一半会留下并列排序，
   而并列时按 id 兜底，运营看到的顺序与预期不符 —— 这种「看起来生效了其实没有」
   最该在门禁里拦下。
"""

import unittest
from unittest import mock

from pydantic import ValidationError

from app.core.exceptions import AppException
from app.models.category import Category
from app.schemas.category import NAME_MAX_LENGTH, CategoryCreate, CategoryReorder, CategoryUpdate
from app.services import category_service
from app.services.category_service import (
    MODULE_TABLE_MAP,
    get_module_overview,
    list_categories,
    reorder_categories,
)
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession

class ModuleWhitelistTests(unittest.TestCase):
    """模块白名单必须与 MODULE_TABLE_MAP 一致（不得再出现第四份硬编码）"""

    def test_table_map_covers_every_content_module(self):
        """分类的模块白名单必须与内容类型注册表同源：新增内容类型时漏配，
        表现为「新增分类」直接 422，而注册表测试仍绿。"""
        from app.core.content_types import IMPLEMENTED_MODULES

        self.assertEqual(set(MODULE_TABLE_MAP), set(IMPLEMENTED_MODULES))

    def test_every_module_can_create_a_category(self):
        """每个已实现的内容模块都必须能新增分类（白名单漏登记 → 必 422）"""
        for module in MODULE_TABLE_MAP:
            with self.subTest(module=module):
                body = CategoryCreate(module=module, name="新分类")
                self.assertEqual(body.module, module)

    def test_unknown_module_is_rejected(self):
        with self.assertRaises(ValidationError):
            CategoryCreate(module="not-a-module", name="新分类")

    def test_name_is_bounded_by_the_db_column(self):
        """超长名称原先直落 MySQL → DataError 500，现在挡成 422"""
        CategoryCreate(module="tutorial", name="x" * NAME_MAX_LENGTH)
        with self.assertRaises(ValidationError):
            CategoryCreate(module="tutorial", name="x" * (NAME_MAX_LENGTH + 1))
        with self.assertRaises(ValidationError):
            CategoryUpdate(name="x" * (NAME_MAX_LENGTH + 1))
        with self.assertRaises(ValidationError):
            CategoryCreate(module="tutorial", name="")

class _CategoryFixture(unittest.IsolatedAsyncioTestCase):
    """共用：内存会话 + FakeRedis（重排会失效分类缓存，不替换会去连真实 Redis）"""

    def setUp(self):
        self.db = MemorySession()
        self._patcher = mock.patch.object(category_service, "redis_client", FakeRedis())
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

class ReorderTests(_CategoryFixture):
    """整组重排：顺序即新的 sort_order（从 1 开始）"""

    def _seed(self):
        self.db.seed(
            Category(id=1, module="node", name="A", sort_order=1),
            Category(id=2, module="node", name="B", sort_order=2),
            Category(id=3, module="node", name="C", sort_order=3),
        )

    async def test_reorder_assigns_sequential_sort_order(self):
        self._seed()
        await reorder_categories(self.db, "node", [3, 1, 2])
        rows = await list_categories(self.db, "node")
        self.assertEqual([c.id for c in rows], [3, 1, 2])
        self.assertEqual([c.sort_order for c in rows], [1, 2, 3])

    async def test_reorder_does_not_touch_other_modules(self):
        self._seed()
        self.db.seed(Category(id=9, module="plugin", name="P", sort_order=7))
        await reorder_categories(self.db, "node", [2, 3, 1])
        other = await list_categories(self.db, "plugin")
        self.assertEqual([c.sort_order for c in other], [7])

    async def test_incomplete_id_set_is_rejected(self):
        """缺项必须拒绝：只重排一部分会留下并列排序，顺序看起来生效了其实没有"""
        self._seed()
        with self.assertRaises(AppException):
            await reorder_categories(self.db, "node", [1, 2])
        # 拒绝后不得留下半成品
        rows = await list_categories(self.db, "node")
        self.assertEqual([c.sort_order for c in rows], [1, 2, 3])

    async def test_duplicate_ids_are_rejected(self):
        self._seed()
        with self.assertRaises(AppException):
            await reorder_categories(self.db, "node", [1, 1, 2])

    async def test_ids_from_another_module_are_rejected(self):
        self._seed()
        self.db.seed(Category(id=9, module="plugin", name="P", sort_order=1))
        with self.assertRaises(AppException):
            await reorder_categories(self.db, "node", [1, 2, 9])

    async def test_empty_list_is_rejected_by_schema(self):
        with self.assertRaises(ValidationError):
            CategoryReorder(ids=[])

if __name__ == "__main__":
    unittest.main()
