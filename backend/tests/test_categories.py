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
from app.models.node import Node
from app.models.resource import Resource
from app.models.tool import Tool
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

    def test_table_map_covers_the_five_admin_tabs(self):
        """守卫自身要有牙：前端「分类管理」页恰好这 5 个 Tab，少一个就是漏配"""
        self.assertEqual(
            set(MODULE_TABLE_MAP),
            {"tutorial", "resource", "node", "plugin", "tool"},
        )

    def test_every_module_can_create_a_category(self):
        """回归：tool 曾被 schema 白名单漏掉，「在线工具」新增分类必 422"""
        for module in MODULE_TABLE_MAP:
            with self.subTest(module=module):
                body = CategoryCreate(module=module, name="新分类")
                self.assertEqual(body.module, module)

    def test_unknown_module_is_rejected(self):
        with self.assertRaises(ValidationError):
            CategoryCreate(module="not-a-module", name="新分类")

    def test_name_is_bounded_by_the_db_column(self):
        """超长名称原先直落 MySQL → DataError 500，现在挡成 422"""
        CategoryCreate(module="tool", name="x" * NAME_MAX_LENGTH)
        with self.assertRaises(ValidationError):
            CategoryCreate(module="tool", name="x" * (NAME_MAX_LENGTH + 1))
        with self.assertRaises(ValidationError):
            CategoryUpdate(name="x" * (NAME_MAX_LENGTH + 1))
        with self.assertRaises(ValidationError):
            CategoryCreate(module="tool", name="")


class _CategoryFixture(unittest.IsolatedAsyncioTestCase):
    """共用：内存会话 + FakeRedis（重排会失效分类缓存，不替换会去连真实 Redis）"""

    def setUp(self):
        self.db = MemorySession()
        self._patcher = mock.patch.object(category_service, "redis_client", FakeRedis())
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()


class ModuleOverviewTests(_CategoryFixture):
    """分类管理页的内容计数与孤儿分类"""

    def _seed(self):
        self.db.seed(
            Category(id=1, module="node", name="加载", sort_order=1),
            Category(id=2, module="node", name="图像", sort_order=2),
            Category(id=3, module="node", name="未使用分类", sort_order=3),
            # 内容：图像 ×2、加载 ×1，另有「潜空间」×3 是内容在用但分类表没有的
            Node(id=11, name="节点A", category="图像"),
            Node(id=12, name="节点B", category="图像"),
            Node(id=13, name="节点C", category="加载"),
            Node(id=14, name="节点D", category="潜空间"),
            Node(id=15, name="节点E", category="潜空间"),
            Node(id=16, name="节点F", category="潜空间"),
        )

    async def test_counts_per_category(self):
        self._seed()
        data = await get_module_overview(self.db, "node")
        counts = {item["name"]: item["content_count"] for item in data["items"]}
        self.assertEqual(counts, {"加载": 1, "图像": 2, "未使用分类": 0})

    async def test_orphan_categories_are_reported(self):
        """内容里出现、分类表未登记的取值必须列出来（否则前台按它筛选永远筛不到）"""
        self._seed()
        data = await get_module_overview(self.db, "node")
        self.assertEqual(data["orphans"], [{"name": "潜空间", "content_count": 3}])

    async def test_total_content_includes_orphans(self):
        """总数含孤儿，否则「共 N 条内容」会比实际少"""
        self._seed()
        data = await get_module_overview(self.db, "node")
        self.assertEqual(data["total_content"], 6)

    async def test_empty_module_is_not_an_error(self):
        data = await get_module_overview(self.db, "plugin")
        self.assertEqual(data["items"], [])
        self.assertEqual(data["orphans"], [])
        self.assertEqual(data["total_content"], 0)

    async def test_resource_module_uses_its_own_column(self):
        """资源的内容列是 resource_type（不是 category）—— 取错列会静默返回全 0"""
        self.db.seed(
            Category(id=21, module="resource", name="model", sort_order=1),
            Resource(id=31, name="资源A", resource_type="model"),
            Resource(id=32, name="资源B", resource_type="tool"),
        )
        data = await get_module_overview(self.db, "resource")
        self.assertEqual(data["items"][0]["content_count"], 1)
        self.assertEqual(data["orphans"], [{"name": "tool", "content_count": 1}])

    async def test_tool_module_is_supported(self):
        """回归：tool 曾整条链路不可用（既不能建分类，也不该统计不到）"""
        self.db.seed(
            Category(id=41, module="tool", name="Prompt", sort_order=1),
            Tool(id=51, name="工具A", category="Prompt"),
        )
        data = await get_module_overview(self.db, "tool")
        self.assertEqual(data["items"][0]["content_count"], 1)


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
