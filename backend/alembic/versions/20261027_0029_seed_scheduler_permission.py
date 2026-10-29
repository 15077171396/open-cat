"""定时任务权限补种：把 PERMISSION_CATALOG 同步进库，并补发给内置角色

Revision ID: seed_scheduler_permission
Revises: scheduled_job_configs
Create Date: 2026-10-27

为什么需要这条迁移：
    定时任务从「只读」升级为「可管理」，新增权限码 `scheduler:manage`
    （启停 / 改周期 / 立即执行）。权限点只在建表时按当时的 PERMISSION_CATALOG
    播种过一次，此后新增的码**不会自动出现在已有部署的库里** —— 结果是代码里有接口、
    路由规则也登记了，但没有任何角色持有它，管理员点保存直接 403。

同步策略（照抄 0026_seed_moderation_permissions，**只增不减**）：
    - 权限点：缺失则插入；已存在则更新名称/模块等元信息（以代码目录为准）。
    - 内置角色：只补齐目录声明中缺失的权限，**不回收**多余权限。
      内置角色可能被运营在界面上手动调整过，迁移擅自回收等于覆盖他人的配置。

授予面（刻意的）：
    - `super_admin`：持有全部权限（由 SYSTEM_ROLES 的 `sorted(ALL_PERMISSION_CODES)` 决定）；
    - `content_admin`：不含 scheduler 模块 → **不授予**（改调度周期不是内容运营职责）；
    - `viewer`：只收 `:view` 码 → **不授予**（可看不可改，正是这里想要的分层）。
    需要放宽时由超管在「角色管理」里显式勾选，迁移不替人做决定。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.services.rbac_catalog import PERMISSION_CATALOG, SYSTEM_ROLES

revision: str = 'seed_scheduler_permission'
down_revision: Union[str, None] = 'scheduled_job_configs'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 本次新增的权限码（downgrade 只回收这些）
_NEW_CODES = ('scheduler:manage',)


def upgrade() -> None:
    conn = op.get_bind()
    import secrets as _secrets

    def _gen_id() -> int:
        return _secrets.randbelow(9_000_000_000) + 1_000_000_000

    # 1) 权限点：以代码目录为唯一事实来源，缺失插入 / 已存在刷新元信息
    existing = {row.code for row in conn.execute(sa.text('SELECT code FROM permissions'))}
    for p in PERMISSION_CATALOG:
        if p.code in existing:
            conn.execute(
                sa.text(
                    'UPDATE permissions SET name=:name, type=:type, '
                    'module=:module, module_name=:module_name, sort=:sort WHERE code=:code'
                ).bindparams(
                    name=p.name, type=p.type, module=p.module,
                    module_name=p.module_name, sort=p.sort, code=p.code,
                )
            )
        else:
            conn.execute(
                sa.text(
                    'INSERT INTO permissions (id, code, name, type, module, module_name, sort) '
                    'VALUES (:id, :code, :name, :type, :module, :module_name, :sort)'
                ).bindparams(
                    id=_gen_id(), code=p.code, name=p.name, type=p.type,
                    module=p.module, module_name=p.module_name, sort=p.sort,
                )
            )

    code_to_perm_id = {
        row.code: row.id
        for row in conn.execute(sa.text('SELECT id, code FROM permissions'))
    }
    code_to_role_id = {
        row.code: row.id
        for row in conn.execute(sa.text('SELECT id, code FROM roles'))
    }

    # 2) 内置角色：补齐目录声明中缺失的权限（不回收多余权限）
    for role in SYSTEM_ROLES:
        role_id = code_to_role_id.get(role['code'])
        if role_id is None:
            continue
        granted = {
            row.code
            for row in conn.execute(
                sa.text(
                    'SELECT p.code FROM role_permissions rp '
                    'JOIN permissions p ON p.id = rp.permission_id '
                    'WHERE rp.role_id = :rid'
                ).bindparams(rid=role_id)
            )
        }
        missing = [c for c in role['permissions'] if c not in granted]
        for code in missing:
            perm_id = code_to_perm_id.get(code)
            if perm_id is None:
                continue
            conn.execute(
                sa.text(
                    'INSERT INTO role_permissions (role_id, permission_id) VALUES (:rid, :pid)'
                ).bindparams(rid=role_id, pid=perm_id)
            )


def downgrade() -> None:
    """仅回收本次新增的权限点与关联，不触碰其他权限。"""
    conn = op.get_bind()
    for code in _NEW_CODES:
        perm_row = conn.execute(
            sa.text('SELECT id FROM permissions WHERE code = :code').bindparams(code=code)
        ).first()
        if perm_row is None:
            continue
        conn.execute(
            sa.text('DELETE FROM role_permissions WHERE permission_id = :pid').bindparams(
                pid=perm_row.id
            )
        )
        conn.execute(
            sa.text('DELETE FROM permissions WHERE id = :pid').bindparams(pid=perm_row.id)
        )
