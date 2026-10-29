"""百科「封面 + 发布作者」字段的读写契约（2026-10-12）。

背景（用户需求）：
    百科列表卡片改布局：左侧 1/3 放首图（封面），右侧 2/3 放文字，
    底部浏览/时间在左、发布作者(头像)在最右。封面与作者此前都不存在
    （迁移 `20261012_0042_add_node_cover_and_author`）。

与 `test_resource_author.py` / `test_plugin_author.py` 同一套约定：
1. 公开列表与详情都要下发（卡片要渲染，缺了就是空白）；
2. **空串归一为 NULL**（前台据此不渲染，而不是显示空名字/破图）；
3. 更新时传空串 = 清除；未传该字段不得抹掉已有值。
"""

import unittest
from datetime import datetime
from unittest import mock

from app.models.node import Node
from app.models.user import User
from app.services import admin_node_service, node_service
from tests.fake_db import MemorySession

ADMIN = User(id=9001, account="admin@test")


def make_node(
    *,
    node_id: int = 1,
    cover_image: str | None = None,
    author_name: str | None = None,
    author_avatar_url: str | None = None,
) -> Node:
    return Node(
        id=node_id,
        name="KSampler",
        category="采样",
        description="ComfyUI 中最核心的采样节点",
        view_count=136,
        # MemorySession 不套 server_default，时间需显式给，否则断言不到「必须下发」
        updated_at=datetime(2026, 10, 12, 9, 30),
        cover_image=cover_image,
        author_name=author_name,
        author_avatar_url=author_avatar_url,
    )


class NodeCoverAuthorReadTests(unittest.IsolatedAsyncioTestCase):
    """公开列表 / 详情必须下发封面与作者字段（卡片渲染依赖它们）"""

    async def test_public_list_exposes_cover_and_author(self):
        db = MemorySession()
        db.seed(make_node(
            cover_image="https://cdn.example.com/cover.png",
            author_name="AI学习猫",
            author_avatar_url="https://cdn.example.com/a.png",
        ))

        item = (await node_service.list_nodes(db, page=1, page_size=10))["items"][0]
        self.assertEqual(item["cover_image"], "https://cdn.example.com/cover.png")
        self.assertEqual(item["author_name"], "AI学习猫")
        self.assertEqual(item["author_avatar_url"], "https://cdn.example.com/a.png")
        self.assertEqual(item["view_count"], 136, "浏览量必须下发（卡片左侧指标）")
        self.assertIsNotNone(item["updated_at"], "时间必须下发（卡片左侧与浏览并排）")

    async def test_public_list_keeps_cover_and_author_null_when_unset(self):
        """未填写 → NULL（不是空串），前台据此不渲染对应区域"""
        db = MemorySession()
        db.seed(make_node())

        item = (await node_service.list_nodes(db, page=1, page_size=10))["items"][0]
        self.assertIsNone(item["cover_image"])
        self.assertIsNone(item["author_name"])
        self.assertIsNone(item["author_avatar_url"])

    async def test_public_detail_exposes_cover_and_author(self):
        db = MemorySession()
        db.seed(make_node(
            cover_image="https://cdn.example.com/cover.png",
            author_name="张三",
            author_avatar_url="https://cdn.example.com/b.png",
        ))

        data = await node_service.get_node_detail(db, 1)
        self.assertEqual(data["cover_image"], "https://cdn.example.com/cover.png")
        self.assertEqual(data["author_name"], "张三")
        self.assertEqual(data["author_avatar_url"], "https://cdn.example.com/b.png")


