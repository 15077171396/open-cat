"""TOTP 二次验证的契约测试。

分两层：
1. **算法层**（`core/totp.py`）—— 直接对齐 **RFC 6238 附录 B 的官方测试向量**。
   自己实现算法最怕「看起来能用但偏移一位」，官方向量是唯一可信的判据。
2. **业务层**（`services/totp_service.py`）—— 绑定分两步、密钥加密落库、恢复码一次性、
   关闭需密码。这些破了不会报错，只会让账号被锁死或被绕过。

⚠️ 时钟：所有断言都传 `at=` 固定时间戳，**不依赖真实时钟**（否则在 30 秒边界上随机失败）。
"""

import time
import unittest

from app.core import totp
from app.core.crypto import is_encrypted
from app.core.exceptions import AppException
from app.models.user import User
from app.services import totp_service as svc

# RFC 6238 附录 B：密钥为 ASCII "12345678901234567890" 的 Base32 编码
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
# (Unix 秒, 8 位验证码)
RFC_VECTORS = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]


class RfcVectorTests(unittest.TestCase):
    def test_matches_rfc6238_appendix_b(self):
        """官方向量逐条对齐（8 位模式）"""
        for timestamp, expected in RFC_VECTORS:
            with self.subTest(ts=timestamp):
                self.assertEqual(totp.totp_code(RFC_SECRET, at=timestamp, digits=8), expected)

    def test_six_digit_code_is_suffix_of_eight_digit(self):
        """6 位模式是 8 位模式的低 6 位（同一次截断取模），用来交叉验证位数处理"""
        for timestamp, expected in RFC_VECTORS:
            self.assertEqual(totp.totp_code(RFC_SECRET, at=timestamp)[-6:], expected[-6:])


class VerifyTests(unittest.TestCase):
    def test_accepts_current_step(self):
        code = totp.totp_code(RFC_SECRET, at=1_700_000_000)
        self.assertTrue(totp.verify_totp(RFC_SECRET, code, at=1_700_000_000))

    def test_accepts_one_step_drift(self):
        """±30 秒容忍：手机与服务器时间几乎不可能完全同步，窗口取 0 会让部分人永远登录失败"""
        code = totp.totp_code(RFC_SECRET, at=1_700_000_000)
        self.assertTrue(totp.verify_totp(RFC_SECRET, code, at=1_700_000_000 + 30))
        self.assertTrue(totp.verify_totp(RFC_SECRET, code, at=1_700_000_000 - 30))

    def test_rejects_two_step_drift(self):
        """窗口不能太大：每多一步就多一个可爆破的码"""
        code = totp.totp_code(RFC_SECRET, at=1_700_000_000)
        self.assertFalse(totp.verify_totp(RFC_SECRET, code, at=1_700_000_000 + 90))

    def test_rejects_malformed_input_without_raising(self):
        for value in (None, "", "abcdef", "12345", "1234567", "   ", "12 34 5"):
            with self.subTest(value=value):
                self.assertFalse(totp.verify_totp(RFC_SECRET, value, at=1_700_000_000))

    def test_broken_secret_returns_false(self):
        """密钥损坏等价于校验失败，不能抛异常（否则用户看到 500 而非「验证码不对」）"""
        self.assertFalse(totp.verify_totp("!!!not-base32!!!", "123456", at=1_700_000_000))

    def test_uses_constant_time_compare(self):
        """必须用 hmac.compare_digest：`==` 会泄漏前缀匹配长度"""
        import pathlib

        source = pathlib.Path(totp.__file__).read_text(encoding="utf-8")
        self.assertIn("hmac.compare_digest", source)


