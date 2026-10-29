"""管理审计补来源字段（IP / User-Agent / 请求关联 ID）

Revision ID: add_audit_source_fields
Revises: add_comment_reply_images
Create Date: 2026-10-11

背景（第三轮增量审查 P1-49③ / 台账 P2-19）：
    ``admin_audit_logs`` 此前只有「谁在什么时候做了什么」——
    **无 IP、无 UA、无请求关联 ID**。共享账号或被劫持的管理会话
    无法归因到来源（同一个人的两台设备无法区分），
    也无法把一条审计行与当时的应用日志 / nginx access log 对齐。

本迁移只加三个**可空**列（历史行为 NULL，前端按缺失展示）：
    - ``ip``：来源 IP，取可信代理解析后的客户端地址（IPv6 最长 45 字符）；
    - ``user_agent``：UA 原文截断 500 字符（足以区分浏览器/设备，不存全文）；
    - ``request_id``：与 ``app/core/request_context.py`` 生成的关联 ID 对齐，
      可在 app.log / Sentry / nginx access log 中按同一 ID 检索。

配套：``admin_audit_service.record_admin_action`` 写入时从请求上下文填充；
365 天保留策略由 scheduler 每日 04:00 的清理任务执行
（``cleanup_expired_audit_logs``）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_audit_source_fields'
down_revision: Union[str, None] = 'add_comment_reply_images'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'admin_audit_logs',
        sa.Column('ip', sa.String(45), nullable=True, comment='来源IP(IPv6最长45字符)'),
    )
    op.add_column(
        'admin_audit_logs',
        sa.Column('user_agent', sa.String(500), nullable=True, comment='User-Agent(截断500字符)'),
    )
    op.add_column(
        'admin_audit_logs',
        sa.Column('request_id', sa.String(64), nullable=True, comment='请求关联ID(与日志/nginx对齐)'),
    )


def downgrade() -> None:
    op.drop_column('admin_audit_logs', 'request_id')
    op.drop_column('admin_audit_logs', 'user_agent')
    op.drop_column('admin_audit_logs', 'ip')