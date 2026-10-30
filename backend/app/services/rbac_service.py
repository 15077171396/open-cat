"""RBAC 服务：权限解析（带缓存）/ 角色 CRUD / 部门 CRUD / 用户角色分配

权限校验分两层（见 core/rbac.py 的路由规则表）：
1. require_admin（业务角色门槛）：users.role='admin' 才能进管理后台
2. require_permission（细粒度）：按路由规则表解析 (method, path) → 权限码，
   super_admin 角色直接放行，其余角色按聚合权限码 any-of 匹配

缓存：rbac:perms:{user_id} → {"roles": [...], "perms": [...]}，TTL 300s；
角色权限变更 → 删除拥有该角色用户的缓存；用户角色分配 → 删除该用户缓存。
"""

import json

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import SCOPE_ALL, SCOPE_LABELS
from app.models.rbac import Department, Role, Permission, RolePermission, UserRole
from app.models.user import User
from app.core.exceptions import AppException, NotFoundError
from app.redis import log_redis_degraded, redis_client
from app.services.admin_audit_service import diff_values, record_admin_action
from app.services.rbac_catalog import ALL_PERMISSION_CODES, permissions_grouped_by_module

SUPER_ADMIN_CODE = "super_admin"

# 权限缓存 TTL（秒）：角色/分配变更时主动失效，TTL 仅兜底
_PERM_CACHE_TTL = 300


# 权限解析（带缓存）

def _perm_cache_key(user_id: int) -> str:
    return f"rbac:perms:{user_id}"


async def _load_permissions_from_db(db: AsyncSession, user_id: int) -> dict:
    """从库中加载用户 RBAC 角色与聚合权限码（2 条查询）"""
    roles = (
        await db.execute(
            select(Role)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
            .order_by(Role.id.asc())
        )
    ).scalars().all()
    if not roles:
        return {"roles": [], "perms": []}
    perms = (
        await db.execute(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id.in_([r.id for r in roles]))
        )
    ).scalars().all()
    return {
        "roles": [{"id": r.id, "code": r.code, "name": r.name} for r in roles],
        "perms": sorted(set(perms)),
    }


async def get_user_rbac(db: AsyncSession, user_id: int) -> dict:
    """获取用户 RBAC 角色与权限码（Redis 缓存 300s，故障降级直查 DB）"""
    cache_key = _perm_cache_key(user_id)
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception as exc:
        log_redis_degraded("rbac_service.get_user_rbac", exc)

    data = await _load_permissions_from_db(db, user_id)
    try:
        await redis_client.set(cache_key, json.dumps(data, ensure_ascii=False), ex=_PERM_CACHE_TTL)
    except Exception as exc:  # noqa: BLE001 降级不阻断主流程
        log_redis_degraded("rbac_service.get_user_rbac", exc)
    return data


async def is_super_admin(rbac: dict) -> bool:
    """用户是否持有 super_admin 角色（rbac 为 get_user_rbac 返回结构）"""
    return any(r["code"] == SUPER_ADMIN_CODE for r in rbac.get("roles", []))


async def invalidate_user_rbac_cache(user_id: int) -> None:
    """失效单个用户的权限缓存（角色分配变更时调用）"""
    try:
        await redis_client.delete(_perm_cache_key(user_id))
    except Exception as exc:
        log_redis_degraded("rbac_service.invalidate_user_rbac_cache", f"user={user_id}: {exc}")


async def invalidate_role_rbac_cache(db: AsyncSession, role_id: int) -> None:
    """失效拥有指定角色的全部用户缓存（角色权限变更/删除时调用）"""
    user_ids = (
        await db.execute(select(UserRole.user_id).where(UserRole.role_id == role_id))
    ).scalars().all()
    if not user_ids:
        return
    try:
        await redis_client.delete(*[_perm_cache_key(uid) for uid in user_ids])
    except Exception as exc:
        log_redis_degraded(
            "rbac_service.invalidate_role_rbac_cache", f"role={role_id}: {exc}"
        )


# 当前用户档案（前端动态菜单数据源）

