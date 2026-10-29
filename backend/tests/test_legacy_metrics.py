"""兼容分支的可查询计数（缺口台账 P2-55 之六）。

## 钉住的东西

三条兼容分支（扩展列表的 `sort=hottest|rating`、评论的旧 `tutorial_id` 两处）
在第三批只留了 `logger.bind(event=...)` 日志。注释写着「连续 7 天为 0 就可以摘」，
但**日志不是判据**：要回答「旧包还在发吗」必须登服务器 grep，于是实际上永远摘不掉。
本文件钉住「计数真的会在真实请求上累加」以及「降级时不会伪装成 0」。

第二点尤其重要：如果 Redis 不可用时快照返回全 0，一次抖动就会在看板上显示成
「旧客户端已经不再用这些参数」，人就会据此摘掉兼容分支 —— 那正是当初
「收窄 sort 白名单」造成的 422 事故（P1-69 之一）的翻版。
"""

import ast
import datetime as dt
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest import mock

import httpx

from app.core import rbac as _rbac
from app.core.legacy_metrics import (
    LEGACY_EVENTS,
    legacy_key,
    legacy_usage_snapshot,
    record_legacy_usage,
)
from app.core.permissions import require_admin
from app.database import get_db
from app.dependencies import get_current_user
from app.models.comment import ContentComment
from app.models.node import Plugin
from app.models.tutorial import Tutorial
from app.models.user import User
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession

APP_DIR = Path(__file__).resolve().parents[1] / "app"
ADMIN = User(id=9001, account="admin@test", password_hash="x", role="admin")


class _BrokenRedis:
    async def incr(self, key):
        raise ConnectionError("redis down")

    async def expire(self, key, ttl):
        raise ConnectionError("redis down")

    def pipeline(self):
        raise ConnectionError("redis down")


class CounterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.redis = FakeRedis()
        patcher = mock.patch("app.core.legacy_metrics.redis_client", self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_increments_and_sets_ttl_once(self):
        await record_legacy_usage("plugin_legacy_sort")
        await record_legacy_usage("plugin_legacy_sort")
        key = legacy_key("plugin_legacy_sort", dt.datetime.now(dt.timezone.utc))
        self.assertEqual(int(await self.redis.get(key)), 2)
        ttl = await self.redis.ttl(key)
        self.assertGreater(ttl, 86400, "保留期应跨得过一个发版周期，否则摘除判据没有历史可比")

    async def test_events_are_counted_separately(self):
        await record_legacy_usage("comment_legacy_param")
        await record_legacy_usage("admin_legacy_param")
        today = dt.datetime.now(dt.timezone.utc)
        self.assertEqual(int(await self.redis.get(legacy_key("comment_legacy_param", today))), 1)
        self.assertEqual(int(await self.redis.get(legacy_key("admin_legacy_param", today))), 1)

    async def test_snapshot_buckets(self):
        await record_legacy_usage("plugin_legacy_sort")
        snap = await legacy_usage_snapshot(days=7)
        self.assertEqual(set(snap), set(LEGACY_EVENTS), "看板必须给每个登记事件都有一行，包括 0 次的那些")
        self.assertEqual(snap["plugin_legacy_sort"]["today"], 1)
        self.assertEqual(snap["plugin_legacy_sort"]["last_days"], 1)

    async def test_failure_degrades_to_empty_not_zeros(self):
        """Redis 挂了必须返回**空**，不能返回一堆 0。"""
        with mock.patch("app.core.legacy_metrics.redis_client", _BrokenRedis()):
            await record_legacy_usage("plugin_legacy_sort")  # 计数失败不能抛到业务
            self.assertEqual(await legacy_usage_snapshot(days=7), {})

    async def test_days_below_one_returns_empty(self):
        self.assertEqual(await legacy_usage_snapshot(days=0), {})


class RegistrationGuardTests(unittest.TestCase):
    """登记表与代码必须双向对齐（漏登记的事件在看板上就是一行永远为 0 的噪音）。"""

    def _literals(self) -> list[tuple[str, int, str]]:
        found = []
        for path in sorted(APP_DIR.rglob("*.py")):
            if path.name == "legacy_metrics.py":
                continue
            src = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(src)):
                if (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "record_legacy_usage"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                ):
                    found.append((path.name, node.lineno, node.args[0].value))
        return found

    def test_every_call_site_is_registered(self):
        literals = self._literals()
        self.assertGreaterEqual(len(literals), 3, f"只找到 {len(literals)} 处计数调用，守卫可能已失效")
        unregistered = [f"{f}:{n} {v!r}" for f, n, v in literals if v not in LEGACY_EVENTS]
        self.assertEqual(unregistered, [], "以下兼容事件没登记进 LEGACY_EVENTS: " + "; ".join(unregistered))

    def test_every_registered_event_is_counted_somewhere(self):
        counted = {v for _f, _n, v in self._literals()}
        dead = sorted(set(LEGACY_EVENTS) - counted)
        self.assertEqual(dead, [], f"以下事件已登记但没有任何计数点，看板会永远显示 0: {dead}")


