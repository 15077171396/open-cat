"""站内消息权限补种：新增 `message:view` / `message:send` 并补发给内置角色

Revision ID: seed_message_permission
Revises: add_message_batch_id
Create Date: 2026-10-28

为什么需要这条迁移：
    「消息通知」是后台新模块，读写用独立权限码。权限点只在建表时按当时的
    PERMISSION_CATALOG 播种过一次，此后新增的码**不会自动出现在已有部署的库里** ——
    结果是代码里有接口、路由规则也登记了，但没有任何角色持有它，管理员点进去直接 403
    （与 `mail_setting:*`、`login_setting:*`、`legal_doc:*`、`payment_setting:*` 同一个坑）。

同步策略（照抄 0061_seed_mail_setting_permission，**只增不减**）：
    - 权限点：缺失则插入；已存在则更新名称/模块等元信息（以代码目录为准）。
    - 内置角色：只补齐目录声明中缺失的权限，**不回收**多余权限
      （内置角色可能被运营在界面上手动调整过）。

授予面（刻意的）：
    - `super_admin`：持有全部权限；
    - `content_admin`：**不含 message 模块** → 不授予。群发系统通知是面向全体用户的触达能力，
      属于运营/平台侧决策，不该随「内容编辑」权限一起下发；
    - `viewer`：只收 `:view` 码 → 自动获得**查看**已发记录（只读管理员能核对「发过什么」）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.services.rbac_catalog import PERMISSION_CATALOG, SYSTEM_ROLES

revision: str = "seed_message_permission"
down_revision: Union[str, None] = "add_message_batch_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 本次新增的权限码（downgrade 只回收这些）
_NEW_CODES = ("message:view", "message:send")


def upgrade() -> None:
    conn = op.get_bind()
    import secrets as _secrets

    def _gen_id() -> int:
        return _secrets.randbelow(9_000_000_000) + 1_000_000_000

    # 1) 权限点：以代码目录为唯一事实来源，缺失插入 / 已存在刷新元信息
    existing = {row.code for row in conn.execute(sa.text("SELECT code FROM permissions"))}
    for p in PERMISSION_CATALOG:
        if p.code in existing:
            conn.execute(
                sa.text(
                    "UPDATE permissions SET name=:name, type=:type, "
                    "module=:module, module_name=:module_name, sort=:sort WHERE code=:code"
                ).bindparams(
                    name=p.name,
                    type=p.type,
                    module=p.module,
                    module_name=p.module_name,
                    sort=p.sort,
                    code=p.code,
                )
            )
        else:
            conn.execute(
                sa.text(
                    "INSERT INTO permissions (id, code, name, type, module, module_name, sort) "
                    "VALUES (:id, :code, :name, :type, :module, :module_name, :sort)"
                ).bindparams(
                    id=_gen_id(),
                    code=p.code,
                    name=p.name,
                    type=p.type,
                    module=p.module,
                    module_name=p.module_name,
                    sort=p.sort,
                )
            )

    code_to_perm_id = {
        row.code: row.id for row in conn.execute(sa.text("SELECT id, code FROM permissions"))
    }
    code_to_role_id = {
        row.code: row.id for row in conn.execute(sa.text("SELECT id, code FROM roles"))
    }

    # 2) 内置角色：补齐目录声明中缺失的权限（不回收多余权限）
    for role in SYSTEM_ROLES:
        role_id = code_to_role_id.get(role["code"])
        if role_id is None:
            continue
        granted = {
            row.code
            for row in conn.execute(
                sa.text(
                    "SELECT p.code FROM role_permissions rp "
                    "JOIN permissions p ON p.id = rp.permission_id "
                    "WHERE rp.role_id = :rid"
                ).bindparams(rid=role_id)
            )
        }
        missing = [c for c in role["permissions"] if c not in granted]
        for code in missing:
            perm_id = code_to_perm_id.get(code)
            if perm_id is None:
                continue
            conn.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission_id) VALUES (:rid, :pid)"
                ).bindparams(rid=role_id, pid=perm_id)
            )


def downgrade() -> None:
    """仅回收本次新增的权限点与关联，不触碰其他权限。"""
    conn = op.get_bind()
    for code in _NEW_CODES:
        perm_row = conn.execute(
            sa.text("SELECT id FROM permissions WHERE code = :code").bindparams(code=code)
        ).first()
        if perm_row is None:
            continue
        conn.execute(
            sa.text("DELETE FROM role_permissions WHERE permission_id = :pid").bindparams(
                pid=perm_row.id
            )
        )
        conn.execute(
            sa.text("DELETE FROM permissions WHERE id = :pid").bindparams(pid=perm_row.id)
        )
