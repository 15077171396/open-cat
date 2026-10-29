"""积分扣减链路测试 (P0-6)。

积分是站内唯一的资金等价物，这里守两条底线：
1. **不能超扣成负余额**：扣减必须是带条件的原子 UPDATE（WHERE points >= cost），
   而不是「读内存值 → 算差 → 写回」；
2. **流水余额必须等于真实余额**：扣减后要先回读再记流水，否则并发充值/扣减下
   balance_after 会记出与真实余额不符的数字，审计对不上账。

用 tests/fake_db.py 的 MemorySession 复刻数据库行锁与原子 UPDATE 语义。
"""

import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from app.core.exceptions import AppException
from app.models.point_transaction import PointTransaction
from app.models.user import User
from app.services import point_service as ps
from tests.fake_db import MemorySession

def make_user(*, id: int = 1, points: int = 100, role: str = "user",
              vip_expire_in: timedelta | None = None) -> User:
    now = datetime.now(timezone.utc)
    return User(
        id=id,
        account=f"u{id}@example.com",
        password_hash="x",
        role=role,
        points=points,
        free_used_seconds=0,
        vip_expire_time=(now + vip_expire_in).replace(tzinfo=None) if vip_expire_in else None,
    )

class TestAdminAdjust(unittest.IsolatedAsyncioTestCase):
    """point_service.adjust_points 的管理端调整"""

    async def _adjust(self, user, action, value, *, note=None):
        db = MemorySession()
        db.seed(user)
        admin = make_user(id=999, role="admin")
        db.seed(admin)
        new_balance = await ps.adjust_points(db, admin, user, action, value, note)
        await db.flush()
        return db, new_balance

    async def test_grant(self):
        user = make_user(points=100)
        db, balance = await self._adjust(user, "grant", 50)
        self.assertEqual(balance, 150)
        row = db.all_rows(PointTransaction)[0]
        self.assertEqual(row["points"], 50)
        self.assertEqual(row["balance_after"], 150)
        self.assertEqual(row["type"], ps.TYPE_ADMIN_GRANT)
        self.assertEqual(row["operator_id"], 999)

    async def test_deduct(self):
        user = make_user(points=100)
        db, balance = await self._adjust(user, "deduct", 40)
        self.assertEqual(balance, 60)
        self.assertEqual(db.all_rows(PointTransaction)[0]["points"], -40)

    async def test_set(self):
        user = make_user(points=100)
        db, balance = await self._adjust(user, "set", 777)
        self.assertEqual(balance, 777)
        # set 是绝对值操作，流水记的是差额而非目标值
        self.assertEqual(db.all_rows(PointTransaction)[0]["points"], 677)

    async def test_deduct_more_than_balance_rejected(self):
        user = make_user(points=10)
        with self.assertRaises(AppException) as ctx:
            await self._adjust(user, "deduct", 999)
        self.assertIn("扣减失败", ctx.exception.message)
        self.assertEqual(user.points, 10)

    async def test_invalid_action(self):
        with self.assertRaises(AppException):
            await self._adjust(make_user(), "multiply", 10)

    async def test_value_bounds(self):
        for bad in (-1, 10_000_001):
            with self.assertRaises(AppException):
                await self._adjust(make_user(), "grant", bad)

    async def test_grant_beyond_cap_rejected(self):
        user = make_user(points=9_999_999)
        with self.assertRaises(AppException) as ctx:
            await self._adjust(user, "grant", 2)
        self.assertIn("不能超过", ctx.exception.message)
        self.assertEqual(user.points, 9_999_999)

    async def test_note_overrides_default_description(self):
        user = make_user(points=0)
        db, _ = await self._adjust(user, "grant", 10, note="活动奖励")
        self.assertEqual(db.all_rows(PointTransaction)[0]["description"], "活动奖励")

if __name__ == "__main__":
    unittest.main()
