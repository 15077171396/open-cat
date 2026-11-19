"""IP 黑名单权限补种：ip_blacklist:view / ip_blacklist:update

Revision ID: seed_ip_blacklist_permission
Revises: add_ops_metric_snapshots
Create Date: 2026-11-19

为什么需要这条迁移：
    权限点只在建表时按当时的 PERMISSION_CATALOG 播种过一次，此后新增的码**不会
    自动出现在已有部署的库里** —— 代码里有接口、路由规则也登记了，但没有任何角色
    持有它，管理员点进去直接 403。本次新增 IP 黑名单模块的两个码（运营级访问封禁，
    独立授权面，不与 setting:update 混用）。

同步逻辑收拢在 `app/services/rbac_seed_service.py`（**只增不减**）。授予面：
super_admin 全量；viewer 按目录派生规则自动获得 ip_blacklist:view。
"""

from typing import Sequence, Union

import asyncio

from app.services.rbac_seed_service import revoke_permission_codes, seed_permission_codes

revision: str = "seed_ip_blacklist_permission"
down_revision: Union[str, None] = "add_ops_metric_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 本次新增的权限码（downgrade 只回收这些）
_NEW_CODES = ("ip_blacklist:view", "ip_blacklist:update")


def upgrade() -> None:
    asyncio.run(seed_permission_codes(_NEW_CODES))


def downgrade() -> None:
    asyncio.run(revoke_permission_codes(_NEW_CODES))