class SecretAndUriTests(unittest.TestCase):
    def test_secret_is_base32_and_decodable(self):
        secret = totp.generate_secret()
        self.assertEqual(len(secret), 32)  # 20 字节 → 32 个 Base32 字符（无 padding）
        self.assertEqual(len(totp._b32decode(secret)), 20)
        self.assertNotEqual(secret, totp.generate_secret(), "密钥必须随机")

    def test_decode_tolerates_user_typing(self):
        """用户会手工抄密钥：小写、空格、连字符都要能解码"""
        base = totp.generate_secret()
        spaced = " ".join([base[:8].lower(), base[8:16], base[16:]])
        self.assertEqual(totp._b32decode(spaced), totp._b32decode(base))

    def test_provisioning_uri_shape(self):
        uri = totp.provisioning_uri("ABCDEF234567", "admin@example.com")
        self.assertTrue(uri.startswith("otpauth://totp/"))
        self.assertIn("secret=ABCDEF234567", uri)
        self.assertIn("issuer=", uri)
        self.assertIn("digits=6", uri)
        self.assertIn("period=30", uri)


class RecoveryCodeTests(unittest.TestCase):
    def test_codes_are_unique_and_formatted(self):
        codes = totp.generate_recovery_codes()
        self.assertEqual(len(codes), 10)
        self.assertEqual(len(set(codes)), 10)
        for code in codes:
            self.assertRegex(code, r"^[A-Z2-9]{5}-[A-Z2-9]{5}$")

    def test_normalize_strips_dashes_and_case(self):
        self.assertEqual(totp.normalize_recovery_code(" abcde-fghjk "), "ABCDEFGHJK")

    def test_hash_is_salted_by_user(self):
        """同一串码在不同账号下哈希不同（防撞码跨账号利用）"""
        self.assertNotEqual(
            totp.hash_recovery_code("ABCDE-FGHJK", 1),
            totp.hash_recovery_code("ABCDE-FGHJK", 2),
        )


class _StubSession:
    """最小会话桩：服务层只用到 commit()"""

    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


def _make_user(user_id: int = 1001, password_hash: str = "x") -> User:
    user = User(id=user_id, account=f"u{user_id}@t.local", password_hash=password_hash)
    user.totp_secret = None
    user.totp_enabled = False
    user.totp_recovery_hashes = None
    return user


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_stores_encrypted_secret_and_keeps_disabled(self):
        """绑定第一步：密钥必须**加密**落库，且**不启用**（否则密钥没存对就永久锁死）"""
        db, user = _StubSession(), _make_user()
        data = await svc.setup_totp(db, user)

        self.assertTrue(data["secret"])
        self.assertTrue(data["uri"].startswith("otpauth://totp/"))
        self.assertEqual(len(data["recovery_codes"]), 10)
        self.assertTrue(is_encrypted(user.totp_secret), "密钥必须加密落库")
        self.assertNotIn(data["secret"], user.totp_secret)
        self.assertFalse(user.totp_enabled)
        self.assertEqual(len(user.totp_recovery_hashes), 10)
        # 恢复码只存哈希：库里不能出现明文
        for code in data["recovery_codes"]:
            self.assertNotIn(code.replace("-", ""), "".join(user.totp_recovery_hashes))
        self.assertEqual(db.commits, 1)

    async def test_setup_rejected_when_already_enabled(self):
        db, user = _StubSession(), _make_user()
        user.totp_enabled = True
        with self.assertRaises(AppException) as ctx:
            await svc.setup_totp(db, user)
        self.assertEqual(ctx.exception.code, 400)

    async def test_enable_requires_correct_code(self):
        db, user = _StubSession(), _make_user()
        data = await svc.setup_totp(db, user)

        with self.assertRaises(AppException):
            await svc.enable_totp(db, user, "000000")

        code = totp.totp_code(data["secret"])
        await svc.enable_totp(db, user, code)
        self.assertTrue(user.totp_enabled)

    async def test_enable_reports_rotated_key_instead_of_silently_passing(self):
        """SECRET_KEY 轮换后旧密文解不开：必须明确要求重绑，绝不能放行"""
        db, user = _StubSession(), _make_user()
        user.totp_secret = "enc:v2:not-a-real-ciphertext"
        with self.assertRaises(AppException) as ctx:
            await svc.enable_totp(db, user, "123456")
        self.assertIn("重新绑定", ctx.exception.message)

    async def test_disable_requires_password(self):
        from app.core.security import hash_password_async

        db = _StubSession()
        user = _make_user(password_hash=await hash_password_async("right-password"))
        user.totp_enabled = True
        user.totp_secret = "enc:v2:x"

        with self.assertRaises(AppException):
            await svc.disable_totp(db, user, "wrong-password")
        self.assertTrue(user.totp_enabled, "密码错误时不得关闭")

        await svc.disable_totp(db, user, "right-password")
        self.assertFalse(user.totp_enabled)
        self.assertIsNone(user.totp_secret)
        self.assertIsNone(user.totp_recovery_hashes)

    async def test_disable_with_broken_hash_returns_400_not_500(self):
        """历史脏数据（非 bcrypt 的 hash）不能让关闭二次验证变成 500"""
        db = _StubSession()
        user = _make_user(password_hash="not-a-bcrypt-hash")
        user.totp_enabled = True
        with self.assertRaises(AppException) as ctx:
            await svc.disable_totp(db, user, "whatever")
        self.assertEqual(ctx.exception.code, 400)

    async def test_recovery_code_is_one_time(self):
        """恢复码用掉即失效 —— 否则一串码可以反复登录，等于永久后门"""
        db, user = _StubSession(), _make_user()
        data = await svc.setup_totp(db, user)
        await svc.enable_totp(db, user, totp.totp_code(data["secret"]))

        first = data["recovery_codes"][0]
        self.assertTrue(await svc.verify_login_totp(db, user, first))
        self.assertFalse(await svc.verify_login_totp(db, user, first), "同一恢复码不能二次使用")
        self.assertEqual(len(user.totp_recovery_hashes), 9)

    async def test_login_totp_accepts_current_code(self):
        db, user = _StubSession(), _make_user()
        data = await svc.setup_totp(db, user)
        await svc.enable_totp(db, user, totp.totp_code(data["secret"]))
        self.assertTrue(await svc.verify_login_totp(db, user, totp.totp_code(data["secret"])))

    async def test_login_totp_rejects_empty(self):
        db, user = _StubSession(), _make_user()
        self.assertFalse(await svc.verify_login_totp(db, user, None))
        self.assertFalse(await svc.verify_login_totp(db, user, ""))

    async def test_status_never_leaks_secret(self):
        user = _make_user()
        user.totp_secret = "enc:v2:xxx"
        user.totp_recovery_hashes = ["a", "b"]
        status = svc.totp_status(user)
        self.assertNotIn("secret", status)
        self.assertEqual(status["pending"], True)
        self.assertEqual(status["recovery_remaining"], 2)


