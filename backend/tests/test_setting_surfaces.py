"""设置项「归属」（surface）的全局守卫。

这份守卫原先住在 `test_login_settings.py` 里，随着模块一个个抽出，它已经跨 5 个模块 ——
每抽一个模块都要去改登录测试的常量列表，这本身就说明位置错了。现在独立成文件，
并且**不再手抄 surface 清单**：

- 清单从 `SETTING_GROUPS` 的声明推导（漏了哪个 surface 都不是「清单没改」的问题，
  而是「这个 surface 没有页面」——那才是真正会让人看不见设置的原因）；
- 每个 surface 必须登记一个真实可达的后台接口（`SURFACE_OWNERS`），
  且该接口要出现在 OpenAPI 里。

三条不变量：
1. 同一设置项**只能归一个界面**（双写 = 两个页面各存一次、互相覆盖且不报错）；
2. 每个设置项**必须归到某个界面**（漏归属 = 后端有定义、界面上找不到）；
3. 每个界面**必须有真实接口**（写了 surface 但没有页面 = 整组设置从后台消失）。
"""

import unittest

from app.services import setting_service
from app.services.setting_service import (
    SURFACE_LEGAL,
    SURFACE_LOGIN,
    SURFACE_MAIL,
    SURFACE_MODEL,
    SURFACE_PAYMENT,
    SURFACE_SETTINGS,
)

#: surface → 承载它的后台接口（OpenAPI 路径）
#: 新增 surface 时必须在这里登记，否则「没有页面的整组设置」会被下面的测试拦下
SURFACE_OWNERS: dict[str, str] = {
    SURFACE_SETTINGS: "/api/v1/admin/settings",
    SURFACE_LOGIN: "/api/v1/admin/login-settings",
    SURFACE_LEGAL: "/api/v1/admin/legal-documents",
    SURFACE_PAYMENT: "/api/v1/admin/payment-settings",
    SURFACE_MODEL: "/api/v1/admin/ai-models/cost-settings",
    SURFACE_MAIL: "/api/v1/admin/mail-settings",
}


class SurfaceOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.declared_surfaces = {g.surface for g in setting_service.SETTING_GROUPS}

    def test_every_surface_has_an_entry_page(self):
        """写了 surface 却没有页面：整组设置从后台消失，且不报错"""
        orphan = sorted(self.declared_surfaces - set(SURFACE_OWNERS))
        self.assertEqual(orphan, [], f"以下 surface 没有承载页面：{orphan}")

    def test_entry_pages_are_real_routes(self):
        from app.main import app

        paths = app.openapi()["paths"]
        for surface, endpoint in SURFACE_OWNERS.items():
            with self.subTest(surface=surface):
                self.assertIn(endpoint, paths, f"{surface} 的接口 {endpoint} 没有注册")

    def test_owner_map_has_no_stale_surface(self):
        """反向：登记了却没有任何分组 → 那个页面会是空的"""
        stale = sorted(set(SURFACE_OWNERS) - self.declared_surfaces)
        self.assertEqual(stale, [], f"以下 surface 已无任何设置项，应清理登记：{stale}")

    def test_surfaces_are_disjoint(self):
        seen: dict[str, str] = {}
        for surface in sorted(self.declared_surfaces):
            for key in setting_service.keys_for_surface(surface):
                with self.subTest(key=key):
                    self.assertNotIn(
                        key, seen, f"设置项 {key} 同时归属两个界面（{seen.get(key)} / {surface}）"
                    )
                    seen[key] = surface

    def test_surfaces_cover_every_definition(self):
        """漏归属的键 = 所有页面都看不到（后端有定义、界面无入口）"""
        all_keys = {d.key for d in setting_service.SETTING_DEFINITIONS}
        covered: set[str] = set()
        for surface in self.declared_surfaces:
            covered |= setting_service.keys_for_surface(surface)
        self.assertEqual(all_keys - covered, set())

    def test_every_group_has_items(self):
        """空分组会在对应页面留一个空区块（导入期已校验，这里防回归）"""
        for group in setting_service.SETTING_GROUPS:
            with self.subTest(group=group.key):
                self.assertTrue(
                    [d for d in setting_service.SETTING_DEFINITIONS if d.group == group.key]
                )


class SettingsPageScopeTests(unittest.TestCase):
    """系统设置页只剩「通用」分组：抽走的模块不能再出现在它的 tab 里"""

    #: 已经抽出独立页面的分组 key（每次抽模块都在这里加一行）
    MOVED_GROUPS = (
        "auth_phone",
        "auth_wechat",
        "auth_sms",
        "miniapp",
        "legal",
        "payment_general",
        "payment_wechat",
        "payment_alipay",
        "ai",
        "mail",
    )

    def test_moved_groups_are_absent_from_settings_payload(self):
        groups = {g["key"] for g in setting_service.groups_payload(surface=SURFACE_SETTINGS)}
        leaked = sorted(set(self.MOVED_GROUPS) & groups)
        self.assertEqual(leaked, [], f"这些分组已抽成独立页，系统设置里不应再出现：{leaked}")

    def test_moved_groups_still_exist_somewhere(self):
        """移走 ≠ 删掉：每个 moved group 必须仍在某个 surface 里"""
        declared = {g.key: g.surface for g in setting_service.SETTING_GROUPS}
        for key in self.MOVED_GROUPS:
            with self.subTest(group=key):
                self.assertIn(key, declared)
                self.assertNotEqual(declared[key], SURFACE_SETTINGS)