class NodeCoverAuthorWriteTests(unittest.IsolatedAsyncioTestCase):
    """管理端写入：空串归一为 NULL、空串可清除、未传不覆盖"""

    def setUp(self):
        patcher = mock.patch.object(admin_node_service, "invalidate_nodes", mock.AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_create_keeps_cover_and_author(self):
        db = MemorySession()
        await admin_node_service.create_node(
            db,
            {
                "name": "KSampler", "category": "采样", "description": "核心采样节点",
                "cover_image": "https://cdn.example.com/c.png", "author_name": "王五",
            },
            ADMIN,
        )
        row = db.all_rows(Node)[0]
        self.assertEqual(row["cover_image"], "https://cdn.example.com/c.png")
        self.assertEqual(row["author_name"], "王五")

    async def test_create_falls_back_to_publishing_account(self):
        """不传作者时由**发布账号**兜底（2026-10-12 需求：不再让运营手填）"""
        db = MemorySession()
        await admin_node_service.create_node(
            db,
            {
                "name": "节点B", "category": "采样", 
                "description": "简介", "author_name": "", "author_avatar_url": "",
            },
            ADMIN,
        )
        row = db.all_rows(Node)[0]
        expected = ADMIN.nickname or ADMIN.account
        self.assertEqual(row["author_name"], expected, "空串/缺省都必须由发布账号兜底")
        self.assertNotEqual(row["author_name"], "", "不得留下空串，否则前台会渲染空名字")

    async def test_update_fills_author_for_historical_content(self):
        """历史内容（作者为空）首次被编辑时自动归属到编辑者，免去逐条补录"""
        db = MemorySession()
        db.seed(make_node())
        await admin_node_service.update_node(db, 1, {"name": "改名后"}, ADMIN)
        row = db.all_rows(Node)[0]
        self.assertEqual(row["author_name"], ADMIN.nickname or ADMIN.account)
        self.assertEqual(row["name"], "改名后", "其余字段照常更新")

    async def test_update_does_not_overwrite_existing_author(self):
        """已有作者不被编辑者覆盖 —— 作者是「谁发布的」，不是「谁最后改的」"""
        db = MemorySession()
        db.seed(make_node(author_name="原作者", author_avatar_url="https://cdn.example.com/a.png"))
        await admin_node_service.update_node(db, 1, {"name": "改名后"}, ADMIN)
        row = db.all_rows(Node)[0]
        self.assertEqual(row["author_name"], "原作者")
        self.assertEqual(row["author_avatar_url"], "https://cdn.example.com/a.png")


    async def test_update_only_touches_provided_fields(self):
        """未传字段不得抹掉已有值"""
        db = MemorySession()
        db.seed(make_node(
            cover_image="https://cdn.example.com/cover.png",
            author_name="张三", author_avatar_url="https://cdn.example.com/b.png",
        ))

        await admin_node_service.update_node(db, 1, {"name": "改名后"}, ADMIN)
        row = db.all_rows(Node)[0]
        self.assertEqual(row["name"], "改名后")
        self.assertEqual(row["cover_image"], "https://cdn.example.com/cover.png")
        self.assertEqual(row["author_name"], "张三")


class AdminGetterCoverAuthorTests(unittest.IsolatedAsyncioTestCase):
    """后台编辑页的回填来源 `admin_node_service.get_node` 必须下发封面与作者。

    其余用例只测「公开读 + 后台写」，从未调用 `get_node`（第五轮审查 R5-18）。
    编辑页靠它回填 → 回填不到就是空 → 保存提交空串 → 后端按「清除」写 NULL，
    与 R5-01（表单未注册字段）串成「只改名字也会抹掉封面与头像」。
    """

    async def test_admin_getter_returns_cover_and_author(self):
        db = MemorySession()
        db.seed(make_node(cover_image="https://cdn.example.com/cover.png",
                          author_name="张三", author_avatar_url="https://cdn.example.com/a.png"))
        data = await admin_node_service.get_node(db, 1)
        self.assertEqual(data["cover_image"], "https://cdn.example.com/cover.png", "缺键 → 编辑页回填不到 → 保存时清掉")
        self.assertEqual(data["author_name"], "张三")
        self.assertEqual(data["author_avatar_url"], "https://cdn.example.com/a.png")

    async def test_round_trip_keeps_cover_and_author(self):
        db = MemorySession()
        db.seed(make_node(cover_image="https://cdn.example.com/cover.png",
                          author_name="张三", author_avatar_url="https://cdn.example.com/a.png"))
        loaded = await admin_node_service.get_node(db, 1)
        await admin_node_service.update_node(
            db, 1,
            {"name": "改名后", "cover_image": loaded["cover_image"],
             "author_name": loaded["author_name"], "author_avatar_url": loaded["author_avatar_url"]},
            ADMIN,
        )
        after = await admin_node_service.get_node(db, 1)
        self.assertEqual(after["name"], "改名后")
        self.assertEqual(after["cover_image"], "https://cdn.example.com/cover.png")
        self.assertEqual(after["author_avatar_url"], "https://cdn.example.com/a.png")


if __name__ == "__main__":
    unittest.main()
