"""侧边菜单配置守卫（`app/services/admin_menu.py`）。

菜单现在是后端下发、前端渲染，配置写错不会报错，只会「静默少一个入口 / 点进去 404 /
菜单丢图标」，所以用静态断言兜住三类问题：

1. **路由对不上**：非外链 key 必须在 `admin-frontend/src/routes/adminRoutes.tsx`
   的路由表里有对应条目（路径写错 = 点进去被重定向到首页）；
2. **权限码不存在**：perm/perms 必须是 RBAC 权限目录里的码（拼错 → 该菜单对所有人不可见）；
3. **图标名未注册**：图标名必须在 `admin-frontend/src/constants/menuIcons.tsx` 里（漏加只会丢图标）。

另有「覆盖性」断言：每个 `type=menu` 的权限码都应该有菜单入口，
新增模块时忘了挂菜单会在这里变红。
"""

import re
import unittest
from pathlib import Path

from app.services.admin_menu import (
    MENU_FOOTER,
    MENU_GROUPS,
    all_menu_perms,
    iter_menu_items,
    menu_tree,
)
from app.services.rbac_catalog import ALL_PERMISSION_CODES, PERMISSION_CATALOG

REPO_ROOT = Path(__file__).resolve().parents[2]
# 路由表是前端路由的唯一数据源（App.tsx 只负责渲染，不再逐条写 <Route>）
ADMIN_ROUTES_TSX = REPO_ROOT / "admin-frontend" / "src" / "routes" / "adminRoutes.tsx"
ADMIN_MENU_ICONS = REPO_ROOT / "admin-frontend" / "src" / "constants" / "menuIcons.tsx"

# 刻意不给菜单入口的菜单级权限码（当前没有；有的话在这里登记并写清原因）
MENU_ENTRY_EXEMPT: set[str] = set()


def _read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"守卫依赖的前端文件不存在: {path}")
    return path.read_text(encoding="utf-8")


def _frontend_route_paths() -> set[str]:
    """路由表里声明的路径（`{ path: '/xxx', ... }`）"""
    return set(re.findall(r"path:\s*'([^']+)'", _read(ADMIN_ROUTES_TSX)))


def _frontend_icon_names() -> set[str]:
    """menuIcons.tsx 的 MENU_ICON_MAP 键名（`'key': <Icon />` 或 `key: <Icon />`）"""
    text = _read(ADMIN_MENU_ICONS)
    start = text.find("MENU_ICON_MAP")
    if start < 0:
        raise AssertionError("menuIcons.tsx 里找不到 MENU_ICON_MAP")
    body = text[start:]
    # 只取对象字面量内部的键，避免把函数体里的标识符也算进来
    return set(re.findall(r"^\s*'?([a-z0-9-]+)'?:\s*<", body, flags=re.MULTILINE))


class AdminMenuConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = iter_menu_items()
        cls.tree = menu_tree()

    def test_keys_are_unique(self):
        keys = [item.key for item in self.items]
        duplicates = {key for key in keys if keys.count(key) > 1}
        self.assertEqual(duplicates, set(), f"菜单 key 重复（antd Menu 会错乱）: {sorted(duplicates)}")

    def test_group_keys_are_unique_and_prefixed_safely(self):
        keys = [group.key for group in MENU_GROUPS]
        self.assertEqual(len(keys), len(set(keys)), f"分组 key 重复: {keys}")
        # 分组 key 会加 `group:` 前缀后作为 antd Menu key，不能与叶子 key 冲突
        leaf_keys = {item.key for item in self.items}
        for key in keys:
            self.assertNotIn(f"group:{key}", leaf_keys, f"分组 key 与叶子 key 冲突: group:{key}")

    def test_every_group_has_items(self):
        empty = [group.key for group in MENU_GROUPS if not group.items]
        self.assertEqual(empty, [], f"存在空分组（侧栏会留一个点开是空的项）: {empty}")

    def test_internal_keys_exist_in_frontend_routes(self):
        """非外链菜单必须指向真实存在的后台路由（写错 key = 点进去被重定向到首页）"""
        routes = _frontend_route_paths()
        self.assertTrue(routes, "adminRoutes.tsx 里没解析到任何 path，守卫可能已失效")
        missing = [
            f"{item.label} → {item.key}"
            for item in self.items
            if not item.url and item.key not in routes
        ]
        self.assertEqual(missing, [], f"菜单 key 在 adminRoutes.tsx 里找不到对应路由: {missing}")

    def test_permission_codes_exist_in_catalog(self):
        unknown = all_menu_perms() - ALL_PERMISSION_CODES
        self.assertEqual(unknown, set(), f"菜单引用了未定义的权限码: {sorted(unknown)}")

    def test_perm_and_perms_are_mutually_exclusive(self):
        bad = [item.key for item in self.items if item.perm and item.perms]
        self.assertEqual(bad, [], f"同一菜单项不要同时声明 perm 与 perms（语义会歧义）: {bad}")

    def test_icon_names_registered_in_frontend(self):
        icons = _frontend_icon_names()
        self.assertTrue(icons, "menuIcons.tsx 里没解析到任何图标名，守卫可能已失效")
        used = {group.icon for group in MENU_GROUPS} | {item.icon for item in self.items}
        missing = used - icons
        self.assertEqual(missing, set(), f"以下图标名未在 menuIcons.tsx 注册（菜单会丢图标）: {sorted(missing)}")

    def test_every_menu_permission_has_entry(self):
        """每个菜单级权限码都应有侧边入口（新增模块忘挂菜单的兜底）"""
        menu_codes = {p.code for p in PERMISSION_CATALOG if p.type == "menu"}
        covered = all_menu_perms()
        uncovered = menu_codes - covered - MENU_ENTRY_EXEMPT
        self.assertEqual(
            uncovered,
            set(),
            "以下菜单级权限没有对应的侧边栏入口（新增模块请同步 admin_menu.py，"
            f"确属刻意不给入口的请登记 MENU_ENTRY_EXEMPT）: {sorted(uncovered)}",
        )

    def test_tree_payload_shape(self):
        """接口载荷：空字段不下发，外链带 url"""
        self.assertIn("groups", self.tree)
        self.assertIn("footer", self.tree)
        self.assertEqual(len(self.tree["groups"]), len(MENU_GROUPS))
        self.assertEqual(len(self.tree["footer"]), len(MENU_FOOTER))
        for group in self.tree["groups"]:
            self.assertEqual(set(group), {"key", "label", "icon", "items"})
            for item in group["items"]:
                self.assertIn("key", item)
                self.assertIn("label", item)
                self.assertIn("icon", item)
                self.assertNotIn(None, item.values(), f"菜单项不应下发 None 字段: {item}")

    def test_external_items_carry_url(self):
        for item in self.items:
            if item.key.startswith("http"):
                self.fail(f"外链应使用任意唯一 key + url 字段，而不是把 URL 当 key: {item.key}")


if __name__ == "__main__":
    unittest.main()
