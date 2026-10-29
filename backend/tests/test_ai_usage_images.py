import unittest
from unittest import mock

from app.config import settings
from app.core.exceptions import AppException
from app.services import ai_usage_service
from tests.conftest import FakeRedis


class ImageQuotaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._redis = ai_usage_service.redis_client
        self._session = ai_usage_service.async_session
        self._retry = ai_usage_service._db_retry_after
        self._orig_limit = settings.AI_WRITING_IMAGE_DAILY_LIMIT
        ai_usage_service.redis_client = FakeRedis()
        ai_usage_service.async_session = lambda: (_ for _ in ()).throw(RuntimeError("db down"))
        ai_usage_service._db_retry_after = 0.0
        settings.AI_WRITING_IMAGE_DAILY_LIMIT = 2

    def tearDown(self):
        ai_usage_service.redis_client = self._redis
        ai_usage_service.async_session = self._session
        ai_usage_service._db_retry_after = self._retry
        settings.AI_WRITING_IMAGE_DAILY_LIMIT = self._orig_limit

    async def test_image_limit_blocks_and_releases(self):
        first = await ai_usage_service.reserve_image_quota(2)
        self.assertIsNotNone(first)
        with self.assertRaises(AppException) as ctx:
            await ai_usage_service.reserve_image_quota(1)
        self.assertEqual(ctx.exception.code, 429)
        await ai_usage_service.release_image_quota(first, 2)
        # 归还后预占清零，同样两张应能再次占上
        self.assertIsNotNone(await ai_usage_service.reserve_image_quota(2))

    async def test_reserve_record_release_counts_usage_once(self):
        """回归：预占与实耗分键，成功出图只记一次（预占 2 + 记 2 + 整张归还 ≠ 4）。"""
        ticket = await ai_usage_service.reserve_image_quota(2)
        self.assertIsNotNone(ticket)
        await ai_usage_service.record_image_usage("wanx", 2, 0.25)
        # Task 8 的调用方式：成功数已由 record_image_usage 记账，票据整张归还
        await ai_usage_service.release_image_quota(ticket)

        with mock.patch.object(
            ai_usage_service.ai_model_service, "has_enabled_image_price",
            new=mock.AsyncMock(return_value=True),
        ):
            summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertEqual(summary["today"]["images"], 2)
        self.assertAlmostEqual(summary["today"]["image_cost"], 0.5)
        self.assertEqual(
            summary["limits"]["remaining_images"],
            summary["limits"]["image_daily"] - 2,
        )
        # 预占键只归 reserve/release 维护，归还后必须归零（否则会永久吃掉额度）
        self.assertEqual(int(await ai_usage_service.redis_client.get(ticket.reserve_key)), 0)

    async def test_record_image_usage_and_summary(self):
        await ai_usage_service.record_image_usage("wanx", 2, 0.25)
        with mock.patch.object(
            ai_usage_service.ai_model_service, "has_enabled_image_price",
            new=mock.AsyncMock(return_value=True),
        ):
            summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertEqual(summary["today"]["images"], 2)
        self.assertAlmostEqual(summary["today"]["image_cost"], 0.5)
        self.assertEqual(summary["today"]["images_by_model"][0]["model"], "wanx")

    async def test_image_cost_is_null_when_no_image_price_configured(self):
        """有图但没配任何图像单价：必须是 null（未配置单价），不是 0 元。"""
        await ai_usage_service.record_image_usage("wanx", 2, 0)
        with mock.patch.object(
            ai_usage_service.ai_model_service, "has_enabled_image_price",
            new=mock.AsyncMock(return_value=False),
        ):
            summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertEqual(summary["today"]["images"], 2)
        self.assertIsNone(summary["today"]["image_cost"])
        self.assertIsNone(summary["month"]["image_cost"])

    async def test_image_cost_stays_numeric_zero_when_price_configured(self):
        """配了单价但本次实际单价为 0（免费模型）：保持数字 0，不能变成 null。"""
        await ai_usage_service.record_image_usage("free-image-model", 1, 0)
        with mock.patch.object(
            ai_usage_service.ai_model_service, "has_enabled_image_price",
            new=mock.AsyncMock(return_value=True),
        ):
            summary = await ai_usage_service.get_usage_summary(days=1)
        self.assertEqual(summary["today"]["image_cost"], 0)
        self.assertIsNotNone(summary["today"]["image_cost"])
