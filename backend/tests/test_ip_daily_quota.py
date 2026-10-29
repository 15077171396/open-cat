"""IP 日配额的行为契约（缺口台账 P2-37 的搬迁兜底）。

这段管线原先手写在 `api/auth.py` 的 send-code 路由里，第四轮审查把它下沉到
`core/rate_limit.check_ip_daily_quota`（限流的归属地）。**搬迁前它没有任何测试**，
所以这里补齐的是"搬完还是不是原来那个行为"：

1. 达到上限抛 429，且**按 IP 分桶**（别人的配额不被牵连）；
2. Redis 故障时**放行**并记降级（这一层拦的是轮换轰炸，单账号频控另有其人；
   在这里 fail-closed 会让 Redis 抖动直接变成全站注册不可用）；
3. key 格式与 TTL 语义不变 —— `SET NX EX` 先落 TTL 再 `INCR`，
   反序会留下无 TTL 的计数 key，那个 IP 从此永久"已达上限"；
   而 key 名一变，发版瞬间历史计数全部作废，等于给当天已被限的 IP 放行。
"""

import unittest
from unittest import mock

from app.core.exceptions import AppException
from app.core.rate_limit import check_ip_daily_quota
from tests.conftest import FakeRedis
from tests.test_security import _http_request

REQ_A = _http_request(peer="203.0.113.11")
REQ_B = _http_request(peer="203.0.113.12")


async def _hit(redis, req, limit=3, scope="send_code"):
    await check_ip_daily_quota(req, scope, limit, "今日已达上限")


class QuotaBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocks_after_the_limit_within_a_day(self):
        fake = FakeRedis()
        with mock.patch("app.core.rate_limit.redis_client", fake):
            for _ in range(3):
                await _hit(fake, REQ_A)
            with self.assertRaises(AppException) as ctx:
                await _hit(fake, REQ_A)
        self.assertEqual(ctx.exception.code, 429)

    async def test_quota_is_per_ip_not_global(self):
        fake = FakeRedis()
        with mock.patch("app.core.rate_limit.redis_client", fake):
            for _ in range(3):
                await _hit(fake, REQ_A)
            await _hit(fake, REQ_B)  # 另一个 IP 不该被 A 的消耗牵连

    async def test_scopes_do_not_share_counters(self):
        """scope 是分命名空间的开关：将来图片上传复用同一函数时不能串到验证码桶里。"""
        fake = FakeRedis()
        with mock.patch("app.core.rate_limit.redis_client", fake):
            for _ in range(3):
                await _hit(fake, REQ_A, limit=3, scope="send_code")
            await _hit(fake, REQ_A, limit=3, scope="image_upload")

    async def test_key_format_is_unchanged_by_the_move(self):
        fake = FakeRedis()
        with mock.patch("app.core.rate_limit.redis_client", fake):
            await _hit(fake, REQ_A)
        self.assertIn("send_code:ip_daily:203.0.113.11", fake._store)

    async def test_counter_gets_a_ttl_on_first_hit(self):
        fake = FakeRedis()
        with mock.patch("app.core.rate_limit.redis_client", fake):
            await _hit(fake, REQ_A)
            ttl = await fake.ttl("send_code:ip_daily:203.0.113.11")
        self.assertGreater(ttl, 0, "计数 key 没有 TTL → 该 IP 永久被限（这正是原实现修过的坑）")


class DegradationTests(unittest.IsolatedAsyncioTestCase):
    async def test_redis_failure_fails_open_and_logs(self):
        class _Broken:
            def pipeline(self):
                raise ConnectionError("redis down")

        with mock.patch("app.core.rate_limit.redis_client", _Broken()):
            await _hit(_Broken(), REQ_A)  # 不抛 = 放行

    async def test_legacy_key_without_ttl_is_repaired(self):
        """历史遗留的无 TTL key（TTL == -1）必须被补上过期，而不是继续卡住该 IP。"""
        fake = FakeRedis()
        fake._store["send_code:ip_daily:203.0.113.11"] = ("9", None)  # 无过期
        with mock.patch("app.core.rate_limit.redis_client", fake):
            with self.assertRaises(AppException):
                await _hit(fake, REQ_A, limit=3)
            self.assertGreater(await fake.ttl("send_code:ip_daily:203.0.113.11"), 0)


if __name__ == "__main__":
    unittest.main()
