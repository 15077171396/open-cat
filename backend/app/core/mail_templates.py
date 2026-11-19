"""验证码邮件模板渲染（2026-11-19）：安全变量替换 + 缺验证码自动回退。

## 为什么不用 Jinja2 渲染用户可编辑的模板

模板正文由管理员在后台编辑并**加密落库**，渲染发生在发信时。Jinja2 会执行
模板内的表达式（SSTI 面），而这里需要的只是「把占位符换成值」——
`str.replace` 足够且零风险；占位符格式统一为 `{{name}}`。

## 防事故回退

验证码邮件里没有验证码 = 用户永远无法完成注册（线下事故）。所以渲染结果
必须包含 `{{code}}` 的替换值（6 位数字），否则**整份回退内置默认模板**
并在日志里 warning —— 配置错误最多退化为默认文案，绝不发出废邮件。

## 变量清单

- `{{code}}`：6 位数字验证码（必含，缺失即回退）
- `{{site_name}}`：平台名称（基础设置）
- `{{expire_minutes}}`：验证码有效期分钟数（与 verification.CODE_EXPIRE 同源换算）
"""

from __future__ import annotations

import re

from loguru import logger

from app.config import settings
from app.utils.verification import CODE_EXPIRE

# 占位符形态：{{name}}（两侧空白容忍）
_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")

# 内置默认模板（与 settings 默认值一致；回退时不读配置，直接用这份）
FALLBACK_SUBJECT = "【后台管理系统】注册验证码 {code}"
FALLBACK_BODY = "您的注册验证码为 {code}，{expire_minutes} 分钟内有效。"

_CODE_DIGITS = 6


def _safe_render(template: str, variables: dict[str, str]) -> str:
    """{{name}} → 值；未提供的变量占位符原样保留（可读，方便发现漏配）"""
    def _replace(match: re.Match) -> str:
        name = match.group(1)
        return variables.get(name, match.group(0))

    return _PLACEHOLDER.sub(_replace, template or "")


def _variables(code: str) -> dict[str, str]:
    return {
        "code": code,
        "site_name": settings.SITE_NAME or "后台管理系统",
        "expire_minutes": str(max(1, CODE_EXPIRE // 60)),
    }


def render_verify_subject(code: str) -> str:
    """渲染验证码邮件主题；缺验证码时回退内置模板。"""
    subject = _safe_render(settings.MAIL_VERIFY_SUBJECT, _variables(code))
    if not _contains_code(subject, code):
        logger.warning("验证码邮件主题渲染后缺少验证码，回退内置模板（请检查 mail_verify_subject 配置）")
        return FALLBACK_SUBJECT.format(**_variables(code))
    return subject


def render_verify_body(code: str) -> str:
    """渲染验证码邮件纯文本正文；缺验证码时回退内置模板。"""
    body = _safe_render(settings.MAIL_VERIFY_BODY, _variables(code))
    if not _contains_code(body, code):
        logger.warning("验证码邮件正文渲染后缺少验证码，回退内置模板（请检查 mail_verify_body 配置）")
        return FALLBACK_BODY.format(**_variables(code))
    return body


def _contains_code(text: str, code: str) -> bool:
    """正文必须包含完整验证码（6 位数字一次性出现即算）。"""
    return bool(code) and code in text
