"""数据权限（行级可见范围）的守卫测试。

数据权限做错的典型后果是**静默越权**：列表看着正常，只是「多了几条不该看见的行」，
没有任何报错，也没有任何页面会提示。所以这里把三件事钉死：

1. 范围解析：多角色取最宽、super_admin 恒为全部、非法值按最窄（fail-closed）；
2. 条件翻译：`all` 不加条件；`dept` 按部门；没有部门的表 / 自己没有部门 → 收敛到 `self`；
3. 角色接口：非法范围直接 400，不落库。
"""

import unittest
from unittest.mock import AsyncMock

from app.core.data_scope import (
    SCOPE_ALL,
    SCOPE_DEPT,
    SCOPE_SELF,
    DataScope,
    apply_scope,
    resolve_data_scope,
)
from app.core.exceptions import AppException
from app.models.rbac import Role
from app.models.user import User


class _Rows:
    """模拟 SQLAlchemy Result 的 `.all()`"""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


def _db(rows):
    db = AsyncMock()
    db.execute.return_value = _Rows(rows)
    return db


def _user(user_id: int = 1001, department_id: int | None = 7) -> User:
    user = User(id=user_id, account=f"u{user_id}", role="admin")
    user.department_id = department_id
    return user


class ResolveDataScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_super_admin_is_always_all(self):
        """super_admin 不受角色范围影响：它是「逃逸通道」，写窄了等于把超管锁住"""
        scope = await resolve_data_scope(_db([("super_admin", "self")]), _user())
        self.assertEqual(scope.mode, SCOPE_ALL)

    async def test_multiple_roles_take_the_widest(self):
        """多给一个角色不该让可见范围变窄（否则「加权限」会变成「减数据」）"""
        scope = await resolve_data_scope(_db([("viewer", "self"), ("ops", "dept")]), _user())
        self.assertEqual(scope.mode, SCOPE_DEPT)

    async def test_single_dept_role(self):
        scope = await resolve_data_scope(_db([("ops", "dept")]), _user(department_id=7))
        self.assertEqual(scope.mode, SCOPE_DEPT)
        self.assertEqual(scope.department_id, 7)

    async def test_no_role_falls_back_to_self(self):
        """没有任何 RBAC 角色：数据面按最窄处理（业务角色 admin 不等于能看全部数据）"""
        scope = await resolve_data_scope(_db([]), _user())
        self.assertEqual(scope.mode, SCOPE_SELF)

    async def test_invalid_value_is_treated_as_self(self):
        """库里被写进非法值（手工改库 / 老数据）时按最窄算，不能当成「全部数据」"""
        scope = await resolve_data_scope(_db([("ops", "everyone")]), _user())
        self.assertEqual(scope.mode, SCOPE_SELF)


class ApplyScopeTests(unittest.TestCase):
    def test_all_adds_nothing(self):
        conditions: list = []
        apply_scope(conditions, DataScope(SCOPE_ALL), owner_col=User.id, dept_col=User.department_id)
        self.assertEqual(conditions, [])

    def test_none_scope_adds_nothing(self):
        """未接入数据权限的调用方传 None = 旧行为（全量）"""
        conditions: list = []
        apply_scope(conditions, None, owner_col=User.id, dept_col=User.department_id)
        self.assertEqual(conditions, [])

    def test_dept_filters_by_department(self):
        conditions: list = []
        apply_scope(
            conditions,
            DataScope(SCOPE_DEPT, user_id=1001, department_id=7),
            owner_col=User.id,
            dept_col=User.department_id,
        )
        self.assertEqual(len(conditions), 1)
        self.assertIn("department_id", str(conditions[0]))

    def test_dept_without_department_column_falls_back_to_self(self):
        """审计日志没有部门列：dept 范围收敛到「仅本人」，宁可少看不能多看"""
        conditions: list = []
        apply_scope(
            conditions,
            DataScope(SCOPE_DEPT, user_id=1001, department_id=7),
            owner_col=User.id,
        )
        self.assertEqual(len(conditions), 1)
        self.assertIn("id", str(conditions[0]))

    def test_dept_without_own_department_falls_back_to_self(self):
        conditions: list = []
        apply_scope(
            conditions,
            DataScope(SCOPE_DEPT, user_id=1001, department_id=None),
            owner_col=User.id,
            dept_col=User.department_id,
        )
        self.assertEqual(len(conditions), 1)
        self.assertIn("id", str(conditions[0]))


class RoleScopeValidationTests(unittest.TestCase):
    def test_role_dict_exposes_scope(self):
        from app.services.rbac_service import role_to_dict

        role = Role(code="ops", name="运营", is_system=False)
        role.data_scope = SCOPE_DEPT
        self.assertEqual(role_to_dict(role)["data_scope"], SCOPE_DEPT)

    def test_invalid_scope_is_rejected(self):
        """非法值直接 400：落库后会被当成「最窄」处理，管理员会以为设成功了"""
        from app.services.rbac_service import _validate_scope

        with self.assertRaises(AppException) as ctx:
            _validate_scope("everyone")
        self.assertEqual(ctx.exception.code, 400)

    def test_valid_scopes_pass(self):
        from app.services.rbac_service import _validate_scope

        for value in (SCOPE_ALL, SCOPE_DEPT, SCOPE_SELF):
            _validate_scope(value)


if __name__ == "__main__":
    unittest.main()
