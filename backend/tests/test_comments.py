"""内容评论测试（缺口台账 P1-9；2026-10-10 增加回复与配图；2026-10-12 泛化目标类型）。

评论是**公开可见的 UGC**，风险等级与昵称/简介同级。这里守七条底线：

1. **机审必须拦得住**：敏感词命中且处置为 reject 时评论不得入库，
   且**必须留痕**（管理员要能看到有人在建站引流）。
2. **软删除生效**：删除后列表查不到，但行还在（备案站点要求可追溯、可恢复）。
3. **不能删别人的评论**：越权必须 403。返回 404 会让用户误以为评论已消失。
4. **内容删除不留孤儿**：内容是硬删除且评论无外键级联，不同步清理就会
   重演 P1-7 那类孤儿数据问题。
5. **只做两级回复**：回复「回复」必须挂到顶层父评论，否则前端无处渲染；
   **删顶层评论必须连带软删回复**，否则列表里会留下挂在不存在父评论下的回复。
6. **配图只收本站域名**：`images` 来自客户端，不校验就等于把评论区
   开放成任意图床（追踪像素 / 盗链）。
7. **多态目标互不串数据**（2026-10-12）：教程 / 节点 / 资源可能**共用同一 id**，
   列表 / 清理 / 回复归属只要漏掉 `target_type` 就会互相污染。

测试全部离线（CI 无 MySQL/Redis），用 tests/fake_db.py 的内存会话。
"""

import unittest

from app.api.comments import resolve_comment_target
from app.core.exceptions import AppException
from app.models.comment import (
    COMMENT_MAX_IMAGES,
    COMMENT_TARGET_NODE,
    COMMENT_TARGET_RESOURCE,
    ContentComment,
)
from app.models.node import Node
from app.models.moderation import (
    MAX_MODERATION_LOGS_PER_USER_PER_DAY,
    ContentModerationLog,
)
from app.models.resource import Resource
from app.models.tutorial import Tutorial
from app.models.user import User
from app.services import comment_service
from app.services.storage import storage
from tests.fake_db import MemorySession


def make_user(user_id: int = 1) -> User:
    return User(id=user_id, account=f"u{user_id}@example.com", password_hash="x", role="user")


def make_tutorial(tutorial_id: int = 100) -> Tutorial:
    t = Tutorial(id=tutorial_id, title="测试教程", description="简介", content="正文", category="ai")
    t.is_published = True
    return t


def make_node(node_id: int = 100) -> Node:
    return Node(id=node_id, name="测试节点", category="core", description="节点简介", content="正文")


def make_resource(resource_id: int = 100) -> Resource:
    return Resource(
        id=resource_id, name="测试资源", description="资源简介",
        content="正文", resource_type="model", file_size="1GB",
    )



class CreateCommentTests(unittest.IsolatedAsyncioTestCase):
    """发布评论：机审与校验"""

    async def test_clean_comment_created(self):
        db = MemorySession()
        user = make_user()
        tutorial = make_tutorial()
        db.seed(user, tutorial)
        result = await comment_service.create_comment(db, user, "tutorial", tutorial.id, "这篇讲得很清楚")
        self.assertEqual(result["content"], "这篇讲得很清楚")
        self.assertEqual(result["user_id"], user.id)

    async def test_blank_content_rejected(self):
        db = MemorySession()
        user, tutorial = make_user(), make_tutorial()
        db.seed(user, tutorial)
        for bad in ("", "   ", None):
            with self.assertRaises(AppException, msg=f"空评论必须被拒: {bad!r}"):
                await comment_service.create_comment(db, user, "tutorial", tutorial.id, bad)

    async def test_sensitive_word_rejected_and_logged(self):
        """命中敏感词：评论不入库，但留痕必须留下（运营要看谁在建站引流）"""
        db = MemorySession()
        user, tutorial = make_user(), make_tutorial()
        db.seed(user, tutorial)
        with self.assertRaises(AppException) as ctx:
            await comment_service.create_comment(db, user, "tutorial", tutorial.id, "有需要的加微信详聊")
        self.assertEqual(ctx.exception.code, 400)
        # 留痕已提交（reject 分支单独 commit）
        from app.models.moderation import ContentModerationLog

        logs = db.all_rows(ContentModerationLog)
        self.assertTrue(logs, "敏感词命中必须留痕")
        self.assertEqual(logs[0]["content_type"], "comment")
        # 评论本体未入库
        self.assertEqual(db.all_rows(ContentComment), [])

    async def test_nonexistent_tutorial_rejected(self):
        db = MemorySession()
        user = make_user()
        db.seed(user)
        with self.assertRaises(Exception):
            await comment_service.create_comment(db, user, "tutorial", 999999, "评论内容")


