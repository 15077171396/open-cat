"""后台「上下架」横切动作的行为快照（第五轮审查 EXT-03 收口）。

`tests/test_admin_crud_matrix.py` 只验证「有没有调用」——它能发现「忘了通知收藏者」，
但发现不了「通知传错了 target_type」或「标题取错列」。这类错误的表现是：
下架一条资源，站内消息却写成「你收藏的教程已下架」，或者标题变成 `None`。

本文件用 fake session 记录**调用参数**，把 node / plugin / resource 三条
「已发布 → 下架」的路径钉住。删除侧的清理已有 `test_content_delete_orphans.py`
覆盖（含「不误伤同 id 的其它类型」），这里不重复。
"""

import unittest
from unittest import mock

from app.models.node import Node, Plugin
from app.models.resource import Resource
from app.models.user import User
from app.services import (
    admin_node_service as ans,
    admin_plugin_service as aps,
    admin_resource_service as ars,
)
from tests.fake_db import MemorySession

ADMIN = User(id=9001, account="admin@test")


class _Recorder:
    """记录 notify 的调用参数"""

    def __init__(self):
        self.calls = []

    async def __call__(self, db, *, target_type, target_id, title):
        self.calls.append({"target_type": target_type, "target_id": target_id, "title": title})
        return 0


def _patch_side_effects(module, recorder, invalidate_name: str):
    """把审计 / 缓存失效换成空实现，只留 notify 的录像。

    每个 admin service 只 import 自己那一个 `invalidate_*`，
    所以缓存失效的名字必须由调用方指定（`mock.patch.object` 对不存在的属性会抛）。
    """
    return [
        mock.patch.object(module, "notify_favoriters_unpublished", recorder),
        mock.patch.object(module, "record_admin_action", lambda *a, **k: None),
        mock.patch.object(module, invalidate_name, _noop),
    ]


async def _noop(*args, **kwargs):
    return None


class PublishTransitionNotifiesTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, module, model, seed_obj, fn_name, *, target_type, title_attr, invalidate):
        db = MemorySession()
        seed_obj.is_published = True
        db.seed(seed_obj)
        recorder = _Recorder()
        patchers = _patch_side_effects(module, recorder, invalidate)
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

        fn = getattr(module, fn_name)
        await fn(db, seed_obj.id, {"is_published": False}, ADMIN)

        self.assertEqual(len(recorder.calls), 1, "下架必须给收藏者发一条通知")
        call = recorder.calls[0]
        self.assertEqual(
            call["target_type"], target_type,
            f"{fn_name} 通知的 target_type 写错了 —— 收藏者会收到「你收藏的{call['target_type']}已下架」",
        )
        self.assertEqual(call["target_id"], seed_obj.id)
        self.assertEqual(
            call["title"], getattr(seed_obj, title_attr),
            "通知里的标题必须取该类型的标题列（node/plugin/resource 是 name，教程是 title）",
        )

    async def test_node_unpublish_notifies_with_correct_args(self):
        await self._run(
            ans, Node, Node(id=11, name="节点甲"), "update_node",
            target_type="node", title_attr="name", invalidate="invalidate_nodes",
        )

    async def test_plugin_unpublish_notifies_with_correct_args(self):
        await self._run(
            aps, Plugin, Plugin(id=12, name="扩展甲"), "update_plugin",
            target_type="plugin", title_attr="name", invalidate="invalidate_plugins",
        )

    async def test_resource_unpublish_notifies_with_correct_args(self):
        await self._run(
            ars, Resource, Resource(id=13, name="资源甲"), "update_resource",
            target_type="resource", title_attr="name", invalidate="invalidate_resources",
        )


class PublishTransitionSilentCasesTests(unittest.IsolatedAsyncioTestCase):
    """不该发通知的两种情况：改成「发布」、以及「不传 is_published」。"""

    async def _no_notify(self, module, model, seed_obj, fn_name, updates, invalidate="invalidate_nodes"):
        db = MemorySession()
        seed_obj.is_published = True
        db.seed(seed_obj)
        recorder = _Recorder()
        for p in _patch_side_effects(module, recorder, invalidate):
            p.start()
            self.addCleanup(p.stop)
        await getattr(module, fn_name)(db, seed_obj.id, updates, ADMIN)
        self.assertEqual(recorder.calls, [], f"{fn_name} 不该发下架通知：{updates}")

    async def test_publishing_does_not_notify(self):
        await self._no_notify(ans, Node, Node(id=21, name="节点乙"), "update_node", {"is_published": True})

    async def test_omitting_publish_state_does_not_notify(self):
        """`updates` 里没有 is_published 时，绝不能当成「改成 False」——
        那会让「编辑一下标题」变成「顺手下架」并给所有收藏者发通知。"""
        await self._no_notify(ans, Node, Node(id=22, name="节点丙"), "update_node", {"name": "改了标题"})

    async def test_draft_staying_draft_does_not_notify(self):
        db = MemorySession()
        node = Node(id=23, name="节点丁")
        node.is_published = False
        db.seed(node)
        recorder = _Recorder()
        for p in _patch_side_effects(ans, recorder, "invalidate_nodes"):
            p.start()
            self.addCleanup(p.stop)
        await ans.update_node(db, 23, {"is_published": False}, ADMIN)
        self.assertEqual(recorder.calls, [], "草稿保存草稿不该发通知")


if __name__ == "__main__":
    unittest.main()
