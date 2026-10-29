"""资源「发布作者」字段的读写契约（2026-10-12）。

背景（用户需求）：
    资源卡片要展示「预览量（左）+ 发布作者/头像（右）」，与扩展卡片同一形态。
    `view_count` 早已存在并对外返回；作者则是新增字段
    （迁移 `20261012_0041_add_resource_author`）。

本文件钉住三条（与 `test_plugin_author.py` 同一套约定，两边不能漂移）：
1. 公开列表与详情都要下发作者字段（卡片要渲染，缺了就是空白）；
2. **空串必须归一为 NULL**：语义上「未填写」就该是 NULL，前台据此不渲染作者行，
   而不是显示一个空名字（与 seo_* 同惯例）；
3. 更新时传空串 = 清除作者；未传该字段时不得抹掉已有作者。
"""

import unittest
from unittest import mock

from app.models.resource import Resource
from app.models.user import User
from app.services import admin_resource_service, resource_service
from tests.fake_db import MemorySession

ADMIN = User(id=9001, account="admin@test")


def make_resource(
    *,
    resource_id: int = 1,
    author_name: str | None = None,
    author_avatar_url: str | None = None,
) -> Resource:
    return Resource(
        id=resource_id,
        name="ComfyUI 整合包",
        description="开箱即用",
        resource_type="model",
        file_size="3.2GB",
        view_count=12300,
        author_name=author_name,
        author_avatar_url=author_avatar_url,
    )


class ResourceAuthorReadTests(unittest.IsolatedAsyncioTestCase):
    """公开列表 / 详情必须下发作者字段（卡片渲染依赖它们）"""

    async def test_public_list_exposes_author_fields(self):
        db = MemorySession()
        db.seed(make_resource(author_name="张三", author_avatar_url="https://cdn.example.com/a.png"))

        item = (await resource_service.get_resources(db, page=1, page_size=10))["items"][0]
        self.assertEqual(item["author_name"], "张三")
        self.assertEqual(item["author_avatar_url"], "https://cdn.example.com/a.png")
        self.assertEqual(item["view_count"], 12300, "预览量必须下发（卡片左侧展示的就是它）")

    async def test_public_list_keeps_author_null_when_unset(self):
        """未填写作者 → NULL（不是空串），前台据此不渲染作者行"""
        db = MemorySession()
        db.seed(make_resource())

        item = (await resource_service.get_resources(db, page=1, page_size=10))["items"][0]
        self.assertIsNone(item["author_name"])
        self.assertIsNone(item["author_avatar_url"])

    async def test_public_detail_exposes_author_fields(self):
        db = MemorySession()
        db.seed(make_resource(author_name="李四", author_avatar_url="https://cdn.example.com/b.png"))

        data = await resource_service.get_resource_detail(db, 1)
        self.assertEqual(data["author_name"], "李四")
        self.assertEqual(data["author_avatar_url"], "https://cdn.example.com/b.png")


