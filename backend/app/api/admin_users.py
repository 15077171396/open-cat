"""管理后台 - 用户管理（含 RBAC 分配）"""

from fastapi import Depends, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional

from app.database import get_db
from app.core.data_scope import resolve_data_scope
from app.core.permissions import require_admin
from app.utils.verification import ACCOUNT_FORMAT_ERROR, is_email_or_phone
from app.models.user import User
from app.core.exceptions import success_response
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.api._admin_shared import require_elevation, router

from app.services.admin_user_service import (
    list_users,
    get_user,
    update_user,
    delete_user,
    batch_delete_users,
    restore_user,
    create_user as create_user_by_admin,
)
from app.services.rbac_service import assign_user_rbac
from app.services.user_import_service import import_users
from app.schemas.content import PageResult
from app.schemas.admin import AdminUserItem, CreatedResult, DeletedResult


# 用户管理

@router.get("/users", response_model=ApiResponse[PageResult[AdminUserItem]])
async def admin_list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    role: str = Query(None),
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    account_status: Literal["active", "deleted", "all"] = Query(
        "active", description="账号状态：active=正常 / deleted=已注销 / all=全部"
    ),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """用户列表（含星球认证状态）

    默认只看正常账号（与历史行为一致）；`account_status=deleted` 查已注销账号，
    列表项的 `deleted_at` 非空，可在该视图里恢复。
    """
    # 数据范围在路由层解析（service 不该知道 RBAC 的存在），super_admin 恒为 all
    scope = await resolve_data_scope(db, admin)
    result = await list_users(db, page, page_size, role, keyword, account_status, scope=scope)
    return success_response(result)


# 用户创建与批量操作（批量路由必须定义在 /users/{user_id} 之前，
# 否则 "batch-delete" 会被当作路径参数解析为 int 而返回 422）

class UserCreateRequest(BaseModel):
    account: str = Field(..., min_length=1, max_length=100, description="手机号或邮箱")
    password: str = Field(..., min_length=6, max_length=50, description="密码(6-50位)")
    role: Literal["user", "admin"] = "user"
    # RBAC：管理后台角色与部门归属（角色权限仅对业务角色 admin 生效）
    rbac_role_ids: list[int] = Field(default_factory=list, max_length=20)
    department_id: int | None = Field(None, ge=1, description="部门ID(空=不归属)")

    @field_validator("account")
    @classmethod
    def validate_account(cls, v):
        """与注册端口径一致：手机号或邮箱格式（同用 utils.verification 唯一定义点）"""
        if is_email_or_phone(v):
            return v
        raise ValueError(ACCOUNT_FORMAT_ERROR)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        """与注册端口径一致：bcrypt 仅消费前 72 字节，超长密码静默截断会使不同密码等价，必须显式拒绝"""
        from app.core.security import validate_password_length

        validate_password_length(v)
        return v


class UserBatchDeleteRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=100)


@router.post("/users", response_model=ApiResponse[CreatedResult])
async def admin_create_user(
    req: UserCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """创建用户（管理员直接创建，免验证码；可同时分配 RBAC 角色与部门）"""
    # 三步必须落在同一事务：建号不提交 → 分配 RBAC → 统一 commit；
    # 若 create_user 自行 commit 后 assign 抛错，会留下 RBAC 未写入的僵尸账号且管理员以为没创建成功
    result = await create_user_by_admin(
        db, admin,
        req.model_dump(exclude={"rbac_role_ids", "department_id"}),
        commit=False,
    )
    # RBAC 角色与部门分配（仅对业务角色 admin 有意义，其余角色仅记录归属）
    if req.rbac_role_ids or req.department_id:
        await assign_user_rbac(
            db, result["id"], admin,
            role_ids=req.rbac_role_ids or None,
            department_id=req.department_id,
        )
    await db.commit()
    return success_response(result, "用户创建成功")


@router.post("/users/import", response_model=ApiResponse[CreatedResult])
async def admin_import_users(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """批量导入用户（CSV：account,password,role,nickname）。

    原子语义：任一行校验失败则整体取消（400 + 行级报告），校验全过才写库。
    授权面与单账号创建一致（user:create）；导入完成写一条汇总审计。
    """
    raw = await file.read()
    result = await import_users(db, admin, raw)
    return success_response(result, f"导入完成：成功创建 {result['imported']} 个账号")


@router.post("/users/batch-delete", response_model=ApiResponse[DeletedResult])
async def admin_batch_delete_users(
    req: UserBatchDeleteRequest,
    _: None = Depends(require_elevation),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """批量删除用户（软删除，与用户注销一致）

    S5 危险操作二次校验：批量删除 = 一次动作抹掉一批账号的可见性，
    前端确认弹窗之外再要求重输密码（5 分钟窗口内复用）。
    """
    result = await batch_delete_users(db, admin, req.ids)
    return success_response(result, f"已删除 {result['deleted']} 个用户")


@router.post("/users/{user_id}/restore", response_model=ApiResponse[DeletedResult])
async def admin_restore_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """恢复已注销用户（清软删除标记，关联数据原样保留）

    与用户自助注册的「复活」不同：那条路径会清空个人数据，
    这里一律保留 —— 语义是「撤销删除」，不是「重开新账号」。
    """
    result = await restore_user(db, admin, user_id)
    return success_response(result, f"已恢复用户 {result['account']}")


@router.get("/users/{user_id}", response_model=ApiResponse[AdminUserItem])
async def admin_get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """用户详情（含认证历史）"""
    result = await get_user(db, user_id, scope=await resolve_data_scope(db, admin))
    return success_response(result)


class UserUpdateRequest(BaseModel):
    role: Optional[Literal["user", "admin"]] = None
    avatar_url: Optional[str] = Field(None, max_length=500)  # 空串表示清除


@router.put("/users/{user_id}", response_model=ApiResponse[None])
async def admin_update_user(
    user_id: int,
    req: UserUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """更新用户信息（角色 / 头像）"""
    await update_user(db, admin, user_id, req.model_dump(exclude_unset=False))
    return success_response(message="用户信息已更新")


@router.delete("/users/{user_id}", response_model=ApiResponse[None])
async def admin_delete_user(
    user_id: int,
    _: None = Depends(require_elevation),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """删除用户（软删除，与用户注销一致；S5 要求密码二次校验）"""
    await delete_user(db, admin, user_id)
    return success_response(message="用户已删除")
