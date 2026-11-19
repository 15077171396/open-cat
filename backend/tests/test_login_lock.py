"""登录失败计数/锁定的作用域（第四轮全量审查 P1-50 + P1-57）。

钉两条不变量：

1. **凭据校验入口自带失败锁定与账号枚举的时序均衡**——账号不存在时也算一次
   bcrypt（响应时间差 = 无鉴权的账号枚举探针）。
2. **锁定按 (IP, 账号) 而不是只按账号**。只按账号时，任何知道目标登录串
   （公开邮箱/手机号）的人每 15 分钟发 10 次假密码，就能把受害者**永久**锁在
   门外，攻击成本约 10 请求/刻钟且受害者无法自救 —— 这是可被第三方滥用的武器，
   不是防护。账号级撞库的正解是人机挑战（台账 P1-37，已决策暂缓）。
"""

import unittest
from unittest import mock

from app.core.exceptions import AuthenticationError
from app.models.user import User
from app.services import auth_service
from tests.conftest import FakeRedis
from tests.fake_db import MemorySession

ACCOUNT = "victim@example.com"
ATTACKER_IP = "203.0.113.66"
VICTIM_IP = "198.51.100.23"


class LockScopeTests(unittest.IsolatedAsyncioTestCase):
    """(IP, 账号) 作用域：锁定不能被第三方拿去当骚扰武器，但攻击者自己躲不掉"""

    def setUp(self):
        self.db = MemorySession()
        self.db.seed(User(id=7, account=ACCOUNT, password_hash="$2b$12$x", role="user"))
        # 口令校验用假实现：本文件测「计数与锁定」，不是 bcrypt（也快一个数量级）
        self._orig_redis = auth_service.redis_client
        auth_service.redis_client = FakeRedis()

    async def asyncTearDown(self):
        auth_service.redis_client = self._orig_redis

    def _wrong_password(self):
        return mock.patch.object(
            auth_service, "verify_password_async", mock.AsyncMock(return_value=False)
        )

    async def _try(self, account=ACCOUNT, ip=ATTACKER_IP) -> str:
        with self._wrong_password():
            with self.assertRaises(AuthenticationError) as ctx:
                await auth_service.verify_account_password(self.db, account, "wrong", client_ip=ip)
        return str(ctx.exception)

    async def test_lock_is_per_ip_and_account(self):
        for _ in range(auth_service.LOGIN_FAIL_LIMIT):
            await self._try()

        self.assertIn("登录失败次数过多", await self._try())

        # 换来源必须只是「账号或密码错误」—— 证明锁定不是账号级的全局武器
        self.assertEqual(await self._try(ip=VICTIM_IP), "账号或密码错误")

    async def test_no_lock_before_reaching_limit(self):
        """未达上限时不得出现锁定文案（否则等于把阈值降到了 1）"""
        for _ in range(auth_service.LOGIN_FAIL_LIMIT - 1):
            self.assertEqual(await self._try(), "账号或密码错误")

    async def test_success_clears_the_counter_for_that_scope(self):
        """成功后清零：否则合法用户偶尔输错几次就会被累计锁住"""
        for _ in range(auth_service.LOGIN_FAIL_LIMIT - 1):
            await self._try()

        with mock.patch.object(
            auth_service, "verify_password_async", mock.AsyncMock(return_value=True)
        ):
            user = await auth_service.verify_account_password(
                self.db, ACCOUNT, "right", client_ip=ATTACKER_IP
            )
        self.assertEqual(user.id, 7)

        # 清零后需要再打满一整轮才会锁。注意差一：第 LIMIT 次失败的响应仍是
        # 「账号或密码错误」（锁是在这次写入的），锁定文案从下一次开始出现。
        for _ in range(auth_service.LOGIN_FAIL_LIMIT):
            self.assertEqual(await self._try(), "账号或密码错误")
        self.assertIn("登录失败次数过多", await self._try())

    async def test_missing_account_still_costs_a_hash(self):
        """账号不存在也要走一次等代价校验（时序均衡），错误文案完全一致"""
        with self._wrong_password() as vpa:
            with self.assertRaises(AuthenticationError) as ctx:
                await auth_service.verify_account_password(
                    self.db, "nobody@example.com", "whatever", client_ip=ATTACKER_IP
                )
        self.assertEqual(str(ctx.exception), "账号或密码错误")
        vpa.assert_awaited()  # 没有短路 → 响应时间不暴露账号是否存在


if __name__ == "__main__":
    unittest.main()
