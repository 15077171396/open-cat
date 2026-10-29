"""登录管理：登录方式开关 / 微信凭据 / 短信通道（2026-10-27 从系统设置抽出）。

抽出的动机与「模型管理」同理：这批配置原先混在几十个系统设置项里，
它既需要**独立的权限面**（客服/运营要能开关登录方式、看短信余量，但不该同时拿到
COS 密钥、支付商户私钥的写权限），也需要**比表单更多的信息**
（「开关开着但凭据没配」= 页面上看起来已启用、实际用户点进去必然失败）。

职责边界：
- 只读写 `SURFACE_LOGIN` 归属的设置键（越界 key 直接报错，见 `update_settings(surface=...)`）；
- 只读地汇总各登录渠道的**可用性**，绝不回传 AppID/Secret/短信凭据的任何片段；
- 小程序 AppID/Secret 自 2026-10-27 起也归本模块（`miniapp` 分组）。此前留在系统设置，
  页面标着「凭据未配齐，去系统设置→小程序填写」，要跨菜单才能改 —— 而这两个键的
  唯一消费方就是 `miniapp_service.code_to_session`（登录 / 绑定），归位后同屏可改。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.integration_settings_service import (
    IntegrationConfig,
    get_auth_config,
)
from app.services.setting_definitions import SETTING_DEFINITIONS, SURFACE_LOGIN
from app.services.setting_service import (
    definitions_payload,
    get_stored_settings,
    groups_payload,
    keys_for_surface,
    update_settings,
)

#: 本模块负责的设置键（= auth 分组全部定义）
LOGIN_SETTING_KEYS: frozenset[str] = frozenset(keys_for_surface(SURFACE_LOGIN))

#: 各短信服务商必须配齐的键（缺一项就发不出短信，且不会在页面上报错）
_SMS_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "tencent": (
        "sms_tencent_secret_id",
        "sms_tencent_secret_key",
        "sms_tencent_sdk_app_id",
        "sms_tencent_sign_name",
        "sms_tencent_template_id",
    ),
    "aliyun": (
        "sms_aliyun_access_key_id",
        "sms_aliyun_access_key_secret",
        "sms_aliyun_sign_name",
        "sms_aliyun_template_code",
    ),
}

SMS_PROVIDER_LABELS: dict[str, str] = {
    "mock": "Mock（仅本地联调，不会真正发短信）",
    "tencent": "腾讯云 SMS",
    "aliyun": "阿里云 SMS",
}


def definitions() -> list[dict]:
    """本模块的设置项定义（顺序即页面区块内字段顺序）"""
    return definitions_payload(surface=SURFACE_LOGIN)


def groups() -> list[dict]:
    """本模块的分组声明（含说明文案）"""
    return groups_payload(surface=SURFACE_LOGIN)


async def stored_values(db: AsyncSession) -> dict[str, str]:
    """已显式配置的值（密钥为掩码）"""
    return await get_stored_settings(db, surface=SURFACE_LOGIN)


async def update(db: AsyncSession, items: dict) -> int:
    """保存登录配置；越界键（例如 cos_secret_key）直接 ValueError"""
    return await update_settings(db, items, surface=SURFACE_LOGIN)


def _has_secret(config: IntegrationConfig, key: str) -> bool:
    """密钥是否已配置（只判断有无，明文不出本函数）"""
    return bool(config.secret(key))


#: 密文存储的键（判断「配没配」要解密后看有无，不能直接读密文）
_SECRET_KEYS: frozenset[str] = frozenset(d.key for d in SETTING_DEFINITIONS if d.secret)


def _is_configured(config: IntegrationConfig, key: str) -> bool:
    return _has_secret(config, key) if key in _SECRET_KEYS else bool(config.str(key))


def _missing_keys(config: IntegrationConfig, keys: tuple[str, ...]) -> list[str]:
    """未配置的键"""
    return [key for key in keys if not _is_configured(config, key)]


def _channel(
    key: str,
    label: str,
    *,
    enabled: bool,
    ready: bool,
    detail: str,
    issues: list[str],
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "enabled": enabled,
        "ready": ready,
        "detail": detail,
        "issues": issues,
    }


def channel_overview(config: IntegrationConfig) -> list[dict[str, Any]]:
    """各登录渠道的可用性摘要（管理页顶部状态卡的数据源）

    三态语义（界面上必须区分，否则「开关打开」会被误读成「能登录」）：
    - `enabled=False`：已关闭，前台不展示该入口；
    - `enabled=True, ready=True`：可用；
    - `enabled=True, ready=False`：**开着的坏状态** —— 用户能点、点了必失败，
      `issues` 说明缺什么，这是本摘要存在的首要理由。
    """
    channels: list[dict[str, Any]] = []

    # 邮箱 + 密码：始终可用，没有开关（列出它是为了让页面上的「登录方式」是完整清单）
    channels.append(
        _channel(
            "email",
            "邮箱密码登录",
            enabled=True,
            ready=True,
            detail="基础登录方式，无开关",
            issues=[],
        )
    )

    # 手机号验证码
    phone_enabled = config.bool("phone_login_enabled")
    provider = config.str("sms_provider") or "mock"
    provider_label = SMS_PROVIDER_LABELS.get(provider, provider)
    sms_issues: list[str] = []
    if provider == "mock":
        sms_issues.append("短信服务商为 Mock，线上不会真正下发验证码")
        sms_ready = False
    elif provider in _SMS_REQUIRED_KEYS:
        missing = _missing_keys(config, _SMS_REQUIRED_KEYS[provider])
        if missing:
            sms_issues.append(f"{provider_label} 还有 {len(missing)} 项凭据未配置")
        sms_ready = not missing
    else:
        sms_issues.append(f"未知的短信服务商：{provider}")
        sms_ready = False
    phone_issues = list(sms_issues) if phone_enabled else []
    channels.append(
        _channel(
            "phone",
            "手机号验证码登录",
            enabled=phone_enabled,
            ready=phone_enabled and sms_ready,
            detail=(
                f"短信通道：{provider_label}"
                + ("；首登自动注册已开启" if config.bool("phone_login_auto_register") else "")
            ),
            issues=phone_issues,
        )
    )

    # 微信扫码（网站应用）：凭据在本模块
    web_enabled = config.bool("wechat_web_login_enabled")
    web_missing = _missing_keys(config, ("wechat_web_appid", "wechat_web_secret"))
    web_issues = (
        [f"网站应用还缺 {len(web_missing)} 项凭据（AppID / AppSecret）"]
        if web_enabled and web_missing
        else []
    )
    channels.append(
        _channel(
            "wechat_web",
            "微信扫码登录",
            enabled=web_enabled,
            ready=web_enabled and not web_missing,
            detail="凭据：网站应用 AppID / AppSecret（在本页下方配置）",
            issues=web_issues,
        )
    )

    # 微信小程序：凭据在本模块（miniapp 分组，2026-10-27 从系统设置归位）
    mini_enabled = config.bool("wechat_mini_login_enabled")
    mini_missing = not all(
        _is_configured(config, key) for key in ("wechat_mini_appid", "wechat_mini_secret")
    )
    mini_issues: list[str] = []
    if mini_enabled and mini_missing:
        mini_issues.append("小程序 AppID / AppSecret 未配齐（在本页「小程序凭据」区块填写）")
    channels.append(
        _channel(
            "wechat_mini",
            "微信小程序登录",
            enabled=mini_enabled,
            ready=mini_enabled and not mini_missing,
            detail="凭据：本页「小程序凭据」（AppID / AppSecret）",
            issues=mini_issues,
        )
    )

    return channels


async def overview(db: AsyncSession) -> dict[str, Any]:
    """登录管理页的完整载荷：定义 + 分组 + 值 + 渠道可用性"""
    config = await get_auth_config()
    return {
        "definitions": definitions(),
        "groups": groups(),
        "values": await stored_values(db),
        "channels": channel_overview(config),
    }
