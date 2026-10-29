"""内容软删除：tutorials / resources 增加 deleted_at（回收站）

Revision ID: add_content_soft_delete
Revises: seed_user_restore_permission
Create Date: 2026-10-27

为什么需要这条迁移：
    教程/资源此前是**硬删除** —— 后台一次误点，正文、章节树、网盘链接、购买记录
    一起消失，且无法恢复（审计里只剩一行「删除了《xxx》」）。运营侧最贵的错误不是
    「改错了」，而是「删掉了找不回来」。

    本迁移只加两列（可空 + 索引），**不改任何既有列、不回填数据**：
    存量行 `deleted_at = NULL`，语义天然等价于「未删除」，因此升级期间无需停写。

设计说明（改这里之前先读）：
    - 软删除时**同时把 is_published 置 0**。这一步是关键：公开端所有读取路径
      （列表 / 详情 / sitemap / 统计 / 百度推送）本来就在过滤 `is_published`，
      因此**不需要改动任何公开查询**就能保证「删掉的内容立刻不可见」。
      少改一处查询，就少一个「删了还在」的静默漏洞。
    - 索引单列 `deleted_at` 即可：后台列表的过滤条件是 `deleted_at IS NULL`
      （绝大多数行命中，走 is_published 索引或全表扫代价都可接受），
      回收站视图是 `deleted_at IS NOT NULL`（行数极少，索引足够）。

⚠️ 迁移 head 变化：`tests/test_admin_audit.py` 里硬编码了 expected head（刻意棘轮），
    本文件已同步改为 `add_content_soft_delete`。新增迁移时照做。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_content_soft_delete'
down_revision: Union[str, None] = 'seed_user_restore_permission'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 教程：软删除标记（NULL = 正常；非 NULL = 在回收站里）
    op.add_column(
        'tutorials',
        sa.Column(
            'deleted_at',
            sa.DateTime(),
            nullable=True,
            comment='软删除时间(回收站, NULL=正常)',
        ),
    )
    op.create_index(op.f('ix_tutorials_deleted_at'), 'tutorials', ['deleted_at'], unique=False)

    # 资源：同上
    op.add_column(
        'resources',
        sa.Column(
            'deleted_at',
            sa.DateTime(),
            nullable=True,
            comment='软删除时间(回收站, NULL=正常)',
        ),
    )
    op.create_index(op.f('ix_resources_deleted_at'), 'resources', ['deleted_at'], unique=False)


def downgrade() -> None:
    # 回滚会**永久丢弃回收站里的内容**（列一删，标记就没了，这些行会重新变成正常内容）。
    # 刻意不做「先物理删除 deleted_at IS NOT NULL 的行」：降级通常发生在出问题时，
    # 那一刻最不该做的就是顺手删数据。运维若确实要清理，先手工确认后再执行 DELETE。
    op.drop_index(op.f('ix_resources_deleted_at'), table_name='resources')
    op.drop_column('resources', 'deleted_at')
    op.drop_index(op.f('ix_tutorials_deleted_at'), table_name='tutorials')
    op.drop_column('tutorials', 'deleted_at')
