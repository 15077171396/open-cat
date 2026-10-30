"""RBAC 管理路由：当前用户档案 / 权限目录 / 角色 CRUD / 部门 CRUD / 用户分配。

⚠️ 本模块必须自己挂 check_route_permission：FastAPI 的 router 级 dependencies 不会跨
include_router 继承，漏挂时这组端点只剩 require_admin（仅角色门槛），role=admin 账号
可给自己分配 super_admin 完成提权。细粒度权限见 core/rbac.py 规则表；
tests/test_admin_route_guards.py 有「每条 /admin 路由都必须挂守卫」的静态守卫。
"""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.i18n import request_locale
from app.core.permissions import require_admin
from app.core.rbac import check_route_permission
from app.core.exceptions import success_response
from app.schemas.common import ApiResponse
from app.models.user import User
from app.services import rbac_service
from app.services.admin_audit_service import record_admin_action
from app.schemas.content import PageResult
from app.schemas.admin import ActionAck, CreatedResult, DepartmentItem, RoleItem

router = APIRouter(dependencies=[Depends(check_route_permission)])


# 请求模型

class RoleCreateRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(..., min_length=1, max_length=50)
    description: str | None = Field(None, max_length=200)
    permission_codes: list[str] = Field(default_factory=list, max_length=100)
    # 数据范围：all=全部 / dept=本部门 / self=仅本人；缺省 all，保持改造前的可见范围
    data_scope: Literal["all", "dept", "self"] = "all"


class RoleUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    description: str | None = Field(None, max_length=200)
    # 不传则保持原值（前端老版本不会带这个字段，不能因此把范围重置成 all）
    data_scope: Literal["all", "dept", "self"] | None = None


class RolePermissionsRequest(BaseModel):
    permission_codes: list[str] = Field(default_factory=list, max_length=100)


class DepartmentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    parent_id: int | None = Field(None, description="上级部门ID(空=顶级)")
    sort_order: int = Field(0, ge=0, le=10000)


class DepartmentUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    parent_id: int | None = Field(None, description="上级部门ID(空=顶级)")
    sort_order: int = Field(0, ge=0, le=10000)


class UserRbacAssignRequest(BaseModel):
    """用户 RBAC 分配；role_ids=None 表示不变更角色，department_id=None 表示不变更部门（0=清空）"""
    role_ids: list[int] | None = Field(None, max_length=20)
    department_id: int | None = Field(None, ge=0, description="部门ID(0=清空归属)")


# 当前用户档案（动态菜单数据源）

