"""登录管理：登录方式开关 / 人机验证（2026-10-27 从系统设置抽出）。

抽出的动机与「模型管理」同理：这批配置原先混在几十个系统设置项里，
它既需要**独立的权限面**（客服/运营要能开关登录方式，但不该同时拿到
COS 密钥的写权限），也需要**比表单更多的信息**
（「开关开着但配置不齐」= 页面上看起来已启用、实际用户点进去必然失败）。

职责边界：
- 只读写 `SURFACE_LOGIN` 归属的设置键（越界 key 直接报错，见 `update_settings(surface=...)`）；
- 只读地汇总各登录渠道的**可用性**，绝不回传任何凭据片段。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.integration_settings_service import (
    IntegrationConfig,
    get_auth_config,
)
from app.services.setting_definitions import SURFACE_LOGIN
from app.services.setting_service import (
    definitions_payload,
    get_stored_settings,
    groups_payload,
    keys_for_surface,
    update_settings,
)

#: 本模块负责的设置键（= auth 分组全部定义）
LOGIN_SETTING_KEYS: frozenset[str] = frozenset(keys_for_surface(SURFACE_LOGIN))


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


def channel_overview(config: IntegrationConfig) -> list[dict[str, Any]]:
    """各登录渠道的可用性摘要（管理页顶部状态卡的数据源）"""
    channels: list[dict[str, Any]] = []

    # 邮箱 + 密码：始终可用，没有开关（列出它是为了让页面上的「登录方式」是完整清单）
    channels.append(
        {
            "key": "email",
            "label": "邮箱密码登录",
            "enabled": True,
            "ready": True,
            "detail": "基础登录方式，无开关",
            "issues": [],
        }
    )

    # 登录图形验证码：开关开着即视为就绪（无外部凭据依赖）
    captcha_enabled = config.bool("login_captcha_enabled")
    channels.append(
        {
            "key": "captcha",
            "label": "登录图形验证码",
            "enabled": captcha_enabled,
            "ready": True,
            "detail": "开启后登录必须先通过图形验证码（服务端自绘，无外部依赖）",
            "issues": [],
        }
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
