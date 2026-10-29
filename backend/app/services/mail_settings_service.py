"""邮件通知（SMTP 通道）：从「系统设置 → 邮件通知」抽出的独立模块（2026-10-27）。

抽出的理由不是配置项多（7 项），而是它**默认就是坏的**：
不填 SMTP，生产环境发验证码直接失败，用户点「获取验证码」只看到一句「发送失败」——
而原来的设置表单只能填参数，无法回答「现在到底能不能发出去」。
所以这个模块除了定义/值，还提供两件表单给不了的东西：

1. **通道状态**（`channel_status`）：缺哪几项、有没有加密、发件人是谁；
2. **发送测试邮件**（`send_test_email`）：用与真实验证码**完全相同**的连接参数
   （`utils/mailer.connect_kwargs`）发一封，成功/失败都给出可读原因。

职责边界：
- 只读写 `SURFACE_MAIL` 归属的设置键（越界 key 直接 ValueError）；
- 绝不回传授权码，状态里只说「配没配」；
- 不承担验证码业务（发码、频控、校验仍在 auth / verification）。
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.setting_definitions import SURFACE_MAIL
from app.services.setting_service import (
    definitions_payload,
    get_stored_settings,
    groups_payload,
    keys_for_surface,
    update_settings,
)
from app.utils.mailer import missing_fields, send_plain_email, sender_address, tls_mode
from app.utils.verification import is_email

#: 本模块负责的设置键（= `mail` 分组全部定义）
MAIL_SETTING_KEYS: frozenset[str] = frozenset(keys_for_surface(SURFACE_MAIL))

#: 测试邮件的进程内冷却（秒）：按钮点两下就发两封，容易被当成「垃圾邮件行为」
TEST_COOLDOWN_SECONDS = 10
_last_test_at: float = 0.0


def definitions() -> list[dict]:
    return definitions_payload(surface=SURFACE_MAIL)


def groups() -> list[dict]:
    return groups_payload(surface=SURFACE_MAIL)


async def stored_values(db: AsyncSession) -> dict[str, str]:
    """已显式配置的值（授权码是掩码）"""
    return await get_stored_settings(db, surface=SURFACE_MAIL)


async def update(db: AsyncSession, items: dict) -> int:
    """保存邮件通道配置；越界键（例如 cos_secret_key）直接 ValueError"""
    return await update_settings(db, items, surface=SURFACE_MAIL)


def channel_status() -> dict[str, Any]:
    """通道状态：能否发信 + 缺什么 + 加密方式 + 发件人

    `ready=False` 时**不是**「发信会报错」，而是「用户收不到验证码、界面只说失败」——
    这是本模块最需要说清楚的一句话，所以放进 `issues`。
    """
    missing = missing_fields()
    mode = tls_mode()
    issues: list[str] = []
    if missing:
        issues.append(f"还缺 {len(missing)} 项：{'、'.join(missing)}")
        issues.append(
            "未配齐时生产环境发不出验证码：用户点「获取验证码」只会看到发送失败，"
            "注册与找回密码都走不通"
        )
    elif mode == "none":
        issues.append("未启用任何加密（既不是 465 的 SSL，也没开 STARTTLS）：多数邮箱服务商会拒收")

    return {
        "ready": not missing,
        # 只给「主机:端口」，不给账号与授权码
        "endpoint": f"{settings.SMTP_HOST}:{settings.SMTP_PORT}" if settings.SMTP_HOST else "",
        "tls": mode,
        "sender": sender_address(),
        "missing": missing,
        "issues": issues,
    }


async def overview(db: AsyncSession) -> dict[str, Any]:
    """邮件通知页的完整载荷：定义 + 分组 + 值 + 通道状态"""
    return {
        "definitions": definitions(),
        "groups": groups(),
        "values": await stored_values(db),
        "channel": channel_status(),
    }


def validate_recipient(to_email: str) -> str:
    """校验收件地址

    复用 `utils/verification.is_email`（账号格式的唯一定义点）：这里另写一条正则，
    就会出现「注册允许的地址、测试邮件说格式错误」这种两套规则。
    """
    address = (to_email or "").strip()
    if not address or len(address) > 254 or not is_email(address):
        raise ValueError("请填写正确的邮箱地址")
    return address


def check_rate_limit() -> None:
    """进程内冷却；跨 worker 不共享（4 个 worker 最多各发一封，够用且不引入 Redis 依赖）"""
    global _last_test_at
    now = time.monotonic()
    if _last_test_at and now - _last_test_at < TEST_COOLDOWN_SECONDS:
        wait = int(TEST_COOLDOWN_SECONDS - (now - _last_test_at)) + 1
        raise RuntimeError(f"发送太频繁，请 {wait} 秒后再试")
    _last_test_at = now


def reset_rate_limit() -> None:
    """测试用：清掉冷却时间"""
    global _last_test_at
    _last_test_at = 0.0


async def send_test_email(to_email: str) -> tuple[bool, str]:
    """发送测试邮件，返回 (是否成功, 失败原因)。

    内容刻意写清「这是后台测试邮件」：运维漏关测试入口时，收件人一眼能看出不是用户行为。
    """
    address = validate_recipient(to_email)
    body = (
        "这是一封来自后台「系统 → 邮件通知」的测试邮件。\n\n"
        f"发信配置：{settings.SMTP_HOST}:{settings.SMTP_PORT}（{tls_mode()}）\n"
        f"发件人：{sender_address()}\n\n"
        "能收到这封邮件，说明注册验证码 / 找回密码的邮件通道是通的。\n"
        "如非本人操作，请检查后台「操作审计」里的「发送测试邮件」记录。"
    )
    return await send_plain_email(address, "【AI学习猫】邮件通道测试", body)