class DeleteCommentTests(unittest.IsolatedAsyncioTestCase):
    """删除：软删除 + 越权防护"""

    async def _seed_comment(self):
        db = MemorySession()
        user, other, tutorial = make_user(1), make_user(2), make_tutorial()
        db.seed(user, other, tutorial)
        comment = await comment_service.create_comment(db, user, "tutorial", tutorial.id, "我的评论")
        return db, user, other, tutorial, comment

    async def test_owner_can_delete_and_it_disappears(self):
        db, user, _other, tutorial, comment = await self._seed_comment()
        await comment_service.delete_comment(db, comment["id"], user.id)
        listing = await comment_service.list_comments(db, "tutorial", tutorial.id)
        self.assertEqual(listing["total"], 0, "删除后不应出现在前台列表")
        # 软删除：行仍在（可追溯、可恢复）
        self.assertEqual(len(db.all_rows(ContentComment)), 1)

    async def test_cannot_delete_others_comment(self):
        db, _user, other, _tutorial, comment = await self._seed_comment()
        with self.assertRaises(AppException) as ctx:
            await comment_service.delete_comment(db, comment["id"], other.id)
        self.assertEqual(ctx.exception.code, 403, "删他人评论必须是 403 而不是 404")

    async def test_admin_can_delete_any_comment(self):
        db, _user, other, tutorial, comment = await self._seed_comment()
        await comment_service.delete_comment(db, comment["id"], other.id, is_admin=True)
        listing = await comment_service.list_comments(db, "tutorial", tutorial.id)
        self.assertEqual(listing["total"], 0)

    async def test_restore_brings_comment_back(self):
        db, user, _other, tutorial, comment = await self._seed_comment()
        await comment_service.delete_comment(db, comment["id"], user.id)
        await comment_service.restore_comment(db, comment["id"])
        listing = await comment_service.list_comments(db, "tutorial", tutorial.id)
        self.assertEqual(listing["total"], 1, "恢复后应重新可见")

    async def test_delete_missing_comment_raises(self):
        db = MemorySession()
        with self.assertRaises(Exception):
            await comment_service.delete_comment(db, 12345, 1)


class SerializationTests(unittest.IsolatedAsyncioTestCase):
    """输出字段：前台不该看到内部管理字段"""

    async def test_public_list_hides_internal_fields(self):
        db = MemorySession()
        user, tutorial = make_user(), make_tutorial()
        db.seed(user, tutorial)
        await comment_service.create_comment(db, user, "tutorial", tutorial.id, "公开评论")
        listing = await comment_service.list_comments(db, "tutorial", tutorial.id)
        for key in ("deleted_at", "is_deleted", "author_account"):
            self.assertNotIn(key, listing["items"][0], f"前台输出不应包含 {key}")

    async def test_admin_list_exposes_internal_fields(self):
        db = MemorySession()
        user, tutorial = make_user(), make_tutorial()
        db.seed(user, tutorial)
        await comment_service.create_comment(db, user, "tutorial", tutorial.id, "后台可见评论")
        listing = await comment_service.admin_list_comments(db)
        item = listing["items"][0]
        self.assertIn("is_deleted", item)
        self.assertEqual(item["target_title"], "测试教程")


