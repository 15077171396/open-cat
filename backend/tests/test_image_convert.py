"""上传转码的尺寸闸（第四轮全量审查 P1-63）。

Pillow 的默认 `MAX_IMAGE_PIXELS` 在 1×~2× 区间只发 **告警**、不抛异常，`except`
看不到它 —— 约 2 亿像素的纯色 PNG（文件本体几百 KB，既过 5MB 尺寸限制也过魔数
校验）照样解码，RGBA 化后编码要 ~0.8GB，`mem_limit: 768m` 的容器 OOM，
`restart: unless-stopped` 下整站 API crash loop。所以必须在**解码前**自己卡尺寸。

全部离线：用 Pillow 现场生成小图，不依赖网络与真实上传链路。
"""

import io
import unittest

from loguru import logger
from PIL import Image

from app.utils import image_convert


def _png_bytes(width: int = 64, height: int = 64) -> bytes:
    img = Image.new("RGB", (width, height))
    # 造一点结构（纯色 PNG 转 WebP 未必更小，会被「无体积收益」分支合法拒绝）
    for x in range(width):
        for y in range(height):
            img.putpixel((x, y), ((x * 7 + y * 13) % 256, (x * 3) % 256, (y * 5) % 256))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _capture_warnings():
    records: list[str] = []
    sink_id = logger.add(lambda m: records.append(str(m)), level="WARNING")
    return records, sink_id


class PixelBudgetGateTests(unittest.TestCase):
    def test_oversized_image_is_rejected_before_decoding(self):
        data = _png_bytes(64, 64)
        # 把上限压到 10 像素：同一张图就必须被拦下
        original = image_convert.MAX_DECODE_PIXELS
        image_convert.MAX_DECODE_PIXELS = 10
        records, sink_id = _capture_warnings()
        try:
            self.assertIsNone(image_convert.convert_png_to_webp(data))
        finally:
            logger.remove(sink_id)
            image_convert.MAX_DECODE_PIXELS = original
        self.assertTrue(
            any("超过转码上限" in r for r in records),
            f"没有走出尺寸闸告警：{records}",
        )

    def test_normal_image_is_not_blocked_by_the_gate(self):
        """默认上限下不得被尺寸闸拦掉（否则真实截图会全部退回原图上传，优化白做）"""
        data = _png_bytes(64, 64)
        records, sink_id = _capture_warnings()
        try:
            out = image_convert.convert_png_to_webp(data)
        finally:
            logger.remove(sink_id)
        self.assertFalse(
            any("超过转码上限" in r for r in records),
            f"64x64 的小图被尺寸闸误拦：{records}",
        )
        # out 允许为 None（无体积收益时保留原图是既有正确语义），但不能是异常
        if out is not None:
            self.assertTrue(out.startswith(b"RIFF"), "转码结果不是 WebP")

    def test_gate_threshold_leaves_realistic_headroom(self):
        """20M 像素 ≈ 4472x4472：手机截图（12MP）与 4K 长图都必须能通过"""
        self.assertGreaterEqual(image_convert.MAX_DECODE_PIXELS, 12_000_000)
        self.assertLess(
            image_convert.MAX_DECODE_PIXELS,
            178_900_000,  # 必须低于 Pillow 默认阈值，否则等于没防
        )


if __name__ == "__main__":
    unittest.main()
