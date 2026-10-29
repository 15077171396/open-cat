"""积分流水的「时间范围 / 收支方向」筛选与时间换算。

这是全站积分对账的唯一入口，两类错误都会静默误导运营：

1. **时区换算错 8 小时**：库内时间列是 naive UTC，而前端 `toISOString()` 带偏移。
   若把带偏移的值直接拿去比较，东八区环境会「选 9 月 6 日却筛出 9 月 5 日 16 点后的记录」。
2. **筛选被静默忽略**：参数拼错（direction 写成 `in`）或条件没进 SQL 时，
   接口会安然返回**全量**流水 —— 运营以为看的是「只支出」，其实看到的是全部。

第 2 类靠 `_RecordingSession` 断言**生成的 SQL 本身**（条件与绑定值），
而不是只看返回值 —— 返回值在桩里是造出来的，看它等于自证。
"""

import unittest
from datetime import datetime

from app.core.exceptions import AppException
from app.services import point_service
from app.services.point_service import list_all_transactions
from app.utils.time import parse_iso_to_naive_utc


class ParseIsoToNaiveUtcTests(unittest.TestCase):
    """前端 ISO 字符串 → naive UTC（库内时间列的统一口径）"""

    def test_empty_becomes_none(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                self.assertIsNone(parse_iso_to_naive_utc(value))

    def test_utc_z_suffix(self):
        self.assertEqual(
            parse_iso_to_naive_utc("2026-09-06T16:00:00.000Z"),
            datetime(2026, 9, 6, 16, 0, 0),
        )

    def test_positive_offset_is_converted(self):
        """东八区当地 0 点 = UTC 前一天 16 点 —— 差 8 小时正是这个筛选最容易错的地方"""
        self.assertEqual(
            parse_iso_to_naive_utc("2026-09-06T00:00:00+08:00"),
            datetime(2026, 9, 5, 16, 0, 0),
        )

    def test_naive_input_is_kept_as_is(self):
        self.assertEqual(
            parse_iso_to_naive_utc("2026-09-06T12:00:00"),
            datetime(2026, 9, 6, 12, 0, 0),
        )

    def test_invalid_raises_value_error(self):
        with self.assertRaises(ValueError):
            parse_iso_to_naive_utc("2026/09/06")

    def test_result_is_always_naive(self):
        """带 tzinfo 的值流到 SQLAlchemy 会被 MySQL 驱动当成本地时间，必须剥掉"""
        self.assertIsNone(parse_iso_to_naive_utc("2026-09-06T00:00:00+08:00").tzinfo)


class RedeemCodeExpiresAtReusesTheSameRuleTests(unittest.TestCase):
    """兑换码过期时间曾自持一份换算逻辑，现复用同一处 —— 防止两份规则各自漂移"""

    def test_delegates_to_the_shared_helper(self):
        from app.api.admin_redeem_codes import _parse_expires_at

        self.assertIsNone(_parse_expires_at(""))
        self.assertEqual(
            _parse_expires_at("2026-09-06T00:00:00+08:00"),
            datetime(2026, 9, 5, 16, 0, 0),
        )
        with self.assertRaises(AppException):
            _parse_expires_at("2026/09/06")


class _StubResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value

    def all(self):
        return self._value

    def scalars(self):
        # 有的 service 用 `.scalars().all()`（审计列表）而非 `.all()`，两者都要支持
        return _StubScalars(self._value)


class _StubScalars:
    def __init__(self, value):
        self._value = value

    def all(self):
        return self._value


class _RecordingSession:
    """只记录语句、不执行：断言「条件真的进了 SQL」。

    为什么不用 tests.fake_db.MemorySession：`list_all_transactions` 用的是
    `select(PointTransaction, User.account).join(User, ...)` 两实体连接查询，
    内存仿真只支持单实体与「分组列 + count」两种形状，跑它会得到错误结果
    （宁可红不可假绿，所以这里换一种更贴问题的验证方式：看语句本身）。
    """

    def __init__(self, total: int = 0):
        self.calls: list[tuple[str, dict]] = []
        self._total = total

    async def execute(self, stmt):
        compiled = stmt.compile()
        self.calls.append((str(compiled), dict(compiled.params)))
        return _StubResult(self._total if len(self.calls) == 1 else [])

    @property
    def sql(self) -> str:
        return " ".join(text for text, _ in self.calls)

    @property
    def bound_values(self) -> list:
        return [value for _, params in self.calls for value in params.values()]


class LedgerFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_direction_income_filters_positive_points(self):
        db = _RecordingSession()
        await list_all_transactions(db, direction="income")
        self.assertIn("point_transactions.points >", db.sql)
        self.assertNotIn("point_transactions.points <", db.sql)

    async def test_direction_expense_filters_negative_points(self):
        db = _RecordingSession()
        await list_all_transactions(db, direction="expense")
        self.assertIn("point_transactions.points <", db.sql)
        self.assertNotIn("point_transactions.points >", db.sql)

    async def test_time_range_is_inclusive_on_both_ends(self):
        start = datetime(2026, 9, 5, 16, 0, 0)
        end = datetime(2026, 9, 6, 15, 59, 59)
        db = _RecordingSession()
        await list_all_transactions(db, start_at=start, end_at=end)
        self.assertIn("point_transactions.created_at >=", db.sql)
        self.assertIn("point_transactions.created_at <=", db.sql)
        # 绑定值必须是调用方给的 naive UTC（换算在路由层已完成，service 不得再动时区）
        self.assertIn(start, db.bound_values)
        self.assertIn(end, db.bound_values)

    async def test_conditions_reach_both_count_and_page_queries(self):
        """count 与列表是两条语句 —— 只给其中一条加条件会让「共 N 条」与列表对不上"""
        db = _RecordingSession()
        await list_all_transactions(db, type="consume", direction="expense")
        self.assertEqual(len(db.calls), 2)
        for sql, _ in db.calls:
            self.assertIn("point_transactions.points <", sql)
            self.assertIn("point_transactions.type =", sql)

    async def test_keyword_still_joins_user_and_escapes(self):
        """账号筛选走 users 连接 + LIKE —— 丢掉 join 会让 keyword 静默失效"""
        db = _RecordingSession()
        await list_all_transactions(db, keyword="abc")
        self.assertIn("JOIN users", db.sql)
        self.assertIn("LIKE", db.sql)

    async def test_operator_is_resolved_with_an_outer_join(self):
        """操作人单独一列展示账号（只给 id 还得再查一次）。

        必须是 **outer** join：consume / redeem 是系统写入（operator_id 为 NULL），
        管理员账号被删后 id 也解析不到 —— inner join 会让这些流水**整条从对账视图里消失**，
        且 total 会跟着变小，运营根本不会察觉少了几条。
        """
        db = _RecordingSession()
        await list_all_transactions(db)
        self.assertIn("LEFT OUTER JOIN users", db.sql)
        # 别名：同一张 users 表被连接两次（流水用户 / 操作人），不加别名会自连接报错
        self.assertIn("AS users_1", db.sql)
        # count 查询刻意不带这个 join（不影响行数），只有列表语句该有
        self.assertNotIn("LEFT OUTER JOIN", db.calls[0][0])
        self.assertIn("LEFT OUTER JOIN", db.calls[1][0])

    async def test_no_filters_means_no_extra_predicates(self):
        """无筛选时不拼任何 WHERE —— 注意断言的是比较符，`created_at` 本身出现在
        SELECT 列与 ORDER BY 里，直接搜列名会误判"""
        db = _RecordingSession()
        await list_all_transactions(db)
        for needle in ("points >", "points <", "created_at >=", "created_at <=", "LIKE", "WHERE"):
            self.assertNotIn(needle, db.sql)

    async def test_invalid_direction_is_rejected_before_touching_db(self):
        """拼错参数必须 400 —— 静默回落成「不筛选」会让运营把全量当成筛过的结果"""
        db = _RecordingSession()
        with self.assertRaises(AppException):
            await list_all_transactions(db, direction="in")
        self.assertEqual(db.calls, [], "校验必须在触库前完成")

    async def test_invalid_type_is_rejected_before_touching_db(self):
        db = _RecordingSession()
        with self.assertRaises(AppException):
            await list_all_transactions(db, type="not-a-type")
        self.assertEqual(db.calls, [])

    async def test_direction_constants_are_the_documented_ones(self):
        """路由/前端都按这两个字面量传参，改名要有意识地改三处"""
        self.assertEqual(point_service.VALID_DIRECTIONS, ("income", "expense"))


if __name__ == "__main__":
    unittest.main()