class ReplyTests(unittest.IsolatedAsyncioTestCase):
    """两级回复：挂载规则、越界校验、列表嵌套"""

    async def _seed(self):
        db = MemorySession()
        u1, u2, tutorial = make_user(1), make_user(2), make_tutorial()
        db.seed(u1, u2, tutorial)
        parent = await comment_service.create_comment(db, u1, "tutorial", tutorial.id, "顶层评论")
        return db, u1, u2, tutorial, parent

    async def test_reply_nested_under_parent(self):
        db, _u1, u2, tutorial, parent = await self._seed()
        await comment_service.create_comment(db, u2, "tutorial", tutorial.id, "回复一下", parent_id=parent["id"])
        listing = await comment_service.list_comments(db, "tutorial", tutorial.id)
        self.assertEqual(listing["total"], 1, "分页只统计顶层评论，回复不占页码")
        item = listing["items"][0]
        self.assertEqual(item["reply_count"], 1)
        self.assertEqual([r["content"] for r in item["replies"]], ["回复一下"])
        self.assertEqual(item["replies"][0]["parent_id"], parent["id"])

    async def test_reply_to_reply_attaches_to_top_level(self):
        """只做两级：回复「回复」时必须挂到顶层父评论，否则前端无处渲染"""
        db, u1, u2, tutorial, parent = await self._seed()
        first = await comment_service.create_comment(
            db, u2, "tutorial", tutorial.id, "一级回复", parent_id=parent["id"]
        )
        second = await comment_service.create_comment(
            db, u1, "tutorial", tutorial.id, "回复的回复", parent_id=first["id"]
        )
        self.assertEqual(second["parent_id"], parent["id"], "二级回复必须挂到顶层父评论")

        item = (await comment_service.list_comments(db, "tutorial", tutorial.id))["items"][0]
        self.assertEqual(len(item["replies"]), 2)
        self.assertEqual(item["reply_count"], 2)

    async def test_reply_to_other_tutorial_rejected(self):
        db, _u1, u2, _tutorial, parent = await self._seed()
        other = make_tutorial(200)
        db.seed(other)
        with self.assertRaises(AppException):
            await comment_service.create_comment(
                db, u2, "tutorial", other.id, "跨教程回复", parent_id=parent["id"]
            )

    async def test_reply_to_deleted_parent_rejected(self):
        db, u1, u2, tutorial, parent = await self._seed()
        await comment_service.delete_comment(db, parent["id"], u1.id)
        with self.assertRaises(Exception):
            await comment_service.create_comment(
                db, u2, "tutorial", tutorial.id, "回复已删除的评论", parent_id=parent["id"]
            )

    async def test_reply_to_missing_parent_rejected(self):
        db, _u1, u2, tutorial, _parent = await self._seed()
        with self.assertRaises(Exception):
            await comment_service.create_comment(
                db, u2, "tutorial", tutorial.id, "回复不存在的评论", parent_id=999999
            )

    async def test_deleting_parent_hides_replies(self):
        """删顶层评论必须连带软删回复 —— 否则会留下挂在不存在父评论下的孤儿回复"""
        db, u1, u2, tutorial, parent = await self._seed()
        await comment_service.create_comment(
            db, u2, "tutorial", tutorial.id, "会被连带删除的回复", parent_id=parent["id"]
        )
        await comment_service.delete_comment(db, parent["id"], u1.id)

        listing = await comment_service.list_comments(db, "tutorial", tutorial.id)
        self.assertEqual(listing["total"], 0)
        self.assertEqual(listing["items"], [])
        # 软删除：两行都还在（可追溯），且都已置 deleted_at
        rows = db.all_rows(ContentComment)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["deleted_at"] for r in rows), "回复也必须被连带软删")

    async def test_deleting_reply_does_not_touch_parent(self):
        db, u1, u2, tutorial, parent = await self._seed()
        reply = await comment_service.create_comment(
            db, u2, "tutorial", tutorial.id, "回复", parent_id=parent["id"]
        )
        await comment_service.delete_comment(db, reply["id"], u2.id)
        listing = await comment_service.list_comments(db, "tutorial", tutorial.id)
        self.assertEqual(listing["total"], 1, "父评论必须仍然可见")
        self.assertEqual(listing["items"][0]["reply_count"], 0)


