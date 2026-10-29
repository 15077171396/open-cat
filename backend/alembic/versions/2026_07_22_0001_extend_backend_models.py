"""extend backend models for frontend alignment

Revision ID: 20260722_0001
Revises: c28b58f8b112
Create Date: 2026-07-22 00:00:00

新增表：
- favorites          用户收藏
- messages           用户消息
- learning_records   学习记录
- announcements      平台公告
- notification_settings 通知设置

修改表：
- users      新增 nickname / bio
- resources  新增 view_count
- plugins    新增 view_count
- nodes      新增 view_count / created_at / updated_at
- tools      新增 usage_count
- downloads  新增 resource_name / file_type
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260722_0001"
down_revision: Union[str, None] = "c28b58f8b112"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    """检查列是否已存在（MySQL 兼容）

    离线 --sql 模式下 MockConnection 不支持 inspect，返回 False 让所有
    add_column 都生成 SQL；在线模式下做真实检查以保证幂等。
    """
    bind = op.get_bind()
    try:
        inspector = sa.inspect(bind)
    except sa.exc.NoInspectionAvailable:
        return False
    if table not in inspector.get_table_names():
        return False
    cols = [c["name"] for c in inspector.get_columns(table)]
    return column in cols


def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    try:
        inspector = sa.inspect(bind)
    except sa.exc.NoInspectionAvailable:
        return False
    return table in inspector.get_table_names()


def upgrade() -> None:
    # ========== 1. 现有表新增列 ==========
    # users
    if not _column_exists("users", "nickname"):
        op.add_column("users", sa.Column("nickname", sa.String(50), nullable=True, comment="昵称"))
    if not _column_exists("users", "bio"):
        op.add_column("users", sa.Column("bio", sa.String(500), nullable=True, comment="个人简介"))

    # resources
    if not _column_exists("resources", "view_count"):
        op.add_column("resources", sa.Column("view_count", sa.Integer(), nullable=False, server_default="0", comment="浏览次数"))

    # plugins
    if not _column_exists("plugins", "view_count"):
        op.add_column("plugins", sa.Column("view_count", sa.Integer(), nullable=False, server_default="0", comment="浏览次数"))

    # nodes
    if not _column_exists("nodes", "view_count"):
        op.add_column("nodes", sa.Column("view_count", sa.Integer(), nullable=False, server_default="0", comment="浏览次数"))
    if not _column_exists("nodes", "created_at"):
        op.add_column("nodes", sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True))
    if not _column_exists("nodes", "updated_at"):
        op.add_column("nodes", sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True))

    # tools
    if not _column_exists("tools", "usage_count"):
        op.add_column("tools", sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0", comment="使用次数"))

    # downloads
    if not _column_exists("downloads", "resource_name"):
        op.add_column("downloads", sa.Column("resource_name", sa.String(200), nullable=True, comment="资源名称快照"))
    if not _column_exists("downloads", "file_type"):
        op.add_column("downloads", sa.Column("file_type", sa.String(20), nullable=True, comment="文件类型: pdf/archive/exe/other"))

    # ========== 2. 新建表 ==========
    if not _table_exists("favorites"):
        op.create_table(
            "favorites",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False, index=True, comment="用户ID"),
            sa.Column("target_type", sa.String(20), nullable=False, index=True, comment="tutorial/resource/plugin/node/workflow"),
            sa.Column("target_id", sa.BigInteger(), nullable=False, index=True, comment="目标ID"),
            sa.Column("title", sa.String(200), nullable=False, comment="目标标题快照"),
            sa.Column("description", sa.String(500), nullable=True, comment="目标描述快照"),
            sa.Column("cover", sa.String(500), nullable=True, comment="目标封面快照"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False, comment="收藏时间"),
            sa.UniqueConstraint("user_id", "target_type", "target_id", name="uq_user_target"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    if not _table_exists("messages"):
        op.create_table(
            "messages",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False, index=True, comment="接收用户ID"),
            sa.Column("sender_id", sa.BigInteger(), nullable=True, comment="发送者ID"),
            sa.Column("type", sa.String(20), nullable=False, index=True, comment="system/comment/like/private"),
            sa.Column("title", sa.String(200), nullable=False, comment="标题"),
            sa.Column("content", sa.Text(), nullable=False, comment="正文"),
            sa.Column("avatar", sa.String(500), nullable=True, comment="发送者头像URL"),
            sa.Column("link", sa.String(500), nullable=True, comment="关联页面路径"),
            sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("0"), comment="是否已读"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False, comment="发送时间"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    if not _table_exists("learning_records"):
        op.create_table(
            "learning_records",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False, index=True, comment="用户ID"),
            sa.Column("tutorial_id", sa.BigInteger(), nullable=False, index=True, comment="教程ID"),
            sa.Column("title", sa.String(200), nullable=False, comment="教程标题快照"),
            sa.Column("cover", sa.String(500), nullable=True, comment="教程封面快照"),
            sa.Column("current_chapter", sa.String(100), nullable=True, comment="当前章节名"),
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0", comment="学习进度 0-100"),
            sa.Column("status", sa.String(20), nullable=False, server_default="in_progress", index=True, comment="in_progress/completed"),
            sa.Column("last_study_at", sa.DateTime(), server_default=sa.func.now(), nullable=False, comment="上次学习时间"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("user_id", "tutorial_id", name="uq_user_tutorial"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    if not _table_exists("announcements"):
        op.create_table(
            "announcements",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("title", sa.String(200), nullable=False, comment="标题"),
            sa.Column("content", sa.Text(), nullable=False, comment="正文"),
            sa.Column("tag", sa.String(20), nullable=False, server_default="公告", comment="标签：公告/活动/资讯"),
            sa.Column("color", sa.String(20), nullable=False, server_default="#3b82f6", comment="标签颜色"),
            sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.text("1"), index=True, comment="是否发布"),
            sa.Column("published_at", sa.DateTime(), nullable=True, comment="发布时间"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )

    if not _table_exists("notification_settings"):
        op.create_table(
            "notification_settings",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), nullable=False, unique=True, index=True, comment="用户ID"),
            sa.Column("system", sa.Boolean(), nullable=False, server_default=sa.text("1"), comment="系统通知"),
            sa.Column("comment", sa.Boolean(), nullable=False, server_default=sa.text("1"), comment="评论回复"),
            sa.Column("like", sa.Boolean(), nullable=False, server_default=sa.text("1"), comment="点赞"),
            sa.Column("private", sa.Boolean(), nullable=False, server_default=sa.text("1"), comment="私信"),
            sa.Column("email", sa.Boolean(), nullable=False, server_default=sa.text("0"), comment="邮件通知"),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )


def downgrade() -> None:
    op.drop_table("notification_settings")
    op.drop_table("announcements")
    op.drop_table("learning_records")
    op.drop_table("messages")
    op.drop_table("favorites")

    op.drop_column("downloads", "file_type")
    op.drop_column("downloads", "resource_name")
    op.drop_column("tools", "usage_count")
    op.drop_column("nodes", "updated_at")
    op.drop_column("nodes", "created_at")
    op.drop_column("nodes", "view_count")
    op.drop_column("plugins", "view_count")
    op.drop_column("resources", "view_count")
    op.drop_column("users", "bio")
    op.drop_column("users", "nickname")
