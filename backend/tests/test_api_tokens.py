"""个人访问令牌（PAT）服务契约测试（2026-11-20，开放 API 第一块）。

钉住四条**破了不报错、只会悄悄失去意义**的契约：

1. **明文只出现一次**：签发时明文只在响应里返回一次，库里只留 SHA-256 摘要
   与展示前缀 —— 摘要推不出明文，这是「丢了只能吊销重建」的前提。
2. **认证 fail-closed**：查无 / 已吊销 / 已过期 / 缺 content:read 作用域一律拒绝，
   不静默降级成游客（客户端必须知道凭据为什么不被接受）。
3. **吊销幂等且可追溯**：首次吊销置时间戳并留审计，重复吊销不重复留痕。
4. **最近使用时刻回写节流**：认证热路径不逐请求写库，同一令牌短期内重复认证
   不触发第二次回写。

认证路径走的是「字面查询 + 绑定值字典」形态，MemorySession 不覆盖该形态，
因此 authenticate 系列用最小假会话按调用顺序回填结果（与 test_login_logs 同款）。
"""

from __future__ import annotations

import hashlib
import time
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from app.core.exceptions import AppException
from app.models.admin_audit_log import AdminAuditLog
from app.models.api_token import PAT_PREFIX, PAT_SCOPE_CONTENT_READ, PersonalAccessToken
from app.models.user import User
from app.services import api_token_service as ats
from app.utils.time import utcnow
from tests.fake_db import MemorySession

ADMIN = User(id=9001, account="admin@test")


# ---------------------------------------------------------------------------
# authenticate 用的最小假会话（按调用顺序回填，宽松签名）
# ---------------------------------------------------------------------------


def _auth_row(**kw):
    """按认证查询取回的列构造一行（字典形态，可下标取值，与服务层消费方式一致）。"""
    base = {
        "id": 7,
        "user_id": 9001,
        "name": "渲染站渲染器",
        "token_prefix": PAT_PREFIX + "aa11",
        "scopes": [PAT_SCOPE_CONTENT_READ],
        "expires_at": None,
        "revoked_at": None,
    }
    base.update(kw)
    return base


def _auth_result(row):
    """构造认证查询的返回值：与服务层一致的 mappings().first() 消费形态。"""
    return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))


def _auth_db(row):
    """认证入口的最小假会话：按调用顺序回填查询结果，并记录每次绑定值。

    返回的 calls 列表按序收集每条查询的绑定值字典（空列表 = 完全没查库）。
    """
    calls: list = []

    async def _call(*args, **kwargs):
        calls.append(args[1] if len(args) > 1 else kwargs)
        return _auth_result(row)

    return SimpleNamespace(execute=_call, calls=calls)


class _TouchSession:
    """最近使用时刻回写用的假会话：宽松签名 + 提交计数，用于断言回写次数。"""

    def __init__(self) -> None:
        self.commits = 0

    async def execute(self, *args, **kwargs):
        return _auth_result(None)

    async def commit(self) -> None:
        self.commits += 1


def _session_factory(session):
    """模拟 async_session 的异步上下文工厂（test_login_logs 同款）。"""

    class _Ctx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *args):
            return False

    return lambda: _Ctx()


# ---------------------------------------------------------------------------
# validate_scopes：作用域白名单
# ---------------------------------------------------------------------------


class ValidateScopesTests(unittest.TestCase):
    """作用域校验：白名单子集制，未知即拒绝。"""

    def test_empty_defaults_to_content_read(self):
        self.assertEqual(ats.validate_scopes(None), [PAT_SCOPE_CONTENT_READ])
        self.assertEqual(ats.validate_scopes([]), [PAT_SCOPE_CONTENT_READ])

    def test_unknown_scope_rejected_and_named(self):
        """未知作用域直接拒绝（不静默丢弃），报错信息要点名是哪一项。"""
        with self.assertRaises(AppException) as ctx:
            ats.validate_scopes([PAT_SCOPE_CONTENT_READ, "bogus:write"])
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("bogus:write", ctx.exception.message)

    def test_duplicates_deduplicated(self):
        self.assertEqual(
            ats.validate_scopes([PAT_SCOPE_CONTENT_READ, PAT_SCOPE_CONTENT_READ]),
            [PAT_SCOPE_CONTENT_READ],
        )


