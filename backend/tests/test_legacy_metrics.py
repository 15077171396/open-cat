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
        await record_legacy_usage("comment_legacy_param")
        await record_legacy_usage("comment_legacy_param")
        key = legacy_key("comment_legacy_param", dt.datetime.now(dt.timezone.utc))
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
        await record_legacy_usage("comment_legacy_param")
        snap = await legacy_usage_snapshot(days=7)
        self.assertEqual(set(snap), set(LEGACY_EVENTS), "看板必须给每个登记事件都有一行，包括 0 次的那些")
        self.assertEqual(snap["comment_legacy_param"]["today"], 1)
        self.assertEqual(snap["comment_legacy_param"]["last_days"], 1)

    async def test_failure_degrades_to_empty_not_zeros(self):
        """Redis 挂了必须返回**空**，不能返回一堆 0。"""
        with mock.patch("app.core.legacy_metrics.redis_client", _BrokenRedis()):
            await record_legacy_usage("comment_legacy_param")  # 计数失败不能抛到业务
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
        self.assertGreaterEqual(len(literals), 2, f"只找到 {len(literals)} 处计数调用，守卫可能已失效")
        unregistered = [f"{f}:{n} {v!r}" for f, n, v in literals if v not in LEGACY_EVENTS]
        self.assertEqual(unregistered, [], "以下兼容事件没登记进 LEGACY_EVENTS: " + "; ".join(unregistered))

    def test_every_registered_event_is_counted_somewhere(self):
        counted = {v for _f, _n, v in self._literals()}
        dead = sorted(set(LEGACY_EVENTS) - counted)
        self.assertEqual(dead, [], f"以下事件已登记但没有任何计数点，看板会永远显示 0: {dead}")

if __name__ == "__main__":
    unittest.main()
