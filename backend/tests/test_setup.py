"""安装向导的契约（`/setup/status`、`/setup/init`）。

这一页的失败代价很高：**它是空库唯一的入口**。错在两个方向都很难补救——

- 明明没有管理员却返回 `needs_setup=False` → 用户永远看不到向导，只能进数据库手动建号；
- 已经有管理员还能再建一个 → 任何人访问 `/setup` 就能给自己开超管。

所以这里把「一次性」「口令哈希」「角色绑定失败不阻断」三件事钉住。
"""

import unittest
from unittest import mock

from app.api.setup import SetupInitRequest, setup_init, setup_status
from app.core.exceptions import AppException
from app.core.security import verify_password
from app.models.user import User
from app.services import setup_service


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class _StubSession:
    """只实现 setup 用到的三个动作：count 查询、add、flush/commit。"""

    def __init__(self, admin_count: int, role=None, bind_raises: bool = False):
        self.admin_count = admin_count
        self.role = role
        self.bind_raises = bind_raises
        self.added: list = []
        self.committed = 0
        self._flushed: list = []

    async def execute(self, stmt, *args, **kwargs):  # noqa: ARG002 - 只按调用次序作答
        text = str(stmt)
        if "count" in text.lower():
            return _Result(self.admin_count)
        return _Result(self.role)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self._flushed.extend(self.added)

    async def commit(self):
        self.committed += 1


class _Request:
    class _Client:
        host = "127.0.0.1"

    client = _Client()


def _payload(**overrides) -> SetupInitRequest:
    data = {"account": "admin@example.com", "password": "Str0ng-Pass-123", "nickname": "站长"}
    data.update(overrides)
    return SetupInitRequest(**data)


class AdminCountTests(unittest.IsolatedAsyncioTestCase):
    async def test_counts_only_live_admins(self):
        """软删除的管理员不能算数，否则注销后再也无法初始化一个干净环境。"""
        db = _StubSession(admin_count=0)
        self.assertEqual(await setup_service.count_live_admins(db), 0)

    async def test_null_count_is_zero(self):
        """空表时 count 可能回 None，不能变成 TypeError。"""
        db = _StubSession(admin_count=None)
        self.assertEqual(await setup_service.count_live_admins(db), 0)


class SetupStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_db_needs_setup(self):
        data = await setup_status(db=_StubSession(admin_count=0))
        self.assertTrue(data["data"].needs_setup)

    async def test_existing_admin_does_not_need_setup(self):
        data = await setup_status(db=_StubSession(admin_count=1))
        self.assertFalse(data["data"].needs_setup)


class SetupInitTests(unittest.IsolatedAsyncioTestCase):
    async def test_creates_admin_with_hashed_password(self):
        db = _StubSession(admin_count=0)
        # RBAC 表未就绪（role=None）时也应建号成功，只是不绑角色
        with mock.patch("app.services.setup_service._bind_super_admin", new=mock.AsyncMock(return_value=False)):
            res = await setup_init(_payload(), _Request(), db=db)

        self.assertTrue(res["data"].ok)
        users = [o for o in db.added if isinstance(o, User)]
        self.assertEqual(len(users), 1)
        user = users[0]
        self.assertEqual(user.role, "admin")
        self.assertEqual(user.account, "admin@example.com")
        # 口令必须是哈希，且能通过校验（原样存明文是这里最容易犯的错）
        self.assertNotEqual(user.password_hash, "Str0ng-Pass-123")
        self.assertTrue(verify_password("Str0ng-Pass-123", user.password_hash))
        # free_started_at 必须显式写：该列无 server_default，NULL 会让免费时长按 created_at 兜底
        self.assertIsNotNone(user.free_started_at)
        self.assertEqual(db.committed, 1)

    async def test_refuses_when_admin_exists(self):
        db = _StubSession(admin_count=1)
        with self.assertRaises(AppException) as ctx:
            await setup_init(_payload(), _Request(), db=db)
        self.assertEqual(ctx.exception.code, 409)
        self.assertEqual(db.added, [], "已初始化时不得写入任何账号")
        self.assertEqual(db.committed, 0)

    async def test_refuses_password_equal_to_account(self):
        db = _StubSession(admin_count=0)
        with self.assertRaises(AppException) as ctx:
            await setup_init(
                _payload(account="admin@example.com", password="admin@example.com"),
                _Request(),
                db=db,
            )
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(db.added, [])

    async def test_role_binding_failure_does_not_block_signup(self):
        """RBAC 绑定失败只降级（提示去后台手动分配），不能让空库环境彻底进不去。"""
        db = _StubSession(admin_count=0)
        with mock.patch("app.services.setup_service._bind_super_admin", new=mock.AsyncMock(return_value=False)):
            res = await setup_init(_payload(), _Request(), db=db)
        self.assertTrue(res["data"].ok)
        self.assertIn("角色管理", res["message"])

    async def test_blank_account_is_rejected(self):
        db = _StubSession(admin_count=0)
        with self.assertRaises(AppException) as ctx:
            await setup_init(_payload(account="   "), _Request(), db=db)
        self.assertEqual(ctx.exception.code, 400)

    async def test_nickname_blank_becomes_none(self):
        db = _StubSession(admin_count=0)
        with mock.patch("app.services.setup_service._bind_super_admin", new=mock.AsyncMock(return_value=True)):
            await setup_init(_payload(nickname="  "), _Request(), db=db)
        user = next(o for o in db.added if isinstance(o, User))
        self.assertIsNone(user.nickname)


if __name__ == "__main__":
    unittest.main()
