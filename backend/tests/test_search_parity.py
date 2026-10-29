"""管理端 / 公开端检索行为一致性（第三轮增量审查 P1-41）。

背景
----
公开端节点/扩展搜索是「全文优先 + LIKE 兜底」，覆盖 name/description/content；
而管理端此前只写 ``Model.name.contains(...)``（**仅名称**）—— 同一句话在
前台搜得到、在后台搜不到，且两个文件几乎逐行同构、加字段时必然持续漂移。

修复后两侧共用 ``search.apply_keyword_filter``（一份行为）。本文件用真实
``MemorySession``（关闭全文走 LIKE 路径，避免 fake_db 不支持 MATCH 文本条件）
把「同一关键词两侧结论一致」钉死 —— 这正是漂移时期唯一会失败、而现在必须通过的场景。

⚠️ 本文件守的是**一致性**，不是「一定能搜到正文」：后者自第四轮全量审查 P1-60 起
在降级态被有意收窄（LIKE 不再扫 MEDIUMTEXT 正文，见 ``search._like_columns``）。
两侧口径变更要一起改，任何「一侧搜得到另一侧搜不到」的实现都会让这里变红。
"""

import unittest
from datetime import datetime, timezone
from unittest import mock

from app.config import settings
from app.models.node import Node, Plugin
from app.services import admin_node_service, admin_plugin_service, node_service, plugin_service
from tests.fake_db import MemorySession

_NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)


def _seed_node(**kwargs) -> Node:
    defaults = dict(
        id=1,
        name="ComfyUI Manager",
        category="comfyui",
        description="节点管理器",
        content="用于解决显存不足与依赖缺失",
        view_count=0,
        created_at=_NOW,
        updated_at=_NOW,
    )
    defaults.update(kwargs)
    return Node(**defaults)


def _seed_plugin(**kwargs) -> Plugin:
    defaults = dict(
        id=1,
        name="ComfyUI-Impact-Pack",
        category="comfyui",
        description="增强节点包",
        content="包含人脸修复与放大工作流",
        view_count=0,
        created_at=_NOW,
        updated_at=_NOW,
    )
    defaults.update(kwargs)
    return Plugin(**defaults)


class NodeSearchParityTests(unittest.IsolatedAsyncioTestCase):
    async def test_keyword_in_content_degrades_consistently_on_both_sides(self):
        """关键词只出现在正文：降级态（全文不可用）两侧都搜不到，但**结论必须一致**

        口径变更（第四轮全量审查 P1-60 方案 A）：LIKE 兜底不再扫 MEDIUMTEXT 正文，
        因为 `LIKE '%kw%'` 用不上索引，而这条路径要跑一次 COUNT + 一次分页 —— 罕见
        关键词等于两次全表扫，全程占住一条池连接（单机 15 条/worker）。

        P1-41 真正钉的是「同一句话不许前台搜得到、后台搜不到」—— 那一层仍然成立。
        全文索引可用时正文照旧命中（见 test_search.py 的
        `test_fulltext_condition_still_covers_content`）。
        """
        with mock.patch.object(settings, "SEARCH_FULLTEXT_ENABLED", False):
            db = MemorySession()
            db.seed(_seed_node())

            public = await node_service.list_nodes(db, page=1, page_size=10, keyword="显存不足")
            admin = await admin_node_service.list_nodes(db, page=1, page_size=10, keyword="显存不足")

        self.assertEqual(public["total"], admin["total"], "两侧结论不一致 = P1-41 的回归")
        self.assertEqual(public["total"], 0, "LIKE 兜底不该再扫正文（P1-60）")

    async def test_keyword_in_name_or_description_still_hits_on_both_sides(self):
        """收窄只影响正文：名称/简介命中不变 —— 这才是降级态下用户还能搜到的部分"""
        with mock.patch.object(settings, "SEARCH_FULLTEXT_ENABLED", False):
            db = MemorySession()
            db.seed(_seed_node())

            public = await node_service.list_nodes(db, page=1, page_size=10, keyword="管理器")
            admin = await admin_node_service.list_nodes(db, page=1, page_size=10, keyword="管理器")

        self.assertEqual(public["total"], 1)
        self.assertEqual(admin["total"], 1)

    async def test_no_keyword_keeps_full_list_on_both_sides(self):
        """无关键词时不得引入任何过滤（LIKE 条件为空时不能生成 AND NULL）"""
        db = MemorySession()
        db.seed(_seed_node(), _seed_node(id=2, name="Other"))

        public = await node_service.list_nodes(db, page=1, page_size=10)
        admin = await admin_node_service.list_nodes(db, page=1, page_size=10)

        self.assertEqual(public["total"], 2)
        self.assertEqual(admin["total"], 2)


class PluginSearchParityTests(unittest.IsolatedAsyncioTestCase):
    async def test_keyword_in_content_degrades_consistently_on_both_sides(self):
        """扩展模块同节点口径（P1-60）：降级态不搜正文，但两侧不许一个搜到一个搜不到"""
        with mock.patch.object(settings, "SEARCH_FULLTEXT_ENABLED", False):
            db = MemorySession()
            db.seed(_seed_plugin())

            public = await plugin_service.list_plugins(db, page=1, page_size=10, keyword="人脸修复")
            admin = await admin_plugin_service.list_plugins(db, page=1, page_size=10, keyword="人脸修复")

        self.assertEqual(public["total"], admin["total"], "两侧结论不一致 = P1-41 的回归")
        self.assertEqual(public["total"], 0, "LIKE 兜底不该再扫正文（P1-60）")

    async def test_totals_match_for_name_keyword(self):
        with mock.patch.object(settings, "SEARCH_FULLTEXT_ENABLED", False):
            db = MemorySession()
            db.seed(_seed_plugin())

            public = await plugin_service.list_plugins(db, page=1, page_size=10, keyword="Impact")
            admin = await admin_plugin_service.list_plugins(db, page=1, page_size=10, keyword="Impact")

        self.assertEqual(public["total"], admin["total"])


if __name__ == "__main__":
    unittest.main()