"""新增数据字典（dict_types / dict_items）并播种常用类型

Revision ID: add_dictionaries
Revises: seed_recycle_bin_job
Create Date: 2026-10-30

字典管理用于把「内容状态 / 反馈状态 / 审核状态 / 用户状态」这类枚举集中到后台维护。
这里先播种与现有业务语义一致的四组默认值，页面安装后即可看到数据；
业务代码仍使用自己的常量，后续可逐步改为读字典。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.database import generate_id

revision: str = "add_dictionaries"
down_revision: Union[str, None] = "seed_recycle_bin_job"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SEEDS: list[tuple[str, str, str, list[tuple[str, str, int, bool, str]]]] = [
    (
        "content_status",
        "内容状态",
        "教程/章节的发布状态",
        [
            ("草稿", "draft", 0, False, "仅后台可见"),
            ("已发布", "published", 1, True, "前台可见"),
            ("已下架", "offline", 2, False, "前台不可见"),
        ],
    ),
    (
        "moderation_status",
        "机审状态",
        "内容机审留痕的复核结果",
        [
            ("待复核", "pending", 0, True, "等待人工处理"),
            ("通过", "approved", 1, False, ""),
            ("拒绝", "rejected", 2, False, ""),
        ],
    ),
    (
        "feedback_status",
        "反馈状态",
        "用户问题反馈的处理进度",
        [
            ("待处理", "pending", 0, True, ""),
            ("处理中", "processing", 1, False, ""),
            ("已处理", "resolved", 2, False, ""),
        ],
    ),
    (
        "user_status",
        "用户状态",
        "用户账号是否可用",
        [
            ("正常", "active", 0, True, ""),
            ("禁用", "disabled", 1, False, "禁止登录"),
        ],
    ),
]


def upgrade() -> None:
    op.create_table(
        "dict_types",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False, comment="类型编码（业务引用）"),
        sa.Column("name", sa.String(length=100), nullable=False, comment="显示名称"),
        sa.Column("description", sa.Text(), nullable=True, comment="说明"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1"), comment="是否启用"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_dict_types_code"),
    )
    op.create_index("ix_dict_types_code", "dict_types", ["code"], unique=False)

    op.create_table(
        "dict_items",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("type_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False, comment="显示标签"),
        sa.Column("value", sa.String(length=100), nullable=False, comment="存储值"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0"), comment="排序"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1"), comment="是否启用"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("0"), comment="是否默认项"),
        sa.Column("remark", sa.String(length=255), nullable=True, comment="备注"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["type_id"], ["dict_types.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("type_id", "value", name="uq_dict_items_type_value"),
    )
    op.create_index("ix_dict_items_type_id", "dict_items", ["type_id"], unique=False)

    # 播种默认字典（幂等：按 code / (type_id,value) 判重）
    conn = op.get_bind()
    for code, name, description, items in SEEDS:
        exists = conn.execute(
            sa.text("SELECT id FROM dict_types WHERE code = :code"), {"code": code}
        ).scalar()
        type_id = int(exists) if exists else generate_id()
        if not exists:
            conn.execute(
                sa.text(
                    "INSERT INTO dict_types (id, code, name, description, is_enabled) "
                    "VALUES (:id, :code, :name, :description, 1)"
                ),
                {"id": type_id, "code": code, "name": name, "description": description},
            )
        for label, value, sort_order, is_default, remark in items:
            item_exists = conn.execute(
                sa.text("SELECT id FROM dict_items WHERE type_id = :tid AND value = :value"),
                {"tid": type_id, "value": value},
            ).scalar()
            if item_exists:
                continue
            conn.execute(
                sa.text(
                    "INSERT INTO dict_items "
                    "(id, type_id, label, value, sort_order, is_enabled, is_default, remark) "
                    "VALUES (:id, :tid, :label, :value, :sort, 1, :default, :remark)"
                ),
                {
                    "id": generate_id(),
                    "tid": type_id,
                    "label": label,
                    "value": value,
                    "sort": sort_order,
                    "default": 1 if is_default else 0,
                    "remark": remark,
                },
            )


def downgrade() -> None:
    op.drop_index("ix_dict_items_type_id", table_name="dict_items")
    op.drop_table("dict_items")
    op.drop_index("ix_dict_types_code", table_name="dict_types")
    op.drop_table("dict_types")
