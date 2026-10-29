"""管理端路由守卫挂载守卫：规则表登记 ≠ 挂载——admin_rbac/admin_audit 的 router 曾是裸
APIRouter（router 级 dependencies 不跨 include_router 继承），端点只剩 require_admin，
role=admin 账号可给自己分配 super_admin 提权。故必须直接断言依赖对象本身。
FastAPI 0.139 起 include_router 延迟展开，app.routes 只有包装对象，须递归展开并累积各层 dependencies
（route.dependant.dependencies 只含路由/参数级，只看它会漏判）。"""

import unittest


def _dep_name(dep) -> str:
    """兼容两种依赖对象：Dependant（有 .call）与 Depends（有 .dependency）"""
    fn = getattr(dep, "call", None) or getattr(dep, "dependency", None)
    return getattr(fn, "__qualname__", str(fn))


def _dep_names(deps) -> tuple:
    return tuple(_dep_name(d) for d in (deps or []))


def _walk(router, prefix: str = "", inherited: tuple = ()):
    """递归展开路由树，产出 (完整路径, 路由对象, 全链路依赖名)。

    依赖名是「祖先 router 级 + 本级 router 级 + 路由/参数级」的并集——
    只看最内层会漏掉挂在 router 上的守卫。
    """
    my_deps = inherited + _dep_names(getattr(router, "dependencies", None))
    for route in getattr(router, "routes", []):
        if type(route).__name__ == "_IncludedRouter":
            ctx = route.include_context
            sub = route.original_router
            # include_context.prefix 是「绝对前缀」（已含祖先 + 本次 include 的前缀），
            # 而被包含 router 自身的 prefix 需另加
            sub_abs = (getattr(ctx, "prefix", "") or "") + (getattr(sub, "prefix", "") or "")
            yield from _walk(
                sub, sub_abs, my_deps + _dep_names(getattr(ctx, "dependencies", None))
            )
        elif hasattr(route, "dependant"):
            yield (
                prefix + getattr(route, "path", ""),
                route,
                my_deps + _dep_names(route.dependant.dependencies),
            )


ADMIN_PREFIX = "/api/v1/admin"


class AdminRouteGuardTests(unittest.TestCase):
    """每条 /api/v1/admin 路由都必须同时挂 require_admin 与 check_route_permission"""

    @classmethod
    def setUpClass(cls):
        from app.main import app

        cls.rows = [row for row in _walk(app) if row[0].startswith(ADMIN_PREFIX)]

    def test_route_enumeration_is_not_empty(self):
        """遍历本身必须有效 —— 前缀写错/展开逻辑失效会让下面的断言变成空断言（假绿）

        当前管理端路由 90+ 条，取 60 作下限：既容得下小幅增删，
        又能在「一条都没遍历到」时立刻变红。
        """
        self.assertGreaterEqual(
            len(self.rows),
            60,
            f"只枚举到 {len(self.rows)} 条管理端路由，路由树展开逻辑可能已失效",
        )

    def test_every_admin_route_has_require_admin(self):
        missing = [path for path, _, deps in self.rows if not any("require_admin" in d for d in deps)]
        self.assertEqual(missing, [], f"以下管理端路由缺少 require_admin 角色门槛: {sorted(set(missing))}")

    def test_every_admin_route_has_check_route_permission(self):
        """核心守卫：router 级 dependencies 不跨 include_router 继承，必须逐层累积才看得见"""
        missing = [
            path
            for path, _, deps in self.rows
            if not any("check_route_permission" in d for d in deps)
        ]
        self.assertEqual(
            missing,
            [],
            "以下管理端路由未挂细粒度权限校验（check_route_permission）。"
            "注意：挂在父 router 上的守卫不会继承到子 router，"
            f"每个 /admin 子 router 必须自己声明 dependencies: {sorted(set(missing))}",
        )

    def test_sub_routers_declare_guard_explicitly(self):
        """admin_rbac / admin_audit 必须在**自己的 router 上**声明守卫

        这是本次漏洞的根因所在，单独立一条断言：即使将来有人把守卫挪到
        app 层统一注册（上面两条仍会通过），这里也会立刻变红，
        提醒「延迟展开/继承语义可能变了，请重新确认」。
        """
        from app.api import admin, admin_audit, admin_rbac

        for module in (admin, admin_rbac, admin_audit):
            deps = _dep_names(getattr(module.router, "dependencies", None))
            self.assertTrue(
                any("check_route_permission" in d for d in deps),
                f"{module.__name__} 的 router 未显式挂载 check_route_permission",
            )

    def test_sensitive_routes_are_covered(self):
        """点名两条最关键的路由，防止「整体通过但个别漏网」被掩盖

        注意路径用的是**路由模板**（`{user_id}`），不是实际取值。
        """
        sensitive = {
            ("PUT", f"{ADMIN_PREFIX}/users/{{user_id}}/rbac"),   # 自我提权入口
            ("GET", f"{ADMIN_PREFIX}/audit-logs"),               # viewer 刻意不可见
        }
        found = set()
        for path, route, deps in self.rows:
            if not any("check_route_permission" in d for d in deps):
                continue
            for method in getattr(route, "methods", set()) or set():
                found.add((method, path))
        for item in sensitive:
            self.assertIn(item, found, f"关键路由未受细粒度权限保护: {item}")


if __name__ == "__main__":
    unittest.main()