class CommentImageTests(unittest.IsolatedAsyncioTestCase):
    """配图：域名白名单 + 数量上限 + 允许只发图不写字"""

    def setUp(self):
        # 用本服务真实会产出的前缀构造合法 URL（CDN 或默认 COS 域名，取决于配置）
        self.prefix = storage.public_url_prefixes()[0]
        self.url = f"{self.prefix}comments/2026/10/10/abc123.jpg"

    async def test_image_only_comment_allowed(self):
        """截图提问很常见，强制写字只会让人随便敲几个字凑数"""
        db = MemorySession()
        user, tutorial = make_user(), make_tutorial()
        db.seed(user, tutorial)
        result = await comment_service.create_comment(db, user, "tutorial", tutorial.id, "", images=[self.url])
        self.assertEqual(result["images"], [self.url])
        self.assertEqual(result["content"], "")

    async def test_external_image_url_rejected(self):
        """不校验域名 = 把评论区开放成任意图床（追踪像素 / 盗链）"""
        db = MemorySession()
        user, tutorial = make_user(), make_tutorial()
        db.seed(user, tutorial)
        with self.assertRaises(AppException) as ctx:
            await comment_service.create_comment(
                db, user, "tutorial", tutorial.id, "看图", images=["https://evil.example.com/x.jpg"]
            )
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(db.all_rows(ContentComment), [], "非法配图不得入库")

    async def test_too_many_images_rejected(self):
        db = MemorySession()
        user, tutorial = make_user(), make_tutorial()
        db.seed(user, tutorial)
        urls = [f"{self.prefix}comments/a{i}.jpg" for i in range(COMMENT_MAX_IMAGES + 1)]
        with self.assertRaises(AppException):
            await comment_service.create_comment(db, user, "tutorial", tutorial.id, "多图", images=urls)

    async def test_duplicate_images_deduped(self):
        db = MemorySession()
        user, tutorial = make_user(), make_tutorial()
        db.seed(user, tutorial)
        result = await comment_service.create_comment(
            db, user, "tutorial", tutorial.id, "重复图", images=[self.url, self.url]
        )
        self.assertEqual(result["images"], [self.url], "同一张图重复提交应去重而非报错")

    async def test_empty_text_and_no_image_rejected(self):
        db = MemorySession()
        user, tutorial = make_user(), make_tutorial()
        db.seed(user, tutorial)
        for content, images in (("   ", []), ("", None)):
            with self.assertRaises(AppException, msg=f"空评论必须被拒: {content!r}/{images!r}"):
                await comment_service.create_comment(db, user, "tutorial", tutorial.id, content, images=images)

    async def test_reply_can_carry_images(self):
        db = MemorySession()
        user, tutorial = make_user(), make_tutorial()
        db.seed(user, tutorial)
        parent = await comment_service.create_comment(db, user, "tutorial", tutorial.id, "顶层")
        reply = await comment_service.create_comment(
            db, user, "tutorial", tutorial.id, "带图的回复", parent_id=parent["id"], images=[self.url]
        )
        self.assertEqual(reply["images"], [self.url])
        item = (await comment_service.list_comments(db, "tutorial", tutorial.id))["items"][0]
        self.assertEqual(item["replies"][0]["images"], [self.url])


