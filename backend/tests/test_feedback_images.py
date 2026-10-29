"""反馈截图：域名白名单 + 数量上限 + 允许只发图不写字。

为什么要有截图：「按钮点了没反应」「布局错位」这类问题，纯文字描述成本高还容易漏细节，
一张截图胜过两百字。但 `images` 由客户端提交，**不校验域名等于把反馈入口开放成任意图床**
（追踪像素 / 盗链 / 让别人替自己托管资源）。

规则与评论配图共用 `utils/image_urls.validate_uploaded_image_urls`，这里钉住反馈侧的行为，
避免以后改公共函数时只顾着评论那条链路。
"""

import unittest

from app.core.exceptions import AppException
from app.models.feedback import FEEDBACK_MAX_IMAGES, Feedback
from app.services import cos_service
from app.services.feedback_service import submit_feedback
from tests.fake_db import MemorySession


class FeedbackImageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # 用本服务真实会产出的前缀构造合法 URL（CDN 或默认 COS 域名，取决于配置）
        self.prefix = cos_service.public_url_prefixes()[0]
        self.url = f"{self.prefix}comments/2026/10/28/abc123.webp"

    async def test_image_only_feedback_allowed(self):
        """纯截图反馈必须能提交：强制写字只会让人随便敲几个字凑数"""
        db = MemorySession()
        result = await submit_feedback(db, 1, "", None, [self.url])
        self.assertIsNotNone(result["id"])
        rows = db.all_rows(Feedback)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["images"], [self.url])
        self.assertEqual(rows[0]["content"], "")

    async def test_external_image_url_rejected(self):
        db = MemorySession()
        with self.assertRaises(AppException) as ctx:
            await submit_feedback(db, 1, "看图", None, ["https://evil.example.com/x.jpg"])
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(db.all_rows(Feedback), [], "非法截图不得入库")

    async def test_too_many_images_rejected(self):
        db = MemorySession()
        urls = [f"{self.prefix}comments/a{i}.webp" for i in range(FEEDBACK_MAX_IMAGES + 1)]
        with self.assertRaises(AppException):
            await submit_feedback(db, 1, "多图", None, urls)
        self.assertEqual(db.all_rows(Feedback), [])

    async def test_duplicate_images_deduped(self):
        db = MemorySession()
        await submit_feedback(db, 1, "重复图", None, [self.url, self.url])
        self.assertEqual(db.all_rows(Feedback)[0]["images"], [self.url], "同一张图应去重而非报错")

    async def test_empty_text_and_no_image_rejected(self):
        db = MemorySession()
        for content, images in (("   ", []), ("", None)):
            with self.assertRaises(AppException, msg=f"空反馈必须被拒: {content!r}/{images!r}"):
                await submit_feedback(db, 1, content, None, images)
        self.assertEqual(db.all_rows(Feedback), [])

    async def test_images_not_a_list_rejected(self):
        """客户端传字符串（如单个 URL）不能被当成字符序列逐字校验"""
        db = MemorySession()
        with self.assertRaises(AppException) as ctx:
            await submit_feedback(db, 1, "看图", None, self.url)  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.code, 400)
