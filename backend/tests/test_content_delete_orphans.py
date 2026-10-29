"""内容删除的孤儿数据清理测试 (P1-7)。

⚠️ 2026-10-27 起教程/资源改为**软删除**（进回收站）：内容行保留、只打 deleted_at 并下架，
但「用户侧记录必须同事务清理」这条契约**完全不变**（否则用户列表里就是点开即 404 的死条目）。
本文件因此多了两条断言：内容行仍在 + 已下架。

收藏表与学习记录表存的是**快照**（标题/描述/封面），**没有外键**，因此教程 /
资源 / 插件 / 节点被硬删除时不会级联。残留行在用户侧就是「我的收藏」「我的学习」
里点开即 404 的死条目，而且用户没有任何途径自行清理——条目永远删不掉。

本文件钉死五条契约：
1. 删除教程 → 指向它的学习记录与收藏**同事务**清空；
2. 删除资源 / 插件 / 节点 → 指向它的收藏清空；
3. **不误伤**：同 id 的其它类型收藏、指向其它内容的收藏必须保留
   （清理条件漏掉 target_type 就会把 `resource#1` 当成 `tutorial#1` 一起删掉）；
4. 批量删除与单条删除口径一致。
5. **评论也要清**（2026-10-12 泛化后新增）：教程 / 节点 / 资源删除时同事务清理其评论。
   评论同样是多态目标，且不同内容**可能共用同一 id**，条件漏掉 target_type
   就会把 `tutorial#1` 的评论在删除 `resource#1` 时一起删掉。
"""

import unittest

from app.core.exceptions import AppException
from app.models.comment import ContentComment
from app.models.favorite import Favorite
from app.models.learning import LearningRecord
from app.models.tutorial import Tutorial
from app.models.user import User
from app.services import (
    admin_tutorial_service as ats,
)
from tests.fake_db import MemorySession

# 审计写入需要能识别操作者（台账 P2-9）；本文件只关心孤儿清理，故给一个最小管理员
ADMIN = User(id=9001, account="admin@test")

def make_tutorial(*, id: int = 1, title: str = "教程A") -> Tutorial:
    return Tutorial(id=id, title=title)

def make_favorite(*, id: int, user_id: int, target_type: str, target_id: int) -> Favorite:
    return Favorite(
        id=id,
        user_id=user_id,
        target_type=target_type,
        target_id=target_id,
        title=f"{target_type}#{target_id} 快照",
    )

def make_learning(*, id: int, user_id: int, tutorial_id: int) -> LearningRecord:
    return LearningRecord(
        id=id, user_id=user_id, tutorial_id=tutorial_id, title=f"教程{tutorial_id} 快照",
    )

def make_comment(*, id: int, target_type: str, target_id: int, user_id: int = 100) -> ContentComment:
    """评论（多态目标）：内容删除时也必须同事务清理，否则同样是孤儿数据"""
    return ContentComment(
        id=id,
        target_type=target_type,
        target_id=target_id,
        user_id=user_id,
        content=f"{target_type}#{target_id} 的评论",
    )

def _fav_keys(db: MemorySession) -> set:
    return {(row["target_type"], row["target_id"]) for row in db.all_rows(Favorite)}

def _comment_keys(db: MemorySession) -> set:
    return {(row["target_type"], row["target_id"]) for row in db.all_rows(ContentComment)}

def _learning_tutorial_ids(db: MemorySession) -> set:
    return {row["tutorial_id"] for row in db.all_rows(LearningRecord)}

