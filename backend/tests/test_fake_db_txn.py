"""`fake_db` 的事务保真度（缺口台账 P1-42 之一）。

## 被钉死的缺陷

`MemorySession.rollback()` 原先只 `self._pending.clear()`：**已经 flush 出去的
INSERT / UPDATE / DELETE 全部留在权威行里**。真实 MySQL 下这些会被回滚掉，
于是所有「失败分支里 rollback 了，所以钱没扣 / 码没核销」的断言在仿真层是空话 ——
数据其实已经改完，测试却照样绿。这类「测试替身比被测系统宽容」正是 P1-42 记的三处失真之一。

同一事务快照也保证 `commit()` 之后不可能再被 rollback 撤销（真实数据库同理）。
"""

import unittest

from app.models.user import User
from tests.fake_db import MemorySession


def _user(points: int = 100) -> User:
    return User(id=4001, account="u4001@example.com", password_hash="x", role="user", points=points)


class RollbackFidelityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = MemorySession()
        self.db.seed(_user(100))

    async def test_update_is_reverted(self):
        table = self.db._tables["users"]
        obj = table.objs[4001]  # seed 时已进 identity map
        self.assertIsNotNone(obj)
        obj.points = 70
        await self.db.flush()
        self.assertEqual(table.rows[4001]["points"], 70, "flush 后必须先看到新值，否则本用例什么都没测到")
        await self.db.rollback()
        self.assertEqual(table.rows[4001]["points"], 100, "rollback 后权威行必须回到事务开始前的值")

    async def test_insert_is_reverted(self):
        self.db.add(User(id=4002, account="u4002@example.com", password_hash="x", role="user", points=5))
        await self.db.flush()
        self.assertIn(4002, self.db._tables["users"].rows)
        await self.db.rollback()
        self.assertNotIn(4002, self.db._tables["users"].rows, "未提交的新增行必须消失")

    async def test_delete_is_reverted(self):
        table = self.db._tables["users"]
        obj = table.objs[4001]
        await self.db.delete(obj)
        self.assertNotIn(4001, table.rows)
        await self.db.rollback()
        self.assertEqual(table.rows[4001]["points"], 100, "已删除的行必须整行还原")
        self.assertIs(table.objs.get(4001), obj, "identity map 也要还原，否则后续断言拿到的是已删对象")

    async def test_commit_closes_the_snapshot(self):
        table = self.db._tables["users"]
        obj = table.objs[4001]
        obj.points = 70
        await self.db.commit()
        await self.db.rollback()
        self.assertEqual(table.rows[4001]["points"], 70, "已提交的事务不可能被 rollback 撤销")


class SnapshotNotVacuousTests(unittest.IsolatedAsyncioTestCase):
    """反向对照：快照只覆盖**本会话**的改动，别的会话不能借用它撤销。"""

    async def test_other_session_rollback_does_not_undo_mine(self):
        db_a = MemorySession()
        db_a.seed(_user(100))
        table = db_a._tables["users"]
        obj = table.objs[4001]
        obj.points = 70
        await db_a.flush()

        other = db_a.new_session()
        await other.rollback()
        self.assertEqual(table.rows[4001]["points"], 70, "别的会话不该有本事撤销本会话已 flush 的写入")

        await db_a.rollback()
        self.assertEqual(table.rows[4001]["points"], 100)


class DirtyColumnOnlyWriteTests(unittest.IsolatedAsyncioTestCase):
    """`flush` 只写脏列 —— 这是仿真层区别于「整行覆盖」实现的关键保真点。

    真实 UPDATE 只写被改动的列；若仿真层把整行都写回，那么**陈旧 ORM 对象**会把
    别的会话已经提交的值覆盖回去，而「代码 UPDATE 后忘了 refresh」这个 bug
    恰好就长得一模一样 —— 于是测试永远抓不到它（台账 P1-42 的同族失真）。
    """

    async def test_stale_object_does_not_clobber_other_writes(self):
        db = MemorySession()
        db.seed(_user(100))
        table = db._tables["users"]
        obj = table.objs[4001]
        # 模拟「另一个会话已经把 points 改成 130 并提交了」，
        # 而本会话手里的对象还停在 100（陈旧快照，且基线也是 100 → points 不算脏列）
        table.rows[4001]["points"] = 130
        obj.role = "vip"  # 本会话真正改动的列

        await db.flush()

        self.assertEqual(obj.points, 100, "前提：本会话的对象确实是陈旧的")
        self.assertEqual(table.rows[4001]["points"], 130, "陈旧对象的旧值不得覆盖权威行")
        self.assertEqual(table.rows[4001]["role"], "vip", "真正改动的列必须写进去")


if __name__ == "__main__":
    unittest.main()
