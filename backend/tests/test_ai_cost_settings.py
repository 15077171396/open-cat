"""AI 成本护栏（2026-10-27 并入「系统 → 模型管理」）守卫。

并入而不是独立成模块，是因为它和单模型单价互相解释：
模型单价填 0 = 回退兜底单价，兜底单价填 0 = 不估算金额。抽成独立页时，
调任一侧都看不到另一侧 —— 这不是布局问题，是「配置读不全就敢改」。

本文件钉住四件事：
1. 归属：4 个键只归 `SURFACE_MODEL`，系统设置里不再出现（双写会让两边互相覆盖）；
2. 写范围：拿着 `ai_model:edit` 也写不了 COS / 支付 / 登录的键；
3. 限额语义：0 = 不限制、剩余额度为 `None` —— 页面若把 0 当「0 次可用」会直接误伤全站；
4. 路由：`/ai-models/cost-settings` 必须命中自己的权限规则，不能被 `{model_id}` 吃掉。
"""

import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import select

from app.core.rbac import match_route_permission
from app.models.system_setting import SystemSetting
from app.services import ai_cost_settings_service, setting_service
from app.services.setting_service import (
    SURFACE_LEGAL,
    SURFACE_LOGIN,
    SURFACE_MODEL,
    SURFACE_PAYMENT,
    SURFACE_SETTINGS,
)
from tests.fake_db import MemorySession

SOURCE = Path(__file__).resolve().parents[1] / "app" / "api" / "admin_ai_models.py"

_COST_KEYS = {
    "ai_global_daily_limit",
    "ai_global_monthly_limit",
    "ai_price_input_per_mtok",
    "ai_price_output_per_mtok",
}

_EMPTY_SUMMARY = {
    "today": {"calls": 0, "total_tokens": 0, "blocked": 0, "estimated_cost": None},
    "month": {"calls": 0, "total_tokens": 0, "estimated_cost": None},
    "limits": {"daily": 0, "monthly": 0, "remaining_today": None, "remaining_month": None},
    "pricing": {"input_per_mtok": 0, "output_per_mtok": 0, "configured": False, "per_model": {}},
}


class OwnershipTests(unittest.TestCase):
    """① 归属：只属于模型管理，系统设置里不再有 ai 分组"""

    def test_module_owns_exactly_the_four_cost_keys(self):
        self.assertEqual(set(ai_cost_settings_service.COST_SETTING_KEYS), _COST_KEYS)

    def test_cost_keys_are_model_surface_only(self):
        for surface in (SURFACE_SETTINGS, SURFACE_LOGIN, SURFACE_LEGAL, SURFACE_PAYMENT):
            with self.subTest(surface=surface):
                self.assertFalse(
                    _COST_KEYS & setting_service.keys_for_surface(surface),
                    f"成本护栏键同时归属 {surface}：会和模型管理互相覆盖",
                )

    def test_system_settings_payload_has_no_ai_group(self):
        """系统设置接口不得再下发 ai 分组（双写 = 两边各存一次）"""
        groups = {g["key"] for g in setting_service.groups_payload(surface=SURFACE_SETTINGS)}
        self.assertNotIn("ai", groups)
        defs = setting_service.definitions_payload(surface=SURFACE_SETTINGS)
        self.assertFalse([d["key"] for d in defs if d["key"] in _COST_KEYS])

    def test_group_label_matches_definitions(self):
        """分组标题与设置项 group_label 一致（导入期也校验，这里给明确报错）"""
        groups = {g["key"]: g["label"] for g in ai_cost_settings_service.groups()}
        self.assertEqual(groups.get("ai"), "全站成本护栏")
        for defn in ai_cost_settings_service.definitions():
            with self.subTest(key=defn["key"]):
                self.assertEqual(defn["group_label"], groups["ai"])


