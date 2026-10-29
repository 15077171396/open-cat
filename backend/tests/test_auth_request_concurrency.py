"""认证申请并发提交链路测试 (P1-8)。

``submit_auth_request`` 原为「先查 pending → 无则插入」。先查后插在并发下必然能
钻过去：两个请求同时查到「没有 pending」，于是都插入，结果是同一用户多条待审核
申请——管理端审核队列出现重复条目，管理员通过其中一条后其余仍挂着，且用户侧
``pending_auth_code`` 被后提交的值覆盖，状态自相矛盾。

修复靠数据库唯一约束兜底：新增 ``pending_key``（pending 时填 'pending'，终态置
NULL）+ (user_id, pending_key) 唯一约束。MySQL 唯一索引中 NULL 不参与冲突判定，
因此「同一用户最多一条 pending」与「历史终态记录条数不限」可以同时成立。

本文件把四条契约钉死：
1. 顺序提交两次 → 第二次被前置检查拦下；
2. 并发提交 N 次 → 只成功一条，其余 400，**不产生第二条 pending**；
3. 驳回/通过后 pending_key 置 NULL → 用户可以重新提交（否则永远锁死）；
4. 终态记录可无限累积（NULL 不参与冲突）——若有人把 pending_key 改成非空常量，
   这条会立刻变红。
"""

import asyncio
import unittest

from sqlalchemy import select

from app.core.exceptions import AppException
from app.models.auth_request import AuthRequest, PENDING_KEY
from app.models.user import User
from app.services import audit_service as aus
from tests.fake_db import MemorySession


def make_user(*, id: int = 1, role: str = "user") -> User:
    return User(
        id=id,
        account=f"u{id}@example.com",
        password_hash="x",
        role=role,
        free_used_seconds=0,
    )


def make_admin(*, id: int = 900) -> User:
    return make_user(id=id, role="admin")


async def _pending_rows(db: MemorySession, user_id: int) -> list[dict]:
    return [
        row for row in db.all_rows(AuthRequest)
        if row["user_id"] == user_id and row["status"] == "pending"
    ]


