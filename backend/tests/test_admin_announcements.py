"""管理端公告列表的筛选语义。

公告是**全站首屏可见**的内容（首页公告条 + 「查看全部」弹窗），列表筛选错一点运营就会
误判「这条已经撤下了 / 这条还在首页」。这里钉三件事：

1. `tag` 是新加的筛选项 —— **必须真的生效**。筛选被静默忽略是最危险的形态：
   运营选「活动」却看到全部公告，会以为活动公告有 5 条。
2. 未知标签必须返回 0 条，而不是回落成「不筛选」。
3. `tag` 与 `is_published` / `keyword` 是 AND 关系（多条件叠加不互相覆盖）。

顺带钉住 `keyword` 只搜标题（公告正文是长文本，搜正文会让结果难以解释）。
"""

import unittest

from app.models.announcement import Announcement
from app.services.admin_announcement_service import admin_list_announcements
from tests.fake_db import MemorySession


def _seed() -> MemorySession:
    db = MemorySession()
    db.seed(
        Announcement(id=1, title="欢迎使用 AI 学习猫平台", content="正文甲", tag="公告", color="#3b82f6", is_published=True),
        Announcement(id=2, title="双十一活动开始", content="正文乙", tag="活动", color="#f97316", is_published=True),
        Announcement(id=3, title="活动预告（草稿）", content="正文丙", tag="活动", color="#f97316", is_published=False),
        Announcement(id=4, title="站点维护通知", content="正文丁", tag="公告", color="#3b82f6", is_published=False),
    )
    return db


class AnnouncementListFilterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = _seed()

    async def _list(self, **kwargs):
        return await admin_list_announcements(self.db, **kwargs)

    async def test_tag_filter_actually_narrows(self):
        """选了标签就必须真的过滤 —— 静默忽略会让运营数错条数"""
        data = await self._list(tag="活动")
        self.assertEqual({i["id"] for i in data["items"]}, {2, 3})
        self.assertEqual(data["total"], 2, "total 必须跟着筛选走（分页器用它）")

    async def test_unknown_tag_returns_empty_not_everything(self):
        data = await self._list(tag="不存在的标签")
        self.assertEqual(data["items"], [])
        self.assertEqual(data["total"], 0)

    async def test_tag_combines_with_publish_state(self):
        data = await self._list(tag="活动", is_published=True)
        self.assertEqual({i["id"] for i in data["items"]}, {2})

    async def test_tag_combines_with_keyword(self):
        data = await self._list(tag="活动", keyword="双十一")
        self.assertEqual({i["id"] for i in data["items"]}, {2})

    async def test_no_filter_returns_everything(self):
        """不带任何条件时必须返回全部（含未发布草稿）——管理端要能看到全部状态"""
        data = await self._list()
        self.assertEqual(data["total"], 4)

    async def test_publish_state_filter_alone(self):
        self.assertEqual((await self._list(is_published=False))["total"], 2)
        self.assertEqual((await self._list(is_published=True))["total"], 2)

    async def test_keyword_searches_title_only(self):
        """正文里的字不该命中（公告正文长，搜正文的结果无法解释）"""
        self.assertEqual((await self._list(keyword="正文甲"))["total"], 0)
        self.assertEqual((await self._list(keyword="欢迎"))["total"], 1)

    async def test_items_carry_fields_the_list_renders(self):
        """列表要渲染标签/颜色/状态/两个时间，缺一个就是空白单元格"""
        item = (await self._list(tag="活动"))["items"][0]
        for key in ("id", "title", "content", "tag", "color", "is_published",
                    "published_at", "created_at", "updated_at"):
            self.assertIn(key, item)


if __name__ == "__main__":
    unittest.main()