# ---------------------------------------------------------------------------
# create_token：签发
# ---------------------------------------------------------------------------


class CreateTokenTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_returns_plaintext_once_and_stores_digest_only(self):
        """明文只在响应里出现一次；库里只留 64 位十六进制摘要与展示前缀。"""
        db = MemorySession()
        out = await ats.create_token(db, ADMIN, "渲染站渲染器", None, None)
        plain = out["token"]
        self.assertTrue(plain.startswith(PAT_PREFIX))
        self.assertEqual(out["prefix"], plain[:12])
        # 一次性返回：响应里明文恰好出现一次
        self.assertEqual(list(out.values()).count(plain), 1)
        rows = db.all_rows(PersonalAccessToken)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        # token 相关列只有摘要与展示前缀，没有明文列
        self.assertEqual({k for k in row if "token" in k}, {"token_hash", "token_prefix"})
        digest = row["token_hash"]
        self.assertEqual(len(digest), 64)
        self.assertTrue(all(ch in "0123456789abcdef" for ch in digest))
        self.assertEqual(digest, hashlib.sha256(plain.encode("utf-8")).hexdigest())
        self.assertEqual(row["token_prefix"], plain[:12])
        # 明文全文绝不落库：任何列值都不等于明文
        self.assertNotIn(plain, list(row.values()))
        # 签发必须留审计
        audit_rows = db.all_rows(AdminAuditLog)
        self.assertEqual(len(audit_rows), 1)
        self.assertEqual(audit_rows[0]["action"], "api_token_create")

    async def test_create_defaults_scope_to_content_read(self):
        db = MemorySession()
        out = await ats.create_token(db, ADMIN, "默认作用域", None, None)
        self.assertEqual(out["scopes"], [PAT_SCOPE_CONTENT_READ])
        self.assertEqual(db.all_rows(PersonalAccessToken)[0]["scopes"], [PAT_SCOPE_CONTENT_READ])

    async def test_create_with_expires_days_sets_expiry(self):
        db = MemorySession()
        out = await ats.create_token(db, ADMIN, "带期限", [PAT_SCOPE_CONTENT_READ], 90)
        self.assertIsNotNone(out["expires_at"])
        row = db.all_rows(PersonalAccessToken)[0]
        self.assertIsNotNone(row["expires_at"])
        expected = utcnow().replace(tzinfo=None) + timedelta(days=90)
        self.assertLess(abs((row["expires_at"] - expected).total_seconds()), 10)

    async def test_create_without_expiry_never_expires(self):
        db = MemorySession()
        out = await ats.create_token(db, ADMIN, "永久", None, None)
        self.assertIsNone(out["expires_at"])
        self.assertIsNone(db.all_rows(PersonalAccessToken)[0]["expires_at"])


# ---------------------------------------------------------------------------
# revoke_token：吊销（幂等）
# ---------------------------------------------------------------------------


class RevokeTokenTests(unittest.IsolatedAsyncioTestCase):
    async def test_revoke_first_true_then_idempotent_with_single_audit(self):
        db = MemorySession()
        db.seed(
            PersonalAccessToken(
                id=55,
                user_id=9001,
                name="待吊销",
                token_prefix=PAT_PREFIX + "xx22",
                token_hash="ab" * 32,
                scopes=[PAT_SCOPE_CONTENT_READ],
            )
        )
        self.assertTrue(await ats.revoke_token(db, ADMIN, 55))
        self.assertIsNotNone(db.all_rows(PersonalAccessToken)[0]["revoked_at"])
        audit_rows = db.all_rows(AdminAuditLog)
        self.assertEqual([r["action"] for r in audit_rows], ["api_token_revoke"])
        # 第二次吊销幂等：返回 False，不重复写审计
        self.assertFalse(await ats.revoke_token(db, ADMIN, 55))
        self.assertEqual(len(db.all_rows(AdminAuditLog)), len(audit_rows))

    async def test_revoke_missing_token_raises_404(self):
        db = MemorySession()
        with self.assertRaises(AppException) as ctx:
            await ats.revoke_token(db, ADMIN, 12345)
        self.assertEqual(ctx.exception.code, 404)