class PurgeTests(unittest.IsolatedAsyncioTestCase):
    """教程删除连带清理：不留孤儿评论"""

    async def test_purge_removes_comments_of_deleted_tutorials(self):
        db = MemorySession()
        user, tutorial = make_user(), make_tutorial(100)
        db.seed(user, tutorial)
        await comment_service.create_comment(db, user, "tutorial", 100, "要被清理的评论")
        self.assertEqual(len(db.all_rows(ContentComment)), 1)

        removed = await comment_service.purge_comments_for_targets(db, "tutorial", [100])
        self.assertEqual(removed, 1)
        self.assertEqual(db.all_rows(ContentComment), [], "教程删除后不应残留评论")

    async def test_purge_does_not_touch_other_tutorials(self):
        """清理条件漏掉教程筛选会把别的内容的评论一起删掉"""
        db = MemorySession()
        user = make_user()
        t1, t2 = make_tutorial(100), make_tutorial(200)
        db.seed(user, t1, t2)
        await comment_service.create_comment(db, user, "tutorial", 100, "教程100的评论")
        await comment_service.create_comment(db, user, "tutorial", 200, "教程200的评论")

        removed = await comment_service.purge_comments_for_targets(db, "tutorial", [100])
        self.assertEqual(removed, 1)
        left = db.all_rows(ContentComment)
        self.assertEqual(len(left), 1, "只应清理指定教程的评论")
        self.assertEqual(left[0]["target_id"], 200)

    async def test_purge_empty_is_noop(self):
        db = MemorySession()
        self.assertEqual(await comment_service.purge_comments_for_targets(db, "tutorial", []), 0)