async def get_admin_profile(db: AsyncSession, user: User) -> dict:
    """当前管理员的 RBAC 档案：角色/权限码/部门（GET /admin/rbac/profile 数据源）"""
    rbac = await get_user_rbac(db, user.id)
    department = None
    if user.department_id:
        dept = (
            await db.execute(select(Department).where(Department.id == user.department_id))
        ).scalar_one_or_none()
        if dept:
            department = {"id": dept.id, "name": dept.name}
    return {
        "user": {
            "id": user.id,
            "account": user.account,
            "nickname": user.nickname,
            "avatar_url": user.avatar_url,
        },
        "roles": rbac["roles"],
        "permissions": rbac["perms"],
        "is_super": await is_super_admin(rbac),
        "department": department,
    }


# 权限目录（分配界面数据源）

async def list_permission_tree(locale: str = "zh") -> list[dict]:
    """权限目录树（按模块分组；目录由代码定义，库中仅存码表映射）"""
    groups = permissions_grouped_by_module(locale)
    return [
        {
            "module": module,
            "name": g["name"],
            "menu": g["menu"],
            "buttons": g["buttons"],
        }
        for module, g in sorted(groups.items())
    ]


# 角色 CRUD

def _validate_scope(data_scope: str) -> None:
    """数据范围白名单校验：非法值直接 400，不落库（落库后会被当成「最窄」处理，
    管理员会以为设成功了，实际全员只能看自己）"""
    if data_scope not in SCOPE_LABELS:
        raise AppException(code=400, message=f"未知数据范围: {data_scope}")


def role_to_dict(role: Role, *, permission_codes: list[str] | None = None, user_count: int | None = None) -> dict:
    data = {
        "id": role.id,
        "code": role.code,
        "name": role.name,
        "description": role.description,
        "is_system": role.is_system,
        # 数据范围随角色一起下发：管理页要能看出「这个角色能看到谁的数据」
        "data_scope": getattr(role, "data_scope", SCOPE_ALL) or SCOPE_ALL,
    }
    if permission_codes is not None:
        data["permissions"] = permission_codes
    if user_count is not None:
        data["user_count"] = user_count
    return data


async def list_roles(db: AsyncSession) -> dict:
    """角色列表（含权限码与用户数，管理页一次取齐）"""
    roles = (
        await db.execute(select(Role).order_by(Role.is_system.desc(), Role.id.asc()))
    ).scalars().all()
    if not roles:
        return {"items": []}
    role_ids = [r.id for r in roles]
    perm_rows = (
        await db.execute(
            select(RolePermission.role_id, Permission.code)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(RolePermission.role_id.in_(role_ids))
        )
    ).all()
    perms_map: dict[int, list[str]] = {}
    for rid, code in perm_rows:
        perms_map.setdefault(rid, []).append(code)
    count_rows = (
        await db.execute(
            select(UserRole.role_id, func.count(UserRole.user_id))
            .where(UserRole.role_id.in_(role_ids))
            .group_by(UserRole.role_id)
        )
    ).all()
    count_map = dict(count_rows)
    return {
        "items": [
            role_to_dict(
                r,
                permission_codes=sorted(perms_map.get(r.id, [])),
                user_count=count_map.get(r.id, 0),
            )
            for r in roles
        ]
    }


async def create_role(
    db: AsyncSession,
    *,
    code: str,
    name: str,
    description: str | None,
    permission_codes: list[str],
    data_scope: str = SCOPE_ALL,
) -> dict:
    """创建角色并绑定权限"""
    existed = (
        await db.execute(select(Role.id).where(Role.code == code))
    ).scalar_one_or_none()
    if existed:
        raise AppException(code=409, message=f"角色编码 '{code}' 已存在")
    invalid = [c for c in permission_codes if c not in ALL_PERMISSION_CODES]
    if invalid:
        raise AppException(code=400, message=f"未知权限码: {', '.join(invalid[:5])}")

    _validate_scope(data_scope)
    role = Role(
        code=code,
        name=name,
        description=description or None,
        is_system=False,
        data_scope=data_scope,
    )
    db.add(role)
    await db.flush()
    for pcode in set(permission_codes):
        pid = (
            await db.execute(select(Permission.id).where(Permission.code == pcode))
        ).scalar_one()
        db.add(RolePermission(role_id=role.id, permission_id=pid))
    await db.commit()
    await db.refresh(role)
    return role_to_dict(role, permission_codes=sorted(set(permission_codes)), user_count=0)


