"""分类分组的契约：组的增删改查、分类移入/移出、越界与误删的拦截。

分组是**同一内容类型内部**的次级组织（`category_groups.module`）。三条硬约束：

1. 组不能跨内容类型使用 —— 教程的分类挂到资源的分组下，前台会筛出空列表；
2. 组下还有分类时**拒绝删除** —— 级联删分类会让内容指向不存在的分类；
3. 「未分组」不是一个真实记录 —— 它是 `group_id IS NULL` 的虚拟组，
   所以「移出分组」与「不改分组」必须是两个入口（`move_category` vs `update_category`）。
"""

import unittest
from unittest import mock

from app.core.exceptions import AppException
from app.models.category import Category, CategoryGroup
from app.services import category_service
from app.services.category_service import (
    create_category,
    create_group,
    delete_group,
    list_groups,
    move_category,
    rename_group,
    reorder_groups,
    update_category,
)
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = MemorySession()
        # 分类写操作会失效公开缓存，不替换会去连真实 Redis
        self._patcher = mock.patch.object(category_service, "redis_client", FakeRedis())
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()


class GroupCrudTests(_Base):
    async def test_create_and_list_groups(self):
        await create_group(self.db, "tutorial", "入门")
        await create_group(self.db, "tutorial", "进阶")
        groups = await list_groups(self.db, "tutorial")
        self.assertEqual([g.name for g in groups], ["入门", "进阶"])
        # sort_order 从 1 起递增，页面按它排序
        self.assertEqual([g.sort_order for g in groups], [1, 2])

    async def test_duplicate_name_is_rejected(self):
        await create_group(self.db, "tutorial", "入门")
        with self.assertRaises(AppException) as ctx:
            await create_group(self.db, "tutorial", "入门")
        self.assertEqual(ctx.exception.code, 400)

    async def test_same_name_allowed_in_another_module(self):
        """分组归属内容类型：教程有「入门」不影响资源的「入门」"""
        await create_group(self.db, "tutorial", "入门")
        group = await create_group(self.db, "resource", "入门")
        self.assertEqual(group.module, "resource")

    async def test_rename_keeps_categories_attached(self):
        group = await create_group(self.db, "tutorial", "入门")
        self.db.seed(Category(id=1, module="tutorial", name="基础", group_id=group.id))

        await rename_group(self.db, group.id, "新手")

        rows = await list_groups(self.db, "tutorial")
        self.assertEqual([g.name for g in rows], ["新手"])
        self.assertEqual(self.db.all_rows(Category)[0]["group_id"], group.id)


class GroupDeleteTests(_Base):
    async def test_delete_blocked_when_categories_remain(self):
        group = await create_group(self.db, "tutorial", "入门")
        self.db.seed(Category(id=1, module="tutorial", name="基础", group_id=group.id))

        with self.assertRaises(AppException) as ctx:
            await delete_group(self.db, group.id)
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("分类", ctx.exception.message)
        # 拒绝后组与分类都必须还在
        self.assertEqual(len(self.db.all_rows(CategoryGroup)), 1)
        self.assertEqual(len(self.db.all_rows(Category)), 1)

    async def test_delete_succeeds_after_moving_categories_out(self):
        group = await create_group(self.db, "tutorial", "入门")
        self.db.seed(Category(id=1, module="tutorial", name="基础", group_id=group.id))

        await move_category(self.db, 1, None)
        await delete_group(self.db, group.id)

        self.assertEqual(self.db.all_rows(CategoryGroup), [])
        self.assertIsNone(self.db.all_rows(Category)[0]["group_id"])


class CategoryGroupAssignmentTests(_Base):
    async def test_create_category_with_group(self):
        group = await create_group(self.db, "tutorial", "入门")
        category = await create_category(self.db, "tutorial", "基础", group_id=group.id)
        self.assertEqual(category.group_id, group.id)

    async def test_cross_module_group_is_rejected(self):
        """越界组合在写入口就拒：否则页面上会出现「资源的分组下面挂教程分类」"""
        group = await create_group(self.db, "resource", "模型")
        with self.assertRaises(AppException) as ctx:
            await create_category(self.db, "tutorial", "基础", group_id=group.id)
        self.assertEqual(ctx.exception.code, 400)

    async def test_update_without_group_id_does_not_move(self):
        """`update_category(group_id=None)` 的语义是「不改」，不是「移出」"""
        group = await create_group(self.db, "tutorial", "入门")
        category = await create_category(self.db, "tutorial", "基础", group_id=group.id)

        await update_category(self.db, category.id, "基础改名")

        rows = self.db.all_rows(Category)
        self.assertEqual(rows[0]["group_id"], group.id)

    async def test_move_category_out_of_group(self):
        group = await create_group(self.db, "tutorial", "入门")
        category = await create_category(self.db, "tutorial", "基础", group_id=group.id)

        await move_category(self.db, category.id, None)

        self.assertIsNone(self.db.all_rows(Category)[0]["group_id"])


class GroupReorderTests(_Base):
    async def test_reorder_assigns_sequential_order(self):
        g1 = await create_group(self.db, "tutorial", "A")
        g2 = await create_group(self.db, "tutorial", "B")
        g3 = await create_group(self.db, "tutorial", "C")

        await reorder_groups(self.db, "tutorial", [g3.id, g1.id, g2.id])

        rows = await list_groups(self.db, "tutorial")
        self.assertEqual([g.name for g in rows], ["C", "A", "B"])
        self.assertEqual([g.sort_order for g in rows], [1, 2, 3])

    async def test_incomplete_id_set_is_rejected(self):
        g1 = await create_group(self.db, "tutorial", "A")
        await create_group(self.db, "tutorial", "B")
        with self.assertRaises(AppException):
            await reorder_groups(self.db, "tutorial", [g1.id])


if __name__ == "__main__":
    unittest.main()