class RouteCountingTests(unittest.IsolatedAsyncioTestCase):
    """三条兼容分支在**真实 HTTP 请求**上确实各自计数（不是只有单测里手调才涨）。"""

    def setUp(self):
        self.redis = FakeRedis()
        db = MemorySession()
        tutorial = Tutorial(id=100, title="教程A", description="d", content="c", category="ai")
        tutorial.is_published = True
        db.seed(ADMIN, tutorial, Plugin(id=200, name="扩展A", category="tool", view_count=3))
        db.seed(ContentComment(id=501, target_type="tutorial", target_id=100, user_id=ADMIN.id, content="评论"))
        self.db = db

    @asynccontextmanager
    async def _client(self):
        from app.main import app

        async def _override_db():
            yield self.db

        async def _override_admin():
            return ADMIN

        async def _no_route_check():
            pass

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[_rbac.check_route_permission] = _no_route_check
        app.dependency_overrides[require_admin] = _override_admin
        # POST /comments 要登录态（ADMIN 本人就是注册用户，直接复用）
        app.dependency_overrides[get_current_user] = _override_admin
        transport = httpx.ASGITransport(app=app)
        patcher = mock.patch("app.core.legacy_metrics.redis_client", self.redis)
        patcher.start()
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=10.0) as client:
                yield client
        finally:
            patcher.stop()
            for dep in (get_db, _rbac.check_route_permission, require_admin, get_current_user):
                app.dependency_overrides.pop(dep, None)

    async def _count(self, event: str) -> int:
        """必须在 `_client()` 上下文**内**调用：FakeRedis 的 patch 随上下文退出即失效，
        在外面读会打到本机真实 Redis，得到「计数没生效」的假结论。"""
        snap = await legacy_usage_snapshot(days=1)
        return snap[event]["today"]

    async def test_legacy_sort_alias_is_counted(self):
        async with self._client() as client:
            self.assertEqual((await client.get("/api/v1/plugins", params={"sort": "hottest"})).status_code, 200)
            self.assertEqual((await client.get("/api/v1/plugins", params={"sort": "rating"})).status_code, 200)
            count = await self._count("plugin_legacy_sort")
        self.assertEqual(count, 2)

    async def test_modern_params_are_not_counted(self):
        """没走兼容分支却计数，看板就会永远显示「还有旧流量」，兼容分支于是永远摘不掉。"""
        async with self._client() as client:
            await client.get("/api/v1/plugins", params={"sort": "comprehensive"})
            await client.get("/api/v1/comments", params={"target_type": "tutorial", "target_id": 100})
            plugin_count = await self._count("plugin_legacy_sort")
            comment_count = await self._count("comment_legacy_param")
        self.assertEqual(plugin_count, 0)
        self.assertEqual(comment_count, 0)

    async def test_public_comment_legacy_param_is_counted(self):
        async with self._client() as client:
            resp = await client.get("/api/v1/comments", params={"tutorial_id": 100})
            count = await self._count("comment_legacy_param")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(count, 1)

    async def test_post_comment_with_legacy_param_is_counted(self):
        """POST 侧也要钉住（第一轮变异验证时发现只测 GET：撤掉 POST 里的计数调用后全绿，
        而 `test_every_call_site_is_registered` 这类静态守卫天然查不出「少了一处调用」。"""
        async with self._client() as client:
            resp = await client.post(
                "/api/v1/comments", json={"tutorial_id": 100, "content": "用旧参数发的评论"}
            )
            count = await self._count("comment_legacy_param")
        self.assertIn(resp.status_code, (200, 201), resp.text)
        self.assertEqual(count, 1)

    async def test_admin_comment_legacy_param_is_counted(self):
        async with self._client() as client:
            resp = await client.get("/api/v1/admin/comments", params={"tutorial_id": 100})
            count = await self._count("admin_legacy_param")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(count, 1)

    async def test_admin_sending_both_params_is_still_legacy_traffic(self):
        """新参数也在时，旧参数被忽略（口径不变），但这次请求**仍算旧流量**：
        摘除判据要回答的是「旧 bundle 还在不会发 tutorial_id 吗」，
        把「这次有没有生效」当成判据会让兼容分支看起来比实际更早归零。"""
        async with self._client() as client:
            resp = await client.get("/api/v1/admin/comments", params={"target_id": 100, "tutorial_id": 999})
            count = await self._count("admin_legacy_param")
        self.assertEqual(count, 1)
        # 同时钉住行为：新参数优先，999 那个不存在的 id 没有把结果带走
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([i["content"] for i in resp.json()["data"]["items"]], ["评论"])


if __name__ == "__main__":
    unittest.main()
