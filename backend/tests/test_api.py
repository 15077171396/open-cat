"""API 层安全与契约单测 (ARCH-015 / SEC-014 / SEC-012)。

通过 httpx.ASGITransport 直接驱动 FastAPI app, 覆盖 get_db/get_current_user,
不访问真实 MySQL/Redis。
"""

import unittest

from tests.conftest import async_client, make_user


class HealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_health(self):
        async with async_client(include_auth=False) as client:
            resp = await client.get("/api/v1/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"], {"status": "ok"})


class MeEndpointTests(unittest.IsolatedAsyncioTestCase):
    """SEC-014: /me 不得返回 pending_auth_code 原文。"""

    async def test_me_returns_has_pending_auth_code_bool(self):
        user = make_user(pending_auth_code="12345678")
        async with async_client(user=user) as client:
            resp = await client.get("/api/v1/auth/me")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertIn("has_pending_auth_code", data)
        self.assertTrue(data["has_pending_auth_code"])
        # 不得泄露原文
        self.assertNotIn("pending_auth_code", data)
        self.assertEqual(data["auth_status"], "pending")

    async def test_me_without_pending_code(self):
        user = make_user(pending_auth_code=None)
        async with async_client(user=user) as client:
            resp = await client.get("/api/v1/auth/me")
        data = resp.json()["data"]
        self.assertFalse(data["has_pending_auth_code"])
        self.assertEqual(data["auth_status"], "none")

    async def test_me_vip_shows_approved(self):
        user = make_user(role="vip", pending_auth_code="9999")
        async with async_client(user=user) as client:
            resp = await client.get("/api/v1/auth/me")
        data = resp.json()["data"]
        self.assertEqual(data["auth_status"], "approved")

    async def test_me_requires_auth(self):
        async with async_client(include_auth=False) as client:
            resp = await client.get("/api/v1/auth/me")
        self.assertEqual(resp.status_code, 401)


class ValidationHandlerTests(unittest.IsolatedAsyncioTestCase):
    """SEC-012: 校验失败统一返回 422 与通用提示。"""

    async def test_register_invalid_body_returns_422(self):
        async with async_client(include_auth=False) as client:
            # 缺少必填字段
            resp = await client.post("/api/v1/auth/register", json={})
        self.assertEqual(resp.status_code, 422)
        body = resp.json()
        self.assertEqual(body["code"], 422)
        # 消息携带第一个出错字段，便于前端/管理后台定位
        self.assertTrue(
            body["message"].startswith("参数校验失败"),
            f"unexpected message: {body['message']}",
        )
        self.assertIn("account", body["message"])

    async def test_register_bad_account_format(self):
        async with async_client(include_auth=False) as client:
            resp = await client.post(
                "/api/v1/auth/register",
                json={"account": "not-an-account", "password": "secret123", "code": "1234"},
            )
        self.assertEqual(resp.status_code, 422)


class SendCodeTests(unittest.IsolatedAsyncioTestCase):
    """send-code 不需鉴权, 但需要真实 verification 流程 (会命中 dev 发送)。"""

    async def test_send_code_invalid_account(self):
        async with async_client(include_auth=False) as client:
            resp = await client.post("/api/v1/auth/send-code", json={"account": "bad"})
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
