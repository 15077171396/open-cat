"""密码强度策略（2026-11-19）：注册 / 管理员建号 / 修改密码统一校验。

## 为什么收口成一个函数

RuoYi / yudao 都把强度校验写死在代码里（最小 5 位），改一次要发版。这里把
「最小长度」「必须同时含字母和数字」做成后台可调配置（系统设置 → 密码策略），
**所有密码写入点都从这一个函数过** —— 校验口径漂移（注册严、改密松）是安全
策略最常见的漏洞形态。

## 语义

- 最小长度：`PASSWORD_MIN_LENGTH`（6~64，默认 8）；
- 混合要求：`PASSWORD_REQUIRE_MIXED`（默认开）= 必须同时含**字母**与**数字**；
- 违规抛 400，message 直接给用户看（「至少 8 位」「需同时包含字母和数字」）；
- **刻意不做**强度打分 / 禁用弱口令字典：后者需要维护词库且引入误伤面，
  当前规模下长度 + 混合已是性价比最高的一档（NIST 800-63B 的最小建议）。
"""

from __future__ import annotations

import re

from app.config import settings
from app.core.exceptions import AppException

_LETTER_RE = re.compile(r"[A-Za-z]")
_DIGIT_RE = re.compile(r"[0-9]")


def validate_password_strength(password: str) -> None:
    """校验密码强度；不合格抛 400（message 面向用户）。合规原样返回。"""
    password = password or ""
    min_length = settings.PASSWORD_MIN_LENGTH

    if len(password) < min_length:
        raise AppException(
            code=400,
            message=f"密码长度至少 {min_length} 位（当前 {len(password)} 位）",
        )

    if settings.PASSWORD_REQUIRE_MIXED and not (_LETTER_RE.search(password) and _DIGIT_RE.search(password)):
        raise AppException(code=400, message="密码需同时包含字母和数字")

    # 控制字符/空白包裹是手滑形态：显式拒绝而不是默默截断
    if password.strip() != password:
        raise AppException(code=400, message="密码首尾不能包含空白字符")
