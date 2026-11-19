"""登录日志表 + 「登录日志清理」任务的默认配置行

Revision ID: add_login_logs
Revises: 0080_drop_commercial_modules
Create Date: 2026-11-18

背景：
    新增「登录日志」模块（RuoYi / yudao 等主流后台的标配）：登录成功 / 失败 /
    登出各写一行安全遥测（账号脱敏、IP、UA、失败原因），排查「账号是不是被人
    试过」时不再只能翻应用日志。配套：
    - `login_logs` 表（每小时由 cleanup_login_logs 清理，保留 90 天）；
    - `scheduled_job_configs` 播种 cleanup_login_logs 默认行（每 60 分钟）；
    - 权限码 login_log:view / login_log:clear 的补种在 0082 统一做
      （0058 同款「以代码目录为唯一事实来源」的全量 upsert）。

⚠️ 播种用 INSERT IGNORE：迁移可重复执行；已有部署若手工建过该行，不覆盖人工配置。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "add_login_logs"
down_revision: Union[str, None] = "0080_drop_commercial_modules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "login_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False,
                  comment="随机 10 位 ID（防枚举）"),
        sa.Column("user_id", sa.BigInteger(), nullable=True, comment="用户ID（失败尝试可能查无此人）"),
        sa.Column("account", sa.String(length=64), nullable=True, server_default="",
                  comment="登录账号（mask_account 脱敏后）"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="failed",
                  comment="success/failed/logout"),
        sa.Column("message", sa.String(length=255), nullable=False, server_default="",
                  comment="结果说明（失败原因等）"),
        sa.Column("ip", sa.String(length=64), nullable=True, comment="客户端 IP"),
        sa.Column("user_agent", sa.String(length=255), nullable=True, comment="User-Agent（截断）"),
        sa.Column("request_id", sa.String(length=36), nullable=True, comment="请求追踪 ID"),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP"), comment="事件时间（UTC）"),
        sa.Index("ix_login_logs_account_created", "account", "created_at"),
        sa.Index("ix_login_logs_user_created", "user_id", "created_at"),
        sa.Index("ix_login_logs_created_at", "created_at"),
        mysql_charset="utf8mb4",
        comment="登录日志（安全遥测；保留 90 天，每小时清理）",
    )

    # 播种「登录日志清理」任务的默认配置行（每 60 分钟）
    op.get_bind().execute(
        sa.text(
            "INSERT IGNORE INTO scheduled_job_configs "
            "(job_id, enabled, schedule_kind, hour, minute, interval_minutes, updated_by) "
            "VALUES ('cleanup_login_logs', 1, 'interval', NULL, NULL, 60, 'migration')"
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM scheduled_job_configs WHERE job_id = 'cleanup_login_logs'")
    )
    op.drop_table("login_logs")