class TestTutorialDeletePurgesOrphans(unittest.IsolatedAsyncioTestCase):
    async def test_delete_tutorial_purges_learning_and_favorites(self):
        db = MemorySession()
        db.seed(
            make_tutorial(id=1),
            make_learning(id=11, user_id=100, tutorial_id=1),
            make_favorite(id=21, user_id=100, target_type="tutorial", target_id=1),
        )
        await ats.delete_tutorial(db, 1, ADMIN)

        self.assertEqual(db.all_rows(LearningRecord), [])
        self.assertEqual(db.all_rows(Favorite), [])
        # 软删除：内容行保留（恢复时才不会是空壳），但必须已打标记且下架
        rows = db.all_rows(Tutorial)
        self.assertEqual(len(rows), 1, "软删除不得物理删除内容行")
        self.assertIsNotNone(rows[0]["deleted_at"])
        self.assertFalse(rows[0]["is_published"], "软删除必须同时下架，否则公开端仍然可见")

    async def test_delete_tutorial_keeps_unrelated_rows(self):
        """不误伤：同 id 的其它类型、以及指向其它教程的记录必须保留"""
        db = MemorySession()
        db.seed(
            make_tutorial(id=1),
            # 同 id 但类型不同：清理条件若漏掉 target_type 就会被一起删掉
            make_favorite(id=21, user_id=100, target_type="resource", target_id=1),
            # 同类型但 id 不同
            make_favorite(id=22, user_id=100, target_type="tutorial", target_id=2),
            make_learning(id=23, user_id=100, tutorial_id=2),
        )
        await ats.delete_tutorial(db, 1, ADMIN)

        self.assertEqual(_fav_keys(db), {("resource", 1), ("tutorial", 2)})
        self.assertEqual(_learning_tutorial_ids(db), {2})

    async def test_batch_delete_tutorials_purges_orphans(self):
        db = MemorySession()
        db.seed(
            make_tutorial(id=1),
            make_tutorial(id=2),
            make_learning(id=11, user_id=100, tutorial_id=1),
            make_learning(id=12, user_id=100, tutorial_id=2),
            make_learning(id=13, user_id=100, tutorial_id=3),
            make_favorite(id=21, user_id=100, target_type="tutorial", target_id=1),
            make_favorite(id=22, user_id=100, target_type="tutorial", target_id=2),
            make_favorite(id=23, user_id=100, target_type="tutorial", target_id=3),
        )
        result = await ats.batch_delete_tutorials(db, [1, 2], ADMIN)

        self.assertEqual(result["deleted"], 2)
        self.assertEqual(_learning_tutorial_ids(db), {3})
        self.assertEqual(_fav_keys(db), {("tutorial", 3)})

    async def test_missing_tutorial_still_raises_404(self):
        db = MemorySession()
        with self.assertRaises(AppException) as ctx:
            await ats.delete_tutorial(db, 999, ADMIN)
        self.assertEqual(ctx.exception.code, 404)

class TutorialCommentPurgeTests(unittest.IsolatedAsyncioTestCase):
    """教程删除也要清评论（第五轮审查 R5-16）。

    本文件开头第 5 条契约一直写着「教程 / 节点 / 资源删除时同事务清理其评论」，
    但实际只有 node / resource 两条路各有两用例，**教程这一路零测试** ——
    而教程是唯一原本就有评论、流量最大的类型，`purge_comments_for_targets` 的
    位置参数还在本批从 `(db, ids)` 改成了 `(db, "tutorial", ids)`。
    删掉 `admin_tutorial_service.py` 里任一处调用，826 条用例全绿，
    线上则留下指向已删教程的孤儿 UGC（正是 P1-7 立过案的那类，用户无从清理）。
    """

    async def test_delete_tutorial_purges_comments(self):
        db = MemorySession()
        db.seed(
            make_tutorial(id=1),
            make_tutorial(id=2),
            make_comment(id=41, target_type="tutorial", target_id=1),
            make_comment(id=42, target_type="tutorial", target_id=2),
            # 同 id 的另一条评论必须活下来：条件漏掉 target_id 就会被一起删掉
            make_comment(id=43, target_type="tutorial", target_id=2),
        )
        await ats.delete_tutorial(db, 1, ADMIN)
        self.assertEqual(_comment_keys(db), {("tutorial", 2)})

    async def test_batch_delete_tutorials_purges_comments(self):
        db = MemorySession()
        db.seed(
            make_tutorial(id=1),
            make_tutorial(id=2),
            make_tutorial(id=3),
            make_comment(id=41, target_type="tutorial", target_id=1),
            make_comment(id=42, target_type="tutorial", target_id=2),
            make_comment(id=44, target_type="tutorial", target_id=3),
        )
        await ats.batch_delete_tutorials(db, [1, 2], ADMIN)
        self.assertEqual(_comment_keys(db), {("tutorial", 3)})
