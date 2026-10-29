"""免费时长计时链路测试 (P0-6)。

覆盖 timer_service 中直接决定用户权益的三件事：
1. 自然时间倒计时的边界（含 VIP/admin 豁免）；
2. VIP 过期后权益**立即**失效，不留长达 2 小时的滞留窗口；
3. 学习时长上报的防刷判重（并发上报不重复累加统计）。

用 FakeRedis 替换 app.redis.redis_client，不触达真实 Redis。
"""

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from app.config import settings
from app.models.user import User
from app.services import timer_service as ts
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession


def make_user(
    *,
    id: int = 1,
    role: str = "user",
    started_ago: timedelta | None = None,
    vip_expire_in: timedelta | None = None,
    free_used_seconds: int = 0,
) -> User:
    """构造用户：started_ago 控制免费时长起点，vip_expire_in 控制 VIP 到期（负数=已过期）"""
    now = datetime.now(timezone.utc)
    return User(
        id=id,
        account=f"u{id}@example.com",
        password_hash="x",
        role=role,
        free_used_seconds=free_used_seconds,
        free_started_at=(now - started_ago).replace(tzinfo=None) if started_ago else None,
        vip_expire_time=(now + vip_expire_in).replace(tzinfo=None) if vip_expire_in else None,
    )


class TestFreeTimeCountdown(unittest.IsolatedAsyncioTestCase):
    """自然时间倒计时边界"""

    def test_fresh_user_gets_full_quota(self):
        user = make_user(started_ago=timedelta(seconds=0))
        status = ts.get_free_time_status(user)
        self.assertEqual(status["free_remaining_seconds"], settings.FREE_TIME_LIMIT)
        self.assertFalse(status["is_exhausted"])

    def test_elapsed_time_is_deducted(self):
        user = make_user(started_ago=timedelta(hours=2))
        status = ts.get_free_time_status(user)
        self.assertEqual(
            status["free_remaining_seconds"], settings.FREE_TIME_LIMIT - 7200
        )
        self.assertFalse(status["is_exhausted"])

    def test_exhausted_exactly_at_limit(self):
        # 恰好跑满 3 小时：remaining 归零且判定耗尽（边界值，差一秒最容易写错）
        user = make_user(started_ago=timedelta(seconds=settings.FREE_TIME_LIMIT))
        status = ts.get_free_time_status(user)
        self.assertEqual(status["free_remaining_seconds"], 0)
        self.assertTrue(status["is_exhausted"])

    def test_never_negative_when_over_limit(self):
        user = make_user(started_ago=timedelta(days=10))
        status = ts.get_free_time_status(user)
        self.assertEqual(status["free_remaining_seconds"], 0)
        self.assertTrue(status["is_exhausted"])

    def test_falls_back_to_created_at_when_start_missing(self):
        user = make_user(started_ago=timedelta(hours=1))
        user.free_started_at = None
        user.created_at = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
        status = ts.get_free_time_status(user)
        self.assertAlmostEqual(
            status["free_remaining_seconds"], settings.FREE_TIME_LIMIT - 3600, delta=5
        )

    def test_no_timestamp_at_all_treated_as_fresh(self):
        user = make_user()
        user.free_started_at = None
        user.created_at = None
        status = ts.get_free_time_status(user)
        self.assertFalse(status["is_exhausted"])

    # ---------- 兜底可观测性（台账 P2-7 / 代码审查 D5） ----------
    # 兜底用 created_at 只有在「MySQL 会话时区 = UTC」时才与 free_started_at 同源。
    # 行为保持不变（避免把用户挡在门外），但**必须可观测**，否则时区配错时
    # 只会静默算出小时级偏差。

    def test_fallback_to_created_at_emits_warning(self):
        user = make_user(started_ago=timedelta(hours=1))
        user.free_started_at = None
        user.created_at = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(
            tzinfo=None
        )
        ts._warned_missing_started.clear()
        with mock.patch.object(ts.logger, "warning") as warn:
            ts.get_free_time_status(user)
        self.assertEqual(warn.call_count, 1)
        self.assertIn("free_started_at", str(warn.call_args))

    def test_fallback_warning_is_deduplicated_per_user(self):
        """脏数据不能让每次请求都刷一条 WARNING（会淹掉真正有用的日志）"""
        user = make_user(started_ago=timedelta(hours=1))
        user.free_started_at = None
        user.created_at = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(
            tzinfo=None
        )
        ts._warned_missing_started.clear()
        with mock.patch.object(ts.logger, "warning") as warn:
            for _ in range(5):
                ts.get_free_time_status(user)
        self.assertEqual(warn.call_count, 1)

    def test_no_warning_when_free_started_at_present(self):
        user = make_user(started_ago=timedelta(minutes=1))
        ts._warned_missing_started.clear()
        with mock.patch.object(ts.logger, "warning") as warn:
            ts.get_free_time_status(user)
        warn.assert_not_called()


