"""邀请海报合成（Pillow）的离线单测。

重点钉住三件容易静默劣化的事：
1. 二维码内容必须是真的邀请注册链接（拼错域名/参数等于海报全废，且没人会发现）；
2. 出图必须是 1080×1440 的合法 PNG（尺寸变了朋友圈裁切会切掉二维码）；
3. 缺中文字体时必须**显式报错**，不能画出一堆方块还返回 200。

字体相关用例在有中文字体的环境才跑（CI 镜像未必装 fonts-noto-cjk）。
"""

import io
import os
import unittest
from unittest import mock

from PIL import Image

from app.config import settings
from app.services import invite_poster


def _has_cjk_font() -> bool:
    return any(os.path.exists(p) for p in invite_poster._FONT_BOLD)


@unittest.skipUnless(_has_cjk_font(), "当前环境没有中文字体（镜像需装 fonts-noto-cjk）")
class RenderTests(unittest.TestCase):
    def test_renders_png_with_design_size(self):
        png = invite_poster._render("CUKVZZQK", "小小怪下士", None)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"), "必须是合法 PNG")
        with Image.open(io.BytesIO(png)) as img:
            self.assertEqual(img.size, (invite_poster.WIDTH, invite_poster.HEIGHT))
            self.assertEqual(img.format, "PNG")
        self.assertGreater(len(png), 30_000, "出图过小通常意味着字体缺失/内容空白")

    def test_long_nickname_does_not_overflow(self):
        """超长昵称要收敛字号，不能顶穿画布"""
        name = "一个非常非常非常长的昵称用来验证排版不会溢出画布边界"
        png = invite_poster._render("ABCD1234", name, None)
        self.assertGreater(len(png), 30_000)

    def test_fit_font_never_below_floor(self):
        font = invite_poster._fit_font("很" * 80, 34, 200)
        self.assertGreaterEqual(font.size, 28, "字号收敛不能低于 28px 可读底线")

    def test_broken_avatar_falls_back(self):
        """坏头像不能抛出，返回 None 由渲染层改用首字底色"""
        self.assertIsNone(invite_poster._circle_avatar(b"not-an-image", 76))


class InviteUrlTests(unittest.TestCase):
    def test_qr_content_is_invite_link(self):
        url = invite_poster.invite_url("CUKVZZQK")
        self.assertTrue(url.endswith("/login?invite=CUKVZZQK"), url)
        self.assertIn("://", url)

    def test_trailing_slash_in_base_url_is_normalized(self):
        with mock.patch.object(settings, "SITE_BASE_URL", "https://example.com/"):
            self.assertEqual(
                invite_poster.invite_url("ABC"), "https://example.com/login?invite=ABC"
            )


class MissingFontTests(unittest.TestCase):
    def test_missing_font_raises_instead_of_drawing_boxes(self):
        """没有任何候选字体时必须抛错：画成方块也会返回 200，属于最难发现的劣化"""
        with mock.patch("os.path.exists", return_value=False):
            invite_poster._font.cache_clear()
            with self.assertRaises(RuntimeError) as ctx:
                invite_poster._font(40, True)
        self.assertIn("fonts-noto-cjk", str(ctx.exception))
        invite_poster._font.cache_clear()


if __name__ == "__main__":
    unittest.main()
