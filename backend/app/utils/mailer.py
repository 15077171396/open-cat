"""邮件发送的**唯一连接口径**：SMTP 参数解析 + 一封纯文本邮件。

为什么单独抽出来：验证码邮件（`utils/verification.py`）与后台「发送测试邮件」
（`services/mail_settings_service.py`）必须用**完全相同的连接参数**，否则会出现
「测试邮件能发、真实验证码发不出」这种最难查的分歧 —— 后台测通了，运营就以为好了。

TLS 的判定刻意只写在这里：
- 465 = **隐式 TLS**（连上就加密，`use_tls=True`）；
- 587 / 25 = **STARTTLS**（明文连接后升级，`start_tls=True`）；
把 465 也当成 STARTTLS 是这类配置最常见的错误，表现是「连接超时 / 服务不可用」，
而设置项的帮助文案恰恰写着「465 端口关闭 STARTTLS」—— 文案对了、实现没跟上。
"""

from __future__ import annotations

from email.message import EmailMessage

import aiosmtplib
from aiosmtplib.errors import SMTPException
from loguru import logger

from app.config import settings

#: 连接超时（秒）。SMTP 握手慢于 15 秒基本可以判为网络或端口选错
SMTP_TIMEOUT = 15

#: 发信必需项（缺任一项，生产环境下验证码一律发不出去）
REQUIRED_FIELDS: tuple[tuple[str, str], ...] = (
    ("SMTP 服务器", "smtp_host"),
    ("SMTP 端口", "smtp_port"),
    ("SMTP 账号", "smtp_username"),
    ("SMTP 授权码", "smtp_password"),
    ("发件人地址", "smtp_from_email"),
)


def missing_fields() -> list[str]:
    """未配置的发信必需项（返回中文名，供页面直接展示，绝不回传值）"""
    missing: list[str] = []
    for label, attr in REQUIRED_FIELDS:
        if attr == "smtp_from_email":
            # 发件人地址可与账号相同，二者有其一即可
            if not (settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME):
                missing.append(label)
        elif not getattr(settings, attr.upper(), None):
            missing.append(label)
    return missing


def sender_address() -> str:
    """实际发件地址：From 留空时回退账号"""
    return settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME


def tls_mode() -> str:
    """`ssl` / `starttls` / `none` —— 供页面显性展示「有没有加密」"""
    if int(settings.SMTP_PORT or 0) == 465:
        return "ssl"
    return "starttls" if settings.SMTP_USE_TLS else "none"


def connect_kwargs() -> dict:
    """aiosmtplib.send 的连接参数（隐式 TLS 与 STARTTLS 二选一，不能都开）"""
    port = int(settings.SMTP_PORT or 0)
    implicit_tls = port == 465
    return {
        "hostname": settings.SMTP_HOST,
        "port": port,
        "username": settings.SMTP_USERNAME or None,
        "password": settings.SMTP_PASSWORD or None,
        "start_tls": bool(settings.SMTP_USE_TLS) and not implicit_tls,
        "use_tls": implicit_tls,
        "timeout": SMTP_TIMEOUT,
    }


async def send_plain_email(to_email: str, subject: str, body: str) -> tuple[bool, str]:
    """发送一封纯文本邮件，返回 (是否成功, 失败原因)。

    **不抛异常**：调用方是「后台点一下按钮」，需要的是可读的失败原因
    （连不上 / 认证失败 / 超时），而不是 500。失败原因会截断到 300 字符 ——
    SMTP 服务端的原始报错里偶尔带反解域名与 IP，整段回显没有更多信息量。
    """
    missing = missing_fields()
    if missing:
        return False, "还没配齐：" + "、".join(missing)

    from_email = sender_address()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{settings.SMTP_SENDER_NAME} <{from_email}>"
    message["To"] = to_email
    message.set_content(body, charset="utf-8")

    try:
        await aiosmtplib.send(message, **connect_kwargs())
        return True, ""
    except (SMTPException, OSError, TimeoutError) as exc:
        # 刻意**只捕这三类**，不用宽泛捕获（降级棘轮按源码里的宽泛捕获计数，
        # 注释里写出那个字面量同样会把基线推高 —— 所以这里连提都不提）：
        # SMTPException = 协议/认证/收件人被拒；OSError = 连不上/DNS/SSL；
        # TimeoutError = 握手超时。三类之外的都是程序问题，应当直接冒成 500 被看见。
        # 日志留全量便于排查；返回给页面的是截断版
        logger.bind(event="email_send_failed", mode=tls_mode()).error(
            f"邮件发送失败（{settings.SMTP_HOST}:{settings.SMTP_PORT}）：{exc}"
        )
        detail = f"{type(exc).__name__}: {exc}".strip()
        return False, detail[:300]