class TestVipPrivilege(unittest.IsolatedAsyncioTestCase):
    """VIP / admin 豁免，以及过期 VIP 的即时降级"""

    def test_admin_always_effective(self):
        self.assertTrue(ts.is_vip_effective(make_user(role="admin")))

    def test_vip_without_expiry_is_permanent(self):
        self.assertTrue(ts.is_vip_effective(make_user(role="vip")))

    def test_vip_not_yet_expired(self):
        self.assertTrue(ts.is_vip_effective(make_user(role="vip", vip_expire_in=timedelta(hours=1))))

    def test_expired_vip_loses_privilege_immediately(self):
        """过期 1 秒即失效：access token 可存活 2 小时，不能让权益跟着滞留"""
        user = make_user(role="vip", vip_expire_in=timedelta(seconds=-1))
        self.assertFalse(ts.is_vip_effective(user))

    def test_expired_vip_is_timed_like_normal_user(self):
        user = make_user(role="vip", vip_expire_in=timedelta(days=-1), started_ago=timedelta(days=2))
        status = ts.get_free_time_status(user)
        self.assertTrue(status["is_exhausted"])

    def test_plain_user_never_vip(self):
        self.assertFalse(ts.is_vip_effective(make_user(role="user", vip_expire_in=timedelta(days=1))))


class TestConsumeAndDedup(unittest.IsolatedAsyncioTestCase):
    """时长上报：累加统计 + 10 秒防刷"""

    async def _consume(self, user, duration=10, redis=None):
        db = MemorySession()
        db.seed(user)
        fake = redis or FakeRedis()
        with mock.patch.object(ts, "redis_client", fake):
            return await ts.check_and_consume(user, db, duration), fake

    async def test_duration_is_clamped(self):
        user = make_user(started_ago=timedelta(seconds=0))
        # duration=100 应被截断到 15，否则可凭超大上报刷高学习时长统计
        await self._consume(user, duration=100)
        self.assertEqual(user.free_used_seconds, 15)

    async def test_negative_duration_never_decreases(self):
        user = make_user(started_ago=timedelta(seconds=0), free_used_seconds=30)
        await self._consume(user, duration=-5)
        self.assertEqual(user.free_used_seconds, 30)

    async def test_duplicate_report_within_10s_is_ignored(self):
        user = make_user(started_ago=timedelta(seconds=0))
        fake = FakeRedis()
        await self._consume(user, duration=10, redis=fake)
        await self._consume(user, duration=10, redis=fake)
        self.assertEqual(user.free_used_seconds, 10)

    async def test_concurrent_reports_only_count_once(self):
        """并发上报：SET NX 必须挡住重复累加（GET+SETEX 写法在这里会双计）"""
        user = make_user(started_ago=timedelta(seconds=0))
        fake = FakeRedis()
        db = MemorySession()
        db.seed(user)
        with mock.patch.object(ts, "redis_client", fake):
            await asyncio.gather(
                *[ts.check_and_consume(user, db, 10) for _ in range(8)]
            )
        self.assertEqual(user.free_used_seconds, 10)

    async def test_vip_consume_does_not_accumulate(self):
        user = make_user(role="vip", started_ago=timedelta(days=30))
        await self._consume(user, duration=10)
        self.assertEqual(user.free_used_seconds, 0)

    async def test_redis_outage_degrades_to_allow(self):
        """Redis 不可用时应 fail-open（统计略高估），而不是让上报接口 500"""

        class BrokenRedis:
            async def set(self, *args, **kwargs):
                raise RuntimeError("redis down")

        user = make_user(started_ago=timedelta(seconds=0))
        db = MemorySession()
        db.seed(user)
        with mock.patch.object(ts, "redis_client", BrokenRedis()):
            status = await ts.check_and_consume(user, db, 10)
        self.assertEqual(user.free_used_seconds, 10)
        self.assertFalse(status["is_exhausted"])


class SeedFreeStartedAtTests(unittest.TestCase):
    """种子脚本必须显式写 ``free_started_at``（台账 P2-7）。

    该列**没有 server_default**，漏写就落 NULL —— 前台只能回退 ``created_at`` 计算
    免费时长，而 ``created_at`` 是数据库写的、时区语义与应用的 ``free_started_at``
    并不同源。这是「兜底路径被真实触发」的主要来源（两个种子脚本此前都漏写）。
    """

    def test_seed_scripts_set_free_started_at(self):
        root = Path(__file__).resolve().parents[2] / "backend"
        for name in ("seed_data.py", "seed_demo.py"):
            path = root / name
            self.assertTrue(path.exists(), f"缺少种子脚本 {name}")
            text = path.read_text(encoding="utf-8")
            self.assertIn(
                "free_started_at=",
                text,
                f"{name} 创建用户时未写 free_started_at（该列无 server_default，会落 NULL）",
            )


if __name__ == "__main__":
    unittest.main()
