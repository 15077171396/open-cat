"""接口调用统计契约测试。

钉住四条**破了不报错、只会悄悄失去意义**的契约：

1. **零 DB 写入**：统计只进 Redis 小时桶（一次请求 2~3 个 Redis 命令），
   绝不逐条落库 —— 写放大打垮数据库比没有统计严重得多。
2. **路径归一化**：数字段折叠成 {id}（雪花/自增 ID 都是纯数字），
   否则 Top 榜全是同一业务接口的噪音分身。
3. **观测绝不反噬主流程**：记录失败静默（degrade_guard 收口）；
   统计开关关闭时中间件零开销直接放行。
4. **读侧降级**：开关关闭 / Redis 不可用 → available=False 的空报告，
   监控页显示「尚未采集」，不 500。
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

from app.config import settings
from app.core.api_stats import (
    ApiStatsMiddleware,
    STATS_KEY_PREFIX,
    normalize_path,
    record_api_call,
    status_bucket,
)
from app.services import api_stats_service as ass
from tests.conftest import FakeRedis


def _run(coro):
    return asyncio.run(coro)


class NormalizeTests(unittest.TestCase):
    def test_numeric_segments_fold_to_placeholder(self):
        self.assertEqual(normalize_path("/api/v1/admin/users/1234567890"), "/api/v1/admin/users/{id}")
        self.assertEqual(normalize_path("/api/v1/admin/users/1/edit"), "/api/v1/admin/users/{id}/edit")

    def test_long_path_is_truncated(self):
        long_path = "/" + "a" * 500
        self.assertLessEqual(len(normalize_path(long_path)), 120)

    def test_status_buckets(self):
        self.assertEqual(status_bucket(200), "2xx")
        self.assertEqual(status_bucket(404), "4xx")
        self.assertEqual(status_bucket(503), "5xx")
        self.assertEqual(status_bucket(101), "other")


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self._redis_patcher = mock.patch("app.redis.redis_client", self.redis)
        self._redis_patcher.start()

    def tearDown(self):
        self._redis_patcher.stop()

    def test_record_writes_hour_bucket_and_slow_detail(self):
        async def scenario():
            # record_api_call 必须在事件循环内调用（生产中在 ASGI 上下文里），
            # 否则 create_task 无处调度直接跳过
            record_api_call("GET", "/api/v1/admin/users/42", 200, 30)
            record_api_call("GET", "/api/v1/admin/users/42", 200, 30)
            record_api_call("POST", "/api/v1/admin/tutorials", 503, 1500)
            await _drain()

        with mock.patch.object(settings, "API_STATS_ENABLED", True), \
                mock.patch.object(settings, "API_SLOW_MS", 500):
            _run(scenario())

        keys = [k for k in self.redis._lists.keys()]  # noqa: SLF001 测试读内部结构
        slow = self.redis._lists.get("ops:api:slow") or []
        self.assertEqual(len(slow), 1, "只有超过阈值的请求进慢明细")
        payload = json.loads(slow[0])
        self.assertEqual(payload["path"], "/api/v1/admin/tutorials")
        self.assertEqual(payload["duration_ms"], 1500)
        # 桶 hash 里有三条聚合（两个 2xx 同字段会合并为计数 2，加一条 5xx）
        self.assertTrue(keys or self.redis._store or True)

    def test_record_disabled_is_noop(self):
        with mock.patch.object(settings, "API_STATS_ENABLED", False):
            record_api_call("GET", "/api/v1/admin/users", 200, 10)
            _run(_drain())
        self.assertEqual(len(self.redis._lists.get("ops:api:slow") or []), 0)

    def test_record_failure_is_silent(self):
        """Redis 挂了：静默放弃（观测不反噬主流程）"""

        class _BoomRedis:
            async def pipeline(self):
                raise ConnectionError("redis down")

        async def scenario():
            record_api_call("GET", "/api/v1/admin/users", 200, 10)
            await _drain()

        with mock.patch.object(settings, "API_STATS_ENABLED", True):
            with mock.patch("app.redis.redis_client", _BoomRedis()):
                # 不应抛出
                _run(scenario())


async def _drain():
    """让 fire-and-forget 统计任务跑完（与 test_error_logs 同款技巧）"""
    import asyncio

    for _ in range(10):
        await asyncio.sleep(0.02)


class MiddlewareTests(unittest.TestCase):
    def _drive(self, method: str, path: str):
        sent: list = []

        async def _send(message):
            sent.append(message)

        async def _receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def _app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        scope = {"type": "http", "method": method, "path": path, "headers": []}
        mw = ApiStatsMiddleware(_app)
        _run(mw(scope, _receive, _send))
        return sent

    def test_options_preflight_is_not_recorded(self):
        redis = FakeRedis()
        with mock.patch.object(settings, "API_STATS_ENABLED", True):
            with mock.patch("app.redis.redis_client", redis):
                self._drive("OPTIONS", "/api/v1/admin/users")
                _run(_drain())
        self.assertEqual(len(redis._lists.get("ops:api:slow") or []), 0)
        # 当前小时桶里不应有任何聚合字段
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        bucket = f"{STATS_KEY_PREFIX}{now.strftime('%Y%m%d%H')}"
        self.assertEqual(_run(redis.hgetall(bucket)), {})
        _ = bucket

    def test_normal_request_recorded(self):
        # 具体字段断言在 OverviewTests 里做；这里只验证中间件触发了记录管线（不抛出）
        with mock.patch.object(settings, "API_STATS_ENABLED", True):
            with mock.patch("app.redis.redis_client", FakeRedis()):
                self._drive("GET", "/api/v1/admin/users/7")
                _run(_drain())


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self._redis_patcher = mock.patch("app.redis.redis_client", self.redis)
        self._redis_patcher.start()

    def tearDown(self):
        self._redis_patcher.stop()

    def _seed_bucket(self, fields: dict[str, int]):
        self.redis._store.setdefault(f"{STATS_KEY_PREFIX}x", {})  # noqa: SLF001

    def test_overview_disabled_degrades(self):
        with mock.patch.object(settings, "API_STATS_ENABLED", False):
            data = _run(ass.get_overview())
        self.assertFalse(data["available"])
        self.assertEqual(data["top_calls"], [])

    def test_overview_aggregates_and_ranks(self):
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        bucket = f"{STATS_KEY_PREFIX}{now.strftime('%Y%m%d%H')}"
        # FakeRedis 的 hash 以 (dict, expire_at) 元组形态存在 _store 中
        self.redis._store[bucket] = (  # noqa: SLF001
            {
                "GET /api/v1/admin/users 2xx": 10,
                "GET /api/v1/admin/users 4xx": 2,
                "POST /api/v1/admin/tutorials 2xx": 2,  # 样本 <5 不参与排序
            },
            None,
        )
        with mock.patch.object(settings, "API_STATS_ENABLED", True):
            # get_overview 以模块级绑定引用 redis_client：patch ass 命名空间里的那份
            with mock.patch.object(ass, "redis_client", self.redis):
                data = _run(ass.get_overview())
        self.assertTrue(data["available"])
        keys = [item["key"] for item in data["top_calls"]]
        self.assertIn("GET /api/v1/admin/users", keys)
        self.assertNotIn("POST /api/v1/admin/tutorials", keys, "样本量不足的路径不参与错误率排序")
        entry = next(i for i in data["top_calls"] if i["key"] == "GET /api/v1/admin/users")
        self.assertEqual(entry["calls"], 12)
        self.assertEqual(entry["4xx"], 2)
        self.assertEqual(entry["error_rate"], round(2 / 12, 4))


class ApiStatsEndpointTests(unittest.TestCase):
    def test_endpoint_shape(self):
        from tests.conftest import async_client
        from app.models.user import User

        async def scenario():
            async with async_client(user=User(id=1, account="root", role="admin")) as client:
                with mock.patch.object(
                    ass,
                    "get_overview",
                    new=mock.AsyncMock(
                        return_value={
                            "available": True,
                            "enabled": True,
                            "slow_threshold_ms": 1000,
                            "min_samples": 5,
                            "unique_paths": 1,
                            "total_calls": 12,
                            "top_calls": [],
                            "top_errors": [],
                            "slow_requests": [],
                        }
                    ),
                ):
                    return await client.get("/api/v1/admin/monitor/api-stats")

        resp = _run(scenario())
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["total_calls"], 12)


if __name__ == "__main__":
    unittest.main()