async def update_role(
    db: AsyncSession,
    role_id: int,
    *,
    name: str,
    description: str | None,
    data_scope: str | None = None,
    admin: User,
) -> dict:
    """更新角色基本信息与数据范围（code 不可改：作为缓存/审计的外部标识）"""
    role = await _get_role(db, role_id)
    before = {
        "name": role.name,
        "description": role.description,
        "data_scope": role.data_scope,
    }
    role.name = name
    role.description = description or None
    if data_scope is not None:
        _validate_scope(data_scope)
        role.data_scope = data_scope
    changes = diff_values(
        before,
        {
            "name": role.name,
            "description": role.description,
            "data_scope": role.data_scope,
        },
    )
    # 角色改权限/数据范围会改变谁能看到什么；审计必须与业务同事务提交
    record_admin_action(
        db, admin, "role_update",
        target_type="role", target_id=role_id,
        summary=f"编辑角色「{role.name}」（{role.code}）",
        detail={"changes": changes},
    )
    await db.commit()
    await db.refresh(role)
    return role_to_dict(role)


async def delete_role(db: AsyncSession, role_id: int) -> None:
    """删除自定义角色（内置角色与仍有用户的角色拒绝删除）"""
    role = await _get_role(db, role_id)
    if role.is_system:
        raise AppException(code=400, message="内置角色不可删除")
    user_count = (
        await db.execute(select(func.count(UserRole.user_id)).where(UserRole.role_id == role_id))
    ).scalar() or 0
    if user_count > 0:
        raise AppException(code=400, message=f"该角色仍有 {user_count} 个用户使用，请先移除后再删除")
    await invalidate_role_rbac_cache(db, role_id)
    await db.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
    await db.execute(delete(UserRole).where(UserRole.role_id == role_id))
    await db.delete(role)
    await db.commit()


async def get_role_permissions(db: AsyncSession, role_id: int) -> list[str]:
    """获取角色已绑定的权限码"""
    await _get_role(db, role_id)
    rows = (
        await db.execute(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
        )
    ).scalars().all()
    return sorted(rows)


async def set_role_permissions(
    db: AsyncSession, role_id: int, permission_codes: list[str], admin: User
) -> dict:
    """整体替换角色权限（差量写库：只增删变化的关联行）

    提权/降权直接决定谁能进管理端，写入管理操作审计（台账 P2-9）。
    仅在**确有变化**时记录：无变化不提交事务，也不该留下审计行。
    """
    role = await _get_role(db, role_id)
    if role.code == SUPER_ADMIN_CODE:
        raise AppException(code=400, message="超级管理员权限不可修改（它是权限校验的逃逸通道）")

    # 不能改自己持有的角色：否则「权限管理员」这类自定义角色可以给自己追加
    # user:edit / setting:update 等，等价于直接拿到全权（第五轮审查 SEC-10）。
    # 需要调整自己的角色时由另一位管理员操作，或由 super_admin 处理。
    holds_role = (
        await db.execute(
            select(UserRole.user_id).where(
                UserRole.user_id == admin.id, UserRole.role_id == role_id
            ).limit(1)
        )
    ).first()
    if holds_role:
        raise AppException(code=400, message="不能修改自己持有的角色权限，请让其他管理员操作")

    invalid = [c for c in permission_codes if c not in ALL_PERMISSION_CODES]
    if invalid:
        raise AppException(code=400, message=f"未知权限码: {', '.join(invalid[:5])}")

    current = set(await get_role_permissions(db, role_id))
    target = set(permission_codes)
    if target != current:
        # 删除被移除的权限关联
        if current - target:
            removed_ids = (
                await db.execute(
                    select(Permission.id).where(Permission.code.in_(list(current - target)))
                )
            ).scalars().all()
            if removed_ids:
                await db.execute(
                    delete(RolePermission).where(
                        RolePermission.role_id == role_id,
                        RolePermission.permission_id.in_(removed_ids),
                    )
                )
        # 新增的权限关联
        if target - current:
            added_ids = (
                await db.execute(
                    select(Permission.id).where(Permission.code.in_(list(target - current)))
                )
            ).scalars().all()
            for pid in added_ids:
                db.add(RolePermission(role_id=role_id, permission_id=pid))
        added = sorted(target - current)
        removed = sorted(current - target)
        record_admin_action(
            db, admin, "role_permission_update",
            target_type="role", target_id=role_id,
            summary=f"变更角色「{role.name}」权限（+{len(added)} / -{len(removed)}）",
            detail={"role_code": role.code, "added": added, "removed": removed},
        )
        await db.commit()
        # 权限变更 → 失效该角色全部用户的缓存
        await invalidate_role_rbac_cache(db, role_id)
    return role_to_dict(role, permission_codes=sorted(target))


