"""RBAC 单测：路由规则覆盖完整性 + 权限匹配 + 令牌撤销机制

- 路由覆盖：通过 OpenAPI 枚举全部 /admin 路由，确保每条 (method, path)
  都在 core/rbac.py 规则表中登记（fail-closed 策略下漏配 = 生产 403）
- 分类管理端点（/api/v1/categories 写操作）使用显式 require_permission，
  一并校验其权限守卫已挂载
- 令牌撤销：jti 黑名单 + 版本号（FakeRedis 模拟）
"""

import unittest

from app.core import rbac as rbac_core
from app.core import security as security_core
from app.core.security import (
    create_access_token,
    create_token_pair,
    decode_token,
    blacklist_access_token,
    bump_token_version,
    get_token_version,
    is_access_token_revoked,
)


class RbacRouteCoverageTests(unittest.TestCase):
    """全部 /admin 路由必须登记权限规则（fail-closed 完整性）"""

    @classmethod
    def setUpClass(cls):
        from app.main import app
        cls.schema = app.openapi()

    def test_every_admin_route_has_permission_rule(self):
        missing = []
        for path, methods in self.schema["paths"].items():
            if not path.startswith("/api/v1/admin/"):
                continue
            for method in ("get", "post", "put", "delete", "patch"):
                if method in methods:
                    required = rbac_core.match_route_permission(method.upper(), path)
                    if required is None:
                        missing.append(f"{method.upper()} {path}")
        self.assertEqual(
            missing, [],
            f"以下管理端路由未登记权限规则（fail-closed 将 403）: {missing}",
        )

    def test_rule_permissions_exist_in_catalog(self):
        """规则表引用的权限码必须在目录中定义（防拼写错误）"""
        from app.services.rbac_catalog import ALL_PERMISSION_CODES
        for _, _, perms in rbac_core._RULES:
            for code in perms:
                self.assertIn(code, ALL_PERMISSION_CODES, f"规则引用了未定义权限: {code}")

    def test_path_matching_with_params(self):
        """路径参数按段匹配，不误吞多级路径"""
        self.assertEqual(
            rbac_core.match_route_permission("GET", "/api/v1/admin/tutorials/123"),
            ["tutorial:view"],
        )
        self.assertEqual(
            rbac_core.match_route_permission("PUT", "/api/v1/admin/users/42/rbac"),
            ["user:edit"],
        )
        # 具体路由优先：batch-delete 不会被 {id} 规则抢先匹配
        self.assertEqual(
            rbac_core.match_route_permission("POST", "/api/v1/admin/users/batch-delete"),
            ["user:delete"],
        )
        # 未登记的路径 → None（fail-closed）
        self.assertIsNone(
            rbac_core.match_route_permission("POST", "/api/v1/admin/unknown-endpoint")
        )

    def test_head_normalized_to_get(self):
        self.assertEqual(
            rbac_core.match_route_permission("HEAD", "/api/v1/admin/dashboard"),
            ["dashboard:view"],
        )

    def test_categories_routes_use_explicit_permission(self):
        """分类管理端点（/admin 前缀外）必须挂 require_permission 显式守卫"""
        from app.api import categories
        for route in categories.router.routes:
            path = route.path
            method = sorted(route.methods)[0]
            if method in ("POST", "PUT", "DELETE"):
                # 参数级 Depends 存于 route.dependant.dependencies（含路由级）
                dep_names = [d.call.__qualname__ for d in route.dependant.dependencies]
                self.assertTrue(
                    any("require_permission" in d for d in dep_names),
                    f"分类写操作缺少权限守卫: {method} {path}",
                )


