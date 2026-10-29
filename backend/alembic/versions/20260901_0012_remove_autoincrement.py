"""去除所有表主键自增，改为应用层生成 10 位随机数字 ID

Revision ID: remove_autoincrement
Revises: add_point_transactions
Create Date: 2026-09-01 00:00:00

防爬虫枚举：原 BIGINT AUTO_INCREMENT 主键可被递增遍历（/users/1、/users/2...）。
改为应用层生成 10 位随机数字 ID（app.database.generate_id）后，
数据库不再自增，新旧数据共存（旧数据保持原 ID，新数据为随机 10 位数）。
"""

from typing import Sequence, Union

from alembic import op


revision: str = 'remove_autoincrement'
down_revision: Union[str, None] = 'add_point_transactions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 全部含自增主键的表
TABLES = [
    'users',
    'notification_settings',
    'auth_requests',
    'tutorials',
    'tutorial_netdisk_links',
    'tutorial_nodes',
    'resources',
    'resource_netdisk_links',
    'downloads',
    'tool_usage_logs',
    'tools',
    'nodes',
    'plugins',
    'plugin_sources',
    'favorites',
    'learning_records',
    'point_transactions',
    'messages',
    'categories',
    'announcements',
]


def upgrade() -> None:
    # MySQL: MODIFY 去掉 AUTO_INCREMENT 属性，主键索引与数据不受影响
    # 父表 id 列被外键引用（错误 1833），迁移期间临时关闭外键检查
    op.execute("SET FOREIGN_KEY_CHECKS = 0")
    for table in TABLES:
        op.execute(f"ALTER TABLE `{table}` MODIFY COLUMN `id` BIGINT NOT NULL")
    op.execute("SET FOREIGN_KEY_CHECKS = 1")


def downgrade() -> None:
    op.execute("SET FOREIGN_KEY_CHECKS = 0")
    for table in TABLES:
        op.execute(f"ALTER TABLE `{table}` MODIFY COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT")
    op.execute("SET FOREIGN_KEY_CHECKS = 1")