async def _get_role(db: AsyncSession, role_id: int) -> Role:
    role = (
        await db.execute(select(Role).where(Role.id == role_id))
    ).scalar_one_or_none()
    if not role:
        raise NotFoundError("角色不存在")
    return role


async def get_role_detail(db: AsyncSession, role_id: int) -> dict:
    """角色详情（含已绑定权限码）。

    收敛 router 层 ORM（第三轮增量审查 P1-41）：``api/admin_rbac.py`` 此前是
    全仓**唯一**在 router 里直接 ``select(Role)`` 的地方 —— 事务与查询归属
    混在一起，新人无法从文件结构判断「ORM 应该写在哪一层」。
    """
    role = await _get_role(db, role_id)
    perms = await get_role_permissions(db, role_id)
    return role_to_dict(role, permission_codes=perms)


# 部门 CRUD

def _dept_to_dict(dept: Department, user_count: int = 0) -> dict:
    return {
        "id": dept.id,
        "name": dept.name,
        "parent_id": dept.parent_id,
        "sort_order": dept.sort_order,
        "user_count": user_count,
        "created_at": dept.created_at.isoformat() if dept.created_at else None,
    }


async def list_departments(db: AsyncSession) -> dict:
    """部门列表（平铺全量，含每部门用户数；树形由前端按 parent_id 组装）"""
    departments = (
        await db.execute(select(Department).order_by(Department.sort_order.asc(), Department.id.asc()))
    ).scalars().all()
    count_rows = (
        await db.execute(
            select(User.department_id, func.count(User.id))
            .where(User.department_id.isnot(None), User.deleted_at.is_(None))
            .group_by(User.department_id)
        )
    ).all()
    count_map = dict(count_rows)
    return {
        "items": [_dept_to_dict(d, count_map.get(d.id, 0)) for d in departments]
    }


