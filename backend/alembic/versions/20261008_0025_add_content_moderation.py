"""内容审核：敏感词命中留痕表 + 用户举报表

Revision ID: add_content_moderation
Revises: add_wechat_openid
Create Date: 2026-10-08

对应缺口台账 P0-5（UGC 无内容审核）。站点有 ICP 备案并提供 UGC，
此前零敏感词过滤、零审核留痕、零举报通道，属合规红线。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_content_moderation'
down_revision: Union[str, None] = 'add_wechat_openid'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'content_moderation_logs',
        sa.Column('id', sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column('content_type', sa.String(length=20), nullable=False,
                  comment='内容类型: feedback/nickname/bio/star_code/avatar'),
        sa.Column('target_id', sa.String(length=64), nullable=True, comment='内容定位ID(用户ID/反馈ID等)'),
        sa.Column('user_id', sa.BigInteger(), nullable=True, comment='内容作者ID'),
        sa.Column('excerpt', sa.Text(), nullable=True, comment='文本摘要(最多200字)'),
        sa.Column('hit_words', sa.String(length=500), nullable=True, comment='命中的敏感词(逗号分隔)'),
        sa.Column('action', sa.String(length=20), nullable=False, comment='处置动作: reject/mask/review'),
        sa.Column('status', sa.String(length=20), nullable=False,
                  server_default='pending', comment='复核状态: pending/approved/rejected'),
        sa.Column('reviewer_id', sa.BigInteger(), nullable=True, comment='复核管理员ID'),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True, comment='复核时间'),
        sa.Column('review_note', sa.String(length=500), nullable=True, comment='复核备注(如判定为误报)'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True, comment='命中时间'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cml_status_created', 'content_moderation_logs', ['status', 'created_at'])
    op.create_index('ix_cml_type_target', 'content_moderation_logs', ['content_type', 'target_id'])
    op.create_index('ix_content_moderation_logs_user_id', 'content_moderation_logs', ['user_id'])

    op.create_table(
        'content_reports',
        sa.Column('id', sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column('reporter_id', sa.BigInteger(), nullable=False, comment='举报人ID'),
        sa.Column('content_type', sa.String(length=20), nullable=False, comment='内容类型'),
        sa.Column('target_id', sa.String(length=64), nullable=False, comment='内容定位ID'),
        sa.Column('reason', sa.String(length=20), nullable=False,
                  comment='举报理由: spam/abuse/illegal/infringement/other'),
        sa.Column('detail', sa.String(length=500), nullable=True, comment='补充说明'),
        sa.Column('status', sa.String(length=20), nullable=False,
                  server_default='pending', comment='处理状态: pending/resolved/dismissed'),
        sa.Column('handler_id', sa.BigInteger(), nullable=True, comment='处理管理员ID'),
        sa.Column('handled_at', sa.DateTime(), nullable=True, comment='处理时间'),
        sa.Column('handle_note', sa.String(length=500), nullable=True, comment='处理备注(内部)'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True, comment='举报时间'),
        sa.PrimaryKeyConstraint('id'),
        # 同一用户对同一内容只允许举报一次，防刷举报
        sa.UniqueConstraint('reporter_id', 'content_type', 'target_id', name='uq_report_user_target'),
    )
    op.create_index('ix_cr_status_created', 'content_reports', ['status', 'created_at'])
    op.create_index('ix_cr_type_target', 'content_reports', ['content_type', 'target_id'])
    op.create_index('ix_content_reports_reporter_id', 'content_reports', ['reporter_id'])


def downgrade() -> None:
    op.drop_index('ix_content_reports_reporter_id', table_name='content_reports')
    op.drop_index('ix_cr_type_target', table_name='content_reports')
    op.drop_index('ix_cr_status_created', table_name='content_reports')
    op.drop_table('content_reports')

    op.drop_index('ix_content_moderation_logs_user_id', table_name='content_moderation_logs')
    op.drop_index('ix_cml_type_target', table_name='content_moderation_logs')
    op.drop_index('ix_cml_status_created', table_name='content_moderation_logs')
    op.drop_table('content_moderation_logs')