@router.get("/rbac/profile", response_model=ApiResponse[ActionAck])
async def rbac_profile(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """当前管理员的 RBAC 档案：角色/权限码/部门归属"""
    result = await rbac_service.get_admin_profile(db, admin)
    return success_response(result)


# 权限目录

@router.get("/rbac/permissions", response_model=ApiResponse[ActionAck])
async def permission_catalog(
    admin: User = Depends(require_admin),
    locale: str = Depends(request_locale),
):
    """权限目录树（按模块分组，角色权限分配界面数据源）"""
    return success_response({"items": await rbac_service.list_permission_tree(locale)})


# 角色管理

@router.get("/rbac/roles", response_model=ApiResponse[PageResult[RoleItem]])
async def list_rbac_roles(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """角色列表（含权限码与用户数）"""
    result = await rbac_service.list_roles(db)
    return success_response(result)


@router.post("/rbac/roles", response_model=ApiResponse[CreatedResult])
async def create_rbac_role(
    req: RoleCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """创建自定义角色"""
    result = await rbac_service.create_role(
        db,
        code=req.code, name=req.name,
        description=req.description,
        permission_codes=req.permission_codes,
        data_scope=req.data_scope,
    )
    # 审计：建/改/删角色是提权面的起点，无留痕等于绕开审计造全权角色
    record_admin_action(
        db, admin, "role_create",
        target_type="role", target_id=result.get("id"),
        summary=f"新建角色「{result.get('name') or req.name}」（{len(req.permission_codes)} 项权限）",
        detail={"code": req.code, "permissions": sorted(req.permission_codes)},
    )
    return success_response(result, "角色创建成功")


@router.get("/rbac/roles/{role_id}", response_model=ApiResponse[RoleItem])
async def get_rbac_role(
    role_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """角色详情（含已绑定权限码）"""
    result = await rbac_service.get_role_detail(db, role_id)
    return success_response(result)


@router.put("/rbac/roles/{role_id}", response_model=ApiResponse[ActionAck])
async def update_rbac_role(
    role_id: int,
    req: RoleUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """更新角色基本信息（编码不可改）"""
    result = await rbac_service.update_role(
        db, role_id, name=req.name, description=req.description, data_scope=req.data_scope
    )
    record_admin_action(
        db, admin, "role_update",
        target_type="role", target_id=role_id,
        summary=f"编辑角色 {role_id}",
        detail={"name": req.name},
    )
    return success_response(result, "角色已更新")


@router.delete("/rbac/roles/{role_id}", response_model=ApiResponse[None])
async def delete_rbac_role(
    role_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """删除自定义角色（内置角色与仍有用户的角色拒绝）"""
    await rbac_service.delete_role(db, role_id)
    # 删除角色会连带解绑该角色下所有用户的权限（服务内已删 RolePermission/UserRole）
    record_admin_action(
        db, admin, "role_delete",
        target_type="role", target_id=role_id,
        summary=f"删除角色 {role_id}（其成员随之失去该角色权限）",
    )
    return success_response(message="角色已删除")


@router.get("/rbac/roles/{role_id}/permissions", response_model=ApiResponse[ActionAck])
async def get_rbac_role_permissions(
    role_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """角色已绑定的权限码列表"""
    perms = await rbac_service.get_role_permissions(db, role_id)
    return success_response({"permission_codes": perms})


@router.put("/rbac/roles/{role_id}/permissions", response_model=ApiResponse[ActionAck])
async def set_rbac_role_permissions(
    role_id: int,
    req: RolePermissionsRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """整体替换角色权限"""
    result = await rbac_service.set_role_permissions(db, role_id, req.permission_codes, admin)
    return success_response(result, "角色权限已更新")


# 部门管理

@router.get("/rbac/departments", response_model=ApiResponse[PageResult[DepartmentItem]])
async def list_rbac_departments(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """部门列表（含每部门用户数，前端按 parent_id 组树）"""
    result = await rbac_service.list_departments(db)
    return success_response(result)


@router.post("/rbac/departments", response_model=ApiResponse[CreatedResult])
async def create_rbac_department(
    req: DepartmentCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """创建部门"""
    result = await rbac_service.create_department(
        db, name=req.name, parent_id=req.parent_id, sort_order=req.sort_order
    )
    record_admin_action(
        db, admin, "department_create",
        target_type="department", target_id=result.get("id"),
        summary=f"新建部门「{result.get('name') or req.name}」",
        detail={"parent_id": req.parent_id},
    )
    return success_response(result, "部门创建成功")


@router.put("/rbac/departments/{department_id}", response_model=ApiResponse[ActionAck])
async def update_rbac_department(
    department_id: int,
    req: DepartmentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """更新部门（防环校验：不可挂到自身或自己的子部门下）"""
    result = await rbac_service.update_department(
        db, department_id, name=req.name, parent_id=req.parent_id, sort_order=req.sort_order
    )
    record_admin_action(
        db, admin, "department_update",
        target_type="department", target_id=department_id,
        summary=f"编辑部门 {department_id}",
        detail={"name": req.name, "parent_id": req.parent_id},
    )
    return success_response(result, "部门已更新")


@router.delete("/rbac/departments/{department_id}", response_model=ApiResponse[None])
async def delete_rbac_department(
    department_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """删除部门（有子部门或归属用户时拒绝）"""
    await rbac_service.delete_department(db, department_id)
    record_admin_action(
        db, admin, "department_delete",
        target_type="department", target_id=department_id,
        summary=f"删除部门 {department_id}",
    )
    return success_response(message="部门已删除")


# 用户 RBAC 分配

@router.put("/users/{user_id}/rbac", response_model=ApiResponse[ActionAck])
async def assign_user_rbac(
    user_id: int,
    req: UserRbacAssignRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """分配用户的 RBAC 角色与部门归属（防自我提权，见 rbac_service 内注释）"""
    result = await rbac_service.assign_user_rbac(
        db, user_id, admin, role_ids=req.role_ids, department_id=req.department_id
    )
    return success_response(result, "用户权限已更新")
