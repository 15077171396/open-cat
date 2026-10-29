"""仿真层的 ORDER BY / LIMIT / OFFSET 支持（缺口台账 P2-51）。

为什么单独测 `fake_db` 本身：它是**所有离线用例的地基**。原先 `_do_select` 只应用
WHERE，把 ORDER BY / LIMIT / OFFSET 静默丢掉 —— 于是「靠 SQL 侧排序与截断保证正确性」
的代码从未被真正验证过，测试绿只是因为业务侧还留着一道 Python 兜底。
补上能力之后必须同时钉住三件事：

1. 排序/截断**真的生效**（否则等于没补）；
2. NULL 的位置与 MySQL 一致（ASC 在前、DESC 在后）—— 位置错了，「待处理排前面」这类
   业务语义会在仿真里通过、在真库上翻车；
3. 遇到不支持的排序形态要**抛错**，不能退回「忽略排序」。
"""

import datetime as dt
import unittest

from sqlalchemy import func, select

from app.models.user import User
from tests.fake_db import MemorySession, _Unsupported


def _user(uid: int, points: int, vip_expire: dt.datetime | None) -> User:
    return User(
        id=uid,
        account=f"acct{uid}",
        nickname=f"n{uid}",
        password_hash="x",
        role="user",
        points=points,
        vip_expire_time=vip_expire,
    )


class OrderLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = MemorySession()
        base = dt.datetime(2026, 1, 1)
        self.db.seed(
            _user(1, 30, base + dt.timedelta(days=1)),
            _user(2, 10, None),                      # NULL 排最前（ASC）
            _user(3, 20, base + dt.timedelta(days=3)),
            _user(4, 50, base + dt.timedelta(days=2)),
        )

    async def _ids(self, stmt):
        return [u.id for u in (await self.db.execute(stmt)).scalars().all()]

    async def test_order_by_column_asc_desc(self):
        self.assertEqual(
            await self._ids(select(User).order_by(User.points.asc())), [2, 3, 1, 4]
        )
        self.assertEqual(
            await self._ids(select(User).order_by(User.points.desc())), [4, 1, 3, 2]
        )

    async def test_limit_and_offset_slice(self):
        self.assertEqual(
            await self._ids(select(User).order_by(User.points.asc()).limit(2)), [2, 3]
        )
        self.assertEqual(
            await self._ids(
                select(User).order_by(User.points.asc()).offset(2).limit(2)
            ),
            [1, 4],
        )

    async def test_null_position_matches_mysql(self):
        """ASC 时 NULL 在前、DESC 时 NULL 在后（与 MySQL / InnoDB 一致）。"""
        asc = await self._ids(select(User).order_by(User.vip_expire_time.asc()))
        desc = await self._ids(select(User).order_by(User.vip_expire_time.desc()))
        self.assertEqual(asc[0], 2)
        self.assertEqual(desc[-1], 2)

    async def test_multi_column_ordering_is_stable(self):
        """多列排序：先按列，再按兜底列 —— 分页不重不漏的前提（同 P2-8 / P2-36）。"""
        self.db.seed(_user(5, 20, None))  # 与 id=3 同分
        ids = await self._ids(
            select(User).order_by(User.points.asc(), User.id.desc())
        )
        self.assertEqual(ids, [2, 5, 3, 1, 4])

    async def test_boolean_expression_ordering(self):
        """`(col == x).desc()`：审核队列「待处理排前面」用的就是这种排序键。"""
        from app.models.moderation import ContentReport

        db = MemorySession()
        now = dt.datetime(2026, 1, 1)
        for rid, status in ((11, "resolved"), (12, "pending"), (13, "pending")):
            db.seed(
                ContentReport(
                    id=rid, reporter_id=1, content_type="tutorial", target_id="t1",
                    reason="spam", status=status, created_at=now,
                )
            )
        rows = (
            await db.execute(
                select(ContentReport)
                .order_by((ContentReport.status == "pending").desc(), ContentReport.id.asc())
            )
        ).scalars().all()
        self.assertEqual([r.id for r in rows], [12, 13, 11])

    async def test_unsupported_order_expression_raises(self):
        """反向对照：不支持的排序形态必须抛错，不能悄悄退回「不排序」。"""
        with self.assertRaises(_Unsupported):
            await self.db.execute(select(User).order_by(func.lower(User.account)))

    async def test_no_order_by_keeps_insertion_order(self):
        self.assertEqual(await self._ids(select(User)), [1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main()
