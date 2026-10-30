"""RBAC 模型：角色 / 权限 / 用户-角色 / 部门。

users.role 保持为业务角色（驱动 VIP 权益等前台逻辑，进后台门槛仍是 role=admin）；
本模块为管理后台细粒度权限层（可见菜单与操作由权限码聚合决定，super_admin 绕过全部检查）；
部门仅作组织归属展示，不参与权限判定。"""

from datetime import datetime

from sqlalchemy import BigInteger, String, Integer, Boolean, DateTime, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class Department(Base):
    """部门（树形结构，parent_id 为空表示顶级部门）"""
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    name: Mapped[str] = mapped_column(String(50), comment="部门名称")
    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True, comment="上级部门ID(空=顶级)")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, comment="排序值(小在前)")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间")

    __table_args__ = (
        UniqueConstraint("parent_id", "name", name="uq_departments_parent_name"),
    )


class Role(Base):
    """RBAC 角色（管理后台权限聚合体；is_system 内置角色不可删除）"""
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, comment="角色编码(如 super_admin)")
    name: Mapped[str] = mapped_column(String(50), comment="角色名称")
    description: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="角色说明")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", comment="内置角色(不可删除)")
    data_scope: Mapped[str] = mapped_column(
        String(20),
        default="all",
        server_default="all",
        comment="数据范围：all=全部 / dept=本部门 / self=仅本人（见 core/data_scope.py）",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间")


class Permission(Base):
    """权限点（菜单/按钮；目录由 rbac_catalog 定义并在迁移中种子化）"""
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    code: Mapped[str] = mapped_column(String(100), unique=True, index=True, comment="权限码(如 tutorial:create)")
    name: Mapped[str] = mapped_column(String(100), comment="权限名称")
    type: Mapped[str] = mapped_column(String(20), comment="menu|button")
    module: Mapped[str] = mapped_column(String(50), index=True, comment="所属模块")
    module_name: Mapped[str] = mapped_column(String(50), comment="模块显示名")
    sort: Mapped[int] = mapped_column(Integer, default=0, comment="排序值")


class RolePermission(Base):
    """角色-权限关联"""
    __tablename__ = "role_permissions"

    role_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    permission_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class UserRole(Base):
    """用户-角色关联"""
    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
