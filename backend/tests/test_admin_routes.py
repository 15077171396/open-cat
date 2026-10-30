"""管理端路由表守卫（`admin-frontend/src/routes/adminRoutes.tsx`）。

路由表把「路径 / 页面组件 / 权限码」放在一处声明，但它是 TS 文件，Python 侧只能静态解析。
四类问题在这里兜住，它们的共同点是：**编译通过、测试不红、上线才发现**。

1. **权限码拼错**：该页对所有人 403（超管除外），运行时没有任何报错；
2. **新页面忘了挂守卫**：页面直接裸奔，任何登录管理员都能进；
3. **路由与菜单权限不一致**：菜单能看见但点进去 403，或反过来「藏了入口但页面可达」；
4. **加了页面没加菜单**：页面存在却没有任何入口，只能靠记 URL 进。

与 `test_admin_menu.py` 的分工：那边校验「菜单 → 路由」方向，这边校验「路由 → 菜单」方向
与权限码本身，两边合起来才是双向闭合。
"""

import re
import unittest
from pathlib import Path

from app.services.admin_menu import iter_menu_items
from app.services.rbac_catalog import ALL_PERMISSION_CODES

REPO_ROOT = Path(__file__).resolve().parents[2]
ROUTES_TSX = REPO_ROOT / "admin-frontend" / "src" / "routes" / "adminRoutes.tsx"

# 一条路由声明形如 `{ path: '/x', element: <X />, perm: 'a:b' },`
# 条目内部没有嵌套花括号（JSX 自闭合标签与数组字面量都不带 `{}`），所以可以整块匹配
ENTRY_RE = re.compile(r"\{[^{}]*?\bpath:\s*'([^']+)'[^{}]*?\}", re.S)
PERM_RE = re.compile(r"(?<![A-Za-z])perm:\s*'([^']+)'")
ANY_PERMS_RE = re.compile(r"anyPerms:\s*\[([^\]]*)\]")

# 刻意不挂页面级权限守卫的路由（有变动请同步理由）
UNGUARDED_EXEMPT: dict[str, str] = {
    "/profile": "个人中心：看自己的资料 / 改自己的密码 / 查自己的记录，与业务权限无关",
}

# 刻意不进菜单覆盖校验的路由：它们是某个列表页的子页面，入口在列表页的按钮上
NO_MENU_EXEMPT: dict[str, str] = {
    "/tutorials/new": "教程新建页，入口是教程列表页的「新增」按钮",
    "/tutorials/:id/edit": "教程编辑页，入口是列表行内的「编辑」",
}


def _routes_source() -> str:
    if not ROUTES_TSX.exists():
        raise AssertionError(f"守卫依赖的前端路由表不存在: {ROUTES_TSX}")
    return ROUTES_TSX.read_text(encoding="utf-8")


def _parse_routes() -> list[dict]:
    """解析路由表条目 → [{path, perm, anyPerms}]"""
    parsed: list[dict] = []
    for block in ENTRY_RE.finditer(_routes_source()):
        body = block.group(0)
        perm = PERM_RE.search(body)
        any_perms = ANY_PERMS_RE.search(body)
        parsed.append(
            {
                "path": block.group(1),
                "perm": perm.group(1) if perm else None,
                "anyPerms": (
                    [code for code in re.findall(r"'([^']+)'", any_perms.group(1))]
                    if any_perms
                    else []
                ),
            }
        )
    return parsed


class AdminRouteTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.routes = _parse_routes()
        cls.by_path = {route["path"]: route for route in cls.routes}
        cls.menu_items = {item.key: item for item in iter_menu_items() if not item.url}

    def _route_codes(self, route: dict) -> set[str]:
        codes = set(route["anyPerms"])
        if route["perm"]:
            codes.add(route["perm"])
        return codes

    def _menu_codes(self, key: str) -> set[str]:
        item = self.menu_items[key]
        codes = set(item.perms)
        if item.perm:
            codes.add(item.perm)
        return codes

    # ---------- 解析自检 ----------

    def test_route_table_is_parsed(self):
        """解析不到路由说明守卫已失效（比解析出错更危险：它会静默通过）"""
        self.assertGreaterEqual(
            len(self.routes), 25, f"只解析到 {len(self.routes)} 条路由，路由表格式可能变了"
        )

    def test_paths_are_unique(self):
        paths = [route["path"] for route in self.routes]
        duplicates = {path for path in paths if paths.count(path) > 1}
        self.assertEqual(duplicates, set(), f"路由路径重复（React Router 会静默取第一个）: {sorted(duplicates)}")

    # ---------- 权限码 ----------

    def test_permission_codes_exist_in_catalog(self):
        unknown: dict[str, list[str]] = {}
        for route in self.routes:
            missing = self._route_codes(route) - ALL_PERMISSION_CODES
            if missing:
                unknown[route["path"]] = sorted(missing)
        self.assertEqual(
            unknown,
            {},
            "以下路由引用了权限目录里不存在的码（该页会对所有人 403，且无任何报错）: "
            f"{unknown}",
        )

    def test_every_route_is_guarded_unless_exempt(self):
        unguarded = [
            route["path"]
            for route in self.routes
            if not self._route_codes(route) and route["path"] not in UNGUARDED_EXEMPT
        ]
        self.assertEqual(
            unguarded,
            [],
            "以下路由没有挂页面级权限守卫（任何登录管理员都能直接访问）。"
            f"确属刻意的请登记 UNGUARDED_EXEMPT 并写明理由: {unguarded}",
        )

    def test_unguarded_exempt_list_is_not_stale(self):
        """豁免项必须仍是真的没挂守卫，否则这行豁免会变成「永久静音」"""
        stale = [
            path
            for path in UNGUARDED_EXEMPT
            if path in self.by_path and self._route_codes(self.by_path[path])
        ]
        self.assertEqual(stale, [], f"以下豁免项其实已经挂了守卫，请从 UNGUARDED_EXEMPT 移除: {stale}")

    # ---------- 路由 ↔ 菜单 ----------

    def test_route_permissions_match_menu_permissions(self):
        """同一页面的路由守卫与菜单可见性必须用同一组权限码

        不一致的两种后果都是线上才发现的：菜单可见但点进去 403（用户以为坏了），
        或菜单隐藏但 URL 直达可用（权限形同虚设）。
        """
        mismatched: dict[str, dict] = {}
        for path, route in self.by_path.items():
            if path not in self.menu_items:
                continue
            route_codes = self._route_codes(route)
            menu_codes = self._menu_codes(path)
            if route_codes != menu_codes:
                mismatched[path] = {"路由": sorted(route_codes), "菜单": sorted(menu_codes)}
        self.assertEqual(mismatched, {}, f"路由守卫与菜单权限不一致: {mismatched}")

    def test_every_route_has_menu_entry(self):
        """加了页面必须加菜单，否则页面存在却没有任何入口"""
        orphan = [
            route["path"]
            for route in self.routes
            if route["path"] not in self.menu_items and route["path"] not in NO_MENU_EXEMPT
        ]
        self.assertEqual(
            orphan,
            [],
            "以下路由没有任何菜单入口（只能靠记 URL 进）。子页面请登记 NO_MENU_EXEMPT: "
            f"{orphan}",
        )

    def test_no_menu_exempt_list_is_not_stale(self):
        stale = [path for path in NO_MENU_EXEMPT if path in self.menu_items]
        self.assertEqual(stale, [], f"以下豁免项其实已有菜单入口，请从 NO_MENU_EXEMPT 移除: {stale}")


if __name__ == "__main__":
    unittest.main()