class TestSubmitAuthRequestSerial(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path_sets_pending_key(self):
        db = MemorySession()
        user = make_user()
        db.seed(user)

        result = await aus.submit_auth_request(db, user, "88888888")
        self.assertEqual(result["status"], "pending")
        self.assertEqual(user.pending_auth_code, "88888888")

        rows = await _pending_rows(db, user.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pending_key"], PENDING_KEY)

    async def test_second_submit_rejected_by_precheck(self):
        """顺序提交：前置查询即可拦下，无需等到唯一约束"""
        db = MemorySession()
        user = make_user()
        db.seed(user)
        await aus.submit_auth_request(db, user, "88888888")

        with self.assertRaises(AppException) as ctx:
            await aus.submit_auth_request(db, user, "99999999")
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("已有待审核", ctx.exception.message)
        self.assertEqual(len(await _pending_rows(db, user.id)), 1)

    async def test_vip_cannot_submit(self):
        db = MemorySession()
        user = make_user(role="vip")
        db.seed(user)
        with self.assertRaises(AppException) as ctx:
            await aus.submit_auth_request(db, user, "88888888")
        self.assertIn("无需重复认证", ctx.exception.message)
        self.assertEqual(db.all_rows(AuthRequest), [])


class TestSubmitAuthRequestConcurrency(unittest.IsolatedAsyncioTestCase):
    async def _race(self, contenders: int = 4):
        """contenders 个并发请求抢同一用户的 pending 名额"""
        root = MemorySession()
        user = make_user()
        root.seed(user)

        sessions = [root] + [root.new_session() for _ in range(contenders - 1)]

        async def _submit(session, code: str):
            try:
                return await aus.submit_auth_request(session, user, code)
            except AppException as exc:
                return exc

        results = await asyncio.gather(
            *(_submit(s, f"code-{i}") for i, s in enumerate(sessions))
        )
        ok = [r for r in results if isinstance(r, dict)]
        failed = [r for r in results if isinstance(r, AppException)]
        return user, ok, failed, root

    async def test_only_one_concurrent_submit_wins(self):
        user, ok, failed, db = await self._race(contenders=4)

        self.assertEqual(len(ok), 1, "并发提交必须只有一个成功")
        self.assertEqual(len(failed), 3)
        for exc in failed:
            self.assertEqual(exc.code, 400)
            self.assertIn("已有待审核", exc.message)

        # 唯一约束是最后一道防线：库里绝不能出现第二条 pending
        self.assertEqual(len(await _pending_rows(db, user.id)), 1)

    async def test_pending_code_is_not_clobbered_by_loser(self):
        """抢锁失败的请求不得改写用户待审核星球号"""
        user, ok, _failed, db = await self._race(contenders=3)
        self.assertEqual(len(ok), 1)
        row = (await _pending_rows(db, user.id))[0]
        self.assertEqual(user.pending_auth_code, row["star_code"])

    async def test_concurrent_sessions_do_not_share_pending_state(self):
        """并发会话共享同一库：验证仿真层确实能观测到对方的插入（否则本测试无意义）"""
        root = MemorySession()
        user = make_user()
        root.seed(user)
        other = root.new_session()
        await aus.submit_auth_request(other, user, "88888888")

        rows = (
            await root.execute(
                select(AuthRequest).where(
                    AuthRequest.user_id == user.id, AuthRequest.status == "pending"
                )
            )
        ).scalars().all()
        self.assertEqual(len(rows), 1)


class TestPendingKeyLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_reject_frees_the_pending_slot(self):
        """驳回后 pending_key 必须置 NULL，否则用户永远无法再次提交"""
        db = MemorySession()
        user = make_user()
        db.seed(user)
        admin = make_admin()
        await aus.submit_auth_request(db, user, "88888888")

        req = (await db.execute(
            select(AuthRequest).where(AuthRequest.user_id == user.id)
        )).scalars().first()
        await aus.reject_auth_request(db, admin, req.id, "星球号不匹配")
        self.assertIsNone(req.pending_key)

        # 重新提交必须成功，且库里仍只有一条 pending
        await aus.submit_auth_request(db, user, "77777777")
        self.assertEqual(len(await _pending_rows(db, user.id)), 1)
        # 历史终态记录保留，供统计与审计
        self.assertEqual(len(db.all_rows(AuthRequest)), 2)

    async def test_approve_frees_the_pending_slot(self):
        db = MemorySession()
        user = make_user()
        db.seed(user)
        admin = make_admin()
        await aus.submit_auth_request(db, user, "88888888")

        req = (await db.execute(
            select(AuthRequest).where(AuthRequest.user_id == user.id)
        )).scalars().first()
        await aus.approve_auth_request(db, admin, req.id, "1month")
        self.assertIsNone(req.pending_key)
        self.assertEqual(user.role, "vip")
        self.assertIsNone(user.pending_auth_code)

    async def test_terminal_rows_can_accumulate(self):
        """终态记录条数不限：这是 (user_id, pending_key) 唯一约束成立的前提。

        若有人把 pending_key 改成非空常量（如直接复用 status），同一用户的多条
        历史记录会立刻撞唯一约束，这条测试会变红。
        """
        db = MemorySession()
        user = make_user()
        db.seed(user)
        admin = make_admin()

        for i in range(3):
            await aus.submit_auth_request(db, user, f"code-{i}")
            req = (await db.execute(
                select(AuthRequest).where(
                    AuthRequest.user_id == user.id, AuthRequest.status == "pending"
                )
            )).scalars().first()
            await aus.reject_auth_request(db, admin, req.id, "测试驳回")

        await aus.submit_auth_request(db, user, "final-code")
        self.assertEqual(len(db.all_rows(AuthRequest)), 4)
        self.assertEqual(len(await _pending_rows(db, user.id)), 1)
