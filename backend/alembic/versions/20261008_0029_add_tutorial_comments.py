"""教程评论表（台账 P1-9）

Revision ID: add_tutorial_comments
Revises: add_fulltext_search
Create Date: 2026-10-08

背景：
    P1-9 指出的留存问题是「内容全是单向的，用户看完教程即离开，没有任何一处
    能让用户说话」。教程评论区是最小留存闭环——它天然产生长尾内容
    （提问与踩坑），不需要新建导航入口，风险最低。

设计取舍：
    - **软删除**（``deleted_at``）：站点有 ICP 备案，删除的 UGC 必须可追溯；
      同时保留管理员误删后的恢复能力（布尔位无法表达删除时间）。
    - **不做楼中楼**：一层回复会引入「父评论被删后子评论怎么显示」的孤儿问题
      （P1-7 已踩过同类坑），先把「能说、能看、能管」跑通。
    - 索引 ``(tutorial_id, deleted_at, created_at)`` 精确覆盖列表查询的固定形态
      「某教程下、未删除、按时间倒序」。

⚠️ 评论无外键：教程目前是**硬删除**，靠外键级联会触发 1451 拒绝删除。
    因此由 ``comment_service.purge_comments_for_tutorials`` 在同一事务内清理，
    已接入教程单条与批量删除路径。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_tutorial_comments'
down_revision: Union[str, None] = 'add_fulltext_search'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tutorial_comments',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('tutorial_id', sa.BigInteger(), nullable=False, comment='所属教程ID'),
        sa.Column('user_id', sa.BigInteger(), nullable=False, comment='评论作者ID'),
        sa.Column('content', sa.String(length=500), nullable=False, comment='评论正文(纯文本, 最多500字)'),
        sa.Column('deleted_at', sa.DateTime(), nullable=True, comment='软删除时间(NULL=正常)'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True, comment='发布时间'),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True, comment='最后更新时间'),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
    )
    op.create_index(
        'ix_tc_tutorial_deleted_created',
        'tutorial_comments',
        ['tutorial_id', 'deleted_at', 'created_at'],
    )
    op.create_index('ix_tc_user_created', 'tutorial_comments', ['user_id', 'created_at'])
    op.create_index('ix_tc_deleted_at', 'tutorial_comments', ['deleted_at'])


def downgrade() -> None:
    op.drop_index('ix_tc_deleted_at', table_name='tutorial_comments')
    op.drop_index('ix_tc_user_created', table_name='tutorial_comments')
    op.drop_index('ix_tc_tutorial_deleted_created', table_name='tutorial_comments')
    op.drop_table('tutorial_comments')
