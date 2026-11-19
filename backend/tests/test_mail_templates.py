"""验证码邮件模板契约测试。

钉住三条**破了不报错、只会悄悄失去意义**的契约：

1. **发出去的邮件必须有验证码**：渲染结果缺 {{code}} 的替换值时整份回退内置
   模板（配置错误最多退化为默认文案，绝不发出废邮件 —— 否则用户永远无法
   完成注册，且只会表现为「收到的邮件里没有验证码」这种最难排查的工单）。
2. **安全渲染**：占位符是 {{name}} 的字面替换（无表达式执行面）；
   未提供的变量占位符原样保留（可读，方便发现漏配）。
3. **三个写入点同一口径**：主题 / 纯文本正文 / HTML 设计稿的品牌字都取
   平台名称（SITE_NAME），改平台名一处生效。
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.config import settings
from app.core import mail_templates
from app.core.mail_templates import (
    FALLBACK_BODY,
    FALLBACK_SUBJECT,
    _safe_render,
    render_verify_body,
    render_verify_subject,
)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self._patches = [
            mock.patch.object(settings, "SITE_NAME", "测试站"),
            mock.patch.object(settings, "MAIL_VERIFY_SUBJECT", "【{{site_name}}】验证码 {{code}}"),
            mock.patch.object(settings, "MAIL_VERIFY_BODY", "您的验证码是 {{code}}，{{expire_minutes}} 分钟内有效。"),
        ]
        for patcher in self._patches:
            patcher.start()

    def tearDown(self):
        for patcher in self._patches:
            patcher.stop()

    def test_renders_all_variables(self):
        subject = render_verify_subject("123456")
        self.assertEqual(subject, "【测试站】验证码 123456")
        body = render_verify_body("123456")
        self.assertIn("123456", body)
        # body 模板只含 code/expire_minutes 两个变量（site_name 由主题与 HTML 品牌字承载）
        self.assertIn("5 分钟内有效", body)

    def test_unknown_placeholder_is_kept_verbatim(self):
        # 未提供的变量占位符原样保留（可读，方便发现漏配），而不是静默抹掉
        self.assertEqual(_safe_render("hi {{unknown_var}}", {}), "hi {{unknown_var}}")

    def test_missing_code_falls_back_to_builtin(self):
        """渲染结果缺验证码 → 整份回退内置模板（防事故兜底）"""
        with mock.patch.object(settings, "MAIL_VERIFY_SUBJECT", "欢迎注册 {{site_name}}"):
            subject = render_verify_subject("123456")
        self.assertEqual(subject, FALLBACK_SUBJECT.format(code="123456", site_name="测试站", expire_minutes="5"))

        with mock.patch.object(settings, "MAIL_VERIFY_BODY", "不含验证码的正文"):
            body = render_verify_body("123456")
        self.assertIn("123456", body)
        self.assertIn("分钟内有效", body)

    def test_expire_minutes_matches_code_expire(self):
        from app.utils.verification import CODE_EXPIRE

        body = render_verify_body("123456")
        self.assertIn(f"{CODE_EXPIRE // 60} 分钟内有效", body)

    def test_html_uses_site_name(self):
        from app.utils.verification import _build_email_html

        html = _build_email_html("123456")
        self.assertIn("测试站", html)
        self.assertIn("123456", html)
        self.assertNotIn("后台管理系统", html, "HTML 品牌字应取平台名称而不是写死")


class SendEmailIntegrationTests(unittest.TestCase):
    def test_send_email_uses_template(self):
        """send_email 的 Subject / 纯文本正文取自模板渲染结果"""
        import asyncio

        from app.utils import verification

        captured: dict = {}

        class _FakeMessage:
            def __setitem__(self, key, value):
                captured[key] = value

            def set_content(self, body, charset=None):
                captured["body"] = body

            def add_alternative(self, html, subtype=None, charset=None):
                captured["html"] = html

        async def scenario():
            with mock.patch.object(settings, "SMTP_HOST", "smtp.example.com"), \
                    mock.patch.object(settings, "SITE_NAME", "测试站"), \
                    mock.patch.object(settings, "MAIL_VERIFY_SUBJECT", "【{{site_name}}】验证码 {{code}}"):
                with mock.patch("aiosmtplib.send", new=mock.AsyncMock()):
                    with mock.patch.object(verification, "EmailMessage", _FakeMessage):
                        ok = await verification.send_email("user@example.com", "654321")
            return ok

        ok = asyncio.run(scenario())
        self.assertTrue(ok)
        self.assertEqual(captured["Subject"], "【测试站】验证码 654321")
        self.assertIn("654321", captured["body"])


if __name__ == "__main__":
    unittest.main()