class _StubRedis:
    """令牌撤销测试专用 Redis 桩：get/set/setex/incr/exists/delete + exists/get pipeline"""

    def __init__(self):
        self._store: dict[str, str] = {}

    def __getattr__(self, name):
        # 桩缺方法时，生产代码的 `except Exception → 记降级` 会把它吞成「Redis 不可用」，
        # 测试**照样绿**（本轮把 setex 换成 set 时就是这样静默失败的）。
        # 所以这里必须响：未登记的命令直接判失败，而不是抛 AttributeError 供降级分支消化。
        raise AssertionError(
            f"_StubRedis 未实现命令 {name!r}：生产代码新用到的命令必须在本桩登记"
        )

    async def get(self, key):
        return self._store.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    async def setex(self, key, ttl, value):
        self._store[key] = value

    async def incr(self, key):
        current = int(self._store.get(key, "0")) + 1
        self._store[key] = str(current)
        return current

    async def delete(self, *keys):
        deleted = sum(1 for k in keys if self._store.pop(k, None) is not None)
        return deleted

    def pipeline(self):
        stub = self

        class _Pipe:
            def __init__(self):
                self._cmds: list[tuple[str, str]] = []

            def exists(self, key):
                self._cmds.append(("exists", key))
                return self

            def get(self, key):
                self._cmds.append(("get", key))
                return self

            async def execute(self):
                return [
                    (1 if stub._store.get(key) is not None else 0)
                    if op == "exists" else stub._store.get(key)
                    for op, key in self._cmds
                ]

        return _Pipe()


class TokenRevocationTests(unittest.IsolatedAsyncioTestCase):
    """jti 黑名单 + 版本号吊销（StubRedis）"""

    async def asyncSetUp(self):
        self._orig_redis = security_core.redis_client
        security_core.redis_client = _StubRedis()

    async def asyncTearDown(self):
        security_core.redis_client = self._orig_redis

    async def test_access_token_carries_jti_and_ver(self):
        token = create_access_token({"sub": "1", "role": "user"})
        payload = decode_token(token)
        self.assertTrue(payload["jti"])
        self.assertEqual(payload["ver"], 0)
        self.assertEqual(payload["type"], "access")

    async def test_logout_blacklists_current_token(self):
        tokens = create_token_pair(1, "user")
        # 登出前有效
        payload = decode_token(tokens["access_token"])
        self.assertFalse(await is_access_token_revoked(payload))
        # 登出 → 黑名单
        await blacklist_access_token(tokens["access_token"])
        self.assertTrue(await is_access_token_revoked(payload))
        # refresh token 不进黑名单（由 Redis 单槽轮换管理）
        await blacklist_access_token(tokens["refresh_token"])

    async def test_version_bump_revokes_all_tokens(self):
        old = create_token_pair(1, "user", ver=0)
        await bump_token_version(1)
        self.assertEqual(await get_token_version(1), 1)
        # 旧版本 token 失效
        self.assertTrue(await is_access_token_revoked(decode_token(old["access_token"])))
        # 新签发（ver=当前版本）有效
        new = create_token_pair(1, "user", ver=await get_token_version(1))
        self.assertFalse(await is_access_token_revoked(decode_token(new["access_token"])))

    async def test_legacy_token_without_jti_still_version_checked(self):
        """升级过渡期：无 jti/ver 的旧 token 按版本 0 处理，版本号校验仍生效"""
        legacy = create_access_token({"sub": "7", "role": "user"})
        legacy_payload = decode_token(legacy)
        legacy_payload.pop("jti")
        legacy_payload.pop("ver")
        self.assertFalse(await is_access_token_revoked(legacy_payload))
        await bump_token_version(7)
        self.assertTrue(await is_access_token_revoked(legacy_payload))


class RouterLayerHasNoOrmTests(unittest.TestCase):
    """`app/api/` 必须零 ORM（第三轮增量审查 P1-41，事务/查询归属收口）

    ``api/admin_rbac.py`` 曾是全仓**唯一**在 router 里直接 ``select(Role)`` 的地方：
    事务归属三套约定并存（多数 admin service 自 commit、setting_service 刻意不 commit、
    router 层做 ORM），新人按哪种写都可能双提交或漏失效缓存。收敛到
    ``rbac_service.get_role_detail`` 后，本守卫防止它再长回来。
    """

    def test_api_layer_has_no_direct_orm(self):
        from pathlib import Path

        api_dir = Path(__file__).resolve().parent.parent / "app" / "api"
        offenders: list[str] = []
        for path in api_dir.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for marker in ("select(", "db.execute(", "db.add("):
                if marker in text:
                    offenders.append(f"{path.name}: {marker}")
        self.assertEqual(
            offenders, [], f"app/api/ 出现直接 ORM 调用（应下沉到 service 层）: {offenders}"
        )


if __name__ == "__main__":
    unittest.main()
