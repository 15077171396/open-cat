"""AI 对话调试台权限补种：新增 `ai_playground:view` 并补发给内置角色

Revision ID: seed_ai_playground_permission
Revises: seed_feedback_reply_permission
Create Date: 2026-11-03

为什么需要这条迁移：
    权限点只在建表时按当时的 PERMISSION_CATALOG 播种过一次，此后新增的码**不会
    自动出现在已有部署的库里** —— 代码里有接口、路由规则也登记了，但没有任何角色
    持有它，管理员点进去直接 403。

同步逻辑收拢在 `app/services/rbac_seed_service.py`（**只增不减**）。授权面：
super_admin 全量；content_admin 不含 AI 模块 → 不授予（对话消耗全站额度，
属平台侧决策）；viewer 按目录派生。
"""

from typing import Sequence, Union

import asyncio

from alembic import op

from app.services.rbac_seed_service import revoke_permission_codes, seed_permission_codes

revision: str = "seed_ai_playground_permission"
down_revision: Union[str, None] = "seed_feedback_reply_permission"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 本次新增的权限码（downgrade 只回收这些）
_NEW_CODES = ("ai_playground:view",)


def upgrade() -> None:
    asyncio.run(seed_permission_codes(_NEW_CODES))


def downgrade() -> None:
    asyncio.run(revoke_permission_codes(_NEW_CODES))
