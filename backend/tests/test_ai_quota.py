"""AI 全站成本护栏与用量统计回归测试（缺口台账 P1-2）。

台账登记的问题是「仅有单用户日限额，无全局日/月上限，也无用量看板 —— 被刷量时
无法感知实际花费」。本文件把护栏的几条**契约**钉死，防止后续重构悄悄摘掉：

1. **默认不限制**：阈值 0 时无论调用多少次都不拦截（升级前后行为一致，
   这是「不影响现有功能」的硬约束）；
2. **超限必拦且计数不虚高**：触顶后抛 429，且被拒的请求**不能**把计数继续顶高
   （「先占后判 + 拒绝即归还」，与 P0-6 积分扣减同一个并发教训）；
3. **失败即归还**：AI 调用失败时归还占位，使计数语义保持「成功调用数」；
4. **Redis 故障 fail-open**：配额层不可用不得把 AI 功能打成 500；
5. **用量可观测**：调用次数 / token / 拦截次数按天与按月都能读出来。

⚠️ 特别守一条**容易被写错**的：日上限触顶时必须同时归还「月」计数——
只归还日计数会让月计数被被拒请求一路顶高，最终把月上限也拖爆。
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.config import settings
from app.core.exceptions import AppException
from app.services import ai_usage_service
from tests.conftest import FakeRedis


class _BrokenRedis:
    """任何命令都抛异常的 Redis：验证护栏的 fail-open 降级。"""

    def __getattr__(self, _name):
        def _raise(*args, **kwargs):
            raise RuntimeError("redis is down")

        return _raise


def _failing_session():
    """让 async_session() 立即抛错：本文件只验证「env 配置 + Redis 计数」这条路径，
    绝不能在测试里去连不可达的 MySQL（否则每次建连要等 10s 超时）。"""
    raise RuntimeError("DB 不可达（测试桩）")


class AiQuotaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig_redis = ai_usage_service.redis_client
        self._orig_session = ai_usage_service.async_session
        self._orig_retry_after = ai_usage_service._db_retry_after
        self._orig_daily = settings.AI_GLOBAL_DAILY_LIMIT
        self._orig_monthly = settings.AI_GLOBAL_MONTHLY_LIMIT
        self._orig_in_price = settings.AI_PRICE_INPUT_PER_MTOK
        self._orig_out_price = settings.AI_PRICE_OUTPUT_PER_MTOK
        self._orig_model_prices = settings.AI_MODEL_PRICES
        self.redis = FakeRedis()
        ai_usage_service.redis_client = self.redis
        ai_usage_service.async_session = _failing_session
        ai_usage_service._db_retry_after = 0.0
        settings.AI_GLOBAL_DAILY_LIMIT = 0
        settings.AI_GLOBAL_MONTHLY_LIMIT = 0
        settings.AI_PRICE_INPUT_PER_MTOK = 0.0
        settings.AI_PRICE_OUTPUT_PER_MTOK = 0.0
        settings.AI_MODEL_PRICES = ""

    def tearDown(self):
        ai_usage_service.redis_client = self._orig_redis
        ai_usage_service.async_session = self._orig_session
        ai_usage_service._db_retry_after = self._orig_retry_after
        settings.AI_GLOBAL_DAILY_LIMIT = self._orig_daily
        settings.AI_GLOBAL_MONTHLY_LIMIT = self._orig_monthly
        settings.AI_PRICE_INPUT_PER_MTOK = self._orig_in_price
        settings.AI_PRICE_OUTPUT_PER_MTOK = self._orig_out_price
        settings.AI_MODEL_PRICES = self._orig_model_prices

    async def _count(self, key: str) -> int:
        return int(await self.redis.get(key) or 0)

    # ---------- 1. 默认不限制 ----------

    async def test_default_limits_never_block(self):
        """阈值 0 = 不限制：这是「升级不改变现有行为」的核心保证"""
        for _ in range(50):
            ticket = await ai_usage_service.reserve_quota()
            self.assertIsNotNone(ticket)

        now = datetime.now(timezone.utc)
        self.assertEqual(await self._count(ai_usage_service._calls_day_key(now)), 50)

    # ---------- 2. 日上限：拦得住，且计数不虚高 ----------

    async def test_daily_limit_blocks_and_does_not_inflate_counter(self):
        settings.AI_GLOBAL_DAILY_LIMIT = 3
        now = datetime.now(timezone.utc)

        for _ in range(3):
            self.assertIsNotNone(await ai_usage_service.reserve_quota())

        with self.assertRaises(AppException) as ctx:
            await ai_usage_service.reserve_quota()
        self.assertEqual(ctx.exception.code, 429)

        # 被拒的这一次必须归还：日计数停在 3，而不是 4
        self.assertEqual(await self._count(ai_usage_service._calls_day_key(now)), 3)

    async def test_daily_limit_also_rolls_back_month_counter(self):
        """日上限触顶时必须同时归还月计数，否则月计数会被被拒请求顶高"""
        settings.AI_GLOBAL_DAILY_LIMIT = 2
        now = datetime.now(timezone.utc)

        for _ in range(2):
            await ai_usage_service.reserve_quota()

        for _ in range(5):
            with self.assertRaises(AppException):
                await ai_usage_service.reserve_quota()

        self.assertEqual(await self._count(ai_usage_service._calls_day_key(now)), 2)
        self.assertEqual(await self._count(ai_usage_service._calls_month_key(now)), 2)

    async def test_blocked_calls_are_counted(self):
        """被拦下的次数要可观测——刷量时的第一手信号"""
        settings.AI_GLOBAL_DAILY_LIMIT = 1
        await ai_usage_service.reserve_quota()
        with self.assertRaises(AppException):
            await ai_usage_service.reserve_quota()

        summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertEqual(summary["today"]["blocked"], 1)

    # ---------- 3. 月上限 ----------

    async def test_monthly_limit_blocks_even_when_daily_unlimited(self):
        settings.AI_GLOBAL_MONTHLY_LIMIT = 2
        for _ in range(2):
            await ai_usage_service.reserve_quota()

        with self.assertRaises(AppException) as ctx:
            await ai_usage_service.reserve_quota()
        self.assertEqual(ctx.exception.code, 429)

        now = datetime.now(timezone.utc)
        self.assertEqual(await self._count(ai_usage_service._calls_month_key(now)), 2)

    # ---------- 4. 失败归还 ----------

    async def test_release_quota_returns_the_slot(self):
        settings.AI_GLOBAL_DAILY_LIMIT = 1
        now = datetime.now(timezone.utc)

        ticket = await ai_usage_service.reserve_quota()
        # 未归还前再占一次应当被拦
        with self.assertRaises(AppException):
            await ai_usage_service.reserve_quota()

        await ai_usage_service.release_quota(ticket)
        self.assertEqual(await self._count(ai_usage_service._calls_day_key(now)), 0)
        # 归还后额度重新可用（模拟「AI 调用失败 → 用户重试成功」）
        self.assertIsNotNone(await ai_usage_service.reserve_quota())

    async def test_release_none_is_noop(self):
        """Redis 故障时拿到的是 None，归还必须是安全的空操作"""
        await ai_usage_service.release_quota(None)  # 不抛异常即通过

    # ---------- 5. Redis 故障 fail-open ----------

    async def test_redis_down_fails_open(self):
        """配额层故障不得阻断 AI 功能：返回 None（放行），且不抛异常"""
        ai_usage_service.redis_client = _BrokenRedis()
        self.assertIsNone(await ai_usage_service.reserve_quota())
        # 统计路径同样不得抛异常
        await ai_usage_service.record_tokens(10, 20)
        summary = await ai_usage_service.get_usage_summary(days=3)
        self.assertEqual(summary["today"]["calls"], 0)
        self.assertEqual(len(summary["trend"]), 3)

    # ---------- 6. 用量统计 ----------

    async def test_record_tokens_accumulates_day_and_month(self):
        await ai_usage_service.record_tokens(100, 50)
        await ai_usage_service.record_tokens(10, 5)

        summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertEqual(summary["today"]["prompt_tokens"], 110)
        self.assertEqual(summary["today"]["completion_tokens"], 55)
        self.assertEqual(summary["today"]["total_tokens"], 165)
        self.assertEqual(summary["month"]["prompt_tokens"], 110)

    async def test_record_provider_usage_reads_last_usage(self):
        """记账走 provider.last_usage 旁路属性（不改 chat() 返回类型）"""

        class _Provider:
            last_usage = {"prompt_tokens": 7, "completion_tokens": 3}

        await ai_usage_service.record_provider_usage(_Provider())
        summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertEqual(summary["today"]["total_tokens"], 10)

    async def test_record_provider_usage_tolerates_missing_usage(self):
        """没有 usage 字段（或为空）时不记账，也不报错"""
        class _Provider:
            last_usage = {}

        await ai_usage_service.record_provider_usage(_Provider())
        summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertEqual(summary["today"]["total_tokens"], 0)

    async def test_summary_trend_is_ascending_and_ends_today(self):
        summary = await ai_usage_service.get_usage_summary(days=7)
        dates = [row["date"] for row in summary["trend"]]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(len(dates), 7)
        self.assertEqual(dates[-1], datetime.now(timezone.utc).date().isoformat())

    async def test_summary_days_is_clamped(self):
        """?days= 越界不得拉爆 Redis 往返，也不能报错"""
        too_many = await ai_usage_service.get_usage_summary(days=100000)
        self.assertEqual(len(too_many["trend"]), ai_usage_service.MAX_SUMMARY_DAYS)
        too_few = await ai_usage_service.get_usage_summary(days=0)
        self.assertEqual(len(too_few["trend"]), 1)

    # ---------- 7. 费用估算 ----------

    async def test_cost_is_none_when_price_not_configured(self):
        """单价未配置时返回 None（而不是假的 0 元），前端据此隐藏金额"""
        await ai_usage_service.record_tokens(1_000_000, 0)
        summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertIsNone(summary["today"]["estimated_cost"])
        self.assertFalse(summary["pricing"]["configured"])

    async def test_cost_is_computed_when_price_configured(self):
        settings.AI_PRICE_INPUT_PER_MTOK = 2.0
        settings.AI_PRICE_OUTPUT_PER_MTOK = 8.0
        await ai_usage_service.record_tokens(1_000_000, 500_000)

        summary = await ai_usage_service.get_usage_summary(days=1)
        # 1M 输入 * 2 元 + 0.5M 输出 * 8 元 = 6 元
        self.assertAlmostEqual(summary["today"]["estimated_cost"], 6.0, places=4)
        self.assertTrue(summary["pricing"]["configured"])

    # ---------- 8. 剩余额度 ----------

    async def test_remaining_is_none_when_unlimited(self):
        summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertIsNone(summary["limits"]["remaining_today"])
        self.assertEqual(summary["limits"]["daily"], 0)

    async def test_remaining_counts_down_and_floors_at_zero(self):
        settings.AI_GLOBAL_DAILY_LIMIT = 5
        await ai_usage_service.reserve_quota()
        await ai_usage_service.reserve_quota()
        summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertEqual(summary["limits"]["remaining_today"], 3)

        settings.AI_GLOBAL_DAILY_LIMIT = 1
        summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertEqual(summary["limits"]["remaining_today"], 0)

    async def test_day_keys_expire(self):
        """日粒度键必须带 TTL，否则 Redis 会无限堆积历史键"""
        await ai_usage_service.reserve_quota()
        now = datetime.now(timezone.utc)
        ttl = await self.redis.ttl(ai_usage_service._calls_day_key(now))
        self.assertGreater(ttl, 0)
        self.assertLessEqual(ttl, ai_usage_service._DAY_TTL)


class AiQuotaDateBoundaryTests(unittest.IsolatedAsyncioTestCase):
    """跨天/跨月时计数器必须切换到新键（否则日上限会跨天持续生效）。"""

    def setUp(self):
        self._orig_redis = ai_usage_service.redis_client
        ai_usage_service.redis_client = FakeRedis()

    def tearDown(self):
        ai_usage_service.redis_client = self._orig_redis

    async def test_keys_switch_across_days(self):
        today = datetime.now(timezone.utc)
        yesterday = today - timedelta(days=1)
        self.assertNotEqual(
            ai_usage_service._calls_day_key(today),
            ai_usage_service._calls_day_key(yesterday),
        )

    async def test_keys_switch_across_months(self):
        self.assertNotEqual(
            ai_usage_service._calls_month_key(datetime(2026, 1, 31, tzinfo=timezone.utc)),
            ai_usage_service._calls_month_key(datetime(2026, 2, 1, tzinfo=timezone.utc)),
        )

    async def test_reserve_uses_current_day_key(self):
        """reserve 实际写的是「今天」的键，而不是某个固定键"""
        with patch.object(ai_usage_service, "datetime") as fake_dt:
            fixed = datetime(2026, 3, 4, 10, 0, tzinfo=timezone.utc)
            fake_dt.now.return_value = fixed
            ticket = await ai_usage_service.reserve_quota()

        self.assertEqual(ticket.day_calls_key, "ai_usage:calls:day:20260304")
        self.assertEqual(ticket.month_calls_key, "ai_usage:calls:month:202603")


class AiModelPricingTests(unittest.IsolatedAsyncioTestCase):
    """按模型分别计价（收口 P2-4 遗留：多模型混用时单一单价会算错）。

    要钉死的核心不变量是**总额 = Σ(各模型各自单价) + 未归因 × 兜底单价**：
    - 少算历史数据（未归因没列出来）会让看板金额偏低，且**不报错**；
    - 各模型串用别人的单价会让金额偏高，同样不报错。
    """

    def setUp(self):
        self._orig_redis = ai_usage_service.redis_client
        self._orig_in = settings.AI_PRICE_INPUT_PER_MTOK
        self._orig_out = settings.AI_PRICE_OUTPUT_PER_MTOK
        self._orig_prices = settings.AI_MODEL_PRICES
        ai_usage_service.redis_client = FakeRedis()
        settings.AI_PRICE_INPUT_PER_MTOK = 0.0
        settings.AI_PRICE_OUTPUT_PER_MTOK = 0.0
        settings.AI_MODEL_PRICES = ""

    def tearDown(self):
        ai_usage_service.redis_client = self._orig_redis
        settings.AI_PRICE_INPUT_PER_MTOK = self._orig_in
        settings.AI_PRICE_OUTPUT_PER_MTOK = self._orig_out
        settings.AI_MODEL_PRICES = self._orig_prices

    async def _row(self, summary: dict, model: str) -> dict:
        for row in summary["today"]["by_model"]:
            if row["model"] == model:
                return row
        raise AssertionError(f"by_model 里没有 {model}: {summary['today']['by_model']}")

    # ---------- 分桶 ----------

    async def test_model_buckets_and_totals_are_both_recorded(self):
        await ai_usage_service.record_tokens(100, 50, "deepseek")
        await ai_usage_service.record_tokens(10, 5, "openai")

        summary = await ai_usage_service.get_usage_summary(days=1)
        # 总量照记（分桶不能取代总量）
        self.assertEqual(summary["today"]["total_tokens"], 165)
        self.assertEqual((await self._row(summary, "deepseek"))["total_tokens"], 150)
        self.assertEqual((await self._row(summary, "openai"))["total_tokens"], 15)

    async def test_by_model_is_sorted_by_total_tokens_desc(self):
        await ai_usage_service.record_tokens(10, 0, "openai")
        await ai_usage_service.record_tokens(1000, 0, "deepseek")

        summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertEqual([r["model"] for r in summary["today"]["by_model"]], ["deepseek", "openai"])

    async def test_month_bucket_also_recorded(self):
        await ai_usage_service.record_tokens(7, 3, "qwen")
        summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertEqual((await self._row({"today": summary["month"]}, "qwen"))["total_tokens"], 10)

    async def test_provider_name_is_picked_up_from_provider(self):
        """record_provider_usage 用 provider_name 归因（Provider 类属性）"""

        class _Provider:
            provider_name = "qwen"
            last_usage = {"prompt_tokens": 4, "completion_tokens": 6}

        await ai_usage_service.record_provider_usage(_Provider())
        summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertEqual((await self._row(summary, "qwen"))["total_tokens"], 10)

    async def test_provider_without_name_records_total_only(self):
        """拿不到模型名时只记总量：宁可少一个维度，也不能归因到错误的模型"""

        class _Provider:
            last_usage = {"prompt_tokens": 4, "completion_tokens": 6}

        await ai_usage_service.record_provider_usage(_Provider())
        summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertEqual(summary["today"]["by_model"], [])
        self.assertEqual(summary["today"]["total_tokens"], 10)
        self.assertEqual(summary["today"]["unattributed"]["total_tokens"], 10)

    # ---------- 单价 ----------

    async def test_per_model_price_overrides_global(self):
        settings.AI_PRICE_INPUT_PER_MTOK = 100.0  # 兜底价（故意设得离谱，好区分）
        settings.AI_MODEL_PRICES = '{"deepseek": {"input": 2.0, "output": 0}}'

        await ai_usage_service.record_tokens(1_000_000, 0, "deepseek")
        summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertAlmostEqual((await self._row(summary, "deepseek"))["estimated_cost"], 2.0, places=4)

    async def test_unlisted_model_falls_back_to_global_price(self):
        settings.AI_PRICE_INPUT_PER_MTOK = 3.0
        settings.AI_MODEL_PRICES = '{"deepseek": {"input": 2.0, "output": 0}}'

        await ai_usage_service.record_tokens(1_000_000, 0, "qwen")
        summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertAlmostEqual((await self._row(summary, "qwen"))["estimated_cost"], 3.0, places=4)

    async def test_total_is_sum_of_model_costs_plus_unattributed(self):
        """核心不变量：总额必须等于「各模型 + 未归因」，一分不多一分不少。"""
        settings.AI_MODEL_PRICES = (
            '{"deepseek": {"input": 1.0, "output": 0}, "openai": {"input": 10.0, "output": 0}}'
        )
        settings.AI_PRICE_INPUT_PER_MTOK = 5.0  # 未归因部分的兜底价

        await ai_usage_service.record_tokens(1_000_000, 0, "deepseek")   # 1 元
        await ai_usage_service.record_tokens(1_000_000, 0, "openai")     # 10 元
        await ai_usage_service.record_tokens(1_000_000, 0)               # 未归因 → 5 元

        summary = await ai_usage_service.get_usage_summary(days=1)
        rows = summary["today"]["by_model"]
        rest = summary["today"]["unattributed"]
        expected = sum(r["estimated_cost"] for r in rows) + rest["estimated_cost"]
        self.assertAlmostEqual(summary["today"]["estimated_cost"], expected, places=4)
        self.assertAlmostEqual(summary["today"]["estimated_cost"], 16.0, places=4)
        self.assertEqual(rest["total_tokens"], 1_000_000)

    async def test_unattributed_is_zero_after_all_tokens_attributed(self):
        await ai_usage_service.record_tokens(100, 100, "deepseek")
        summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertEqual(summary["today"]["unattributed"]["total_tokens"], 0)

    async def test_configured_true_when_only_model_prices_set(self):
        """只配了 AI_MODEL_PRICES（全局价仍为 0）也应展示金额"""
        settings.AI_MODEL_PRICES = '{"deepseek": {"input": 1.0, "output": 0}}'
        summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertTrue(summary["pricing"]["configured"])
        self.assertEqual(summary["pricing"]["per_model"]["deepseek"]["input"], 1.0)

    async def test_all_prices_zero_keeps_cost_none(self):
        """单价全为 0 时仍是 None（不是假的 0 元），前端据此隐藏金额"""
        await ai_usage_service.record_tokens(1_000_000, 0, "deepseek")
        summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertIsNone(summary["today"]["estimated_cost"])
        self.assertIsNone((await self._row(summary, "deepseek"))["estimated_cost"])
        self.assertFalse(summary["pricing"]["configured"])

    # ---------- 配置容错 ----------

    async def test_invalid_json_falls_back_without_raising(self):
        settings.AI_MODEL_PRICES = "{不是 JSON"
        settings.AI_PRICE_INPUT_PER_MTOK = 2.0
        await ai_usage_service.record_tokens(1_000_000, 0, "deepseek")

        summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertEqual(summary["pricing"]["per_model"], {})
        self.assertAlmostEqual(summary["today"]["estimated_cost"], 2.0, places=4)

    async def test_non_object_json_is_ignored(self):
        settings.AI_MODEL_PRICES = "[1, 2, 3]"
        self.assertEqual(ai_usage_service._model_prices(), {})

    async def test_bad_entry_is_skipped_but_valid_ones_kept(self):
        settings.AI_MODEL_PRICES = (
            '{"deepseek": {"input": 1.0, "output": 0}, "qwen": 5, "openai": {"input": "x"}}'
        )
        table = ai_usage_service._model_prices()
        self.assertEqual(table, {"deepseek": (1.0, 0.0)})

    async def test_unknown_model_key_warns(self):
        """配置里写了不存在的 Provider 名要打 WARNING：这类拼写错误不会报错，
        只会让单价静默失效（看板金额悄悄退回兜底价）。"""
        with patch.object(ai_usage_service.logger, "warning") as warn:
            settings.AI_MODEL_PRICES = '{"deepsek": {"input": 1.0, "output": 0}}'
            ai_usage_service._model_prices()
        self.assertTrue(warn.called)
        self.assertIn("deepsek", str(warn.call_args))

    async def test_price_cache_follows_config_change(self):
        settings.AI_MODEL_PRICES = '{"deepseek": {"input": 1.0, "output": 0}}'
        self.assertEqual(ai_usage_service._model_prices()["deepseek"], (1.0, 0.0))
        settings.AI_MODEL_PRICES = '{"deepseek": {"input": 9.0, "output": 0}}'
        self.assertEqual(ai_usage_service._model_prices()["deepseek"], (9.0, 0.0))

    def test_model_slug_keeps_key_structure_intact(self):
        """含空格或 ':' 的模型名不能把键结构打乱（否则同一个模型被拆成多个桶）"""
        self.assertNotIn(":", ai_usage_service._model_slug("a:b c"))
        self.assertEqual(ai_usage_service._model_slug("deepseek"), "deepseek")
        self.assertEqual(ai_usage_service._model_slug(""), "unknown")
        self.assertEqual(ai_usage_service._model_slug("gpt-4o.mini_v2"), "gpt-4o.mini_v2")

    async def test_redis_down_does_not_break_summary(self):
        """统计层故障不得影响看板：返回空的分模型列表而不是抛异常"""
        ai_usage_service.redis_client = _BrokenRedis()
        summary = await ai_usage_service.get_usage_summary(days=2)
        self.assertEqual(summary["today"]["by_model"], [])
        self.assertEqual(summary["today"]["unattributed"]["total_tokens"], 0)
