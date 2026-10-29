"""AI 全站成本护栏：日/月调用上限 + 兜底单价（归属「系统 → 模型管理」）。

**为什么和模型管理同页**：模型管理抽出时（2026-10-27）只带走了模型与单模型单价，
把「全站上限 + 兜底单价」留在了系统设置的 `ai` 分组里。结果同一件成本问题被劈成两半：
调单模型单价时看不到全站限额，改兜底单价时看不到哪些模型已经单独定价 —— 而这两件事
在业务上必须**同时**看：把某模型单价填 0 就是「回退兜底单价」，兜底单价填 0 就是
「不估算金额」，三处（限额 / 兜底单价 / 模型单价）互相解释。

职责边界：
- 只读写 `SURFACE_MODEL` 归属的设置键（越界 key 直接 ValueError）；
- 只读地汇总**当前生效**的限额与用量（`ai_usage_service` 那一份，管理端摘要与真实限流
  同源，避免「页面显示还剩 900 次、实际第 200 次就被拒」）；
- 不碰 AI Provider 凭据（那是 `ai_model_service` / `ai_config_service` 的事）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai_usage_service import get_usage_summary
from app.services.setting_definitions import SURFACE_MODEL
from app.services.setting_service import (
    definitions_payload,
    get_stored_settings,
    groups_payload,
    keys_for_surface,
    update_settings,
)

#: 本模块负责的设置键（= `ai` 分组全部定义）
COST_SETTING_KEYS: frozenset[str] = frozenset(keys_for_surface(SURFACE_MODEL))


def definitions() -> list[dict]:
    """成本护栏的设置项定义（顺序即页面内字段顺序）"""
    return definitions_payload(surface=SURFACE_MODEL)


def groups() -> list[dict]:
    """分组声明（含说明文案）"""
    return groups_payload(surface=SURFACE_MODEL)


async def stored_values(db: AsyncSession) -> dict[str, str]:
    """已显式配置的值（这几项都不是密钥，不需要掩码）"""
    return await get_stored_settings(db, surface=SURFACE_MODEL)


async def update(db: AsyncSession, items: dict) -> int:
    """保存成本护栏；越界键（例如 cos_secret_key）直接 ValueError"""
    return await update_settings(db, items, surface=SURFACE_MODEL)


async def _quota_snapshot() -> dict[str, Any]:
    """当前生效的限额 + 实际用量（供页面显示「今天用了多少 / 还剩多少」）

    直接复用看板的汇总函数而不是自己读 Redis 键：
    ① 限额的「后台优先、.env 兜底」解析逻辑只有一处，重写必然漂移；
    ② 未设上限时 `remaining_*` 是 `None`（页面显示「未限制」），
       如果这里换算成 0 就会变成「0 次可用」——这是限流类配置最典型的误读。
    """
    summary = await get_usage_summary(days=1)
    today = summary["today"]
    month = summary["month"]
    limits = summary["limits"]
    pricing = summary["pricing"]
    return {
        "today_calls": today["calls"],
        "month_calls": month["calls"],
        "today_tokens": today["total_tokens"],
        "month_tokens": month["total_tokens"],
        "today_blocked": today["blocked"],
        "daily_limit": limits["daily"],
        "monthly_limit": limits["monthly"],
        # None = 未设上限（前端必须显示「未限制」，不能当成 0）
        "remaining_today": limits["remaining_today"],
        "remaining_month": limits["remaining_month"],
        "today_cost": today["estimated_cost"],
        "month_cost": month["estimated_cost"],
        "price_configured": pricing["configured"],
        "priced_models": len(pricing["per_model"]),
    }


async def overview(db: AsyncSession) -> dict[str, Any]:
    """模型管理页成本区块的完整载荷：定义 + 分组 + 值 + 当前限额/用量"""
    return {
        "definitions": definitions(),
        "groups": groups(),
        "values": await stored_values(db),
        "quota": await _quota_snapshot(),
    }
