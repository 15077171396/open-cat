"""定时任务配置表 + 按目录播种默认行

Revision ID: scheduled_job_configs
Revises: add_content_views
Create Date: 2026-10-27

背景：
    「系统 → 定时任务」从只读升级为可管理（启停 / 改周期 / 立即执行一次）。
    调度器原本把周期写死在 `scheduler/tasks.py` 的 `add_job(...)` 里，想改一次
    就得改代码 + 重建镜像 + 重启容器；现在周期与开关落到这张表，调度器每 5 秒
    对齐一次并热重载。

设计取舍：
    - `job_id` 作自然主键（不加随机 id）：一行就是一个已在代码里注册的任务，
      不存在「两行对应同一个任务」的合法状态；
    - 只存「跑不跑 / 什么周期」，**不存「做什么」** —— handler 永远是代码；
    - 建表后立刻按 `JOB_SPECS` 播种 8 行默认配置。不播种的后果：页面显示的是
      「代码默认值」，但库里没有行，第一次保存会走 INSERT、且无法区分「从没配过」
      与「配成默认值」，排查时分不清。

⚠️ 播种用 `INSERT IGNORE`：迁移可重复执行（手工重跑不会因主键冲突中断）。
⚠️ 目录里将来新增任务，**必须再写一条迁移插默认行**（照抄本文件下半段）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.services.scheduled_job_service import JOB_SPECS

revision: str = 'scheduled_job_configs'
down_revision: Union[str, None] = 'add_content_views'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'scheduled_job_configs',
        sa.Column('job_id', sa.String(length=64), nullable=False,
                  comment='任务注册名，与 scheduled_job_service.JOB_SPECS 一致'),
        sa.Column('enabled', sa.Boolean(), server_default='1', nullable=False, comment='是否参与调度'),
        sa.Column('schedule_kind', sa.String(length=16), server_default='daily', nullable=False,
                  comment='daily / interval'),
        sa.Column('hour', sa.SmallInteger(), nullable=True, comment='daily：执行小时（北京时间 0-23）'),
        sa.Column('minute', sa.SmallInteger(), nullable=True, comment='daily：执行分钟（0-59）'),
        sa.Column('interval_minutes', sa.Integer(), nullable=True, comment='interval：间隔分钟（1-10080）'),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False,
                  comment='最后修改时间'),
        sa.Column('updated_by', sa.String(length=120), nullable=True, comment='最后修改者账号'),
        sa.PrimaryKeyConstraint('job_id'),
        mysql_charset='utf8mb4',
        comment='定时任务配置（期望状态；实际生效状态看 Redis ops:jobs:applied）',
    )

    # 按代码目录播种默认行：要让「库里有没有这行」不再是灰区
    conn = op.get_bind()
    for spec in JOB_SPECS:
        conn.execute(
            sa.text(
                'INSERT IGNORE INTO scheduled_job_configs '
                '(job_id, enabled, schedule_kind, hour, minute, interval_minutes) '
                'VALUES (:job_id, 1, :kind, :hour, :minute, :interval_minutes)'
            ).bindparams(
                job_id=spec.id,
                kind=spec.kind,
                hour=spec.hour if spec.kind == 'daily' else None,
                minute=spec.minute if spec.kind == 'daily' else None,
                interval_minutes=spec.interval_minutes if spec.kind == 'interval' else None,
            )
        )


def downgrade() -> None:
    """整表丢弃。

    刻意不做「把库里的配置回写进代码」：代码里的 `JOB_SPECS` 才是默认值的权威来源，
    回滚后调度器会按代码默认周期运行（等同「所有配置恢复出厂」）。
    """
    op.drop_table('scheduled_job_configs')
