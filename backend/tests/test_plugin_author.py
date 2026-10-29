"""扩展「发布作者」字段的读写契约（2026-10-12）。

背景（用户需求）：
    扩展卡片改为展示「预览量 + 发布作者(头像)」，不再展示安装量。
    作者此前**没有任何持久化字段** —— 第三方导入接口虽然会返回 `author`
    （仓库类为 owner），但那只用于管理端表单的临时回填，关掉页面就没了。
    迁移 `20261012_0039_add_plugin_author` 补上 `author_name` / `author_avatar_url`。

本文件钉住三条：
1. 公开列表与详情都要下发这两个字段（卡片要渲染，缺了就是空白）；
2. **空串必须归一为 NULL**：语义上「未填写」就该是 NULL，前台据此不渲染作者行，
   而不是显示一个空名字（与 seo_* 同惯例）；
3. 更新时传空串 = 清除作者（否则运营填错了没法改回去）。

测试全部离线（CI 无 MySQL/Redis），用 tests/fake_db.py 的内存会话。
"""

import unittest
from unittest import mock

from app.models.node import Plugin
from app.models.user import User
from app.services import admin_plugin_service, plugin_service
from tests.fake_db import MemorySession

ADMIN = User(id=9001, account="admin@test")


def make_plugin(
    *,
    plugin_id: int = 1,
    author_name: str | None = None,
    author_avatar_url: str | None = None,
) -> Plugin:
    return Plugin(
        id=plugin_id,
        name="ComfyUI Manager",
        category="comfyui",
        description="图形化管理自定义节点",
        view_count=7,
        author_name=author_name,
        author_avatar_url=author_avatar_url,
    )


class PluginAuthorReadTests(unittest.IsolatedAsyncioTestCase):
    """公开列表 / 详情必须下发作者字段（卡片渲染依赖它们）"""

    async def test_public_list_exposes_author_fields(self):
        db = MemorySession()
        db.seed(make_plugin(author_name="张三", author_avatar_url="https://cdn.example.com/a.png"))

        result = await plugin_service.list_plugins(db, page=1, page_size=10)
        item = result["items"][0]
        self.assertEqual(item["author_name"], "张三")
        self.assertEqual(item["author_avatar_url"], "https://cdn.example.com/a.png")
        self.assertEqual(item["view_count"], 7, "预览量必须下发（卡片展示的就是它）")

    async def test_public_list_keeps_author_null_when_unset(self):
        """未填写作者 → NULL（不是空串），前台据此不渲染作者行"""
        db = MemorySession()
        db.seed(make_plugin())

        item = (await plugin_service.list_plugins(db, page=1, page_size=10))["items"][0]
        self.assertIsNone(item["author_name"])
        self.assertIsNone(item["author_avatar_url"])

    async def test_public_detail_exposes_author_fields(self):
        db = MemorySession()
        db.seed(make_plugin(author_name="李四", author_avatar_url="https://cdn.example.com/b.png"))

        data = await plugin_service.get_plugin_detail(db, 1)
        self.assertEqual(data["author_name"], "李四")
        self.assertEqual(data["author_avatar_url"], "https://cdn.example.com/b.png")