class MultiTargetTests(unittest.IsolatedAsyncioTestCase):
    """多态目标（2026-10-12 泛化）：教程 / 节点 / 资源评论必须完全隔离。

    最容易出错的地方是**多种内容共用同一个 id**：教程 100、节点 100、资源 100 都存在，
    只要列表 / 清理 / 回复归属的条件漏掉 `target_type`，数据就会互相串——
    与 P1-7「清理收藏时漏掉 target_type」是同一类事故。故本类刻意让三者同 id。
    """

    async def _seed(self):
        db = MemorySession()
        user, tutorial, node, resource = make_user(), make_tutorial(100), make_node(100), make_resource(100)
        db.seed(user, tutorial, node, resource)
        return db, user, tutorial, node, resource

    async def test_node_comment_created_and_listed(self):
        db, user, _tutorial, node, _resource = await self._seed()
        result = await comment_service.create_comment(
            db, user, COMMENT_TARGET_NODE, node.id, "这个节点讲得很清楚"
        )
        self.assertEqual(result["target_type"], COMMENT_TARGET_NODE)
        self.assertEqual(result["target_id"], node.id)

        listing = await comment_service.list_comments(db, COMMENT_TARGET_NODE, node.id)
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["items"][0]["content"], "这个节点讲得很清楚")

    async def test_resource_comment_created_and_listed(self):
        """资源评论（2026-10-12 新增的第三种可评论内容）"""
        db, user, _tutorial, _node, resource = await self._seed()
        result = await comment_service.create_comment(
            db, user, COMMENT_TARGET_RESOURCE, resource.id, "这个资源很好用"
        )
        self.assertEqual(result["target_type"], COMMENT_TARGET_RESOURCE)
        self.assertEqual(result["target_id"], resource.id)

        listing = await comment_service.list_comments(db, COMMENT_TARGET_RESOURCE, resource.id)
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["items"][0]["content"], "这个资源很好用")

    async def test_same_id_does_not_leak_between_types(self):
        """同 id 的教程 / 节点 / 资源评论必须各归各的"""
        db, user, tutorial, node, resource = await self._seed()
        await comment_service.create_comment(db, user, "tutorial", tutorial.id, "教程评论")
        await comment_service.create_comment(db, user, COMMENT_TARGET_NODE, node.id, "节点评论")
        await comment_service.create_comment(db, user, COMMENT_TARGET_RESOURCE, resource.id, "资源评论")

        t_list = await comment_service.list_comments(db, "tutorial", tutorial.id)
        n_list = await comment_service.list_comments(db, COMMENT_TARGET_NODE, node.id)
        r_list = await comment_service.list_comments(db, COMMENT_TARGET_RESOURCE, resource.id)
        self.assertEqual([i["content"] for i in t_list["items"]], ["教程评论"])
        self.assertEqual([i["content"] for i in n_list["items"]], ["节点评论"])
        self.assertEqual([i["content"] for i in r_list["items"]], ["资源评论"])

    async def test_reply_cannot_cross_target_types(self):
        """跨内容回复必须拒绝：否则回复会挂到另一棵评论树上，界面上永远看不到"""
        db, user, tutorial, node, resource = await self._seed()
        parent = await comment_service.create_comment(db, user, "tutorial", tutorial.id, "教程顶层")
        for target_type, target_id in (
            (COMMENT_TARGET_NODE, node.id),
            (COMMENT_TARGET_RESOURCE, resource.id),
        ):
            with self.subTest(target=target_type):
                with self.assertRaises(AppException) as ctx:
                    await comment_service.create_comment(
                        db, user, target_type, target_id, "跨内容回复", parent_id=parent["id"]
                    )
                self.assertEqual(ctx.exception.code, 400)

    async def test_node_reply_nested_under_node_parent(self):
        db, user, _tutorial, node, _resource = await self._seed()
        parent = await comment_service.create_comment(db, user, COMMENT_TARGET_NODE, node.id, "节点顶层")
        await comment_service.create_comment(
            db, user, COMMENT_TARGET_NODE, node.id, "节点回复", parent_id=parent["id"]
        )
        listing = await comment_service.list_comments(db, COMMENT_TARGET_NODE, node.id)
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["items"][0]["reply_count"], 1)

    async def test_unknown_target_type_rejected(self):
        db, user, _tutorial, _node, _resource = await self._seed()
        with self.assertRaises(AppException) as ctx:
            await comment_service.create_comment(db, user, "unknown_type", 1, "不支持的评论目标")
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(db.all_rows(ContentComment), [], "非法目标类型不得入库")

    async def test_nonexistent_node_rejected(self):
        db, user, _tutorial, _node, _resource = await self._seed()
        with self.assertRaises(Exception):
            await comment_service.create_comment(db, user, COMMENT_TARGET_NODE, 999999, "评论内容")

    async def test_nonexistent_resource_rejected(self):
        db, user, _tutorial, _node, _resource = await self._seed()
        with self.assertRaises(Exception):
            await comment_service.create_comment(db, user, COMMENT_TARGET_RESOURCE, 999999, "评论内容")

    async def test_purge_only_removes_matching_target_type(self):
        """清理条件漏掉 target_type 就会把同 id 的其它内容评论一起删掉"""
        db, user, tutorial, node, resource = await self._seed()
        await comment_service.create_comment(db, user, "tutorial", tutorial.id, "教程评论")
        await comment_service.create_comment(db, user, COMMENT_TARGET_NODE, node.id, "节点评论")
        await comment_service.create_comment(db, user, COMMENT_TARGET_RESOURCE, resource.id, "资源评论")

        removed = await comment_service.purge_comments_for_targets(db, COMMENT_TARGET_RESOURCE, [resource.id])
        self.assertEqual(removed, 1)
        left = {(r["target_type"], r["content"]) for r in db.all_rows(ContentComment)}
        self.assertEqual(
            left, {("tutorial", "教程评论"), (COMMENT_TARGET_NODE, "节点评论")},
            "只应清理资源评论",
        )

    async def test_admin_list_shows_target_title_per_type(self):
        """后台列表要能分辨「这条评论属于教程 / 节点 / 资源」，标题按类型回查"""
        db, user, tutorial, node, resource = await self._seed()
        await comment_service.create_comment(db, user, "tutorial", tutorial.id, "教程评论")
        await comment_service.create_comment(db, user, COMMENT_TARGET_NODE, node.id, "节点评论")
        await comment_service.create_comment(db, user, COMMENT_TARGET_RESOURCE, resource.id, "资源评论")

        listing = await comment_service.admin_list_comments(db)
        by_type = {i["target_type"]: i for i in listing["items"]}
        self.assertEqual(by_type["tutorial"]["target_title"], "测试教程")
        self.assertEqual(by_type[COMMENT_TARGET_NODE]["target_title"], "测试节点")
        self.assertEqual(by_type[COMMENT_TARGET_RESOURCE]["target_title"], "测试资源")

    async def test_admin_list_filter_by_target(self):
        db, user, tutorial, node, resource = await self._seed()
        await comment_service.create_comment(db, user, "tutorial", tutorial.id, "教程评论")
        await comment_service.create_comment(db, user, COMMENT_TARGET_NODE, node.id, "节点评论")
        await comment_service.create_comment(db, user, COMMENT_TARGET_RESOURCE, resource.id, "资源评论")

        listing = await comment_service.admin_list_comments(
            db, target_type=COMMENT_TARGET_RESOURCE, target_id=resource.id
        )
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["items"][0]["content"], "资源评论")


