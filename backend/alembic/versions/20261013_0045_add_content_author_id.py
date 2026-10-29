"""内容新增「发布作者账号」引用（author_id），让改昵称/换头像能联动

Revision ID: add_content_author_id
Revises: add_updated_at_indexes
Create Date: 2026-10-13

背景（用户反馈）：
    后台改了自己的昵称、传了头像，但前台内容卡片上的作者还是旧名字。
    根因：此前作者只以 `author_name` / `author_avatar_url` **快照**存在内容行里，
    没有任何指向账号的引用，所以改资料不会回写历史内容。

本次改动：给 plugins / resources / nodes 各加 `author_id`（可空 + 索引），
    前台展示改为**以 author_id 指向的账号为准**（实时解析），
    快照列保留作为**作者账号被删除后的兜底**。

数据回填：
    用 `admin_audit_logs` 里 `content_create` 的操作者回填 author_id ——
    这是唯一能可靠推断「谁发布的」的历史来源；没有创建记录的行保持 NULL
    （展示继续用快照，行为与改动前一致）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_content_author_id'
down_revision: Union[str, None] = 'add_updated_at_indexes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (表名, 审计日志里的 target_type)
TABLES = (("plugins", "plugin"), ("resources", "resource"), ("nodes", "node"))


def upgrade() -> None:
    for table, _ in TABLES:
        op.add_column(
            table,
            sa.Column('author_id', sa.BigInteger(), nullable=True, comment='发布作者用户ID'),
        )
        op.create_index(f'ix_{table}_author_id', table, ['author_id'])

    # 回填：审计日志的 content_create 记录了「谁创建的」
    conn = op.get_bind()
    for table, target_type in TABLES:
        conn.execute(
            sa.text(
                f"""
                UPDATE {table} x
                JOIN admin_audit_logs a
                  ON a.target_type = :tt AND a.target_id = x.id AND a.action = 'content_create'
                SET x.author_id = a.admin_id
                WHERE x.author_id IS NULL
                """
            ),
            {"tt": target_type},
        )


def downgrade() -> None:
    for table, _ in TABLES:
        op.drop_index(f'ix_{table}_author_id', table_name=table)
        op.drop_column(table, 'author_id')
