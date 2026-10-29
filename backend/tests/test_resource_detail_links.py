"""资源详情：网盘链接**数量**必须对游客可见（2026-10-12）。

背景（前台需求）：
    资源详情页的「下载链接」Tab 在**没有链接**时不该出现 —— 空 Tab 没有信息量，
    只会让人多点一次。于是前端需要提前知道「这条资源到底有没有链接」。

    但 `get_resource_detail` 对游客**刻意不返回 links**（只有登录用户才拿得到
    真实链接与提取码，这是设计而非疏漏）。若响应里只有 `netdisk_links`，
    游客拿到的永远是 `[]`，前端就无法区分「这条资源没配链接」和
    「你没登录所以看不到」—— 只能二选一：给所有资源都显示一个空 Tab，
    或者把**有链接**的资源也一起藏掉（后者直接砍掉登录转化入口）。两者都是错的。

    因此补 `netdisk_link_count`，与 `tutorial_service` 的同名字段同一约定：
    **数量永远返回，内容仍然只给登录用户**。

本文件钉住这条契约，防止有人「顺手」把它改成只返回 links。
"""

import unittest
from contextlib import asynccontextmanager
from unittest import mock

import httpx

from app.models.resource import Resource, ResourceNetdiskLink
from app.models.user import User
from app.services import resource_service
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession


def _link(link_id: int, resource_id: int = 2) -> ResourceNetdiskLink:
    return ResourceNetdiskLink(
        id=link_id,
        resource_id=resource_id,
        platform="baidu",
        url=f"https://pan.baidu.com/s/{link_id}",
        extract_code="1234",
        sort_order=link_id,
    )


def _seed(*, with_links: bool) -> MemorySession:
    """MemorySession 不走真实 selectinload，关系需手动挂上（已实测可行）。"""
    db = MemorySession()
    resource = Resource(id=2, name="资源2")
    db.seed(resource)
    if with_links:
        resource.netdisk_links = [_link(11), _link(12)]
    else:
        resource.netdisk_links = []
    return db


class ResourceDetailLinkCountTests(unittest.IsolatedAsyncioTestCase):
    async def test_guest_gets_count_but_not_links(self):
        """游客：拿到数量、拿不到内容 —— 这是前端的判断依据，也是权限边界"""
        db = _seed(with_links=True)
        data = await resource_service.get_resource_detail(db, 2, None)

        self.assertEqual(data["netdisk_link_count"], 2, "游客必须能拿到链接数量")
        self.assertEqual(data["netdisk_links"], [], "游客不得看到链接内容")

    async def test_guest_without_links_gets_zero(self):
        """数量为 0 时前端才会隐藏 Tab —— 这条是「隐藏」分支的唯一依据"""
        db = _seed(with_links=False)
        data = await resource_service.get_resource_detail(db, 2, None)

        self.assertEqual(data["netdisk_link_count"], 0)
        self.assertEqual(data["netdisk_links"], [])

    async def test_logged_in_gets_links_and_matching_count(self):
        db = _seed(with_links=True)
        data = await resource_service.get_resource_detail(
            db, 2, User(id=9, account="u@example.com", password_hash="x", role="user")
        )

        self.assertEqual(len(data["netdisk_links"]), 2)
        self.assertEqual(
            data["netdisk_link_count"],
            len(data["netdisk_links"]),
            "数量必须与返回的链接条数一致，否则前端会渲染出一个空 Tab",
        )


class ResourceDetailRouteTests(unittest.IsolatedAsyncioTestCase):
    """契约必须**经真实路由**成立：前端读的是 HTTP 响应，不是 service 返回值。"""

    def setUp(self):
        self.db = _seed(with_links=True)
        from app.core import rate_limit as _rate_limit

        self._patchers = [mock.patch.object(_rate_limit, "redis_client", FakeRedis())]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)

    @asynccontextmanager
    async def _client(self):
        from app.database import get_db
        from app.main import app

        async def _override_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_db
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test", timeout=10.0
            ) as client:
                yield client
        finally:
            app.dependency_overrides.pop(get_db, None)

    async def test_route_exposes_count_to_guest(self):
        async with self._client() as client:
            resp = await client.get("/api/v1/resources/2")
        self.assertEqual(resp.status_code, 200, resp.text[:300])
        data = resp.json()["data"]
        self.assertIn("netdisk_link_count", data, "缺少该字段 → 前端无法判断是否渲染下载链接 Tab")
        self.assertEqual(data["netdisk_link_count"], 2)
        self.assertEqual(data["netdisk_links"], [], "游客仍不应拿到链接内容")


if __name__ == "__main__":
    unittest.main()