class CommentTargetParamTests(unittest.TestCase):
    """目标参数契约（路由层解析）：GET 与 POST 共用同一份解析函数。

    这是前端唯一依赖的目标契约，故单独钉住。重点是「非法目标必须在碰数据库
    之前就被拒」——否则任意字符串都会变成一次无意义的查询。
    """

    def test_target_type_and_id_used_directly(self):
        self.assertEqual(resolve_comment_target("node", 7, None), ("node", 7))
        self.assertEqual(resolve_comment_target("tutorial", 7, None), ("tutorial", 7))
        # 资源是 2026-10-12 新增的第三种可评论内容
        self.assertEqual(resolve_comment_target("resource", 7, None), ("resource", 7))

    def test_missing_target_id_rejected(self):
        for t, i in (("tutorial", None), (None, None), ("", 1)):
            with self.assertRaises(AppException, msg=f"必须拒绝: {t!r}/{i!r}"):
                resolve_comment_target(t, i, None)

    def test_unknown_target_type_rejected(self):
        with self.assertRaises(AppException) as ctx:
            resolve_comment_target("unknown_type", 1, None)
        self.assertEqual(ctx.exception.code, 400)

    def test_legacy_tutorial_id_bridged(self):
        """过渡期兼容：旧 bundle 只发 tutorial_id 时翻译成 (tutorial, id)"""
        self.assertEqual(resolve_comment_target(None, None, 42), ("tutorial", 42))
        self.assertEqual(resolve_comment_target("", None, 42), ("tutorial", 42))

    def test_explicit_target_wins_over_legacy(self):
        """两个都传时以新参数为准（兼容层不能反过来覆盖新契约）"""
        self.assertEqual(resolve_comment_target("node", 7, 42), ("node", 7))


class CommentReadVisibilityTests(unittest.IsolatedAsyncioTestCase):
    """读侧必须与写侧同一套可见性口径（第五轮审查 R5-08）。"""

    async def test_unpublished_tutorial_comments_not_readable(self):
        """教程下架后评论区必须一起关闭。

        原实现只在**创建侧**用 `extra_conditions` 要求已发布，读侧一条校验都没有：
        `GET /tutorials/{id}` 立刻 404，而 `GET /comments?target_type=tutorial&target_id=<id>`
        仍把整条讨论（正文 / 配图 URL / 昵称 / 头像 / user_id / 时间）匿名分页吐出去。
        运营点下的「下架」对评论区没有生效，而这些是用户自己写的可识别信息。
        """
        db = MemorySession()
        user, tutorial = make_user(), make_tutorial(100)
        db.seed(user, tutorial)
        await comment_service.create_comment(db, user, "tutorial", tutorial.id, "下架前发的评论")

        # 转草稿（等同下架）
        db.all_rows(Tutorial)[0]["is_published"] = False

        listing = await comment_service.list_comments(db, "tutorial", tutorial.id)
        self.assertEqual(listing["total"], 0, "内容不可见时评论区必须同步不可见")
        self.assertEqual(listing["items"], [])
        # 仍是 200 的分页形状：旧 bundle 只读 items/total，不该因为改成 404 而白屏
        self.assertEqual(set(listing), {"items", "total", "page", "page_size"})

    async def test_missing_target_returns_empty_not_error(self):
        """目标根本不存在时同样返回空分页，而不是 500 或泄漏"""
        db = MemorySession()
        db.seed(make_user(), make_tutorial(100))
        listing = await comment_service.list_comments(db, "tutorial", 999999)
        self.assertEqual(listing["total"], 0)

    async def test_published_tutorial_still_readable(self):
        """反向对照：可见性校验没把正常路径一起关掉（否则上面两条会假绿）"""
        db = MemorySession()
        user, tutorial = make_user(), make_tutorial(100)
        db.seed(user, tutorial)
        await comment_service.create_comment(db, user, "tutorial", tutorial.id, "正常可见")
        listing = await comment_service.list_comments(db, "tutorial", tutorial.id)
        self.assertEqual(listing["total"], 1)