class PluginAuthorWriteTests(unittest.IsolatedAsyncioTestCase):
    """管理端写入：空串归一为 NULL、空串可清除"""

    def setUp(self):
        # 写操作末尾会 invalidate_plugins()（摸 Redis）——本文件只关心字段落库，
        # 直接把它换成 no-op，避免测试去连真实 Redis 而静默变慢
        patcher = mock.patch.object(admin_plugin_service, "invalidate_plugins", mock.AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_create_keeps_author(self):
        db = MemorySession()
        await admin_plugin_service.create_plugin(
            db,
            {
                "name": "扩展A", "category": "comfyui", "platform": "ComfyUI",
                "description": "简介", "author_name": "王五",
                "author_avatar_url": "https://cdn.example.com/c.png",
            },
            ADMIN,
        )
        rows = db.all_rows(Plugin)
        self.assertEqual(rows[0]["author_name"], "王五")
        self.assertEqual(rows[0]["author_avatar_url"], "https://cdn.example.com/c.png")

    async def test_create_falls_back_to_publishing_account(self):
        """不传作者时由**发布账号**兜底（2026-10-12 需求：不再让运营手填）"""
        db = MemorySession()
        await admin_plugin_service.create_plugin(
            db,
            {
                "name": "扩展B", "category": "comfyui", "platform": "ComfyUI",
                
                "description": "简介", "author_name": "", "author_avatar_url": "",
            },
            ADMIN,
        )
        row = db.all_rows(Plugin)[0]
        expected = ADMIN.nickname or ADMIN.account
        self.assertEqual(row["author_name"], expected, "空串/缺省都必须由发布账号兜底")
        self.assertNotEqual(row["author_name"], "", "不得留下空串，否则前台会渲染空名字")

    async def test_update_fills_author_for_historical_content(self):
        """历史内容（作者为空）首次被编辑时自动归属到编辑者，免去逐条补录"""
        db = MemorySession()
        db.seed(make_plugin())
        await admin_plugin_service.update_plugin(db, 1, {"name": "改名后"}, ADMIN)
        row = db.all_rows(Plugin)[0]
        self.assertEqual(row["author_name"], ADMIN.nickname or ADMIN.account)
        self.assertEqual(row["name"], "改名后", "其余字段照常更新")

    async def test_update_does_not_overwrite_existing_author(self):
        """已有作者不被编辑者覆盖 —— 作者是「谁发布的」，不是「谁最后改的」"""
        db = MemorySession()
        db.seed(make_plugin(author_name="原作者", author_avatar_url="https://cdn.example.com/a.png"))
        await admin_plugin_service.update_plugin(db, 1, {"name": "改名后"}, ADMIN)
        row = db.all_rows(Plugin)[0]
        self.assertEqual(row["author_name"], "原作者")
        self.assertEqual(row["author_avatar_url"], "https://cdn.example.com/a.png")


    async def test_update_only_touches_author_when_provided(self):
        """未传该字段时不得把已有作者抹掉（PUT 的 exclude_unset=False 会带上全部键）"""
        db = MemorySession()
        db.seed(make_plugin(author_name="张三", author_avatar_url="https://cdn.example.com/a.png"))

        await admin_plugin_service.update_plugin(db, 1, {"name": "改名后"}, ADMIN)
        row = db.all_rows(Plugin)[0]
        self.assertEqual(row["name"], "改名后")
        self.assertEqual(row["author_name"], "张三", "只改名字不该清掉作者")
        self.assertEqual(row["author_avatar_url"], "https://cdn.example.com/a.png")


class AdminGetterAuthorFieldsTests(unittest.IsolatedAsyncioTestCase):
    """后台编辑页的回填来源 `admin_plugin_service.get_plugin` 必须下发作者字段。

    本文件其余用例只测「公开读 + 后台写」，从未调用过 `get_plugin`
    （第五轮审查 R5-18）。少这一条的后果不是「详情页缺字段」那么轻：
    编辑页靠它把已有作者回填进表单，回填不到就是空 → 表单空 → 保存提交空串
    → 后端按「清除」写 NULL，与 R5-01 那条表单缺陷串成**运营改一次标题就永久清空作者**。
    """

    def setUp(self):
        # 与下方写作用例同一处理：写操作末尾会 invalidate_plugins()（摸 Redis），
        # 本类只关心字段能否读回来
        patcher = mock.patch.object(admin_plugin_service, "invalidate_plugins", mock.AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_admin_getter_returns_author_fields(self):
        db = MemorySession()
        db.seed(make_plugin(author_name="张三", author_avatar_url="https://cdn.example.com/a.png"))
        data = await admin_plugin_service.get_plugin(db, 1)
        self.assertEqual(data["author_name"], "张三", "编辑页回填拿不到就会把已有作者清掉")
        self.assertEqual(data["author_avatar_url"], "https://cdn.example.com/a.png")

    async def test_round_trip_keeps_author(self):
        """「admin 读 → 原样写回」不得丢字段（表单提交的就是读到的那份）"""
        db = MemorySession()
        db.seed(make_plugin(author_name="张三", author_avatar_url="https://cdn.example.com/a.png"))
        loaded = await admin_plugin_service.get_plugin(db, 1)
        await admin_plugin_service.update_plugin(
            db, 1,
            {"name": loaded["name"], "author_name": loaded["author_name"],
             "author_avatar_url": loaded["author_avatar_url"]},
            ADMIN,
        )
        after = await admin_plugin_service.get_plugin(db, 1)
        self.assertEqual(after["author_name"], "张三")
        self.assertEqual(after["author_avatar_url"], "https://cdn.example.com/a.png")


if __name__ == "__main__":
    unittest.main()
