"""兑换码并发链路测试 (P0-6)。

兑换码是「限量名额 + 真实积分」的组合，超发等于直接送钱。这里把
redemption_service 声明的三条并发契约全部变成断言：

1. 名额核销靠原子 UPDATE（WHERE used_count < max_uses），**绝不超发**；
2. 同一用户重复兑换靠 (code_id, user_id) 唯一约束兜底，savepoint 保证
   后到请求只回滚本段插入、会话不进入失败态；
3. 积分用相对值自增（points = points + N），并发充值/扣减下不丢失更新。

并发用 asyncio.gather + 独立会话（共享同一库）模拟，仿真层保证
UPDATE 的「判定 + 写入」不会被协程切换打断（等价于数据库行锁）。
"""

import asyncio
import unittest
from datetime import datetime, timedelta

from app.core.exceptions import AppException
from app.models.redemption_code import RedemptionCode, RedemptionRecord
from app.models.user import User
from app.services import redemption_service as rs
from tests.fake_db import MemorySession


def make_code(*, id: int = 1, code: str = "AAAA-BBBB-CCCC-DDDD", points: int = 100,
              max_uses: int = 1, status: str = "active",
              expires_in: timedelta | None = None) -> RedemptionCode:
    return RedemptionCode(
        id=id,
        code=code,
        points=points,
        max_uses=max_uses,
        used_count=0,
        status=status,
        expires_at=(datetime.now() + expires_in) if expires_in else None,
    )


def make_user(*, id: int = 1, points: int = 0) -> User:
    return User(id=id, account=f"u{id}@example.com", password_hash="x",
                role="user", points=points, free_used_seconds=0)


def seed(db: MemorySession, code: RedemptionCode, users) -> None:
    db.seed(code, *users)


class TestRedeemHappyPath(unittest.IsolatedAsyncioTestCase):
    async def test_single_use_code(self):
        db = MemorySession()
        code = make_code(points=100, max_uses=1)
        user = make_user()
        seed(db, code, [user])

        result = await rs.redeem_code(db, user, "aaaa-bbbb-cccc-dddd")
        self.assertEqual(result["points"], 100)
        self.assertEqual(result["balance"], 100)
        self.assertEqual(user.points, 100)
        self.assertEqual(db.all_rows(RedemptionCode)[0]["used_count"], 1)

    async def test_code_is_case_and_space_insensitive(self):
        db = MemorySession()
        code = make_code()
        user = make_user()
        seed(db, code, [user])
        await rs.redeem_code(db, user, "  aaaa-bbbb-cccc-dddd  ")
        self.assertEqual(user.points, 100)

    async def test_balance_is_added_not_overwritten(self):
        """积分必须相对自增：写成绝对值会吞掉用户原有余额"""
        db = MemorySession()
        code = make_code(points=50)
        user = make_user(points=30)
        seed(db, code, [user])
        await rs.redeem_code(db, user, code.code)
        self.assertEqual(user.points, 80)


