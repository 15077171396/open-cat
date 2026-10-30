"""播种「回收站清理」定时任务的默认配置行

Revision ID: seed_recycle_bin_job
Revises: 8c014540ffa7
Create Date: 2026-10-30

背景：`JOB_SPECS` 新增 `purge_recycle_bin`（教程 90 天 / 评论 30 天，用户不自动清理）。
按 `scheduled_job_configs` 的约定，新增任务必须补一行默认配置，否则后台会处于
「库里没有这行」的灰区（看似默认值、第一次保存却走 INSERT）。

`INSERT IGNORE`：迁移可重复执行；已有部署若手工建过该行，不覆盖人工配置。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "seed_recycle_bin_job"
down_revision: Union[str, None] = "8c014540ffa7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "INSERT IGNORE INTO scheduled_job_configs "
            "(job_id, enabled, schedule_kind, hour, minute, interval_minutes, updated_by) "
            "VALUES ('purge_recycle_bin', 1, 'daily', 4, 50, NULL, 'migration')"
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM scheduled_job_configs WHERE job_id = 'purge_recycle_bin'")
    )
