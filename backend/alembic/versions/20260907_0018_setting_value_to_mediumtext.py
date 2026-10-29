"""system_settings.value 列升级为 MEDIUMTEXT

Revision ID: setting_value_mediumtext
Revises: add_invite_referral
Create Date: 2026-09-07

法律文档（隐私政策/用户协议/免责声明）以富文本 HTML 存储在
system_settings.value，易超 TEXT 64KB 上限导致保存失败，
升级为 MEDIUMTEXT (16MB)。
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'setting_value_mediumtext'
down_revision: Union[str, None] = 'add_invite_referral'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# MySQL MODIFY COLUMN 需保留完整列定义
_VALUE_COLUMN_COMMENT = '设置值（NULL=使用默认值）'


def upgrade() -> None:
    op.execute(
        "ALTER TABLE system_settings MODIFY COLUMN value "
        f"MEDIUMTEXT NULL COMMENT '{_VALUE_COLUMN_COMMENT}'"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE system_settings MODIFY COLUMN value "
        f"TEXT NULL COMMENT '{_VALUE_COLUMN_COMMENT}'"
    )
