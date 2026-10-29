import unittest
from unittest import mock

from app.models.ai_model import AiModel
from app.services import ai_model_service
from app.services.ai import factory
from tests.conftest import make_user
from tests.fake_db import MemorySession


class ModalityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = MemorySession()
        self.admin = make_user(role="admin")
        self._patch = mock.patch.object(
            ai_model_service, "_invalidate_runtime_caches", new=mock.AsyncMock()
        )
        self._patch.start()
        factory._providers.clear()

    async def asyncTearDown(self):
        self._patch.stop()
        factory._providers.clear()

    def test_registry_filters_by_modality(self):
        self.assertIsNotNone(factory.provider_factory("deepseek", modality="text"))
        self.assertIsNone(factory.provider_factory("deepseek", modality="image"))

    def test_image_provider_instance(self):
        """图像 Provider 只注册在 image 模态，文本工厂不会取到它"""
        from app.services.ai.dashscope_image_provider import DashScopeImageProvider

        self.assertIs(
            factory.provider_factory("dashscope_image", modality="image"),
            DashScopeImageProvider,
        )
        self.assertIsNone(factory.provider_factory("dashscope_image", modality="text"))

    async def test_text_model_still_becomes_text_default(self):
        await ai_model_service.create_model(self.db, {
            "display_name": "DeepSeek", "provider": "deepseek", "is_enabled": True,
        }, self.admin)
        row = self.db.all_rows(AiModel)[0]
        self.assertEqual(row["modality"], "text")
        self.assertTrue(row["is_default"])

    def _image_model(self, code, image_price, is_enabled=True):
        return AiModel(
            code=code, display_name=code, provider="dashscope_image", modality="image",
            image_price=image_price, model_name="", base_url="", api_key_enc="",
            input_price=0, output_price=0, is_enabled=is_enabled, is_default=False,
            sort_order=0,
        )

    async def test_has_enabled_image_price_requires_enabled_priced_image_model(self):
        self.db.seed(self._image_model("wanx-free", 0))
        self.assertFalse(await ai_model_service.has_enabled_image_price(self.db))

        self.db.seed(self._image_model("wanx-paid", 0.25, is_enabled=False))
        self.assertFalse(
            await ai_model_service.has_enabled_image_price(self.db),
            "禁用模型的单价不能参与「是否已配置图片单价」的判断",
        )

        paid = self.db.all_rows(AiModel)[1]
        paid_row = self.db.get(AiModel, paid["id"])
        paid_row.is_enabled = True
        await self.db.flush()
        self.assertTrue(await ai_model_service.has_enabled_image_price(self.db))