class TestRedeemRejections(unittest.IsolatedAsyncioTestCase):
    async def _expect_reject(self, code, user, code_str=None, keyword=""):
        db = MemorySession()
        seed(db, code, [user])
        with self.assertRaises(AppException) as ctx:
            # 显式区分 None（用码本身）与 ""（空串，测其被拒）
            await rs.redeem_code(db, user, code.code if code_str is None else code_str)
        self.assertEqual(ctx.exception.code, 400)
        if keyword:
            self.assertIn(keyword, ctx.exception.message)
        self.assertEqual(user.points, 0)
        self.assertEqual(len(db.all_rows(RedemptionRecord)), 0)

    async def test_empty_code(self):
        await self._expect_reject(make_code(), make_user(), "", "请输入兑换码")

    async def test_nonexistent_code(self):
        await self._expect_reject(make_code(), make_user(), "ZZZZ-ZZZZ-ZZZZ-ZZZZ", "不存在")

    async def test_disabled_code(self):
        await self._expect_reject(make_code(status="disabled"), make_user(), None, "作废")

    async def test_expired_code(self):
        await self._expect_reject(
            make_code(expires_in=timedelta(days=-1)), make_user(), None, "过期"
        )

    async def test_exhausted_code(self):
        code = make_code(max_uses=1)
        code.used_count = 1
        await self._expect_reject(code, make_user(), None, "名额已满")

    async def test_duplicate_redeem_same_user(self):
        db = MemorySession()
        code = make_code(max_uses=10)
        user = make_user()
        seed(db, code, [user])
        await rs.redeem_code(db, user, code.code)
        with self.assertRaises(AppException) as ctx:
            await rs.redeem_code(db, user, code.code)
        self.assertIn("已兑换过", ctx.exception.message)
        # 重复兑换不得二次加积分
        self.assertEqual(user.points, 100)
        self.assertEqual(db.all_rows(RedemptionCode)[0]["used_count"], 1)

    async def test_overlong_code_rejected(self):
        await self._expect_reject(
            make_code(), make_user(), "X" * 33, "格式不正确"
        )

    async def test_balance_over_cap_is_rejected(self):
        """兑换后余额超出上限必须拒绝 —— 与管理员调整积分同口径（第二轮增量审查 P1）

        此前只有管理员路径校验上限，兑换码路径没有，形成「换条入金口就能绕过」
        的口径分裂：单张高面额码可把余额顶到远超管理员被允许的上限。
        """
        from app.services.point_service import MAX_POINTS_BALANCE

        db = MemorySession()
        code = make_code(points=MAX_POINTS_BALANCE, max_uses=1)
        user = make_user(points=1)  # 1 + MAX 恰好越界
        seed(db, code, [user])

        with self.assertRaises(AppException) as ctx:
            await rs.redeem_code(db, user, "aaaa-bbbb-cccc-dddd")
        self.assertEqual(ctx.exception.code, 400)
        # 积分未被加上（条件 UPDATE 未命中）
        self.assertEqual(user.points, 1)

    async def test_balance_exactly_at_cap_is_allowed(self):
        """恰好等于上限应当放行（边界不可误伤：<= 而非 <）"""
        from app.services.point_service import MAX_POINTS_BALANCE

        db = MemorySession()
        code = make_code(points=100, max_uses=1)
        user = make_user(points=MAX_POINTS_BALANCE - 100)
        seed(db, code, [user])

        result = await rs.redeem_code(db, user, "aaaa-bbbb-cccc-dddd")
        self.assertEqual(result["balance"], MAX_POINTS_BALANCE)