class ResourceAuthorWriteTests(unittest.IsolatedAsyncioTestCase):
    """管理端写入：空串归一为 NULL、空串可清除、未传不覆盖"""

    def setUp(self):
        # 写操作末尾会 invalidate_resources()（摸 Redis）——本文件只关心字段落库
        patcher = mock.patch.object(admin_resource_service, "invalidate_resources", mock.AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_create_keeps_author(self):
        db = MemorySession()
        await admin_resource_service.create_resource(
            db,
            {
                "name": "资源A", "description": "简介", "resource_type": "model",
                "author_name": "王五", "author_avatar_url": "https://cdn.example.com/c.png",
            },
            ADMIN,
        )
        row = db.all_rows(Resource)[0]
        self.assertEqual(row["author_name"], "王五")
        self.assertEqual(row["author_avatar_url"], "https://cdn.example.com/c.png")

    async def test_create_falls_back_to_publishing_account(self):
        """不传作者时由**发布账号**兜底（2026-10-12 需求：不再让运营手填）"""
        db = MemorySession()
        await admin_resource_service.create_resource(
            db,
            {
                "name": "资源B", "category": "model", "resource_type": "model",
                "description": "简介", "author_name": "", "author_avatar_url": "",
            },
            ADMIN,
        )
        row = db.all_rows(Resource)[0]
        expected = ADMIN.nickname or ADMIN.account
        self.assertEqual(row["author_name"], expected, "空串/缺省都必须由发布账号兜底")
        self.assertNotEqual(row["author_name"], "", "不得留下空串，否则前台会渲染空名字")

    async def test_update_fills_author_for_historical_content(self):
        """历史内容（作者为空）首次被编辑时自动归属到编辑者，免去逐条补录"""
        db = MemorySession()
        db.seed(make_resource())
        await admin_resource_service.update_resource(db, 1, {"name": "改名后"}, ADMIN)
        row = db.all_rows(Resource)[0]
        self.assertEqual(row["author_name"], ADMIN.nickname or ADMIN.account)
        self.assertEqual(row["name"], "改名后", "其余字段照常更新")

    async def test_update_does_not_overwrite_existing_author(self):
        """已有作者不被编辑者覆盖 —— 作者是「谁发布的」，不是「谁最后改的」"""
        db = MemorySession()
        db.seed(make_resource(author_name="原作者", author_avatar_url="https://cdn.example.com/a.png"))
        await admin_resource_service.update_resource(db, 1, {"name": "改名后"}, ADMIN)
        row = db.all_rows(Resource)[0]
        self.assertEqual(row["author_name"], "原作者")
        self.assertEqual(row["author_avatar_url"], "https://cdn.example.com/a.png")


    async def test_update_only_touches_author_when_provided(self):
        """未传该字段时不得把已有作者抹掉"""
        db = MemorySession()
        db.seed(make_resource(author_name="张三", author_avatar_url="https://cdn.example.com/a.png"))

        await admin_resource_service.update_resource(db, 1, {"name": "改名后"}, ADMIN)
        row = db.all_rows(Resource)[0]
        self.assertEqual(row["name"], "改名后")
        self.assertEqual(row["author_name"], "张三", "只改名字不该清掉作者")


class AdminGetterAuthorFieldsTests(unittest.IsolatedAsyncioTestCase):
    """后台编辑页的回填来源 `admin_resource_service.get_resource` 必须下发作者字段。

    其余用例只测「公开读 + 后台写」，从未调用 `get_resource`（第五轮审查 R5-18）。
    资源页的 `cover_image` 有隐藏域注册、`author_avatar_url` 原先没有（R5-01），
    两条凑齐就是「运营编辑一次资源就永久丢掉作者头像」。
    """

    def setUp(self):
        patcher = mock.patch.object(admin_resource_service, "invalidate_resources", mock.AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_admin_getter_returns_author_fields(self):
        db = MemorySession()
        db.seed(make_resource(author_name="张三", author_avatar_url="https://cdn.example.com/a.png"))
        data = await admin_resource_service.get_resource(db, 1)
        self.assertEqual(data["author_name"], "张三", "缺键 → 编辑页回填不到 → 保存时清掉")
        self.assertEqual(data["author_avatar_url"], "https://cdn.example.com/a.png")

    async def test_round_trip_keeps_author(self):
        db = MemorySession()
        db.seed(make_resource(author_name="张三", author_avatar_url="https://cdn.example.com/a.png"))
        loaded = await admin_resource_service.get_resource(db, 1)
        await admin_resource_service.update_resource(
            db, 1,
            {"name": loaded["name"], "author_name": loaded["author_name"],
             "author_avatar_url": loaded["author_avatar_url"]},
            ADMIN,
        )
        after = await admin_resource_service.get_resource(db, 1)
        self.assertEqual(after["author_name"], "张三")
        self.assertEqual(after["author_avatar_url"], "https://cdn.example.com/a.png")


if __name__ == "__main__":
    unittest.main()
