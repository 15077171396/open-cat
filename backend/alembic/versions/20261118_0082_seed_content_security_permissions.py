"""内容安全权限补种：login_log:view / login_log:clear / moderation:words

Revision ID: seed_content_security_permissions
Revises: add_login_logs
Create Date: 2026-11-18

为什么需要这条迁移：
    权限点只在建表时按当时的 PERMISSION_CATALOG 播种过一次，此后新增的码**不会
    自动出现在已有部署的库里** —— 代码里有接口、路由规则也登记了，但没有任何角色
    持有它，管理员点进去直接 403。本次新增：
    - `login_log:view` / `login_log:clear`（登录日志模块，2026-11-18）
    - `moderation:words`（敏感词库维护，2026-11-18）：词库内容直接决定 UGC 拦截
      边界，独立于 moderation:view / moderation:review。

同步逻辑收拢在 `app/services/rbac_seed_service.py`（**只增不减**）。授予面：
super_admin 全量；content_admin/viewer 按目录派生规则自动获得（viewer 得
login_log:view；content_admin 得 moderation:words）。
"""

from typing import Sequence, Union

import asyncio

from app.services.rbac_seed_service import revoke_permission_codes, seed_permission_codes

revision: str = "seed_content_security_permissions"
down_revision: Union[str, None] = "add_login_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 本次新增的权限码（downgrade 只回收这些）
_NEW_CODES = ("login_log:view", "login_log:clear", "moderation:words")


def upgrade() -> None:
    asyncio.run(seed_permission_codes(_NEW_CODES))


def downgrade() -> None:
    asyncio.run(revoke_permission_codes(_NEW_CODES))