class CommentPagingTiebreakTests(unittest.IsolatedAsyncioTestCase):
    """同秒评论必须跨页不重不漏（第五轮审查 R5-19）。"""

    async def test_same_created_at_pages_without_overlap(self):
        import datetime as dt

        db = MemorySession()
        user, tutorial = make_user(), make_tutorial(100)
        db.seed(user, tutorial)
        # 三条 created_at **完全相同**的顶层评论：批量导入或同秒连发才会出现这种数据，
        # 而既有用例一律把时间戳错开，结构上测不到这个分支
        same = dt.datetime(2026, 10, 12, 3, 4, 5)
        ids = []
        for i in range(3):
            c = ContentComment(
                id=700000 + i, target_type="tutorial", target_id=tutorial.id,
                user_id=user.id, content=f"同秒评论{i}", created_at=same,
            )
            db.seed(c)
            ids.append(c.id)

        p1 = await comment_service.list_comments(db, "tutorial", tutorial.id, page=1, page_size=2)
        p2 = await comment_service.list_comments(db, "tutorial", tutorial.id, page=2, page_size=2)
        got = [i["id"] for i in p1["items"]] + [i["id"] for i in p2["items"]]
        self.assertEqual(len(got), 3, f"跨页共取到 {len(got)} 条，应为 3")
        self.assertEqual(len(set(got)), 3, f"同秒评论跨页重复了：{got}")
        self.assertEqual(got, sorted(ids, reverse=True), "并列时必须按 id 倒序，顺序要可预期")


class ModerationLogCapTests(unittest.IsolatedAsyncioTestCase):
    """被拒提交不得无限写留痕（第五轮审查 R5-11）。"""

    async def test_rejected_flood_is_bounded(self):
        db = MemorySession()
        user, tutorial = make_user(), make_tutorial(100)
        db.seed(user, tutorial)

        # 同一条命中基线词库的正文反复提交：评论一行都进不去，原先留痕却每请求 +1
        bad = "加微信 13800000000 有优惠"
        attempts = MAX_MODERATION_LOGS_PER_USER_PER_DAY + 5
        for _ in range(attempts):
            with self.assertRaises(AppException):
                await comment_service.create_comment(db, user, "tutorial", tutorial.id, bad)

        rows = db.all_rows(ContentModerationLog)
        self.assertEqual(
            len(rows), MAX_MODERATION_LOGS_PER_USER_PER_DAY,
            f"留痕应被每用户每日上界拦住，实际写了 {len(rows)} 行",
        )
        self.assertEqual(len(db.all_rows(ContentComment)), 0, "被拒内容始终不得入库")

    async def test_nonexistent_target_writes_no_log(self):
        """目标是垃圾时连留痕都不写（校验顺序调整后成立）"""
        db = MemorySession()
        db.seed(make_user(), make_tutorial(100))
        with self.assertRaises(AppException):
            await comment_service.create_comment(db, make_user(), "tutorial", 999999, "加微信 13800000000")
        self.assertEqual(db.all_rows(ContentModerationLog), [], "目标不存在时不应留下留痕")


if __name__ == "__main__":
    unittest.main()
