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

from app.models.user import User
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

if __name__ == "__main__":
    unittest.main()
