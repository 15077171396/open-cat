"""数据质量巡检的契约测试。

钉住四条契约（都属「破了不报错、只是悄悄失去意义」那类）：

1. **只读**：模块里不得出现任何 DML/DDL 关键字 —— 巡检把数据改坏了，
   比它发现的任何问题都严重。
2. **单项失败不中断整轮**：某条查询报错时该项 `count=None` 且不计入问题总数，
   其余项照常给出结论。
3. **0 与「查不到」必须区分**：0 条 = 正常（不出现在 issues），
   None = 查询失败（也不出现在 issues，但绝不能当成 0 去「证明健康」）。
4. **多态目标按白名单查**：`favorites.target_type` 里存在历史遗留取值（如 `workflow`，
   没有对应表），未知类型必须跳过 —— 否则每轮都报一批假阳性。
"""

import re
import unittest
from pathlib import Path

from app.services import data_quality_service as dqs

SERVICE_FILE = Path(dqs.__file__)


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _StubSession:
    """按 SQL 片段匹配返回值的假会话；`fail` 里的片段命中时抛错"""

    def __init__(self, values: dict[str, int] | None = None, fail: set[str] | None = None):
        self.values = values or {}
        self.fail = fail or set()
        self.sqls: list[str] = []

    async def execute(self, stmt):
        sql = str(stmt)
        self.sqls.append(sql)
        for fragment, value in self.values.items():
            if fragment in sql:
                if fragment in self.fail:
                    raise RuntimeError("simulated db error")
                return _Result(value)
        if self.fail & {"*"}:
            raise RuntimeError("simulated db error")
        return _Result(0)


class AggregateTests(unittest.IsolatedAsyncioTestCase):
    async def test_counts_are_summed_into_issue_total(self):
        session = _StubSession({"points < 0": 2, "role = 'vip'": 3})
        result = await dqs.collect_issues(session)
        self.assertEqual(result["issue_total"], 5)
        codes = {i["code"] for i in result["issues"]}
        self.assertIn("negative_points", codes)
        self.assertIn("vip_expired_stale", codes)

    async def test_zero_counts_produce_no_issue(self):
        result = await dqs.collect_issues(_StubSession())
        self.assertEqual(result["issue_total"], 0)
        self.assertEqual(result["issues"], [])
        self.assertTrue(result["checks"], "即使全为 0 也要回传 checks，便于后台展示「已检查」")

    async def test_one_failed_check_does_not_break_the_round(self):
        session = _StubSession({"points < 0": 4, "role = 'vip'": 1}, fail={"role = 'vip'"})
        result = await dqs.collect_issues(session)
        self.assertIsNone(result["checks"]["vip_expired_stale"]["count"])
        self.assertEqual(result["checks"]["negative_points"]["count"], 4)
        self.assertEqual(result["issue_total"], 4, "失败的检查项不能计入总数，也不能让整轮返回 0")

    async def test_orphan_favorites_are_split_per_type(self):
        session = _StubSession()
        await dqs.collect_issues(session)
        favorite_sqls = [s for s in session.sqls if "FROM favorites" in s]
        self.assertEqual(len(favorite_sqls), len(dqs._FAVORITE_TARGET_TABLES))
        joined = " ".join(favorite_sqls)
        self.assertNotIn("workflow", joined, "历史遗留 target_type 没有对应表，不能去查")

    async def test_issue_text_carries_actionable_label(self):
        result = await dqs.collect_issues(_StubSession({"points < 0": 1}))
        self.assertIn("积分为负", result["issues"][0]["text"])
        self.assertEqual(result["issues"][0]["level"], "error")


class ReadOnlyGuardTests(unittest.TestCase):
    def test_module_contains_no_dml_or_ddl(self):
        """只读契约：巡检脚本里出现写语句即判红（含将来有人「顺手」加清理逻辑）"""
        source = SERVICE_FILE.read_text(encoding="utf-8")
        # 只看 SQL 字符串：把模块源码里的英文大写 DML 关键字全揪出来
        offenders = re.findall(
            r"\b(DELETE\s+FROM|UPDATE\s+\w+\s+SET|INSERT\s+INTO|TRUNCATE\s+TABLE|DROP\s+TABLE|ALTER\s+TABLE)\b",
            source,
            flags=re.IGNORECASE,
        )
        self.assertEqual(offenders, [], f"巡检模块出现写操作，违反只读契约: {offenders}")

    def test_target_tables_are_safe_identifiers(self):
        """表名会拼进 SQL 字符串，必须是白名单形态（防将来有人从请求参数取值拼进来）"""
        for target_type, table in dqs._FAVORITE_TARGET_TABLES.items():
            self.assertRegex(target_type, r"^[a-z_]+$")
            self.assertRegex(table, r"^[a-z_]+$")

    def test_all_issue_codes_have_text(self):
        """每个检查项都要有可读文案，否则后台会出现一串裸 code"""
        for code, (level, text) in dqs._ISSUE_TEXT.items():
            self.assertIn(level, ("warn", "error"))
            self.assertTrue(text.strip())
            self.assertNotIn(code, ("", None))


if __name__ == "__main__":
    unittest.main()
