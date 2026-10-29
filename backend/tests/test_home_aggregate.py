"""首页聚合的扇出上限与陈旧兜底（第四轮全量审查 P1-59）。

原实现的失效链：`_single_flight` 等锁 5s 超时后**放弃排他、直接查库**，而一次聚合
是 8 个独立 Session 并发（池上限 5+10=15/worker）。一个慢子查询让持锁超过 5s，
排队者会**集体**放弃排他 —— 3 个超时请求就要 24 条连接 > 15，于是该 worker 上
所有路由一起排队到 `pool_timeout` 后 500。注释里那句「宁可多查一次，也不要让一个
板块拖垮整站」实际打开了放大器：它防住了排队，却没防住并发扇出。
"""

import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest import mock

from app.api import home


class AggregateFanoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_sub_queries_are_capped(self):
        active = 0
        peak = 0

        @asynccontextmanager
        async def fake_session():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.02)
                yield object()
            finally:
                active -= 1

        async def sub(_session):
            await asyncio.sleep(0.02)
            return {"ok": True}

        with mock.patch.object(home, "async_session", fake_session):
            await asyncio.gather(*[home._run_with_session(sub) for _ in range(8)])

        self.assertLessEqual(
            peak,
            home._AGGREGATE_MAX_CONCURRENT,
            f"一次聚合最多同时占了 {peak} 条连接，超过上限 {home._AGGREGATE_MAX_CONCURRENT}",
        )

    async def test_sub_query_failure_still_degrades_to_none(self):
        """单板块异常不能把整页打挂（既有语义，改动后必须仍成立）"""

        @asynccontextmanager
        async def boom_session():
            yield object()

        async def sub(_session):
            raise RuntimeError("板块炸了")

        with mock.patch.object(home, "async_session", boom_session):
            self.assertIsNone(await home._run_with_session(sub))

    def test_stale_window_is_wider_than_cache_ttl(self):
        """兜底陈旧值必须比缓存 TTL 更久，否则「缓存刚过期 + 锁被打满」时仍然打库"""
        self.assertGreater(home._STALE_MAX_AGE, home.CACHE_TTL)


if __name__ == "__main__":
    unittest.main()
