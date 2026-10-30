"""数据权限：行级可见范围（`all` / `dept` / `self`）。

**与按钮级 RBAC 的区别**：权限码回答「能不能点这个按钮」，数据范围回答「点进去能看见谁的数据」。
只做到按钮级的后台有个典型漏洞 —— 一个「用户查看」权限就能翻遍全站用户，
多人协作时等于没有边界。

**范围来自角色**：`roles.data_scope`，一个用户有多个角色时取**最宽**的那个
（多给一个角色不该让可见范围变窄，这是运维直觉；反过来会让「加权限」变成「减数据」）。
`super_admin` 恒为 `all`。

**为什么用 dataclass 传而不是在 service 里查角色**：
service 不该知道 RBAC 的存在（那是鉴权层的事）。路由层解析成 `DataScope` 传下去，
service 只负责把它翻译成 WHERE 条件。

**降级方向**：取值非法、角色缺失、dept 但没有部门 —— 一律按**最窄**（`self`）处理。
数据面宁可少给，不能多给。
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rbac import Role, UserRole
from app.models.user import User

SCOPE_ALL = "all"
SCOPE_DEPT = "dept"
SCOPE_SELF = "self"

SCOPE_LABELS: dict[str, str] = {
    SCOPE_ALL: "全部数据",
    SCOPE_DEPT: "本部门数据",
    SCOPE_SELF: "仅本人数据",
}

# 宽度排序：多角色取最宽
_WIDTH = {SCOPE_SELF: 0, SCOPE_DEPT: 1, SCOPE_ALL: 2}


@dataclass(frozen=True)
class DataScope:
    """一次请求解析出的可见范围"""

    mode: str = SCOPE_ALL
    user_id: int = 0
    department_id: int | None = None

    @property
    def is_all(self) -> bool:
        return self.mode == SCOPE_ALL


# 全量范围：不附加任何条件（读接口默认值，也让未接入数据权限的调用方保持旧行为）
UNRESTRICTED = DataScope(SCOPE_ALL)


async def resolve_data_scope(db: AsyncSession, user: User) -> DataScope:
    """解析该管理员的数据范围（一次查询，取最宽的角色范围）"""
    rows = (
        await db.execute(
            select(Role.code, Role.data_scope)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user.id)
        )
    ).all()

    department_id = getattr(user, "department_id", None)

    if not rows:
        # 没有任何 RBAC 角色：业务角色可能是 admin（走旧通道），但数据面按最窄处理
        return DataScope(SCOPE_SELF, user.id, department_id)

    if any(code == "super_admin" for code, _ in rows):
        return DataScope(SCOPE_ALL, user.id, department_id)

    widest = SCOPE_SELF
    for _code, scope in rows:
        # 非法取值按最窄算（fail-closed）：拼错的 scope 不该被当成「全部数据」
        normalized = scope if scope in _WIDTH else SCOPE_SELF
        if _WIDTH[normalized] > _WIDTH[widest]:
            widest = normalized

    return DataScope(widest, user.id, department_id)


def apply_scope(conditions: list, scope: DataScope | None, *, owner_col, dept_col=None) -> list:
    """把数据范围翻译成 WHERE 条件并追加到 `conditions`（原地修改后返回，便于链式调用）。

    :param owner_col: 「这条数据属于谁」的列（用户 id）
    :param dept_col: 「这条数据属于哪个部门」的列；没有部门概念的表传 None，
        此时 `dept` 范围会退化为 `self` —— 宁可少看，不能多看。
    """
    if scope is None or scope.is_all:
        return conditions

    if scope.mode == SCOPE_DEPT and dept_col is not None and scope.department_id is not None:
        conditions.append(dept_col == scope.department_id)
        return conditions

    # dept 但自己没有部门 / dept 但该表无部门列 / self：都收敛到「仅本人」
    conditions.append(owner_col == scope.user_id)
    return conditions
