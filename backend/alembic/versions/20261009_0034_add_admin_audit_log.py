"""管理操作审计表 + 权限补种（台账 P2-9 / 代码审查 D7）

Revision ID: add_admin_audit_log
Revises: rename_plugin_to_extension
Create Date: 2026-10-09

背景：
    认证审批、用户角色提权、积分调整、兑换码生成等敏感管理操作此前**只写日志**
    （``logger.bind(event="admin_...")``）。日志是运维视角的易失数据：不进结构化存储、
    无法按操作者/目标检索、容器重建即丢、也不进备份。多管理员场景下
    「谁在什么时候把谁提成了 admin」必须可追溯。

本迁移做两件事：
1. 建 ``admin_audit_logs`` 表（含三种固定查询形态的索引）；
2. 补种 ``audit:view`` 权限并授予内置角色 —— **必须做**：管理端是 fail-closed，
   不补种就会出现「代码有接口、路由也登记了，但没有任何角色持有该权限」
   → 管理员打开操作审计被拦成 403（同 0026 / 0030 的教训）。

⚠️ 表**刻意不设外键**（admin_id / target_id 都不加 ForeignKey）：
    审计记录必须在「操作者或目标被删除之后依然存在」。加外键会触发 1451 拒绝删除，
    或（若配级联）把审计记录一起删掉 —— 两种结果都让审计失去意义。

⚠️ 权限授予策略与 0026 / 0030 一致：**只增不减**。只补齐目录声明中缺失的权限，
    不回收运营手工配置过的权限。``viewer``（只读管理员）在目录里**刻意排除了
    ``audit:view``**（审计日志是最敏感的一类只读数据），因此不会被授予。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.services.rbac_catalog import PERMISSION_CATALOG, SYSTEM_ROLES


revision: str = 'add_admin_audit_log'
down_revision: Union[str, None] = 'rename_plugin_to_extension'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("admin_id", sa.BigInteger(), nullable=False, comment="操作者用户ID"),
        sa.Column(
            "admin_account",
            sa.String(length=120),
            nullable=False,
            server_default="",
            comment="操作者账号快照(改名/注销后仍可追溯)",
        ),
        sa.Column("action", sa.String(length=50), nullable=False, comment="动作码"),
        sa.Column(
            "target_type",
            sa.String(length=30),
            nullable=False,
            server_default="",
            comment="目标类型，批量操作为空",
        ),
        sa.Column("target_id", sa.BigInteger(), nullable=True, comment="目标ID(批量操作为空)"),
        sa.Column("summary", sa.String(length=500), nullable=False, comment="人类可读摘要"),
        sa.Column("detail", sa.JSON(), nullable=True, comment="结构化明细(仅安全字段)"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
            comment="操作时间",
        ),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_aal_admin_created", "admin_audit_logs", ["admin_id", "created_at"])
    op.create_index("ix_aal_action_created", "admin_audit_logs", ["action", "created_at"])
    op.create_index("ix_aal_target", "admin_audit_logs", ["target_type", "target_id"])

    # ---- 权限补种（幂等：缺失插入 / 已存在刷新元信息）----
    conn = op.get_bind()
    import secrets as _secrets

    def _gen_id() -> int:
        return _secrets.randbelow(9_000_000_000) + 1_000_000_000

    existing = {row.code for row in conn.execute(sa.text("SELECT code FROM permissions"))}
    for p in PERMISSION_CATALOG:
        if p.code in existing:
            conn.execute(
                sa.text(
                    "UPDATE permissions SET name=:name, type=:type, "
                    "module=:module, module_name=:module_name, sort=:sort WHERE code=:code"
                ).bindparams(
                    name=p.name, type=p.type, module=p.module,
                    module_name=p.module_name, sort=p.sort, code=p.code,
                )
            )
        else:
            conn.execute(
                sa.text(
                    "INSERT INTO permissions (id, code, name, type, module, module_name, sort) "
                    "VALUES (:id, :code, :name, :type, :module, :module_name, :sort)"
                ).bindparams(
                    id=_gen_id(), code=p.code, name=p.name, type=p.type,
                    module=p.module, module_name=p.module_name, sort=p.sort,
                )
            )

    code_to_perm_id = {
        row.code: row.id for row in conn.execute(sa.text("SELECT id, code FROM permissions"))
    }
    code_to_role_id = {
        row.code: row.id for row in conn.execute(sa.text("SELECT id, code FROM roles"))
    }

    # 内置角色：只补齐目录声明中缺失的权限（不回收多余权限，见文件头说明）
    for role in SYSTEM_ROLES:
        role_id = code_to_role_id.get(role["code"])
        if role_id is None:
            continue
        granted = {
            row.code
            for row in conn.execute(
                sa.text(
                    "SELECT p.code FROM role_permissions rp "
                    "JOIN permissions p ON p.id = rp.permission_id WHERE rp.role_id = :rid"
                ).bindparams(rid=role_id)
            )
        }
        for code in [c for c in role["permissions"] if c not in granted]:
            perm_id = code_to_perm_id.get(code)
            if perm_id is None:
                continue
            conn.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission_id) VALUES (:rid, :pid)"
                ).bindparams(rid=role_id, pid=perm_id)
            )


def downgrade() -> None:
    """只回收本次新增的 audit:view 权限与关联，不触碰其它权限。"""
    conn = op.get_bind()
    row = conn.execute(
        sa.text("SELECT id FROM permissions WHERE code = 'audit:view'")
    ).first()
    if row is not None:
        conn.execute(
            sa.text("DELETE FROM role_permissions WHERE permission_id = :pid").bindparams(
                pid=row.id
            )
        )
        conn.execute(
            sa.text("DELETE FROM permissions WHERE id = :pid").bindparams(pid=row.id)
        )

    op.drop_index("ix_aal_target", table_name="admin_audit_logs")
    op.drop_index("ix_aal_action_created", table_name="admin_audit_logs")
    op.drop_index("ix_aal_admin_created", table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")
