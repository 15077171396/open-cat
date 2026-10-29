"""管理端评论列表的筛选语义（重点是新加的发布时间区间）。

审核场景里最常见的入口是「昨天/本周新增了哪些评论」，此前只能靠翻页按时间倒序找 ——
所以补了 `start_at` / `end_at`。这里钉三件事：

1. **区间是闭区间且按发布时间**（不是删除时间），两端各留一条边界用例。
2. **默认仍然只给未删除的**。`include_deleted` 是个危险开关：打开后列表里混着已删除的，
   若「只看时间区间」时不小心默认带上它们，审核者会看到一堆早就处理过的评论。
3. **筛选之间是 AND**：时间区间 + 内容类型 + 关键词叠加，谁都不覆盖谁。
   静默忽略其中一个，结果都会「看起来有数据、其实是另一批」。

另外钉住两个既有约定（改坏了没有报错，只会静默变糟）：
- 纯图片评论的正文是空串，但**必须**出现在列表里（只按正文筛会把最需人工过目的内容漏掉）；
- `target_title` 必须回查出来（评论已泛化到多种内容，「这条评论属于哪篇内容」是处置前提）。
"""

import unittest
from datetime import datetime

from app.models.comment import COMMENT_TARGET_NODE, COMMENT_TARGET_TUTORIAL, ContentComment
from app.models.node import Node
from app.models.tutorial import Tutorial
from app.models.user import User
from app.services import comment_service
from tests.fake_db import MemorySession


def _seed() -> tuple[MemorySession, User]:
    """三条评论跨三天，另有一条已删除的、一条纯图片的"""
    db = MemorySession()
    user = User(id=1, account="u1@example.com", password_hash="x", role="user")
    tutorial = Tutorial(id=100, title="测试教程", description="简介", content="正文", category="ai")
    tutorial.is_published = True
    node = Node(id=200, name="测试节点", category="core", description="简介", content="正文")
    db.seed(user, tutorial, node)
    db.seed(
        ContentComment(id=1, target_type=COMMENT_TARGET_TUTORIAL, target_id=100, user_id=1,
                       content="10-10 的评论", created_at=datetime(2026, 10, 10, 8, 0, 0)),
        ContentComment(id=2, target_type=COMMENT_TARGET_TUTORIAL, target_id=100, user_id=1,
                       content="10-12 的评论", created_at=datetime(2026, 10, 12, 8, 0, 0)),
        ContentComment(id=3, target_type=COMMENT_TARGET_NODE, target_id=200, user_id=1,
                       content="10-14 的评论", created_at=datetime(2026, 10, 14, 8, 0, 0)),
        # 已删除：默认不得出现在列表里
        ContentComment(id=4, target_type=COMMENT_TARGET_TUTORIAL, target_id=100, user_id=1,
                       content="已删除的评论", created_at=datetime(2026, 10, 12, 9, 0, 0),
                       deleted_at=datetime(2026, 10, 12, 10, 0, 0)),
        # 纯图片评论：正文为空，配图靠 images 字段
        ContentComment(id=5, target_type=COMMENT_TARGET_TUTORIAL, target_id=100, user_id=1,
                       content="", images=["https://cdn.example.com/a.png"],
                       created_at=datetime(2026, 10, 12, 11, 0, 0)),
    )
    return db, user


def _ids(data: dict) -> list[int]:
    return [i["id"] for i in data["items"]]


class AdminListFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_time_range_is_inclusive_on_both_ends(self):
        db, _ = _seed()
        data = await comment_service.admin_list_comments(
            db, start_at=datetime(2026, 10, 12, 0, 0, 0), end_at=datetime(2026, 10, 12, 23, 59, 59)
        )
        # 含「12 日 08:00」与「12 日 11:00」两条；已删除的（12 日 09:00）默认排除
        self.assertEqual(_ids(data), [5, 2])
        self.assertEqual(data["total"], 2, "total 必须跟着区间走（分页器用它）")

    async def test_boundary_is_exactly_inclusive(self):
        """端点值本身必须命中 —— 差一秒就会漏掉当天首尾的评论"""
        db, _ = _seed()
        data = await comment_service.admin_list_comments(
            db, start_at=datetime(2026, 10, 12, 8, 0, 0), end_at=datetime(2026, 10, 12, 8, 0, 0)
        )
        self.assertEqual(_ids(data), [2])

    async def test_open_ended_range_works(self):
        db, _ = _seed()
        only_start = await comment_service.admin_list_comments(db, start_at=datetime(2026, 10, 13, 0, 0, 0))
        self.assertEqual(_ids(only_start), [3])
        only_end = await comment_service.admin_list_comments(db, end_at=datetime(2026, 10, 11, 0, 0, 0))
        self.assertEqual(_ids(only_end), [1])

    async def test_deleted_comments_are_hidden_by_default(self):
        db, _ = _seed()
        data = await comment_service.admin_list_comments(db)
        self.assertNotIn(4, _ids(data))

    async def test_include_deleted_brings_them_back(self):
        db, _ = _seed()
        data = await comment_service.admin_list_comments(db, include_deleted=True)
        self.assertIn(4, _ids(data))
        self.assertEqual(data["total"], 5)

    async def test_time_range_does_not_imply_include_deleted(self):
        """区间筛选**不得**顺带把已删除的放出来 —— 审核者会看到一堆早处理过的评论"""
        db, _ = _seed()
        data = await comment_service.admin_list_comments(
            db, start_at=datetime(2026, 10, 12, 0, 0, 0), end_at=datetime(2026, 10, 12, 23, 59, 59)
        )
        self.assertNotIn(4, _ids(data))

    async def test_range_and_target_type_are_anded(self):
        db, _ = _seed()
        data = await comment_service.admin_list_comments(
            db, target_type=COMMENT_TARGET_TUTORIAL,
            start_at=datetime(2026, 10, 12, 0, 0, 0), end_at=datetime(2026, 10, 12, 23, 59, 59),
        )
        self.assertEqual(_ids(data), [5, 2], "节点那条（10-14）不该出现在本区间内")

    async def test_range_and_keyword_are_anded(self):
        db, _ = _seed()
        data = await comment_service.admin_list_comments(
            db, keyword="10-12", start_at=datetime(2026, 10, 12, 0, 0, 0)
        )
        self.assertEqual(_ids(data), [2])

    async def test_no_filters_returns_all_visible(self):
        db, _ = _seed()
        self.assertEqual((await comment_service.admin_list_comments(db))["total"], 4)


class AdminListShapeTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_only_comment_is_listed_and_carries_images(self):
        """纯图片评论正文是空串 —— 只按正文筛会漏掉最需人工过目的内容"""
        db, _ = _seed()
        item = next(i for i in (await comment_service.admin_list_comments(db))["items"] if i["id"] == 5)
        self.assertEqual(item["content"], "")
        self.assertEqual(item["images"], ["https://cdn.example.com/a.png"])

    async def test_target_title_is_resolved_per_type(self):
        """评论是多态目标，「属于哪篇内容」是处置前提"""
        db, _ = _seed()
        items = {i["id"]: i for i in (await comment_service.admin_list_comments(db))["items"]}
        self.assertEqual(items[1]["target_title"], "测试教程")
        self.assertEqual(items[3]["target_title"], "测试节点")

    async def test_manage_only_fields_are_present(self):
        db, _ = _seed()
        item = (await comment_service.admin_list_comments(db))["items"][0]
        for key in ("is_deleted", "deleted_at", "updated_at", "author_account", "parent_id"):
            self.assertIn(key, item, f"后台列表缺 {key} —— 前端按缺失渲染会静默空列")


if __name__ == "__main__":
    unittest.main()
