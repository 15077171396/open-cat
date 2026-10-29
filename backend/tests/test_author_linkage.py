"""作者展示「联动」契约：改昵称/换头像后，已发布内容上的作者要跟着变（2026-10-13）。

背景（用户反馈）：
    「为什么我后台改信息了，发布者和头像没有联动」

根因：作者此前只以 `author_name` / `author_avatar_url` **快照**存在内容行里，
    没有任何指向账号的引用，所以改资料不会回写历史内容。

修复：三张内容表新增 `author_id`（迁移 `20261013_0043`），
    前台展示**以 author_id 指向的账号为准**（实时解析），快照仅作兜底。

本文件钉住三件事：
1. 账号存在 → 展示**账号当前**昵称/头像（哪怕快照是旧值）；
2. 账号已删 → 回落**快照**（内容不会因此变成「无作者」）；
3. 没有 author_id（第三方导入带出的原文作者，站内无账号）→ 用快照。
三个模块（plugin / resource / node）各验一遍，避免只改一处。
"""

import unittest
from datetime import datetime

from app.models.node import Node, Plugin
from app.models.resource import Resource
from app.models.user import User
from app.services import node_service, plugin_service, resource_service
from tests.fake_db import MemorySession

AUTHOR_ID = 4


def make_author(nickname="新昵称", avatar="https://cdn.example.com/new.png") -> User:
    return User(id=AUTHOR_ID, account="author@test", nickname=nickname, avatar_url=avatar)


def make_node(author_id=None, author_name="旧昵称", author_avatar_url=None) -> Node:
    return Node(
        id=1, name="KSampler", category="采样", description="简介", view_count=1,
        updated_at=datetime(2026, 10, 13, 10, 0),
        author_id=author_id, author_name=author_name, author_avatar_url=author_avatar_url,
    )


def make_resource(author_id=None, author_name="旧昵称", author_avatar_url=None) -> Resource:
    return Resource(
        id=1, name="整合包", description="简介", resource_type="model", file_size="1GB",
        download_count=0, view_count=1, updated_at=datetime(2026, 10, 13, 10, 0),
        author_id=author_id, author_name=author_name, author_avatar_url=author_avatar_url,
    )


def make_plugin(author_id=None, author_name="旧昵称", author_avatar_url=None) -> Plugin:
    return Plugin(
        id=1, name="ComfyUI Manager", category="comfyui", description="简介", view_count=1,
        author_id=author_id, author_name=author_name, author_avatar_url=author_avatar_url,
    )


class NodeAuthorLinkageTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_uses_live_account_values(self):
        """快照是旧昵称/无头像，但账号已改名换头像 → 展示账号当前值"""
        db = MemorySession()
        db.seed(make_author())
        db.seed(make_node(author_id=AUTHOR_ID, author_name="旧昵称", author_avatar_url=None))

        item = (await node_service.list_nodes(db, page=1, page_size=10))["items"][0]
        self.assertEqual(item["author_name"], "新昵称", "改昵称必须联动到已发布内容")
        self.assertEqual(item["author_avatar_url"], "https://cdn.example.com/new.png", "换头像必须联动")

    async def test_detail_uses_live_account_values(self):
        db = MemorySession()
        db.seed(make_author())
        db.seed(make_node(author_id=AUTHOR_ID, author_name="旧昵称"))

        data = await node_service.get_node_detail(db, 1)
        self.assertEqual(data["author_name"], "新昵称")
        self.assertEqual(data["author_avatar_url"], "https://cdn.example.com/new.png")

    async def test_falls_back_to_snapshot_when_account_deleted(self):
        """作者账号被删除 → 回落快照，内容不会变成「无作者」"""
        db = MemorySession()
        db.seed(make_node(author_id=99999, author_name="已注销的用户"))

        item = (await node_service.list_nodes(db, page=1, page_size=10))["items"][0]
        self.assertEqual(item["author_name"], "已注销的用户")
        self.assertIsNone(item["author_avatar_url"])

    async def test_keeps_snapshot_when_no_author_id(self):
        """无 author_id（第三方导入带出的原文作者，站内无账号）→ 用快照"""
        db = MemorySession()
        db.seed(make_author())
        db.seed(make_node(author_id=None, author_name="GitHub 上游作者"))

        item = (await node_service.list_nodes(db, page=1, page_size=10))["items"][0]
        self.assertEqual(item["author_name"], "GitHub 上游作者", "不能被发布账号的昵称覆盖")

    async def test_account_clearing_avatar_falls_back_to_letter(self):
        """账号把头像删了 → 下发 None（前台回落到首字兜底），而不是继续用旧快照头像"""
        db = MemorySession()
        db.seed(make_author(avatar=None))
        db.seed(make_node(author_id=AUTHOR_ID, author_name="旧昵称", author_avatar_url="https://cdn.example.com/old.png"))

        item = (await node_service.list_nodes(db, page=1, page_size=10))["items"][0]
        self.assertEqual(item["author_name"], "新昵称")
        self.assertIsNone(item["author_avatar_url"], "账号已无头像，不应继续展示旧快照头像")


class ResourceAuthorLinkageTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_uses_live_account_values(self):
        db = MemorySession()
        db.seed(make_author())
        db.seed(make_resource(author_id=AUTHOR_ID, author_name="旧昵称"))

        item = (await resource_service.get_resources(db, page=1, page_size=10))["items"][0]
        self.assertEqual(item["author_name"], "新昵称")
        self.assertEqual(item["author_avatar_url"], "https://cdn.example.com/new.png")

    async def test_detail_uses_live_account_values(self):
        db = MemorySession()
        db.seed(make_author())
        db.seed(make_resource(author_id=AUTHOR_ID, author_name="旧昵称"))

        data = await resource_service.get_resource_detail(db, 1)
        self.assertEqual(data["author_name"], "新昵称")


class PluginAuthorLinkageTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_uses_live_account_values(self):
        db = MemorySession()
        db.seed(make_author())
        db.seed(make_plugin(author_id=AUTHOR_ID, author_name="旧昵称"))

        item = (await plugin_service.list_plugins(db, page=1, page_size=10))["items"][0]
        self.assertEqual(item["author_name"], "新昵称")
        self.assertEqual(item["author_avatar_url"], "https://cdn.example.com/new.png")

    async def test_detail_uses_live_account_values(self):
        db = MemorySession()
        db.seed(make_author())
        db.seed(make_plugin(author_id=AUTHOR_ID, author_name="旧昵称"))

        data = await plugin_service.get_plugin_detail(db, 1)
        self.assertEqual(data["author_name"], "新昵称")


if __name__ == "__main__":
    unittest.main()
