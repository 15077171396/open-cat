"""验证码服务单测 (ARCH-015)。

使用 FakeRedis 替换 verification 模块的 redis_client, 不依赖真实 Redis。
"""

import unittest
from pathlib import Path

from tests.conftest import FakeRedis
from app.utils import verification as v
from app.utils.verification import is_email, is_email_or_phone


class VerificationTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._fake = FakeRedis()
        self._orig_redis = v.redis_client
        v.redis_client = self._fake
        # 打桩发送通道：单测不应依赖外部 SMTP/短信网关。
        # (本地 .env 可能配置了不可达的 SMTP，真实发送会导致测试不稳定)
        self._orig_send_email = v.send_email
        self._orig_send_sms = v.send_sms

        async def _fake_send_email(to_email: str, code: str) -> bool:
            return True

        async def _fake_send_sms(phone: str, code: str) -> bool:
            return True

        v.send_email = _fake_send_email
        v.send_sms = _fake_send_sms

    async def asyncTearDown(self):
        v.redis_client = self._orig_redis
        v.send_email = self._orig_send_email
        v.send_sms = self._orig_send_sms

    async def test_send_code_stores_code_and_interval(self):
        result = await v.send_code("user@example.com")
        self.assertTrue(result["success"])
        # 验证码与发送间隔键都已写入
        self.assertIsNotNone(await self._fake.get(f"{v.CODE_PREFIX}user@example.com"))
        self.assertTrue(await self._fake.exists(f"{v.CODE_PREFIX}interval:user@example.com"))

    async def test_send_code_rate_limited(self):
        first = await v.send_code("13800000001")
        self.assertTrue(first["success"])
        second = await v.send_code("13800000001")
        self.assertFalse(second["success"])
        self.assertIn("频繁", second["message"])

    async def test_send_code_failure_releases_interval_lock(self):
        # 发送失败时应回滚频控锁，用户可立即重试
        async def _fail_send(to_email: str, code: str) -> bool:
            return False

        v.send_email = _fail_send
        first = await v.send_code("retry@example.com")
        self.assertFalse(first["success"])
        # 频控锁已被回滚，第二次调用可再次进入发送流程
        second = await v.send_code("retry@example.com")
        self.assertFalse(second["success"])
        self.assertIn("发送失败", second["message"])

    async def test_send_code_invalid_account_does_not_lock(self):
        # 无效账号不应占用 60 秒频控锁
        result = await v.send_code("not-an-account")
        self.assertFalse(result["success"])
        self.assertFalse(
            await self._fake.exists(f"{v.CODE_PREFIX}interval:not-an-account")
        )

    async def test_send_code_rejects_invalid_account(self):
        result = await v.send_code("not-an-account")
        self.assertFalse(result["success"])

    async def test_verify_code_success_deletes_code(self):
        await self._fake.set(f"{v.CODE_PREFIX}a@b.com", "123456", ex=v.CODE_EXPIRE)
        ok = await v.verify_code("a@b.com", "123456")
        self.assertTrue(ok)
        # 成功后验证码应被删除, 不可重放
        self.assertIsNone(await self._fake.get(f"{v.CODE_PREFIX}a@b.com"))

    async def test_verify_code_wrong_increments_fail_counter(self):
        await self._fake.set(f"{v.CODE_PREFIX}a@b.com", "123456", ex=v.CODE_EXPIRE)
        self.assertFalse(await v.verify_code("a@b.com", "000000"))
        fail_count = await self._fake.get("verify_fail:a@b.com")
        self.assertEqual(int(fail_count), 1)

    async def test_verify_code_locks_after_max_attempts(self):
        await self._fake.set(f"{v.CODE_PREFIX}a@b.com", "123456", ex=v.CODE_EXPIRE)
        for _ in range(v.MAX_VERIFY_ATTEMPTS):
            await v.verify_code("a@b.com", "000000")
        # 达到上限后锁定键存在, 后续校验直接 False
        self.assertTrue(await self._fake.exists("verify_lock:a@b.com"))
        # 验证码已被删除
        self.assertIsNone(await self._fake.get(f"{v.CODE_PREFIX}a@b.com"))

    async def test_verify_code_missing_returns_false(self):
        self.assertFalse(await v.verify_code("nobody@example.com", "123456"))

    async def test_verify_code_locked_returns_false(self):
        await self._fake.set("verify_lock:a@b.com", "1", ex=v.VERIFY_LOCK_SECONDS)
        await self._fake.set(f"{v.CODE_PREFIX}a@b.com", "123456", ex=v.CODE_EXPIRE)
        # 即使验证码正确, 锁定期内也返回 False
        self.assertFalse(await v.verify_code("a@b.com", "123456"))

    def test_mask_phone_and_email(self):
        self.assertEqual(v.mask_account("13800001234"), "138****1234")
        self.assertEqual(v.mask_account("abcdef@example.com"), "a***@example.com")

class SingleSourceValidationTests(unittest.TestCase):
    """账号正则只能有一处定义：此前 schemas/auth.py 与 api/admin_users.py 各自重抄手机号/邮箱
    正则与报错文案，改口径漏一处不报错，只会让「注册能过、后台建号被拒」悄悄发生。"""

    APP_DIR = Path(__file__).resolve().parents[1] / "app"
    FORBIDDEN = ("1[3-9]", "[\w.+-]+@")

    def test_regex_literals_live_only_in_verification(self):
        offenders = []
        for path in sorted(self.APP_DIR.rglob("*.py")):
            if path.name == "verification.py":
                continue
            src = path.read_text(encoding="utf-8")
            for frag in self.FORBIDDEN:
                if frag in src:
                    offenders.append(f"{path.relative_to(self.APP_DIR)} 含 {frag!r}")
        self.assertEqual(offenders, [], "账号格式正则必须只用 utils.verification 一处: " + "; ".join(offenders))

    def test_email_is_case_insensitive_to_match_mysql_collation(self):
        """库侧排序规则是 `utf8mb4_general_ci`/`unicode_ci`（大小写不敏感），
        校验侧若敏感就会出现「User@X.com 注册被拒、user@x.com 却是同一个账号」。"""
        self.assertTrue(is_email("User@Example.COM"))
        self.assertTrue(is_email_or_phone("user@example.com"))
        self.assertFalse(is_email_or_phone("1234567890"))
        self.assertFalse(is_email_or_phone("abc"))


if __name__ == "__main__":
    unittest.main()
