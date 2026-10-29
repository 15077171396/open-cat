"""AI 护栏配置的「后台可改」链路（2026-10-19）。

背景：日/月调用上限与 tokens 单价原先只能改 .env + 重启，运营看着看板的
「未配置单价 / 未设全站上限」却无处可填。现在与 AI Provider 凭据同一口径：
**DB（后台系统设置）优先，逐项回退环境变量**，且必须满足三条契约：

1. **零行为变更**：后台一项都不配时，行为与升级前完全一致（等于读 .env）；
2. **保存即生效**：后台保存后主动失效缓存，另一个 gunicorn worker 不能继续用旧值；
3. **配置层故障不伤业务**：DB/Redis 不可用一律回退 env，且**不得**让 AI 调用卡在
   建连超时上（DB 故障后 60s 内不再重试）。

本文件只覆盖「后台配置 → 生效值」这条链路；护栏本身的并发语义在 test_ai_quota.py。
"""

import unittest

from app.config import settings
from app.core.exceptions import AppException
from app.models.system_setting import SystemSetting
from app.services import ai_usage_service, setting_service
from tests.conftest import FakeRedis


class _BrokenRedis:
    """任何命令都抛异常的 Redis"""

    def __getattr__(self, _name):
        def _raise(*_args, **_kwargs):
            raise RuntimeError("redis is down")

        return _raise


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """只实现 _load_setting_overrides 用到的最小会话接口"""

    def __init__(self, rows, counter=None):
        self._rows = rows
        self._counter = counter

    async def __aenter__(self):
        if self._counter is not None:
            self._counter["queries"] += 1
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        return _Rows(self._rows)


def _rows_for(values: dict[str, str]):
    return [SystemSetting(setting_key=k, value=v) for k, v in values.items()]


class AiQuotaConfigTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig_redis = ai_usage_service.redis_client
        self._orig_session = ai_usage_service.async_session
        self._orig_retry_after = ai_usage_service._db_retry_after
        self._orig_warned = ai_usage_service._db_unavailable_warned
        self._orig_env = (
            settings.AI_GLOBAL_DAILY_LIMIT,
            settings.AI_GLOBAL_MONTHLY_LIMIT,
            settings.AI_PRICE_INPUT_PER_MTOK,
            settings.AI_PRICE_OUTPUT_PER_MTOK,
            settings.AI_MODEL_PRICES,
        )
        self.redis = FakeRedis()
        ai_usage_service.redis_client = self.redis
        ai_usage_service._db_retry_after = 0.0
        ai_usage_service._db_unavailable_warned = True  # 测试里不刷告警
        # 干净的 env 基线：默认全部「不启用」
        settings.AI_GLOBAL_DAILY_LIMIT = 0
        settings.AI_GLOBAL_MONTHLY_LIMIT = 0
        settings.AI_PRICE_INPUT_PER_MTOK = 0.0
        settings.AI_PRICE_OUTPUT_PER_MTOK = 0.0
        settings.AI_MODEL_PRICES = ""
        self.counter = {"queries": 0}

    def tearDown(self):
        ai_usage_service.redis_client = self._orig_redis
        ai_usage_service.async_session = self._orig_session
        ai_usage_service._db_retry_after = self._orig_retry_after
        ai_usage_service._db_unavailable_warned = self._orig_warned
        (
            settings.AI_GLOBAL_DAILY_LIMIT,
            settings.AI_GLOBAL_MONTHLY_LIMIT,
            settings.AI_PRICE_INPUT_PER_MTOK,
            settings.AI_PRICE_OUTPUT_PER_MTOK,
            settings.AI_MODEL_PRICES,
        ) = self._orig_env

    def _db(self, values: dict[str, str]):
        ai_usage_service.async_session = lambda: _FakeSession(_rows_for(values), self.counter)

    # ---------- 1. 零行为变更 ----------

    async def test_no_admin_config_equals_env(self):
        """后台一项都没配 → 与升级前完全一致（全部读 env）"""
        self._db({})
        cfg = await ai_usage_service.get_quota_config()
        self.assertEqual(cfg.daily_limit, 0)
        self.assertEqual(cfg.monthly_limit, 0)
        self.assertFalse(cfg.price_configured)

    async def test_blank_values_fall_back_to_env(self):
        """空串 = 「没配」而不是「配成 0」——否则恢复默认会静默关掉 env 里的限额"""
        settings.AI_GLOBAL_DAILY_LIMIT = 5000
        settings.AI_PRICE_INPUT_PER_MTOK = 1.0
        self._db({"ai_global_daily_limit": "", "ai_price_input_per_mtok": "  "})
        cfg = await ai_usage_service.get_quota_config()
        self.assertEqual(cfg.daily_limit, 5000)
        self.assertEqual(cfg.price_input, 1.0)

    # ---------- 2. 后台值生效 ----------

    async def test_admin_price_overrides_env(self):
        settings.AI_PRICE_INPUT_PER_MTOK = 1.0
        self._db({"ai_price_input_per_mtok": "2", "ai_price_output_per_mtok": "8"})
        cfg = await ai_usage_service.get_quota_config()
        self.assertEqual((cfg.price_input, cfg.price_output), (2.0, 8.0))
        self.assertTrue(cfg.price_configured)

    async def test_admin_limits_override_env(self):
        settings.AI_GLOBAL_DAILY_LIMIT = 1000
        self._db({"ai_global_daily_limit": "3"})
        cfg = await ai_usage_service.get_quota_config()
        self.assertEqual(cfg.daily_limit, 3)
        # 未配置的月份上限仍沿用 env
        self.assertEqual(cfg.monthly_limit, 0)

    async def test_model_management_prices_override_env(self):
        """模型管理里的单价按 code 覆盖 env，但未覆盖的模型仍保留 env 单价"""
        from unittest import mock

        settings.AI_MODEL_PRICES = '{"deepseek": {"input": 9, "output": 9}}'
        self._db({})
        with mock.patch.object(
            ai_usage_service.ai_model_service,
            "list_model_prices",
            new=mock.AsyncMock(return_value={"openai": (1.0, 2.0)}),
        ):
            cfg = await ai_usage_service.get_quota_config()
        self.assertEqual(cfg.price_for("openai"), (1.0, 2.0))
        # 与旧「整表替换」不同：模型管理只覆盖自己声明的模型
        self.assertEqual(cfg.price_for("deepseek"), (9.0, 9.0))

    async def test_summary_uses_admin_price(self):
        """看板金额必须按后台单价算 —— 这是「填了单价却没变化」的回归点"""
        self._db({"ai_price_input_per_mtok": "2", "ai_price_output_per_mtok": "8"})
        await ai_usage_service.record_tokens(1_000_000, 500_000)
        summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertAlmostEqual(summary["today"]["estimated_cost"], 6.0, places=4)
        self.assertTrue(summary["pricing"]["configured"])
        self.assertEqual(summary["pricing"]["input_per_mtok"], 2.0)

    async def test_daily_limit_from_admin_blocks_calls(self):
        self._db({"ai_global_daily_limit": "2"})
        for _ in range(2):
            self.assertIsNotNone(await ai_usage_service.reserve_quota())
        with self.assertRaises(AppException) as ctx:
            await ai_usage_service.reserve_quota()
        self.assertEqual(ctx.exception.code, 429)

    # ---------- 3. 缓存与失效 ----------

    async def test_second_read_hits_cache_not_db(self):
        self._db({"ai_price_input_per_mtok": "1"})
        await ai_usage_service.get_quota_config()
        await ai_usage_service.get_quota_config()
        self.assertEqual(self.counter["queries"], 1, "60s 内应只回源一次 DB")

    async def test_invalidate_forces_reload(self):
        self._db({"ai_price_input_per_mtok": "1"})
        self.assertEqual((await ai_usage_service.get_quota_config()).price_input, 1.0)

        # 后台改成 5 元并保存 → 失效缓存 → 立即读到新值（另一个 worker 同理）
        self._db({"ai_price_input_per_mtok": "5"})
        await ai_usage_service.invalidate_quota_config_cache()
        self.assertEqual((await ai_usage_service.get_quota_config()).price_input, 5.0)

    # ---------- 4. 故障降级 ----------

    async def test_db_failure_falls_back_to_env(self):
        def _boom():
            raise RuntimeError("db down")

        settings.AI_PRICE_INPUT_PER_MTOK = 1.5
        ai_usage_service.async_session = _boom
        cfg = await ai_usage_service.get_quota_config()
        self.assertEqual(cfg.price_input, 1.5)

    async def test_db_failure_backs_off(self):
        """DB 挂了不能每次 AI 调用都等 10s 建连超时"""
        def _boom():
            raise RuntimeError("db down")

        ai_usage_service.async_session = _boom
        await ai_usage_service.get_quota_config()
        self.assertGreater(ai_usage_service._db_retry_after, 0.0)

        # 退避期内不再触碰 DB：把 async_session 换成会抛 AssertionError 的桩来证明
        ai_usage_service.async_session = lambda: (_ for _ in ()).throw(
            AssertionError("退避期内不应再查库")
        )
        await ai_usage_service.get_quota_config()

    async def test_redis_failure_still_reads_db(self):
        """Redis 只是缓存层：它挂了仍应读到后台配置（否则改完单价看板永远不变）"""
        ai_usage_service.redis_client = _BrokenRedis()
        self._db({"ai_price_output_per_mtok": "4"})
        cfg = await ai_usage_service.get_quota_config()
        self.assertEqual(cfg.price_output, 4.0)


class AiQuotaSettingValidationTests(unittest.TestCase):
    """后台表单的值必须先过校验：坏值落库后运行时只会打日志，表现为「配了不生效」"""

    def _check(self, key: str, value):
        defn = next(d for d in setting_service.SETTING_DEFINITIONS if d.key == key)
        return setting_service._validate_setting_value(defn, value)

    def test_number_accepts_and_normalizes(self):
        self.assertEqual(self._check("ai_global_daily_limit", "5000"), "5000")
        self.assertEqual(self._check("ai_global_daily_limit", " 5000.0 "), "5000")
        self.assertEqual(self._check("ai_price_input_per_mtok", "0.0001"), "0.0001")

    def test_number_rejects_garbage_and_negatives(self):
        for bad in ("abc", "-1", "nan", "inf", "1,000"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self._check("ai_global_daily_limit", bad)

    def test_number_blank_means_restore_default(self):
        self.assertEqual(self._check("ai_global_daily_limit", "   "), "")

    def test_definitions_are_admin_only(self):
        """这 4 个护栏键只给后台/后端用，绝不能进公开设置（会发给所有访客）"""
        defs = {d.key: d for d in setting_service.SETTING_DEFINITIONS}
        for key in (
            "ai_global_daily_limit",
            "ai_global_monthly_limit",
            "ai_price_input_per_mtok",
            "ai_price_output_per_mtok",
        ):
            with self.subTest(key=key):
                self.assertFalse(defs[key].public)
                self.assertFalse(defs[key].secret)
                self.assertEqual(defs[key].group, "ai")


if __name__ == "__main__":
    unittest.main()