class TestRedeemConcurrency(unittest.IsolatedAsyncioTestCase):
    """并发抢兑：名额与积分都不能多发"""

    async def _race(self, *, max_uses: int, contenders: int):
        """让 contenders 个用户并发抢同一个码，返回 (成功结果, 失败异常)"""
        root = MemorySession()
        code = make_code(points=100, max_uses=max_uses)
        users = [make_user(id=i + 1) for i in range(contenders)]
        seed(root, code, users)

        async def attempt(user):
            # 每个并发请求使用独立会话，但共享同一份数据库
            session = root.new_session()
            try:
                return await rs.redeem_code(session, user, code.code)
            except AppException as exc:
                return exc

        results = await asyncio.gather(*[attempt(u) for u in users])
        ok = [r for r in results if isinstance(r, dict)]
        failed = [r for r in results if isinstance(r, AppException)]
        return root, users, ok, failed

    async def test_single_use_code_only_one_winner(self):
        root, users, ok, failed = await self._race(max_uses=1, contenders=8)
        self.assertEqual(len(ok), 1)
        self.assertEqual(len(failed), 7)
        self.assertEqual(root.all_rows(RedemptionCode)[0]["used_count"], 1)
        # 只有赢家拿到积分
        self.assertEqual(sum(u.points for u in users), 100)

    async def test_limited_code_never_oversubscribed(self):
        root, users, ok, failed = await self._race(max_uses=3, contenders=20)
        self.assertEqual(len(ok), 3)
        self.assertEqual(len(failed), 17)
        self.assertEqual(root.all_rows(RedemptionCode)[0]["used_count"], 3)
        self.assertEqual(sum(u.points for u in users), 300)
        # 失败者必须收到「名额已满」而不是别的错误
        self.assertTrue(all("名额已满" in f.message for f in failed))

    async def test_same_user_double_submit(self):
        """同一用户双开/连点：唯一约束兜底，只成功一次"""
        root = MemorySession()
        code = make_code(points=100, max_uses=10)
        user = make_user()
        seed(root, code, [user])

        async def attempt():
            session = root.new_session()
            try:
                return await rs.redeem_code(session, user, code.code)
            except AppException as exc:
                return exc

        results = await asyncio.gather(*[attempt() for _ in range(5)])
        ok = [r for r in results if isinstance(r, dict)]
        self.assertEqual(len(ok), 1)
        self.assertEqual(user.points, 100)
        self.assertEqual(root.all_rows(RedemptionCode)[0]["used_count"], 1)

    async def test_concurrent_redeem_does_not_lose_point_updates(self):
        """并发兑换 + 并发扣费交错时，积分必须逐笔累加而非互相覆盖"""
        root = MemorySession()
        code = make_code(points=10, max_uses=50)
        user = make_user(points=0)
        seed(root, code, [user])

        # 5 个码各兑一次（模拟多次充值并发落到同一用户）
        async def attempt(i):
            session = root.new_session()
            c = make_code(id=100 + i, code=f"C{i}-BBBB-CCCC-DDDD", points=10, max_uses=50)
            session.seed(c)
            return await rs.redeem_code(session, user, c.code)

        await asyncio.gather(*[attempt(i) for i in range(5)])
        # 相对自增保证 5 笔各 +10；若写成绝对值更新，最终只剩 10
        self.assertEqual(root.all_rows(User)[0]["points"], 50)


class TestAdminCodeOps(unittest.IsolatedAsyncioTestCase):
    async def test_create_codes_bounds(self):
        db = MemorySession()
        admin = make_user(id=999)
        db.seed(admin)
        for kwargs in ({"points": 0, "max_uses": 1, "count": 1},
                       {"points": 1, "max_uses": 0, "count": 1},
                       {"points": 1, "max_uses": 1, "count": 0},
                       {"points": 1, "max_uses": 1, "count": 101}):
            with self.assertRaises(AppException):
                await rs.create_codes(db, admin, **kwargs)

    async def test_create_codes_generates_unique(self):
        db = MemorySession()
        admin = make_user(id=999)
        db.seed(admin)
        codes = await rs.create_codes(db, admin, points=10, max_uses=2, count=50)
        self.assertEqual(len(codes), 50)
        self.assertEqual(len({c["code"] for c in codes}), 50)
        self.assertTrue(all(c["eff_status"] == "active" for c in codes))

    async def test_expiry_must_be_future(self):
        db = MemorySession()
        admin = make_user(id=999)
        db.seed(admin)
        with self.assertRaises(AppException):
            await rs.create_codes(db, admin, points=10, max_uses=1, count=1,
                                  expires_at=datetime.now() - timedelta(days=1))

    async def test_disable_code(self):
        db = MemorySession()
        code = make_code()
        db.seed(code)
        result = await rs.disable_code(db, code.id)
        self.assertEqual(result["eff_status"], "disabled")
        with self.assertRaises(AppException):
            await rs.disable_code(db, code.id)

    async def test_eff_status_priority(self):
        # 已过期的码即使已兑满也展示为「已过期」，与 _eff_status 优先级一致
        self.assertEqual(rs._eff_status(make_code(status="disabled")), "disabled")
        expired = make_code(expires_in=timedelta(days=-1))
        expired.used_count = expired.max_uses
        self.assertEqual(rs._eff_status(expired), "expired")
        exhausted = make_code(max_uses=1)
        exhausted.used_count = 1
        self.assertEqual(rs._eff_status(exhausted), "exhausted")


if __name__ == "__main__":
    unittest.main()
