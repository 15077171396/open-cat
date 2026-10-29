"""AI 模型管理（ai_models）契约测试。

钉死的是「不报错但会出事」的几类问题：
1. 第一条模型自动成为默认；任何时刻最多一个默认且必须是启用状态；
2. 禁用/删除默认模型必须自动接替，不能留下「没有默认」的半坏状态；
3. Key 必须加密落库、掩码提交不能覆盖真实值；
4. 被工具引用的模型不能删除（否则工具静默回退，问题只在回答质量上体现）；
5. 运行时解析按 code → 供应商 → 全局默认回退，禁用模型自动回退；
6. 模型单价进入用量计价，且只覆盖自己声明的 code。
"""

import json
import unittest
from contextlib import asynccontextmanager
from unittest import mock

from sqlalchemy import select

from app.core import crypto
from app.core.exceptions import AppException
from app.models.ai_model import AiModel
from app.models.ai_writing_prompt import AiWritingPrompt
from app.services import ai_config_service, ai_model_service
from tests.conftest import FakeRedis, make_user
from tests.fake_db import MemorySession


def _session_ctx(session):
    @asynccontextmanager
    async def _ctx():
        yield session

    return _ctx


def _model(**kwargs):
    data = {
        "code": "deepseek",
        "display_name": "DeepSeek",
        "provider": "deepseek",
        "model_name": "",
        "base_url": "",
        "api_key_enc": "",
        "input_price": 0,
        "output_price": 0,
        "is_enabled": True,
        "is_default": False,
        "sort_order": 0,
        "remark": None,
    }
    data.update(kwargs)
    return AiModel(**data)


class AiModelValidationTests(unittest.TestCase):
    def test_provider_must_be_registered(self):
        with self.assertRaises(AppException):
            ai_model_service._clean_provider("no-such-provider")

    def test_base_url_rejects_loopback_and_non_http(self):
        for bad in ("http://127.0.0.1:8000", "ftp://example.com", "javascript:alert(1)"):
            with self.subTest(bad=bad):
                with self.assertRaises(AppException):
                    ai_model_service._clean_base_url(bad)

    def test_code_must_be_slug(self):
        for bad in ("Bad Code", "-leading", "中文", "a" * 65):
            with self.subTest(bad=bad):
                with self.assertRaises(AppException):
                    ai_model_service._clean_code(bad)
        self.assertEqual(ai_model_service._clean_code("DeepSeek-Reasoner"), "deepseek-reasoner")

    def test_price_rejects_negative_and_nan(self):
        for bad in (-1, "nan", "inf", "abc"):
            with self.subTest(bad=bad):
                with self.assertRaises(AppException):
                    ai_model_service._clean_price(bad, "单价")

    def test_create_payload_requires_display_name(self):
        with self.assertRaises(AppException):
            ai_model_service._normalize_payload(
                {"provider": "deepseek", "display_name": " "}, partial=False
            )


class AiModelCrudTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = MemorySession()
        self.admin = make_user(role="admin")
        self._patch = mock.patch.object(
            ai_model_service,
            "_invalidate_runtime_caches",
            new=mock.AsyncMock(),
        )
        self._patch.start()

    async def asyncTearDown(self):
        self._patch.stop()

    async def _create(self, **overrides):
        data = {
            "code": overrides.pop("code", None),
            "display_name": overrides.pop("display_name", "DeepSeek"),
            "provider": overrides.pop("provider", "deepseek"),
            "api_key": overrides.pop("api_key", "sk-test-1234"),
            "is_enabled": overrides.pop("is_enabled", True),
            "is_default": overrides.pop("is_default", False),
        }
        data.update(overrides)
        return await ai_model_service.create_model(self.db, data, self.admin)

    async def test_first_model_becomes_default_and_key_is_encrypted(self):
        result = await self._create()
        row = self.db.all_rows(AiModel)[0]
        self.assertEqual(result["code"], "deepseek")
        self.assertTrue(row["is_default"])
        self.assertTrue(row["api_key_enc"].startswith("enc:v2:"))
        self.assertNotIn("sk-test-1234", row["api_key_enc"])
        self.assertEqual(crypto.decrypt_secret(row["api_key_enc"]), "sk-test-1234")

    async def test_only_one_default(self):
        await self._create(code="deepseek", display_name="DeepSeek")
        await self._create(code="deepseek-reasoner", display_name="DeepSeek R1", model_name="deepseek-reasoner")
        rows = self.db.all_rows(AiModel)
        self.assertEqual(sum(1 for row in rows if row["is_default"]), 1)
        self.assertTrue(next(row for row in rows if row["code"] == "deepseek")["is_default"])

    async def test_set_default_demotes_previous(self):
        await self._create(code="deepseek", display_name="DeepSeek")
        second = await self._create(code="reasoner", display_name="Reasoner")
        await ai_model_service.update_model(self.db, second["id"], {"is_default": True}, self.admin)
        rows = {row["code"]: row for row in self.db.all_rows(AiModel)}
        self.assertTrue(rows["reasoner"]["is_default"])
        self.assertFalse(rows["deepseek"]["is_default"])

    async def test_disable_default_promotes_next_enabled(self):
        await self._create(code="deepseek", display_name="DeepSeek")
        await self._create(code="reasoner", display_name="Reasoner")
        first = self.db.all_rows(AiModel)[0]
        await ai_model_service.update_model(self.db, first["id"], {"is_enabled": False}, self.admin)
        rows = {row["code"]: row for row in self.db.all_rows(AiModel)}
        self.assertFalse(rows["deepseek"]["is_enabled"])
        self.assertFalse(rows["deepseek"]["is_default"], "禁用的模型不能保留默认标记")
        self.assertTrue(rows["reasoner"]["is_default"])

    async def test_mask_submission_does_not_overwrite_key(self):
        result = await self._create()
        before = self.db.all_rows(AiModel)[0]["api_key_enc"]
        await ai_model_service.update_model(
            self.db,
            result["id"],
            {"api_key": crypto.mask_secret(before)},
            self.admin,
        )
        self.assertEqual(self.db.all_rows(AiModel)[0]["api_key_enc"], before)

    async def test_clear_key_falls_back_to_env(self):
        result = await self._create()
        await ai_model_service.update_model(self.db, result["id"], {"api_key": ""}, self.admin)
        self.assertEqual(self.db.all_rows(AiModel)[0]["api_key_enc"], "")

    async def test_list_never_exposes_plaintext_or_ciphertext_key(self):
        await self._create(api_key="sk-secret-9999")
        data = await ai_model_service.list_models(self.db)
        blob = json.dumps(data, ensure_ascii=False, default=str)
        self.assertNotIn("sk-secret-9999", blob)
        self.assertNotIn("enc:v", blob)
        item = data["items"][0]
        self.assertTrue(item["api_key_masked"].endswith("9999"))
        self.assertEqual(item["api_key_source"], "database")

    async def test_delete_referenced_model_is_blocked(self):
        result = await self._create()
        with mock.patch.object(
            ai_model_service, "_tool_reference_count", new=mock.AsyncMock(return_value=3)
        ):
            with self.assertRaises(AppException) as ctx:
                await ai_model_service.delete_model(self.db, result["id"], self.admin)
        self.assertEqual(ctx.exception.code, 409)
        self.assertEqual(len(self.db.all_rows(AiModel)), 1)

    async def test_delete_model_bound_by_writing_prompt_is_blocked(self):
        """提示词仍绑定 model_code 时删模型 = 写作配置静默失效，必须 409。"""
        result = await self._create()
        self.db.seed(AiWritingPrompt(
            operation="rewrite", display_name="改写", description="",
            system_prompt="sys", user_template="{{selection}}", output_type="markdown",
            model_code=result["code"], temperature=0.7, max_tokens=2000,
            max_input_chars=6000, is_enabled=True, sort_order=10,
        ))
        with mock.patch.object(
            ai_model_service, "_tool_reference_count", new=mock.AsyncMock(return_value=0)
        ):
            with self.assertRaises(AppException) as ctx:
                await ai_model_service.delete_model(self.db, result["id"], self.admin)
        self.assertEqual(ctx.exception.code, 409)
        self.assertIn("AI 写作", ctx.exception.message)
        self.assertEqual(len(self.db.all_rows(AiModel)), 1)

    async def test_delete_default_promotes_next(self):
        await self._create(code="deepseek", display_name="DeepSeek")
        second = await self._create(code="reasoner", display_name="Reasoner")
        first = self.db.all_rows(AiModel)[0]
        with mock.patch.object(
            ai_model_service, "_tool_reference_count", new=mock.AsyncMock(return_value=0)
        ):
            await ai_model_service.delete_model(self.db, first["id"], self.admin)
        rows = self.db.all_rows(AiModel)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "reasoner")
        self.assertTrue(rows[0]["is_default"])
        self.assertEqual(second["id"], rows[0]["id"])


class AiModelResolverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = MemorySession()
        self.db.seed(
            _model(code="deepseek", display_name="DeepSeek", is_default=True, sort_order=10),
            _model(code="openai", display_name="OpenAI", provider="openai", sort_order=20),
            _model(code="qwen", display_name="Qwen", provider="qwen", sort_order=30),
        )

    async def _resolve(self, ref):
        return await ai_model_service._select_model_row(self.db, ref)

    async def test_code_hit(self):
        self.assertEqual((await self._resolve("qwen")).code, "qwen")

    async def test_provider_hit(self):
        self.assertEqual((await self._resolve("openai")).code, "openai")

    async def test_unknown_falls_back_to_default(self):
        self.assertEqual((await self._resolve("typo-model")).code, "deepseek")

    async def test_disabled_code_falls_back_to_default(self):
        row = (await self._resolve("qwen"))
        row.is_enabled = False
        await self.db.flush()
        self.assertEqual((await self._resolve("qwen")).code, "deepseek")

    async def test_provider_without_models_returns_none_for_legacy_fallback(self):
        empty = MemorySession()
        empty.seed(_model(code="deepseek", display_name="DeepSeek", is_default=True))
        self.assertIsNone(await ai_model_service._select_model_row(empty, "openai"))

    async def test_model_prices_include_disabled_and_zero_is_skipped(self):
        rows = (await self.db.execute(select(AiModel))).scalars().all()
        deepseek = next(row for row in rows if row.code == "deepseek")
        deepseek.input_price = 1.5
        deepseek.output_price = 2.5
        deepseek.is_enabled = False
        await self.db.flush()
        prices = await ai_model_service.list_model_prices(self.db)
        self.assertEqual(prices, {"deepseek": (1.5, 2.5)})

    async def test_load_enabled_model_never_falls_back(self):
        """绑定模型的严格解析：未知/禁用都返回 None，绝不换成默认模型。"""
        qwen = (await self._resolve("qwen"))
        qwen.is_enabled = False
        await self.db.flush()
        with mock.patch.object(ai_model_service, "async_session", _session_ctx(self.db)):
            self.assertIsNone(await ai_model_service.load_enabled_model("qwen"))
            self.assertIsNone(await ai_model_service.load_enabled_model("no-such-model"))
            material = await ai_model_service.load_enabled_model("deepseek")
        self.assertIsNotNone(material)
        self.assertEqual(material.code, "deepseek")


class RuntimeConfigTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = MemorySession()
        self.db.seed(
            _model(
                code="deepseek",
                display_name="DeepSeek DB",
                api_key_enc=crypto.encrypt_secret("sk-db-key"),
                is_default=True,
                input_price=1,
            ),
            _model(
                code="reasoner",
                display_name="Reasoner",
                model_name="deepseek-reasoner",
                sort_order=10,
            ),
        )
        self._orig_session = ai_model_service.async_session
        ai_model_service.async_session = lambda: _session_ctx(self.db)()
        self._orig_redis = ai_config_service.redis_client
        ai_config_service.redis_client = FakeRedis()
        ai_config_service._LOCAL_CACHE.clear()

    async def asyncTearDown(self):
        ai_model_service.async_session = self._orig_session
        ai_config_service.redis_client = self._orig_redis
        ai_config_service._LOCAL_CACHE.clear()

    async def test_resolves_by_code_and_falls_back_per_field(self):
        cfg = await ai_config_service.get_runtime_config("reasoner")
        self.assertEqual(cfg.provider, "deepseek")
        self.assertEqual(cfg.code, "reasoner")
        self.assertEqual(cfg.default_model, "deepseek-reasoner")
        # 空 Key/BaseURL 逐项回退供应商 env
        from app.config import settings

        self.assertEqual(cfg.base_url, settings.DEEPSEEK_BASE_URL)

    async def test_resolves_default_when_ref_is_none(self):
        cfg = await ai_config_service.get_runtime_config(None)
        self.assertEqual(cfg.code, "deepseek")
        self.assertEqual(cfg.api_key, "sk-db-key")

    async def test_unknown_ref_uses_global_default(self):
        cfg = await ai_config_service.get_runtime_config("no-such-model")
        self.assertEqual(cfg.code, "deepseek")

    async def test_cache_is_invalidated_and_redis_holds_ciphertext_only(self):
        cfg = await ai_config_service.get_runtime_config("deepseek")
        self.assertEqual(cfg.api_key, "sk-db-key")
        cached = str(ai_config_service.redis_client._store)
        self.assertNotIn("sk-db-key", cached)
        self.assertIn("api_key_enc", cached)

        row = self.db.all_rows(AiModel)[0]
        self.db._table(AiModel).objs[row["id"]].api_key_enc = crypto.encrypt_secret("sk-rotated")
        await self.db.flush()
        await ai_config_service.invalidate_ai_config_cache()
        cfg = await ai_config_service.get_runtime_config("deepseek")
        self.assertEqual(cfg.api_key, "sk-rotated")


if __name__ == "__main__":
    unittest.main()