# ---------------------------------------------------------------------------
# authenticate：认证 fail-closed + 回写节流
# ---------------------------------------------------------------------------


class AuthenticateTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_pat_prefix_returns_none_without_lookup(self):
        """非 oc_pat_ 前缀直接交回调用方（交给既有登录态通道），完全不碰库。"""
        db = _auth_db(_auth_row())
        self.assertIsNone(await ats.authenticate("some.jwt.string", db))
        self.assertEqual(db.calls, [])

    async def test_unknown_digest_raises_401(self):
        db = _auth_db(None)
        with self.assertRaises(AppException) as ctx:
            await ats.authenticate(PAT_PREFIX + "0" * 40, db)
        self.assertEqual(ctx.exception.code, 401)
        self.assertIn("无效", ctx.exception.message)

    async def test_fail_closed_for_revoked_expired_or_missing_scope(self):
        """已吊销 / 已过期 / 缺 content:read 三档各拒绝一次，报错点名原因。"""
        plain = PAT_PREFIX + "c3d4e5f6" * 5
        naive_now = utcnow().replace(tzinfo=None)
        cases = [
            ("已吊销", _auth_row(revoked_at=naive_now - timedelta(days=1)), "吊销"),
            ("已过期", _auth_row(expires_at=naive_now - timedelta(hours=1)), "过期"),
            ("缺作用域", _auth_row(scopes=["report:write"]), PAT_SCOPE_CONTENT_READ),
        ]
        for label, row, needle in cases:
            with self.subTest(label):
                db = _auth_db(row)
                with self.assertRaises(AppException) as ctx:
                    await ats.authenticate(plain, db)
                self.assertEqual(ctx.exception.code, 401)
                self.assertIn(needle, ctx.exception.message)

    async def test_valid_token_returns_principal_and_touches_once(self):
        """有效令牌返回属主与作用域；首次认证回写最近使用时刻（提交一次）。"""
        plain = PAT_PREFIX + "a1b2c3d4" * 5
        db = _auth_db(_auth_row(id=31, user_id=9002, name="报表机"))
        touch = _TouchSession()
        with (
            mock.patch.object(ats, "_last_used_written", {}),
            mock.patch.object(ats, "async_session", _session_factory(touch)),
        ):
            principal = await ats.authenticate(plain, db)
        # 查询绑定值里的摘要必须与明文一一对应（认证唯一依据）
        self.assertEqual(db.calls[0]["digest"], hashlib.sha256(plain.encode("utf-8")).hexdigest())
        self.assertEqual(principal["id"], 31)
        self.assertEqual(principal["user_id"], 9002)
        self.assertEqual(principal["name"], "报表机")
        self.assertEqual(principal["scopes"], [PAT_SCOPE_CONTENT_READ])
        self.assertEqual(touch.commits, 1)

    async def test_throttled_token_skips_touch_write(self):
        """同一令牌短期内重复认证：不再回写，认证结果不受影响。"""
        plain = PAT_PREFIX + "f0e1d2c3" * 5
        db = _auth_db(_auth_row(id=31))
        touch = _TouchSession()
        with (
            mock.patch.object(ats, "_last_used_written", {31: time.monotonic()}),
            mock.patch.object(ats, "async_session", _session_factory(touch)),
        ):
            principal = await ats.authenticate(plain, db)
        self.assertEqual(touch.commits, 0)
        self.assertEqual(principal["id"], 31)


if __name__ == "__main__":
    unittest.main()