class WriteScopeTests(unittest.IsolatedAsyncioTestCase):
    """② 写通道只能写成本护栏 4 项"""

    async def asyncSetUp(self):
        self.db = MemorySession()

    async def _rows(self):
        return {
            r.setting_key: r.value
            for r in (await self.db.execute(select(SystemSetting))).scalars().all()
        }

    async def test_accepts_cost_keys(self):
        changed = await ai_cost_settings_service.update(
            self.db, {"ai_global_daily_limit": "1000", "ai_price_input_per_mtok": "1.5"}
        )
        await self.db.commit()
        self.assertEqual(changed, 2)
        self.assertEqual((await self._rows())["ai_global_daily_limit"], "1000")

    async def test_rejects_foreign_keys(self):
        for key in ("cos_secret_key", "payment_enabled", "phone_login_enabled", "site_name"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    await ai_cost_settings_service.update(
                        self.db, {"ai_global_daily_limit": "10", key: "injected"}
                    )
        self.assertEqual(await self._rows(), {}, "越界写入没有被整批放弃")


class QuotaSnapshotTests(unittest.IsolatedAsyncioTestCase):
    """③ 限额语义：0 = 不限制；剩余为 None 时页面必须显示「未限制」"""

    async def asyncSetUp(self):
        self.db = MemorySession()

    async def _snapshot(self, summary: dict) -> dict:
        with mock.patch.object(ai_cost_settings_service, "get_usage_summary", return_value=summary):
            return (await ai_cost_settings_service.overview(self.db))["quota"]

    async def test_zero_limit_keeps_remaining_none(self):
        quota = await self._snapshot(_EMPTY_SUMMARY)
        self.assertEqual(quota["daily_limit"], 0)
        self.assertIsNone(quota["remaining_today"])
        self.assertIsNone(quota["remaining_month"])

    async def test_usage_and_remaining_come_from_summary(self):
        summary = {
            "today": {"calls": 830, "total_tokens": 120_000, "blocked": 7, "estimated_cost": 1.23},
            "month": {"calls": 12_000, "total_tokens": 3_000_000, "estimated_cost": 18.4},
            "limits": {
                "daily": 1000,
                "monthly": 50_000,
                "remaining_today": 170,
                "remaining_month": 38_000,
            },
            "pricing": {
                "input_per_mtok": 1.0,
                "output_per_mtok": 2.0,
                "configured": True,
                "per_model": {"deepseek-chat": {"input": 1.0, "output": 2.0}},
            },
        }
        quota = await self._snapshot(summary)
        self.assertEqual(quota["today_calls"], 830)
        self.assertEqual(quota["remaining_today"], 170)
        self.assertEqual(quota["today_blocked"], 7)
        self.assertEqual(quota["priced_models"], 1)
        self.assertTrue(quota["price_configured"])

    async def test_overview_carries_definitions_and_values(self):
        with mock.patch.object(
            ai_cost_settings_service, "get_usage_summary", return_value=_EMPTY_SUMMARY
        ):
            payload = await ai_cost_settings_service.overview(self.db)
        self.assertEqual({d["key"] for d in payload["definitions"]}, _COST_KEYS)
        self.assertIn("quota", payload)
        self.assertIsInstance(payload["values"], dict)


class RouteTests(unittest.TestCase):
    """④ 路由与权限：字面量路径不能被 {model_id} 抢先匹配"""

    def test_cost_settings_has_own_permission_codes(self):
        self.assertEqual(
            match_route_permission("GET", "/api/v1/admin/ai-models/cost-settings"),
            ["ai_model:view"],
        )
        self.assertEqual(
            match_route_permission("PUT", "/api/v1/admin/ai-models/cost-settings"),
            ["ai_model:edit"],
        )

    def test_model_detail_still_matched_by_param_rule(self):
        self.assertEqual(
            match_route_permission("PUT", "/api/v1/admin/ai-models/42"), ["ai_model:edit"]
        )

    def test_routes_are_registered_in_openapi(self):
        from app.main import app

        paths = app.openapi()["paths"]
        self.assertIn("/api/v1/admin/ai-models/cost-settings", paths)
        self.assertIn("get", paths["/api/v1/admin/ai-models/cost-settings"])
        self.assertIn("put", paths["/api/v1/admin/ai-models/cost-settings"])

    def test_declared_before_model_id_route(self):
        """FastAPI 按声明顺序匹配：字面量必须排在 `{model_id}` 之前，否则 PUT 走错函数

        本仓库的 FastAPI 版本把 include_router 保留为 `_IncludedRouter`，无法从
        `app.routes` 还原真实顺序，所以直接读源码判断声明先后（等价且稳定）。
        """
        source = SOURCE.read_text(encoding="utf-8")
        literal = source.index('@router.put("/ai-models/cost-settings"')
        param = source.index('@router.put("/ai-models/{model_id}"')
        self.assertLess(
            literal, param, "cost-settings 必须声明在 {model_id} 之前，否则 PUT 会被当成改模型"
        )
