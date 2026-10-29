"""AI Provider 注册表一致性：两份手工注册表曾互不关联，只登记一份的新 Provider 会静默用
DeepSeek 的 key/base_url/model 构造真实实例（不报错）。现已合并为 registry.py 唯一事实来源。
本文件守卫四类静默失效：①新写 *_provider.py 但没注册；②settings_prefix/env_prefix 不存在；
③DEFAULT_AI_MODEL 不在注册表里；④未注册名字仍能静默取到配置。"""

import unittest

from app.config import settings
from app.services import ai_config_service
from app.services.ai import factory, registry


class ProviderRegistryTests(unittest.TestCase):
    def test_auto_discovery_matches_registered(self):
        """`*_provider.py` 文件名必须与注册名一一对应

        两边任一多出都是静默失效：多写的模块不生效，少写的注册无处发生。
        """
        import pkgutil

        import app.services.ai as pkg

        on_disk = {
            info.name.removesuffix("_provider")
            for info in pkgutil.iter_modules(pkg.__path__)
            if info.name.endswith("_provider")
        }
        registered = set(registry.registered_provider_names())
        self.assertEqual(
            on_disk,
            registered,
            "Provider 模块与注册表不一致 —— 改文件名或漏写 @register_provider 都会这样。"
            f" 目录={sorted(on_disk)} 注册表={sorted(registered)}",
        )

    def test_registry_is_single_source_for_config_layer(self):
        """配置层不再维护第二份表：注册即拥有配置定位"""
        for name, _cls, meta in registry.provider_constants():
            with self.subTest(provider=name):
                self.assertTrue(meta.settings_prefix, f"{name} 未声明 settings_prefix")
                self.assertTrue(meta.env_prefix, f"{name} 未声明 env_prefix")
                self.assertIn(meta.modality, ("text", "image"), f"{name} 的模态声明非法")
                # factory 与 ai_config_service 都从同一处取，不存在「两份表不一致」
                self.assertIs(registry.provider_meta(name), meta)

    def test_env_prefixes_resolve_to_real_settings(self):
        """环境变量前缀必须真的存在于 settings（前缀写错会静默取到空 key）"""
        for name, _cls, meta in registry.provider_constants():
            for suffix in ("API_KEY", "BASE_URL", "MODEL"):
                with self.subTest(provider=name, suffix=suffix):
                    self.assertTrue(
                        hasattr(settings, f"{meta.env_prefix}_{suffix}"),
                        f"{name} 的 env 前缀 {meta.env_prefix} 缺 {suffix} 字段",
                    )

    def test_provider_registry_backs_model_management_options(self):
        """模型管理的供应商下拉必须与注册表同一集合

        模型凭据已从 SETTING_DEFINITIONS 迁到 ai_models，原先「设置键必须登记」
        的守卫随之失效；新的入口是 providers_payload —— 漏一个供应商，
        后台新增模型时就选不到它（不报错、静默缺项）。
        """
        from app.services import ai_model_service

        self.assertEqual(
            {item["name"] for item in ai_model_service.providers_payload()},
            set(registry.registered_provider_names()),
        )

    def test_default_model_is_registered(self):
        """`DEFAULT_AI_MODEL` 必须在文本注册表里，否则回退链自身就是坏的"""
        self.assertIn(
            settings.DEFAULT_AI_MODEL, registry.registered_provider_names("text")
        )

    def test_unknown_provider_has_no_config(self):
        """未注册名不得再取到配置（no silent fallback，P1-40）"""
        import asyncio

        self.assertIsNone(registry.provider_meta("no-such-provider"))
        with self.assertRaises(ValueError):
            asyncio.run(ai_config_service.get_provider_config("no-such-provider"))

    def test_factory_reexports_registry_names(self):
        """factory 的公开入口与注册表保持同一集合（调用方沿用 factory）"""
        self.assertEqual(
            factory.registered_provider_names(), registry.registered_provider_names()
        )


if __name__ == "__main__":
    unittest.main()