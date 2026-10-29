"""支付与渠道：支付开关 / 微信支付 / 支付宝配置（2026-10-27 从系统设置抽出）。

抽出的理由与登录管理、法律文档同源，但这里的「信息面」尤其重要 —— 支付配置有两种
**不会报错、只会静默失效**的状态：

1. 渠道开着但凭据没配齐 → 前台不展示该渠道（用户看不到，运营以为「已经开了」）；
2. Mock 支付可用 → 下单直接标记为已支付。这在本地是便利，在任何对外环境都是灾难。

原设置表单只能显示「开关是开还是关」，这两种状态都读不出来。本模块把
`payments/factory.py` 的判定（`mock_allowed` / `wechat_ready` / `alipay_ready` /
`missing_requirements`）直接搬进页面顶部的状态卡，页面与实际下单链路**同一份判断**。

职责边界：
- 只读写 `SURFACE_PAYMENT` 归属的设置键（越界 key 整批拒绝）；
- 只读地汇总渠道可用性与风险提示，绝不回传商户号/私钥/APIv3 密钥的任何片段；
- 充值套餐、支付订单仍在「运营管理」下（那是业务数据，不是配置）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.integration_settings_service import IntegrationConfig, get_payment_config
from app.services.payments.factory import (
    CHANNEL_LABELS,
    alipay_ready,
    missing_requirements,
    mock_allowed,
    wechat_ready,
)
from app.services.setting_definitions import SURFACE_PAYMENT
from app.services.setting_service import (
    definitions_payload,
    get_stored_settings,
    groups_payload,
    keys_for_surface,
    update_settings,
)

#: 本模块负责的设置键（= 三个 payment_* 分组的全部定义）
PAYMENT_SETTING_KEYS: frozenset[str] = frozenset(keys_for_surface(SURFACE_PAYMENT))


def definitions() -> list[dict]:
    return definitions_payload(surface=SURFACE_PAYMENT)


def groups() -> list[dict]:
    return groups_payload(surface=SURFACE_PAYMENT)


async def stored_values(db: AsyncSession) -> dict[str, str]:
    """已显式配置的值（密钥为掩码）"""
    return await get_stored_settings(db, surface=SURFACE_PAYMENT)


async def update(db: AsyncSession, items: dict) -> int:
    """保存支付配置；越界键（例如 cos_secret_key）直接 ValueError"""
    return await update_settings(db, items, surface=SURFACE_PAYMENT)


def _channel(
    key: str,
    *,
    enabled: bool,
    ready: bool,
    detail: str,
    issues: list[str],
) -> dict[str, Any]:
    return {
        "key": key,
        "label": CHANNEL_LABELS[key],
        "enabled": enabled,
        "ready": ready,
        "detail": detail,
        "issues": issues,
    }


def channel_overview(config: IntegrationConfig) -> list[dict[str, Any]]:
    """各支付渠道的可用性（四态：关闭 / 可用 / 开着但配不齐 / 被总开关拦住）

    ⚠️ `ready` 的语义是「**现在真的能下单成功**」，因此包含总开关 ——
    与 `payments.factory.available_channels` 完全同口径（那边第一行就判总开关）。
    若这里只判「渠道自身就绪」，页面会出现「渠道显示可用、总开关关着、用户点不了」的错位。
    总开关关闭导致的不可用会写进 `issues`，避免只看到一个黄条却不知道为什么。
    """
    total = config.bool("payment_enabled")
    channels: list[dict[str, Any]] = []

    def gate(enabled: bool, ok: bool, issues: list[str]) -> tuple[bool, list[str]]:
        """把「渠道自身就绪」与「总开关」合成最终 ready"""
        if not enabled:
            return False, []
        if not total:
            return False, ["在线支付总开关已关闭（见上方第一项），该渠道虽已开启但暂不可用"] + issues
        return ok, issues

    # 模拟支付：开关 + 环境双重判断，是本页最需要说清楚的一项
    mock_enabled = config.bool("payment_mock_enabled")
    mock_ok = mock_allowed(config)
    env = settings.APP_ENV or "unknown"
    mock_issues: list[str] = []
    if mock_enabled:
        if mock_ok:
            mock_issues.append(f"当前环境（{env}）下 Mock 支付真实可用：下单会直接标记为已支付")
        else:
            mock_issues.append(f"当前环境（{env}）下已被服务端拒绝，开关不会生效")
    mock_ready, mock_issues = gate(mock_enabled, mock_ok, mock_issues)
    channels.append(
        _channel(
            "mock",
            enabled=mock_enabled,
            ready=mock_ready,
            detail=f"当前环境：{env}；仅本地/验收环境使用，生产环境一律拒绝",
            issues=mock_issues,
        )
    )

    # 微信支付
    wechat_enabled = config.bool("wechat_pay_enabled")
    wechat_missing = missing_requirements("wechat", config)
    wechat_issues = (
        [f"还缺 {len(wechat_missing)} 项必需配置（商户号 / AppID / 证书序列号 / APIv3 密钥 / 私钥 / 平台公钥）"]
        if wechat_enabled and wechat_missing
        else []
    )
    wechat_ready_now, wechat_issues = gate(wechat_enabled, wechat_ready(config), wechat_issues)
    channels.append(
        _channel(
            "wechat",
            enabled=wechat_enabled,
            ready=wechat_ready_now,
            detail="微信支付 v3 Native 扫码；回调地址留空时用站点根地址 + /api/v1/payments/notify/wechat",
            issues=wechat_issues,
        )
    )

    # 支付宝
    alipay_enabled = config.bool("alipay_enabled")
    alipay_missing = missing_requirements("alipay", config)
    alipay_issues = (
        [f"还缺 {len(alipay_missing)} 项必需配置（AppID / 应用私钥 / 支付宝公钥）"]
        if alipay_enabled and alipay_missing
        else []
    )
    alipay_ready_now, alipay_issues = gate(alipay_enabled, alipay_ready(config), alipay_issues)
    channels.append(
        _channel(
            "alipay",
            enabled=alipay_enabled,
            ready=alipay_ready_now,
            detail="支付宝电脑网站支付；私钥用于签名，支付宝公钥用于验签（两者别填反）",
            issues=alipay_issues,
        )
    )

    return channels


def warnings_of(config: IntegrationConfig, channels: list[dict[str, Any]]) -> list[dict[str, str]]:
    """整页级别的风险提示（比单渠道 issues 更靠上，因为它决定「充值能不能用」）

    `level` 语义：`danger` = 会导致错误收款/无法收款，`warn` = 配置无效或链路不通，
    `info` = 仅提示（如总开关关闭，属正常运营选择）。
    """
    result: list[dict[str, str]] = []
    enabled = config.bool("payment_enabled")
    usable = [c for c in channels if c["ready"]]

    if not enabled:
        result.append(
            {
                "level": "info",
                "text": "在线支付总开关已关闭：下单接口直接拒绝，前台不展示充值入口。",
            }
        )
        return result

    if mock_allowed(config):
        result.append(
            {
                "level": "danger",
                "text": (
                    f"Mock 支付在当前环境（{settings.APP_ENV}）真实可用 —— 任何用户点「支付」都会被直接标记为已支付。"
                    "如果这台机器对外提供服务，请立刻关闭它。"
                ),
            }
        )

    if not usable:
        result.append(
            {
                "level": "warn",
                "text": "支付总开关是打开的，但没有任何可用渠道：前台会展示充值入口，用户点下去必然失败。",
            }
        )

    broken = [c for c in channels if c["enabled"] and not c["ready"]]
    if broken and usable:
        result.append(
            {
                "level": "warn",
                "text": (
                    "以下渠道开关是打开的、但当前不可用（凭据没配齐，或被环境拒绝），"
                    "前台不会展示它们：" + "、".join(c["label"] for c in broken) + "。"
                ),
            }
        )
    return result


async def overview(db: AsyncSession) -> dict[str, Any]:
    """支付与渠道页的完整载荷：定义 + 分组 + 值 + 渠道可用性 + 风险提示"""
    config = await get_payment_config()
    channels = channel_overview(config)
    return {
        "definitions": definitions(),
        "groups": groups(),
        "values": await stored_values(db),
        "enabled": config.bool("payment_enabled"),
        "env": settings.APP_ENV,
        "channels": channels,
        "warnings": warnings_of(config, channels),
    }