async def create_department(db: AsyncSession, *, name: str, parent_id: int | None, sort_order: int) -> dict:
    """创建部门（同父级下名称唯一）"""
    if parent_id is not None:
        await _get_department(db, parent_id)
    existed = (
        await db.execute(
            select(Department.id).where(
                Department.name == name,
                Department.parent_id == parent_id if parent_id is not None else Department.parent_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existed:
        raise AppException(code=400, message=f"同级下已存在部门 '{name}'")
    dept = Department(name=name, parent_id=parent_id, sort_order=sort_order)
    db.add(dept)
    await db.commit()
    await db.refresh(dept)
    return _dept_to_dict(dept)


async def update_department(db: AsyncSession, department_id: int, *, name: str, parent_id: int | None, sort_order: int) -> dict:
    """更新部门（不允许把自己挂到自己/后代下，防环）"""
    dept = await _get_department(db, department_id)
    if parent_id is not None:
        if parent_id == department_id:
            raise AppException(code=400, message="上级部门不能是自身")
        await _get_department(db, parent_id)
        # 防环：沿 parent 链向上找，若遇到自身则拒绝
        ancestor_id = parent_id
        for _ in range(50):  # 防御性上限
            if ancestor_id == department_id:
                raise AppException(code=400, message="不能将部门挂到自己的子部门下")
            ancestor = await _get_department(db, ancestor_id)
            if ancestor.parent_id is None:
                break
            ancestor_id = ancestor.parent_id
    existed = (
        await db.execute(
            select(Department.id).where(
                Department.name == name,
                Department.parent_id == parent_id if parent_id is not None else Department.parent_id.is_(None),
                Department.id != department_id,
            )
        )
    ).scalar_one_or_none()
    if existed:
        raise AppException(code=400, message=f"同级下已存在部门 '{name}'")
    dept.name = name
    dept.parent_id = parent_id
    dept.sort_order = sort_order
    await db.commit()
    await db.refresh(dept)
    return _dept_to_dict(dept)


async def delete_department(db: AsyncSession, department_id: int) -> None:
    """删除部门（有子部门或仍有用户归属时拒绝）"""
    dept = await _get_department(db, department_id)
    child_count = (
        await db.execute(select(func.count(Department.id)).where(Department.parent_id == department_id))
    ).scalar() or 0
    if child_count > 0:
        raise AppException(code=400, message=f"该部门下有 {child_count} 个子部门，请先删除子部门")
    user_count = (
        await db.execute(
            select(func.count(User.id)).where(User.department_id == department_id, User.deleted_at.is_(None))
        )
    ).scalar() or 0
    if user_count > 0:
        raise AppException(code=400, message=f"仍有 {user_count} 个用户归属该部门，请先转移用户")
    await db.delete(dept)
    await db.commit()


async def _get_department(db: AsyncSession, department_id: int) -> Department:
    dept = (
        await db.execute(select(Department).where(Department.id == department_id))
    ).scalar_one_or_none()
    if not dept:
        raise NotFoundError("部门不存在")
    return dept


# 用户角色/部门分配

async def assign_user_rbac(
    db: AsyncSession,
    user_id: int,
    admin: User,
    *,
    role_ids: list[int] | None = None,
    department_id: int | None = None,
) -> dict:
    """分配用户的 RBAC 角色与部门（role_ids=None 表示不变更角色，[] 表示清空）

    安全约束（第二轮增量审查 2026-10-10 P0-2，此前三条全部缺失）：
    1. **禁止变更自己的角色**：否则任何持有 `user:edit` 的管理员都能自我提权；
    2. **非 super_admin 不得分配 super_admin 角色**：该角色在 core/rbac.py:206
       「一票放行」全部权限校验，等于全站最高权限，不能让普通管理员能授予；
    3. **写管理操作审计**：此前提权行为完全没有留痕，出事无法归因。

    对照：``set_role_permissions`` 早已保护 super_admin 角色本身不可被改权限，
    但「把 super_admin 分配给谁」此前没有任何约束 —— 只锁了一扇门。
    """
    user = (
        await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if not user:
        raise NotFoundError("用户不存在")

    before_role_ids: list[int] | None = None
    if role_ids is not None:
        before_role_ids = sorted(
            (
                await db.execute(select(UserRole.role_id).where(UserRole.user_id == user_id))
            ).scalars().all()
        )
        # 约束 1：禁止自我提权（改自己的角色一律拒绝；改部门不受限）
        if user_id == admin.id:
            raise AppException(code=400, message="不能变更自己的角色，请由其他管理员操作")

        role_ids = list(set(role_ids))
        if role_ids:
            roles = (
                await db.execute(select(Role).where(Role.id.in_(role_ids)))
            ).scalars().all()
            if len(roles) != len(role_ids):
                raise AppException(code=400, message="存在无效的角色 ID")
            # 约束 2：只有 super_admin 才能授予 super_admin
            if any(r.code == SUPER_ADMIN_CODE for r in roles):
                if not await is_super_admin(await get_user_rbac(db, admin.id)):
                    raise AppException(
                        code=403, message="仅超级管理员可分配超级管理员角色"
                    )
        # 整体替换关联（差量：删除后重建，分配为低频管理操作）
        await db.execute(delete(UserRole).where(UserRole.user_id == user_id))
        for rid in role_ids:
            db.add(UserRole(user_id=user_id, role_id=rid))

    before_department_id = user.department_id
    if department_id is not None:
        if department_id:
            await _get_department(db, department_id)
            user.department_id = department_id
        else:
            user.department_id = None  # 0/显式清空

    changes: dict = {}
    if role_ids is not None:
        changes["role_ids"] = [before_role_ids or [], sorted(role_ids)]
    if department_id is not None:
        changes["department_id"] = [before_department_id, user.department_id]

    # 约束 3：审计留痕（record_admin_action 只 add 不 commit，随本次事务提交）
    record_admin_action(
        db, admin, "user_rbac_assign",
        target_type="user", target_id=user_id,
        summary=f"变更用户#{user_id}的 RBAC 分配",
        detail={"changes": changes},
    )

    await db.commit()
    # 角色变更 → 失效该用户权限缓存（权限按请求实时校验，无需吊销 token）
    await invalidate_user_rbac_cache(user_id)
    rbac = await get_user_rbac(db, user_id)
    return {
        "user_id": user_id,
        "roles": rbac["roles"],
        "department_id": user.department_id,
    }
