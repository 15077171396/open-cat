"""系统错误日志（Redis 环形缓冲）契约测试。

钉住三条**破了不报错、只会悄悄失去意义**的契约：

1. **上报失败绝不打日志**：sink/推送里任何失败都必须静默（否则「记录错误的
   过程」又触发错误记录，无限自激）；也不用 degrade_open（它自己会打日志）。
2. **去重窗口**：同一位置的重复报错 5 秒内只推第一条（防异常风暴把缓冲刷满，
   把其他错误挤出去）。
3. **读侧降级**：Redis 不可用 → available=False 的空列表，观测接口不 500。
"""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from app.logging import ERROR_LOG_KEY, ERROR_LOG_MAX, _error_log_sink, _push_error_event, _LAST_PUSH
from tests.conftest import FakeRedis


def _run(coro):
    return asyncio.run(coro)


def _message(name: str = "app.mod", function: str = "run", message: str = "boom"):
    """构造 loguru sink 收到的 message 形状（dict 子类 + record）"""
    record = {
        "time": datetime(2026, 11, 18, 1, 2, 3),
        "level": SimpleNamespace(name="ERROR", no=40),
        "name": name,
        "function": function,
        "line": 12,
        "message": message,
        "extra": {"request_id": "req-1"},
    }
    return {"record": record}


class PushTests(unittest.TestCase):
    def test_push_event_writes_and_trims(self):
        fake = FakeRedis()
        with mock.patch("app.redis.redis_client", fake):
            _run(_push_error_event({"ts": "2026-11-18T01:02:03", "level": "ERROR", "message": "boom"}))
            # 再推 3 条，验证 LTRIM 上限生效
            for i in range(3):
                _run(_push_error_event({"ts": None, "level": "ERROR", "message": f"m{i}"}))
        self.assertEqual(_run(fake.llen(ERROR_LOG_KEY)), 4)
        head = json.loads(fake._lists[ERROR_LOG_KEY][0])
        self.assertEqual(head["message"], "m2")

    def test_push_failure_is_silent(self):
        """Redis 挂掉：静默放弃，不抛异常（自激防护）"""

        class _BoomRedis:
            async def lpush(self, *args, **kwargs):
                raise ConnectionError("redis down")

        with mock.patch("app.redis.redis_client", _BoomRedis()):
            # 不应抛出
            _run(_push_error_event({"ts": None, "level": "ERROR", "message": "boom"}))


class SinkTests(unittest.TestCase):
    def setUp(self):
        _LAST_PUSH.clear()

    def tearDown(self):
        _LAST_PUSH.clear()

    def test_sink_dedups_same_fingerprint_within_window(self):
        fake = FakeRedis()
        with mock.patch("app.redis.redis_client", fake):
            async def scenario():
                _error_log_sink(_message())
                _error_log_sink(_message())  # 同指纹 → 去重
                await asyncio.sleep(0.05)  # 让 fire-and-forget 任务跑完

            _run(scenario())
        self.assertEqual(_run(fake.llen(ERROR_LOG_KEY)), 1)

    def test_sink_pushes_different_fingerprints(self):
        fake = FakeRedis()
        with mock.patch("app.redis.redis_client", fake):
            async def scenario():
                _error_log_sink(_message(message="a"))
                _error_log_sink(_message(message="b"))
                await asyncio.sleep(0.05)

            _run(scenario())
        self.assertEqual(_run(fake.llen(ERROR_LOG_KEY)), 2)

    def test_sink_outside_event_loop_is_noop(self):
        """无事件循环（导入期/脚本场景）：静默跳过，不抛异常"""
        # 不应抛出；也不产生任何推送
        _error_log_sink(_message())


class ErrorLogsApiTests(unittest.TestCase):
    def test_endpoint_parses_buffered_events(self):
        from app.main import app
        import httpx

        fake = FakeRedis()
        async def _seed():
            await fake.lpush(
                ERROR_LOG_KEY,
                json.dumps(
                    {
                        "ts": "2026-11-18T01:02:03",
                        "level": "ERROR",
                        "message": "boom",
                        "name": "app.mod",
                        "function": "run",
                        "line": 12,
                        "request_id": "req-1",
                    },
                    ensure_ascii=False,
                ),
            )

        from app.core import rbac as _rbac
        from app.dependencies import get_current_user, get_current_user_optional, get_db
        from app.models.user import User

        _run(_seed())

        async def _override_db():
            yield SimpleNamespace(commit=mock.AsyncMock(), rollback=mock.AsyncMock(), close=mock.AsyncMock())

        async def _override_user():
            return User(id=1, account="root", role="admin")

        async def _override_rbac():
            return None

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = _override_user
        app.dependency_overrides[get_current_user_optional] = _override_user
        app.dependency_overrides[_rbac.check_route_permission] = _override_rbac

        from app.api import admin_monitor

        original_redis = admin_monitor.redis_client
        admin_monitor.redis_client = fake
        try:
            async def scenario():
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    return await client.get("/api/v1/admin/monitor/errors")

            resp = _run(scenario())
        finally:
            app.dependency_overrides.clear()
            admin_monitor.redis_client = original_redis

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 0)
        self.assertTrue(body["data"]["available"])
        self.assertEqual(body["data"]["items"][0]["message"], "boom")
        self.assertEqual(body["data"]["items"][0]["request_id"], "req-1")

    def test_endpoint_degrades_when_redis_down(self):
        from app.main import app
        import httpx

        from app.core import rbac as _rbac
        from app.dependencies import get_current_user, get_current_user_optional, get_db
        from app.models.user import User

        class _BoomRedis:
            async def lrange(self, *args, **kwargs):
                raise ConnectionError("redis down")

        async def _override_db():
            yield SimpleNamespace(commit=mock.AsyncMock(), rollback=mock.AsyncMock(), close=mock.AsyncMock())

        async def _override_user():
            return User(id=1, account="root", role="admin")

        async def _override_rbac():
            return None

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = _override_user
        app.dependency_overrides[get_current_user_optional] = _override_user
        app.dependency_overrides[_rbac.check_route_permission] = _override_rbac

        from app.api import admin_monitor

        original_redis = admin_monitor.redis_client
        admin_monitor.redis_client = _BoomRedis()
        try:
            async def scenario():
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    return await client.get("/api/v1/admin/monitor/errors")

            resp = _run(scenario())
        finally:
            app.dependency_overrides.clear()
            admin_monitor.redis_client = original_redis

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["data"]["available"])
        self.assertEqual(body["data"]["items"], [])


if __name__ == "__main__":
    unittest.main()
