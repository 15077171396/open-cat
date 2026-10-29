"""企业级 RBAC：角色/权限/关联表/部门 + users.department_id + 内置角色种子

Revision ID: add_rbac
Revises: add_resource_sort_index
Create Date: 2026-09-08 00:00:00

变更内容：
- departments           部门表（树形）
- roles                 RBAC 角色表（super_admin/content_admin/viewer 内置种子）
- permissions           权限点表（种子来自 app.services.rbac_catalog.PERMISSION_CATALOG）
- role_permissions      角色-权限关联
- user_roles            用户-角色关联
- users.department_id   用户部门归属

兼容性：存量 role='admin' 用户自动分配 super_admin 角色（部署后行为零变化）；
管理后台进入门槛仍是 users.role='admin'，RBAC 决定登录后的菜单与按钮可见性。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.services.rbac_catalog import PERMISSION_CATALOG, SYSTEM_ROLES

revision: str = 'add_rbac'
down_revision: Union[str, None] = 'add_resource_sort_index'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'departments',
        sa.Column('id', sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column('name', sa.String(length=50), nullable=False, comment='部门名称'),
        sa.Column('parent_id', sa.BigInteger(), nullable=True, comment='上级部门ID(空=顶级)'),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0', comment='排序值(小在前)'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False, comment='创建时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('parent_id', 'name', name='uq_departments_parent_name'),
    )
    op.create_index('ix_departments_parent_id', 'departments', ['parent_id'])

    op.create_table(
        'roles',
        sa.Column('id', sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column('code', sa.String(length=50), nullable=False, comment='角色编码(如 super_admin)'),
        sa.Column('name', sa.String(length=50), nullable=False, comment='角色名称'),
        sa.Column('description', sa.String(length=200), nullable=True, comment='角色说明'),
        sa.Column('is_system', sa.Boolean(), nullable=False, server_default='0', comment='内置角色(不可删除)'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False, comment='创建时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_roles_code'),
    )
    op.create_index('ix_roles_code', 'roles', ['code'])

    op.create_table(
        'permissions',
        sa.Column('id', sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column('code', sa.String(length=100), nullable=False, comment='权限码(如 tutorial:create)'),
        sa.Column('name', sa.String(length=100), nullable=False, comment='权限名称'),
        sa.Column('type', sa.String(length=20), nullable=False, comment='menu|button'),
        sa.Column('module', sa.String(length=50), nullable=False, comment='所属模块'),
        sa.Column('module_name', sa.String(length=50), nullable=False, comment='模块显示名'),
        sa.Column('sort', sa.Integer(), nullable=False, server_default='0', comment='排序值'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_permissions_code'),
    )
    op.create_index('ix_permissions_code', 'permissions', ['code'])
    op.create_index('ix_permissions_module', 'permissions', ['module'])

    op.create_table(
        'role_permissions',
        sa.Column('role_id', sa.BigInteger(), nullable=False),
        sa.Column('permission_id', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('role_id', 'permission_id'),
    )
    op.create_index('ix_role_permissions_permission_id', 'role_permissions', ['permission_id'])

    op.create_table(
        'user_roles',
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('role_id', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('user_id', 'role_id'),
    )
    op.create_index('ix_user_roles_role_id', 'user_roles', ['role_id'])

    op.add_column(
        'users',
        sa.Column('department_id', sa.BigInteger(), nullable=True, comment='部门ID(组织归属,不参与权限判定)'),
    )
    op.create_index('ix_users_department_id', 'users', ['department_id'])

    # ===== 种子数据 =====
    # 1) 权限点（来自代码目录，保证与运行时声明一致）
    permissions_table = sa.table(
        'permissions',
        sa.column('id', sa.BigInteger),
        sa.column('code', sa.String),
        sa.column('name', sa.String),
        sa.column('type', sa.String),
        sa.column('module', sa.String),
        sa.column('module_name', sa.String),
        sa.column('sort', sa.Integer),
    )
    import secrets as _secrets

    def _gen_id() -> int:
        return _secrets.randbelow(9_000_000_000) + 1_000_000_000

    perm_rows = [
        {
            'id': _gen_id(),
            'code': p.code, 'name': p.name, 'type': p.type,
            'module': p.module, 'module_name': p.module_name, 'sort': p.sort,
        }
        for p in PERMISSION_CATALOG
    ]
    op.bulk_insert(permissions_table, perm_rows)

    # 2) 内置角色 + 角色-权限关联
    roles_table = sa.table(
        'roles',
        sa.column('id', sa.BigInteger),
        sa.column('code', sa.String),
        sa.column('name', sa.String),
        sa.column('description', sa.String),
        sa.column('is_system', sa.Boolean),
    )
    role_permissions_table = sa.table(
        'role_permissions',
        sa.column('role_id', sa.BigInteger),
        sa.column('permission_id', sa.BigInteger),
    )
    conn = op.get_bind()
    code_to_perm_id = {
        row.code: row.id
        for row in conn.execute(sa.text('SELECT id, code FROM permissions'))
    }
    role_rows = []
    for r in SYSTEM_ROLES:
        role_rows.append({
            'id': _gen_id(),
            'code': r['code'], 'name': r['name'],
            'description': r['description'], 'is_system': True,
        })
    op.bulk_insert(roles_table, role_rows)
    code_to_role_id = {
        row.code: row.id
        for row in conn.execute(sa.text('SELECT id, code FROM roles'))
    }
    rp_rows = []
    for r in SYSTEM_ROLES:
        rid = code_to_role_id[r['code']]
        for pcode in r['permissions']:
            pid = code_to_perm_id.get(pcode)
            if pid is not None:
                rp_rows.append({'role_id': rid, 'permission_id': pid})
    if rp_rows:
        op.bulk_insert(role_permissions_table, rp_rows)

    # 3) 存量管理员（users.role='admin'）自动分配 super_admin：部署后行为零变化
    super_role_id = code_to_role_id['super_admin']
    op.execute(
        sa.text(
            'INSERT INTO user_roles (user_id, role_id) '
            "SELECT u.id, :rid FROM users u "
            "WHERE u.role = 'admin' AND u.deleted_at IS NULL "
            'AND NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.user_id = u.id AND ur.role_id = :rid)'
        ).bindparams(rid=super_role_id)
    )


def downgrade() -> None:
    op.drop_index('ix_users_department_id', table_name='users')
    op.drop_column('users', 'department_id')
    op.drop_index('ix_user_roles_role_id', table_name='user_roles')
    op.drop_table('user_roles')
    op.drop_index('ix_role_permissions_permission_id', table_name='role_permissions')
    op.drop_table('role_permissions')
    op.drop_table('permissions')
    op.drop_table('roles')
    op.drop_index('ix_departments_parent_id', table_name='departments')
    op.drop_table('departments')
