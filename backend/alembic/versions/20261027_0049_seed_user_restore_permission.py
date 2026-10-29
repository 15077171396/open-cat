"""用户恢复权限补种（`user:restore`）

Revision ID: seed_user_restore_permission
Revises: add_list_sort_indexes_v2
Create Date: 2026-10-27

为什么需要这条迁移：
    权限点只在建表时按当时的 ``PERMISSION_CATALOG`` 播种过一次，此后新增的
    ``user:restore`` **不会自动出现在已有部署的库里**。管理端是 **fail-closed**
    （未匹配规则一律 403）—— 结果是接口在、规则也登记了，但没有任何角色持有该权限，
    管理员点「恢复」直接被拦成 403。这类「新功能上线即不可用」的坑必须靠迁移堵住
    （与 0026 / 0030 同一做法）。

同步策略（刻意选**只增不减**，与 0026 / 0030 一致）：
    - 权限点：缺失则插入；已存在则更新名称/模块等元信息（以代码目录为准）。
    - 内置角色：只补齐目录声明中缺失的权限，**不回收**多余权限。

⚠️ 只有内置角色会被补种。`user:restore` 在 SYSTEM_ROLES 里只会落到 super_admin
（content_admin 不含 user 模块，viewer 只拿 ``:view``）。运维自建的、持有
``user:delete`` 的自定义角色**不会自动获得**恢复权限 —— 需要管理员在
「角色管理」里手动勾选，这是刻意的：恢复账号等于把访问权还给已被处置的对象。

新增权限码后请**照抄本文件再写一条新迁移**，不要回头改早期的播种迁移 ——
已上线的库不会重跑旧迁移。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.services.rbac_catalog import PERMISSION_CATALOG, SYSTEM_ROLES

revision: str = 'seed_user_restore_permission'
down_revision: Union[str, None] = 'add_list_sort_indexes_v2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
        row.code: row.id for row in conn.execute(sa.text('SELECT id, code FROM permissions'))
    }
    code_to_role_id = {
        row.code: row.id for row in conn.execute(sa.text('SELECT id, code FROM roles'))
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
                    'JOIN permissions p ON p.id = rp.permission_id WHERE rp.role_id = :rid'
                ).bindparams(rid=role_id)
            )
        }
        for code in [c for c in role['permissions'] if c not in granted]:
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
    for code in ('user:restore',):
        perm_row = conn.execute(
            sa.text('SELECT id FROM permissions WHERE code = :code').bindparams(code=code)
        ).first()
        if perm_row is None:
            continue
        conn.execute(
            sa.text('DELETE FROM role_permissions WHERE permission_id = :pid').bindparams(pid=perm_row.id)
        )
        conn.execute(sa.text('DELETE FROM permissions WHERE id = :pid').bindparams(pid=perm_row.id))
