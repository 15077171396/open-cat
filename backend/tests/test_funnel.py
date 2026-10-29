"""转化漏斗与留存的契约测试。

钉住四条**破了不报错、只会让运营看到错误结论**的契约：

1. **阶段必须嵌套**：后一阶段只数走完了前面所有阶段的人，人数单调递减。
   一旦改成「各阶段独立计数」，就会出现「免费耗尽 1 人 > 有内容消费 0 人」，
   阶段转化率直接失真 —— 而界面上看不出任何异常。
2. **未知不能当 0**：聚合失败时 `count=None`，`rate=None`。编造 0 会被读成
   「没人走到这一步」，导致砍掉一个其实有人用的功能。
3. **中间断链后不再算比率**：上一阶段未知时，后面所有 `rate_from_prev` 必须是 None
   —— 拿错分母算出来的比率比没有比率更危险。
4. **只读**：本模块只允许 SELECT（它跑在看板刷新路径上，写操作会污染数据）。
"""

import re
import unittest
from pathlib import Path

from app.services import funnel_service as fs

SERVICE_FILE = Path(fs.__file__)
REPO_ROOT = Path(__file__).resolve().parents[2]


class RateTests(unittest.TestCase):
    def test_zero_denominator_is_unknown_not_zero(self):
        """分母为 0（还没有样本）→ None，不能是 0.0"""
        self.assertIsNone(fs._rate(0, 0))
        self.assertIsNone(fs._rate(5, 0))
        self.assertIsNone(fs._rate(5, None))
        self.assertIsNone(fs._rate(None, 5))

    def test_normal_rate_is_rounded(self):
        self.assertEqual(fs._rate(1, 3), 0.3333)
        self.assertEqual(fs._rate(0, 10), 0.0)
        self.assertEqual(fs._rate(10, 10), 1.0)


class StageTests(unittest.TestCase):
    def test_stage_keys_are_stable(self):
        """key 是前端渲染与埋点对齐的锚点，改名等于静默改变展示"""
        self.assertEqual(
            [key for key, _ in fs.FUNNEL_STAGES],
            ["registered", "consumed", "free_exhausted", "applied", "approved"],
        )

    def test_nested_counts_produce_monotonic_stages(self):
        stages = fs.build_stages([100, 40, 30, 8, 5])
        counts = [s["count"] for s in stages]
        self.assertEqual(counts, sorted(counts, reverse=True), "阶段人数必须单调递减")
        self.assertEqual(stages[1]["rate_from_prev"], 0.4)
        self.assertEqual(stages[2]["rate_from_prev"], 0.75)
        self.assertEqual(stages[4]["rate_from_start"], 0.05)

    def test_first_stage_has_no_prev_rate(self):
        stages = fs.build_stages([10, 5, 5, 0, 0])
        self.assertIsNone(stages[0]["rate_from_prev"])
        self.assertEqual(stages[0]["rate_from_start"], 1.0)

    def test_unknown_stage_breaks_following_rates(self):
        """中间一环未知 → 后面所有阶段间比率都必须是 None（分母不可信）"""
        stages = fs.build_stages([10, None, 4, 2, 1])
        self.assertIsNone(stages[1]["count"])
        self.assertIsNone(stages[2]["rate_from_prev"])
        self.assertIsNone(stages[3]["rate_from_prev"])
        self.assertIsNone(stages[4]["rate_from_prev"])
        # 相对起点的比率仍可算（分母是已知的注册数），但该阶段本身未知
        self.assertIsNone(stages[1]["rate_from_start"])

    def test_zero_counts_are_zero_not_none(self):
        """真的没人走到这一步 = 0（与「未知」区分开）"""
        stages = fs.build_stages([10, 0, 0, 0, 0])
        self.assertEqual(stages[1]["count"], 0)
        self.assertEqual(stages[1]["rate_from_prev"], 0.0)


class ReadOnlyGuardTests(unittest.TestCase):
    def test_module_has_no_dml(self):
        source = SERVICE_FILE.read_text(encoding="utf-8")
        offenders = re.findall(
            r"\b(DELETE\s+FROM|UPDATE\s+\w+\s+SET|INSERT\s+INTO|TRUNCATE\s+TABLE|DROP\s+TABLE|ALTER\s+TABLE)\b",
            source,
            flags=re.IGNORECASE,
        )
        self.assertEqual(offenders, [], f"漏斗模块出现写操作，违反只读契约: {offenders}")

    def test_free_limit_is_parameterised(self):
        """免费时长必须取配置，不能写死 10800 —— 改配置时漏斗口径要跟着变"""
        source = SERVICE_FILE.read_text(encoding="utf-8")
        self.assertIn("settings.FREE_TIME_LIMIT", source)
        self.assertNotIn("INTERVAL 10800", source)


class RouteTests(unittest.TestCase):
    def test_route_registered_and_reuses_dashboard_permission(self):
        from app.core.rbac import match_route_permission
        from app.main import app

        self.assertIn("/api/v1/admin/funnel", app.openapi()["paths"])
        self.assertEqual(match_route_permission("GET", "/api/v1/admin/funnel"), ["dashboard:view"])

    def test_payload_survives_response_model(self):
        """response_model 会在返回前校验：字段不在模型里会被静默丢弃"""
        from app.schemas.admin import StatsResult

        payload = {
            "funnel": {
                "days": 30,
                "since": "2026-09-27",
                "stages": [{"key": "registered", "label": "注册", "count": 3,
                            "rate_from_start": 1.0, "rate_from_prev": None}],
                "approval_rate": 0.5,
                "paid_rate": 0.1,
                "ts": 1,
            },
            "retention": {"days": 14, "rows": [], "ts": 1},
        }
        dumped = StatsResult.model_validate(payload).model_dump()
        self.assertIn("funnel", dumped)
        self.assertIn("retention", dumped)


if __name__ == "__main__":
    unittest.main()
