"""核心指标快照表 + 「核心指标快照」任务的默认配置行

Revision ID: add_ops_metric_snapshots
Revises: seed_content_security_permissions
Create Date: 2026-11-18

背景：
    新增每小时「核心指标快照」任务：把注册用户总量 / 教程总量 / 评论总量 /
    待处理反馈 / 待复核机审 / 近 1 小时登录失败等读数写入 `ops_metric_snapshots`
    一行（metrics 为 JSON，加指标不改表结构），看板新增「系统指标趋势」面板回看
    历史水位。配套 scheduled_job_configs 播种默认行（每 60 分钟）。

⚠️ 播种用 INSERT IGNORE：迁移可重复执行；已有部署若手工建过该行，不覆盖人工配置。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "add_ops_metric_snapshots"
down_revision: Union[str, None] = "seed_content_security_permissions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ops_metric_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False,
                  comment="随机 10 位 ID（防枚举）"),
        sa.Column("taken_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP"), comment="采样时间（UTC）"),
        sa.Column("metrics", sa.JSON(), nullable=True,
                  comment="指标键值对（键定义见 ops_metric_service.METRIC_LABELS）"),
        sa.Index("ix_ops_snapshots_taken_at", "taken_at"),
        mysql_charset="utf8mb4",
        comment="核心指标快照（每小时一帧；保留 180 天，写入前顺带清理）",
    )

    op.get_bind().execute(
        sa.text(
            "INSERT IGNORE INTO scheduled_job_configs "
            "(job_id, enabled, schedule_kind, hour, minute, interval_minutes, updated_by) "
            "VALUES ('ops_metric_snapshot', 1, 'interval', NULL, NULL, 60, 'migration')"
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM scheduled_job_configs WHERE job_id = 'ops_metric_snapshot'")
    )
    op.drop_table("ops_metric_snapshots")
