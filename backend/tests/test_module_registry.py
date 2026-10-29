"""模块注册表与「模块开关」的契约。

为什么值得单独测：模块开关是**裁剪式交付**的唯一手段。它错在两个方向都很难发现——

- 关掉了但接口仍可调 → 安全/合规问题（支付接口在"没有支付"的站上还能被打）；
- 关掉了但菜单还在 → 点进去 403，用户以为坏了；
- 菜单过滤把整组裁空 → 侧边栏出现只有标题的空折叠块。

本文件把这三条钉住，并校验注册表与菜单/权限目录不漂移。
"""

import unittest
from unittest import mock

from starlette.requests import Request

from app import module_registry
from app.config import settings
from app.core import rbac as rbac_core
from app.core.exceptions import PermissionDeniedError
from app.services import admin_menu


class _SettingsOverride:
    """临时改 ENABLED_MODULES 并清缓存（门面用 lru 之外的模块级缓存）。"""

    def __init__(self, value: str):
        self.value = value
        self._patcher = mock.patch.object(settings, "ENABLED_MODULES", value)

    def __enter__(self):
        self._patcher.start()
        module_registry.reset_cache()
        return self

    def __exit__(self, *exc):
        self._patcher.stop()
        module_registry.reset_cache()
        return False


class RegistryIntegrityTests(unittest.TestCase):
    def test_keys_are_unique(self):
        keys = [m.key for m in module_registry.MODULES]
        self.assertEqual(len(keys), len(set(keys)), "模块 key 重复")

    def test_path_prefixes_are_not_ambiguous(self):
        """两个模块不能声明同一个路径前缀，否则「关掉哪个」取决于遍历顺序。"""
        seen: dict[str, str] = {}
        for module in module_registry.MODULES:
            for prefix in module.path_prefixes:
                self.assertNotIn(
                    prefix, seen, f"路径前缀 {prefix} 同时被 {seen.get(prefix)} 与 {module.key} 声明"
                )
                seen[prefix] = module.key

    def test_every_path_prefix_maps_back_to_its_module(self):
        for module in module_registry.MODULES:
            for prefix in module.path_prefixes:
                found = module_registry.module_for_path(prefix + "/x")
                self.assertIsNotNone(found, f"{prefix} 下的路径没有归属模块")
                self.assertEqual(found.key, module.key, f"{prefix} 归属错乱")

    def test_menu_keys_exist_in_menu_config(self):
        menu_keys = {item.key for item in admin_menu.iter_menu_items()}
        for module in module_registry.MODULES:
            for key in module.menu_keys:
                self.assertIn(key, menu_keys, f"模块 {module.key} 声明的菜单 {key} 不存在")

    def test_every_menu_item_belongs_to_a_module(self):
        """新增菜单项却忘了登记模块时，它永远不会被开关过滤掉。"""
        for item in admin_menu.iter_menu_items():
            self.assertIn(
                item.key,
                module_registry.MENU_MODULE,
                f"菜单 {item.key} 未登记到任何模块（module_registry.MENU_MODULE）",
            )

    def test_core_is_always_on(self):
        core = next(m for m in module_registry.MODULES if m.key == "core")
        self.assertTrue(core.always_on)
        with _SettingsOverride("tutorial"):
            self.assertTrue(module_registry.is_enabled("core"), "core 不能从配置里被关掉")


class SwitchSemanticsTests(unittest.TestCase):
    def test_default_keeps_commercial_modules_off(self):
        with _SettingsOverride(""):
            self.assertFalse(module_registry.is_enabled("payment"))
            self.assertFalse(module_registry.is_enabled("points"))
            self.assertFalse(module_registry.is_enabled("auth_request"))
            self.assertTrue(module_registry.is_enabled("tutorial"))

    def test_whitelist_only_enables_listed(self):
        with _SettingsOverride("tutorial,category"):
            self.assertTrue(module_registry.is_enabled("tutorial"))
            self.assertTrue(module_registry.is_enabled("core"), "核心模块始终启用")
            self.assertFalse(module_registry.is_enabled("node"))

    def test_unknown_module_fails_loudly(self):
        with self.assertRaises(RuntimeError) as ctx:
            with _SettingsOverride("tutorial,nope"):
                module_registry.enabled_keys()
        self.assertIn("nope", str(ctx.exception))

    def test_path_check_is_segment_aware(self):
        """`/admin/payment-products` 不能被 `/admin/payment` 前缀误吞（反之亦然）。"""
        with _SettingsOverride("core,payment"):
            self.assertTrue(module_registry.is_path_enabled("/api/v1/admin/payment-products"))
        with _SettingsOverride("core"):
            self.assertFalse(module_registry.is_path_enabled("/api/v1/admin/payment-products"))

    def test_unknown_path_is_allowed(self):
        """未登记路径交给 rbac 规则表 fail-closed，不在这一层判死。"""
        self.assertTrue(module_registry.is_path_enabled("/api/v1/admin/whatever-new"))


class MenuFilteringTests(unittest.TestCase):
    def _menu_keys(self, tree: dict) -> set[str]:
        keys = {i["key"] for g in tree["groups"] for i in g["items"]}
        keys |= {i["key"] for i in tree["footer"]}
        return keys

    def test_disabled_modules_disappear_from_menu(self):
        with _SettingsOverride(""):
            keys = self._menu_keys(admin_menu.menu_tree())
        self.assertNotIn("/payment-products", keys)
        self.assertNotIn("/points", keys)
        self.assertNotIn("/auth-requests", keys)
        self.assertIn("/tutorials", keys)

    def test_unfiltered_tree_still_has_everything(self):
        """only_enabled=False 用于校验注册表完整性 / 后台诊断，必须给全量。"""
        keys = self._menu_keys(admin_menu.menu_tree(only_enabled=False))
        self.assertIn("/payment-products", keys)
        self.assertIn("/tutorials", keys)

    def test_no_empty_group_is_rendered(self):
        """把整组模块都关掉时，不能留下只有标题的空折叠块。"""
        with _SettingsOverride("core"):
            tree = admin_menu.menu_tree()
        for group in tree["groups"]:
            self.assertTrue(group["items"], f"分组 {group['key']} 被裁空却仍被返回")


class RouteDenialTests(unittest.IsolatedAsyncioTestCase):
    async def _call(self, path: str) -> None:
        scope = {"type": "http", "method": "GET", "path": path, "headers": []}
        await rbac_core.check_route_permission(Request(scope), db=None, user=None)

    async def test_disabled_module_route_is_rejected(self):
        with _SettingsOverride("core"):
            with self.assertRaises(PermissionDeniedError) as ctx:
                await self._call("/api/v1/admin/payment-orders")
        self.assertIn("未启用", str(ctx.exception))

    async def test_enabled_module_falls_through_to_permission_check(self):
        """启用中的模块不该被这一层拦掉（后续由权限码判定，用 None 会话会抛别的错）。"""
        with _SettingsOverride("core,tutorial"):
            with self.assertRaises(Exception) as ctx:
                await self._call("/api/v1/admin/tutorials")
        self.assertNotIn("未启用", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
