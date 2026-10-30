"""监控服务回归：服务器指标、缓存指标、在线会话（不连真实 Redis）。"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

from app.models.user import User
from app.services import monitor_service
from tests.fake_db import MemorySession


def _run(coro):
    return asyncio.run(coro)


class _FakeRedis:
    def __init__(self, payloads=None, fail=False):
        self.payloads = payloads or {}
        self.fail = fail

    async def info(self):
        if self.fail:
            raise ConnectionError("redis down")
        return {
            "redis_version": "7.2.0",
            "used_memory": 1024,
            "used_memory_human": "1K",
            "used_memory_peak_human": "2K",
            "maxmemory_human": "未限制",
            "connected_clients": 3,
            "keyspace_hits": 90,
            "keyspace_misses": 10,
            "uptime_in_seconds": 3600,
        }

    async def dbsize(self):
        return 2

    async def scan_iter(self, match="*", count=500):
        if self.fail:
            raise ConnectionError("redis down")
        for key in self.payloads:
            yield key

    async def mget(self, keys):
        return [self.payloads.get(key) for key in keys]


class MonitorTests(unittest.TestCase):
    def test_server_stats_returns_metrics(self):
        data = _run(monitor_service.get_server_stats())
        self.assertTrue(data["available"])
        self.assertGreaterEqual(data["cpu_count"], 1)
        self.assertGreaterEqual(data["memory_total"], 1)
        self.assertGreaterEqual(data["uptime_seconds"], 0)

    def test_cache_stats_hit_rate_and_prefixes(self):
        fake = _FakeRedis(
            payloads={
                "sess:a": "1",
                "sess:b": "1",
                "refresh_token:1:a": "1",
            }
        )
        with mock.patch.object(monitor_service, "redis_client", fake):
            data = _run(monitor_service.get_cache_stats())
        self.assertTrue(data["available"])
        self.assertEqual(data["total_keys"], 2)
        self.assertEqual(data["hit_rate"], 90.0)
        prefixes = {row["prefix"]: row["count"] for row in data["prefixes"]}
        self.assertEqual(prefixes["sess:"], 2)
        self.assertEqual(prefixes["refresh_token:"], 1)

    def test_cache_stats_degrades_when_redis_down(self):
        with mock.patch.object(monitor_service, "redis_client", _FakeRedis(fail=True)):
            data = _run(monitor_service.get_cache_stats())
        self.assertFalse(data["available"])
        self.assertIn("Redis", data["error"])

    def test_online_sessions_join_user_accounts_and_filter(self):
        db = MemorySession()
        admin = User(id=1, account="admin@test", role="admin", password_hash="x")
        user = User(id=2, account="alice@test", role="user", nickname="Alice", password_hash="x")
        db.add_all([admin, user])
        _run(db.commit())
        fake = _FakeRedis(
            payloads={
                "sess:a": json.dumps(
                    {"uid": 1, "ua": "Chrome/120 Windows", "ip": "1.1.1.1", "created_at": 100, "last_seen": 300}
                ),
                "sess:b": json.dumps(
                    {"uid": 2, "ua": "Safari/17 iPhone", "ip": "2.2.2.2", "created_at": 200, "last_seen": 400}
                ),
            }
        )
        with mock.patch.object(monitor_service, "redis_client", fake):
            data = _run(monitor_service.list_online_sessions(db, page=1, page_size=20))
        self.assertTrue(data["available"])
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["unique_users"], 2)
        self.assertEqual(data["items"][0]["account"], "alice@test")
        self.assertEqual(data["items"][0]["device"], "Safari · iOS")

        with mock.patch.object(monitor_service, "redis_client", fake):
            filtered = _run(
                monitor_service.list_online_sessions(db, keyword="alice", page=1, page_size=20)
            )
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["items"][0]["account"], "alice@test")


if __name__ == "__main__":
    unittest.main()
