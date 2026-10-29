"""管理端/用户 TOTP 二次验证：users 增加 totp 相关列

Revision ID: add_user_totp
Revises: add_search_logs
Create Date: 2026-10-27

为什么需要这条迁移：
    后台是最高权限面（能改全站内容、看全量手机号邮箱、改积分、批删账号），
    而此前**只凭一个密码**就能进。密码泄露（撞库、复用、钓鱼）没有任何第二道门。

设计说明：
    - `totp_secret` 存**加密后的密钥**（`core/crypto.py`，SECRET_KEY 派生），
      不是明文：拖库者拿到密文也无法直接算出验证码。
    - `totp_enabled` 与 `totp_secret` **分开**：绑定时先写 secret 但保持 enabled=0，
      用户拿 App 验证一次通过才置 1。这样「扫了码但没验证」不会把账号锁死
      （否则密钥没存对 = 再也登录不进去）。
    - `totp_recovery_hashes` 存恢复码的 sha256（不存明文），一次性使用。
      没有恢复码的话，手机丢了就等于账号永久锁死（单机项目没有运维后台可救）。
    - 三列都可空 / 有默认值，**存量用户完全不受影响**（enabled 默认 0 = 不启用）。

⚠️ 迁移 head 变化：`tests/test_admin_audit.py` 里硬编码了 expected head（刻意棘轮），
    本文件已同步改为 `add_user_totp`。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_user_totp'
down_revision: Union[str, None] = 'add_search_logs'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('totp_secret', sa.String(length=255), nullable=True,
                  comment='TOTP 密钥(加密存储, NULL=未绑定)'),
    )
    op.add_column(
        'users',
        sa.Column('totp_enabled', sa.Boolean(), nullable=False, server_default='0',
                  comment='是否启用二次验证(1=登录需验证码)'),
    )
    op.add_column(
        'users',
        sa.Column('totp_recovery_hashes', sa.JSON(), nullable=True,
                  comment='恢复码哈希列表(一次性, NULL=无)'),
    )


def downgrade() -> None:
    # 回滚会**丢失全部用户的二次验证绑定**：这些人下次登录只需要密码。
    # 属安全性下降而非数据损坏，但仍应在降级说明里提示「回滚后建议全员重绑」。
    op.drop_column('users', 'totp_recovery_hashes')
    op.drop_column('users', 'totp_enabled')
    op.drop_column('users', 'totp_secret')
