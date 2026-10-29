"""管理后台教程批量操作与筛选的鉴权/参数校验单测。

复用 conftest 的 async_client（覆盖 get_db/get_current_user，不访问真实 MySQL），
仅覆盖鉴权与参数校验路径；需要 DB 的业务逻辑由部署环境验证。
"""

import unittest

from tests.conftest import async_client, make_user


class TutorialListFilterTests(unittest.IsolatedAsyncioTestCase):
    """GET /admin/tutorials 筛选参数校验与鉴权。"""

    async def test_list_requires_login(self):
        async with async_client(include_auth=False) as client:
            resp = await client.get("/api/v1/admin/tutorials")
        self.assertEqual(resp.status_code, 401)

    async def test_list_requires_admin_role(self):
        user = make_user(role="user")
        async with async_client(user=user) as client:
            resp = await client.get("/api/v1/admin/tutorials")
        self.assertEqual(resp.status_code, 403)

    async def test_list_invalid_status_rejected(self):
        user = make_user(role="admin")
        async with async_client(user=user) as client:
            resp = await client.get(
                "/api/v1/admin/tutorials", params={"status": "archived"}
            )
        self.assertEqual(resp.status_code, 422)

    async def test_list_invalid_access_mode_rejected(self):
        user = make_user(role="admin")
        async with async_client(user=user) as client:
            resp = await client.get(
                "/api/v1/admin/tutorials", params={"access_mode": "svip"}
            )
        self.assertEqual(resp.status_code, 422)

    async def test_list_invalid_is_published_rejected(self):
        user = make_user(role="admin")
        async with async_client(user=user) as client:
            # 注：pydantic 对 bool 宽松转换（"yes"/"true"/"1" 均合法），须用真正非法值
            resp = await client.get(
                "/api/v1/admin/tutorials", params={"is_published": "maybe"}
            )
        self.assertEqual(resp.status_code, 422)


class TutorialBatchDeleteTests(unittest.IsolatedAsyncioTestCase):
    """POST /admin/tutorials/batch-delete 鉴权与参数校验。"""

    async def test_batch_delete_requires_login(self):
        async with async_client(include_auth=False) as client:
            resp = await client.post(
                "/api/v1/admin/tutorials/batch-delete", json={"ids": [1]}
            )
        self.assertEqual(resp.status_code, 401)

    async def test_batch_delete_requires_admin_role(self):
        user = make_user(role="user")
        async with async_client(user=user) as client:
            resp = await client.post(
                "/api/v1/admin/tutorials/batch-delete", json={"ids": [1]}
            )
        self.assertEqual(resp.status_code, 403)

    async def test_batch_delete_empty_ids_rejected(self):
        user = make_user(role="admin")
        async with async_client(user=user) as client:
            resp = await client.post(
                "/api/v1/admin/tutorials/batch-delete", json={"ids": []}
            )
        self.assertEqual(resp.status_code, 422)

    async def test_batch_delete_too_many_ids_rejected(self):
        user = make_user(role="admin")
        async with async_client(user=user) as client:
            resp = await client.post(
                "/api/v1/admin/tutorials/batch-delete",
                json={"ids": list(range(1, 102))},
            )
        self.assertEqual(resp.status_code, 422)

    async def test_batch_delete_missing_ids_rejected(self):
        user = make_user(role="admin")
        async with async_client(user=user) as client:
            resp = await client.post("/api/v1/admin/tutorials/batch-delete", json={})
        self.assertEqual(resp.status_code, 422)


class TutorialBatchPublishTests(unittest.IsolatedAsyncioTestCase):
    """POST /admin/tutorials/batch-publish 鉴权与参数校验。"""

    async def test_batch_publish_requires_admin_role(self):
        user = make_user(role="user")
        async with async_client(user=user) as client:
            resp = await client.post(
                "/api/v1/admin/tutorials/batch-publish", json={"ids": [1]}
            )
        self.assertEqual(resp.status_code, 403)

    async def test_batch_publish_empty_ids_rejected(self):
        user = make_user(role="admin")
        async with async_client(user=user) as client:
            resp = await client.post(
                "/api/v1/admin/tutorials/batch-publish", json={"ids": []}
            )
        self.assertEqual(resp.status_code, 422)

    async def test_batch_publish_invalid_publish_type_rejected(self):
        user = make_user(role="admin")
        async with async_client(user=user) as client:
            # 注："true"/"yes" 等字符串会被 pydantic 宽松转换为 bool，须用真正非法值
            resp = await client.post(
                "/api/v1/admin/tutorials/batch-publish",
                json={"ids": [1], "publish": "maybe"},
            )
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
