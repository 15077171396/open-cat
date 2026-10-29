"""评论每日配额的口径（缺口台账 P2-57 之三）。

`_check_daily_quota` 的三条口径此前**零用例**：

1. **跨内容类型共享**一份预算，不是「每种内容各 30 条」；
2. **自然日**而非滚动 24 小时，且日界按 **naive UTC**（全库 UTC 约定，
   见 `tests/test_db_session_timezone.py` 与 compose 里 MySQL 的 `TZ=UTC`）；
3. **软删除的评论仍占额度**（防灌水是目的，删掉重发不该刷新预算）。

第 3 条钉的是**现状**而不是主张：如果产品决定「删了就不该继续占用」，
那是一次显式改动，不该由一次重构顺手改掉。
"""

import datetime as dt
import unittest
from unittest import mock

from sqlalchemy import update

from app.core.exceptions import AppException
from app.models.comment import MAX_COMMENTS_PER_USER_PER_DAY, ContentComment
from app.models.node import Node
from app.models.tutorial import Tutorial
from app.models.user import User
from app.services import comment_service
from tests.fake_db import MemorySession

USER = User(id=701, account="u701@example.com", password_hash="x", role="user")


def _db(with_node: bool = False) -> MemorySession:
    db = MemorySession()
    tutorial = Tutorial(id=801, title="教程A", description="d", content="c", category="ai")
    tutorial.is_published = True
    db.seed(USER, tutorial)
    if with_node:
        db.seed(Node(id=801, name="节点A", category="core", description="d", content="c"))
    return db


def _seed_today(db: MemorySession, count: int, *, target_type: str = "tutorial", when=None) -> None:
    now = when or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    for i in range(count):
        db.seed(
            ContentComment(
                id=9000 + i + (0 if target_type == "tutorial" else 500),
                target_type=target_type,
                target_id=801,
                user_id=USER.id,
                content="x",
                created_at=now,
            )
        )


class SharedBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_quota_is_per_user_not_per_target_type(self):
        """刷满教程评论后，换一种内容类型继续发也必须被拒。

        配额数的是「该用户今日的评论行数」，条件里**没有** target_type ——
        这条断言的意义是：将来有人「按类型分别限额」时必须显式改这里。
        """
        db = _db(with_node=True)
        _seed_today(db, MAX_COMMENTS_PER_USER_PER_DAY, target_type="tutorial")
        with self.assertRaises(AppException) as ctx:
            await comment_service.create_comment(db, USER, "node", 801, "换一种内容再发")
        self.assertEqual(ctx.exception.code, 429)

    async def test_one_below_limit_still_passes(self):
        db = _db()
        _seed_today(db, MAX_COMMENTS_PER_USER_PER_DAY - 1)
        result = await comment_service.create_comment(db, USER, "tutorial", 801, "还能再发一条")
        self.assertEqual(result["content"], "还能再发一条")


class DayBoundaryTests(unittest.IsolatedAsyncioTestCase):
    """日界必须是 UTC 零点。用冻结时钟才能确定性地把「本地今天 / UTC 昨天」分叉测出来。"""

    NOW_UTC = dt.datetime(2026, 10, 12, 0, 30)  # naive UTC，与 service 内部同形状

    class _FrozenDT:
        """只替换 service 里的 `dt.datetime.now`，其余属性透传真实模块。"""

        timezone = dt.timezone
        timedelta = dt.timedelta

        class _DateTime:
            tzinfo = dt.timezone.utc

            @staticmethod
            def now(tz=None):
                return DayBoundaryTests.NOW_UTC.replace(tzinfo=tz) if tz else DayBoundaryTests.NOW_UTC

        datetime = _DateTime

    def _seed_at(self, db: MemorySession, count: int, when: dt.datetime) -> None:
        for i in range(count):
            db.seed(
                ContentComment(
                    id=9500 + i, target_type="tutorial", target_id=801,
                    user_id=USER.id, content="x", created_at=when,
                )
            )

    async def test_rows_from_utc_yesterday_do_not_count(self):
        """冻结在 UTC 2026-10-12 00:30（= 本地 UTC+8 的 10-12 08:30）。

        行时间取 2026-10-11 17:00 UTC —— 它在 UTC 侧属于**昨天**，
        但在 UTC+8 侧是 10-12 01:00（今天凌晨）。两种口径在这里分叉：
        若哪天把日界改成本地时区，这批行就会被算进今天、把用户误封 429。
        """
        utc_yesterday_but_local_today = dt.datetime(2026, 10, 11, 17, 0)
        db = _db()
        with mock.patch.object(comment_service, "dt", self._FrozenDT()):
            self._seed_at(db, MAX_COMMENTS_PER_USER_PER_DAY, utc_yesterday_but_local_today)
            result = await comment_service.create_comment(db, USER, "tutorial", 801, "UTC 口径下仍可发")
        self.assertEqual(result["content"], "UTC 口径下仍可发")

    async def test_rows_from_utc_today_do_count(self):
        same_day_minutes_ago = self.NOW_UTC - dt.timedelta(minutes=1)
        db = _db()
        with mock.patch.object(comment_service, "dt", self._FrozenDT()):
            self._seed_at(db, MAX_COMMENTS_PER_USER_PER_DAY, same_day_minutes_ago)
            with self.assertRaises(AppException) as ctx:
                await comment_service.create_comment(db, USER, "tutorial", 801, "今日已满")
        self.assertEqual(ctx.exception.code, 429)

    async def test_yesterday_rows_do_not_count_plain(self):
        today_start = dt.datetime.now(dt.timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=None
        )
        db = _db()
        _seed_today(db, MAX_COMMENTS_PER_USER_PER_DAY, when=today_start - dt.timedelta(seconds=1))
        result = await comment_service.create_comment(db, USER, "tutorial", 801, "新的一天")
        self.assertEqual(result["content"], "新的一天")


class DeletedRowsStillCountTests(unittest.IsolatedAsyncioTestCase):
    async def test_soft_deleted_comments_still_consume_the_quota(self):
        db = _db()
        _seed_today(db, MAX_COMMENTS_PER_USER_PER_DAY)
        for row in db.all_rows(ContentComment)[:10]:
            await db.execute(
                update(ContentComment)
                .where(ContentComment.id == row["id"])
                .values(deleted_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None))
            )
        with self.assertRaises(AppException) as ctx:
            await comment_service.create_comment(db, USER, "tutorial", 801, "删掉重发")
        self.assertEqual(ctx.exception.code, 429, "改了口径却没改这条断言 = 决策没被记录")


if __name__ == "__main__":
    unittest.main()