class GuardTests(unittest.TestCase):
    def test_totp_failure_has_separate_counter(self):
        """验证码失败必须独立计数：共用密码计数器会被「密码成功即清零」抹掉，永远锁不住"""
        import pathlib

        import app.services.auth_service as auth_service

        source = pathlib.Path(auth_service.__file__).read_text(encoding="utf-8")
        self.assertIn("totp_fail:", source)
        self.assertIn("totp_lock:", source)

    def test_model_columns_exist(self):
        columns = {c.name for c in User.__table__.columns}
        for name in ("totp_secret", "totp_enabled", "totp_recovery_hashes"):
            self.assertIn(name, columns)

    def test_user_dict_does_not_expose_totp(self):
        """`to_dict` 是对外序列化入口，绝不能把密钥带出去"""
        user = _make_user()
        user.totp_secret = "enc:v2:xxx"
        dumped = user.to_dict()
        self.assertNotIn("totp_secret", dumped)
        self.assertNotIn("totp_recovery_hashes", dumped)

    def test_routes_registered(self):
        from app.main import app

        paths = app.openapi()["paths"]
        for path in (
            "/api/v1/auth/totp",
            "/api/v1/auth/totp/setup",
            "/api/v1/auth/totp/enable",
            "/api/v1/auth/totp/disable",
            "/api/v1/auth/totp/recovery",
            "/api/v1/auth/logout-all",
        ):
            self.assertIn(path, paths, f"缺少接口 {path}")

    def test_login_request_accepts_totp_code(self):
        from app.schemas.auth import LoginRequest

        req = LoginRequest(account="a@b.c", password="x", totp_code="123456")
        self.assertEqual(req.totp_code, "123456")


if __name__ == "__main__":
    unittest.main()
