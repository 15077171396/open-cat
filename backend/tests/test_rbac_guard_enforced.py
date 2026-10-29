"""RBAC 守卫必须真的会拒绝：变异实验实测把守卫函数体改成 return 后 551 个用例仍全绿
（conftest 覆盖了该依赖，而既有测试只断言规则表登记或依赖被挂载，从不断言它会拒绝）。
本文件只打桩「用户持有什么权限」（get_user_rbac 依赖 JOIN 而替身不实现），不打桩守卫本身，
并直接跑真实 app——守卫被改成空操作时本文件用例立刻变红。"""

import contextlib
import unittest
from unittest import mock

import httpx
from fastapi import Request

from app.core.exceptions import AppException
from app.core.rbac import check_route_permission
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.services import rbac_service
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession


def _admin_user(uid: int = 1) -> User:
    return User(id=uid, account=f"a{uid}@example.com", password_hash="x", role="admin")


def _stub_rbac(*, is_super: bool, perms: list[str]):
    """只注入「操作者持有什么权限」这一事实，守卫的判断逻辑仍是真的在跑"""

    async def _fake_get_user_rbac(db, user_id):
        roles = [{"id": 1, "code": "super_admin", "name": "超管"}] if is_super else []
        return {"roles": roles, "perms": perms}

    return mock.patch.object(rbac_service, "get_user_rbac", _fake_get_user_rbac)


@contextlib.asynccontextmanager
async def _real_guard_client(user: User):
    """与 conftest.async_client 同构，但**刻意不覆盖 check_route_permission**。

    这正是本文件存在的理由 —— 那个夹具把守卫变成了 no-op。
    """
    from app.core import rate_limit as _rate_limit
    from app.main import app
    from app.services import health_service as _health_service
    from app.utils import verification as _verification

    async def _override_db():
        yield MemorySession()

    async def _override_user():
        return user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user

    fake_redis = FakeRedis()
    originals = (
        _rate_limit.redis_client,
        _verification.redis_client,
        _health_service.redis_client,
    )
    _rate_limit.redis_client = fake_redis
    _verification.redis_client = fake_redis
    _health_service.redis_client = fake_redis

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        (
            _rate_limit.redis_client,
            _verification.redis_client,
            _health_service.redis_client,
        ) = originals


class RouteGuardDeniesTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_without_any_permission_is_denied(self):
        """role=admin 但没有任何 RBAC 权限 → 403（守卫失效时此用例必红）"""
        with _stub_rbac(is_super=False, perms=[]):
            async with _real_guard_client(_admin_user()) as client:
                resp = await client.get("/api/v1/admin/users")
        self.assertEqual(
            resp.status_code, 403,
            "没有任何 RBAC 权限的管理员竟能访问 /admin/users —— 守卫没生效",
        )

    async def test_admin_with_unrelated_permission_is_denied(self):
        """权限码不匹配（any-of）也必须拒绝 —— 防止「有任意权限就放行」"""
        with _stub_rbac(is_super=False, perms=["tutorial:view"]):
            async with _real_guard_client(_admin_user()) as client:
                resp = await client.get("/api/v1/admin/users")
        self.assertEqual(resp.status_code, 403)

    async def test_admin_with_matching_permission_passes_guard(self):
        """反向对照：权限匹配时不该被 403。

        没有这条，「守卫永远拒绝」也能让上面两条通过 —— 那同样是假绿。
        这里只断言「不是 403」：后续业务逻辑跑在 MemorySession 上，可能因替身
        不支持某条语句而 500，与本用例要验证的东西无关。
        """
        with _stub_rbac(is_super=False, perms=["user:view"]):
            async with _real_guard_client(_admin_user()) as client:
                resp = await client.get("/api/v1/admin/users")
        self.assertNotEqual(resp.status_code, 403)

    async def test_super_admin_bypasses(self):
        with _stub_rbac(is_super=True, perms=[]):
            async with _real_guard_client(_admin_user()) as client:
                resp = await client.get("/api/v1/admin/users")
        self.assertNotEqual(resp.status_code, 403)


class FailClosedTests(unittest.IsolatedAsyncioTestCase):
    """未登记的路径必须 fail-closed（新增管理端路由忘了登记规则时不能放行）"""

    @staticmethod
    def _request(method: str, path: str) -> Request:
        return Request(
            {"type": "http", "method": method, "path": path, "headers": []}
        )

    async def test_unregistered_path_denied_for_normal_admin(self):
        req = self._request("GET", "/api/v1/admin/definitely-not-registered")
        with _stub_rbac(is_super=False, perms=["user:view"]):
            with self.assertRaises(AppException) as ctx:
                await check_route_permission(req, db=MemorySession(), user=_admin_user())
        self.assertEqual(ctx.exception.code, 403)
        self.assertIn("未配置访问权限", ctx.exception.message)

    async def test_unregistered_path_denied_even_for_super_admin(self):
        """刻意如此：规则缺失是**配置错误**，不该被 super_admin 的逃逸通道掩盖"""
        req = self._request("GET", "/api/v1/admin/definitely-not-registered")
        with _stub_rbac(is_super=True, perms=[]):
            with self.assertRaises(AppException) as ctx:
                await check_route_permission(req, db=MemorySession(), user=_admin_user())
        self.assertEqual(ctx.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
