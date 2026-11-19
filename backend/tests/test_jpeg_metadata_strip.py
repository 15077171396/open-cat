"""JPEG 隐私元数据剥离的单元测试（S8 收尾项，2026-11-20）。

strip_jpeg_metadata 是纯二进制段拼接：不重编码、画质零损失；
解析异常一律降级为返回原字节（剥离是隐私加固，不是上传前置条件）。
测试用合成段序列 + Pillow 真实 JPEG 双路径验证。
"""

import io
import unittest

from PIL import Image

from app.utils.image_convert import strip_jpeg_metadata

_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"
_SOS = b"\xff\xda\x00\x02"  # SOS 段头本身只有 marker（压缩数据紧随其后）

# 段构造：FF marker + 2 字节大端长度（含自身）+ 载荷
def _seg(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + (len(payload) + 2).to_bytes(2, "big") + payload


_APP1_EXIF = _seg(0xE1, b"Exif\x00\x00GPS:39.9,116.4")   # 假 EXIF（含定位样貌）
_APP13_PS = _seg(0xED, b"Photoshop 3.0\x00edit-history")  # 假 PS 编辑历史
_APP2_ICC = _seg(0xE2, b"ICC_PROFILE\x00sRGB")            # ICC：必须保留（剥了会偏色）
_COM = _seg(0xFE, b"author: someone")                      # 注释段
_BODY = b"\x00\x11compressed-image-data"                   # SOS 后的压缩数据


class StripJpegMetadataTests(unittest.TestCase):
    def test_drops_exif_photoshop_and_comment_keeps_icc(self):
        jpeg = _SOI + _APP1_EXIF + _APP13_PS + _APP2_ICC + _COM + _SOS + _BODY + _EOI
        out = strip_jpeg_metadata(jpeg)
        self.assertNotIn(b"GPS:39.9", out, "EXIF（含定位样貌）必须整段消失")
        self.assertNotIn(b"edit-history", out, "PS 编辑历史必须整段消失")
        self.assertNotIn(b"author: someone", out, "注释段必须整段消失")
        self.assertIn(b"ICC_PROFILE", out, "ICC 必须保留（剥了会偏色）")
        self.assertIn(_BODY, out, "压缩数据原样保留（无损）")
        self.assertTrue(out.startswith(_SOI) and out.endswith(_EOI))

    def test_real_pillow_jpeg_loses_exif(self):
        buf = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(
            buf, "JPEG", exif=b"Exif\x00\x00MM\x00*" + b"\x00" * 20
        )
        original = buf.getvalue()
        self.assertIn(b"Exif", original, "测试前提：原图确实带了 EXIF")
        out = strip_jpeg_metadata(original)
        self.assertNotIn(b"Exif", out)
        # 剥离后仍是合法图片（Pillow 可开、尺寸不变）
        with Image.open(io.BytesIO(out)) as img:
            self.assertEqual(img.size, (2, 2))

    def test_non_jpeg_passthrough(self):
        self.assertEqual(strip_jpeg_metadata(b"\x89PNG\r\n\x1a\nnot-a-jpeg"), b"\x89PNG\r\n\x1a\nnot-a-jpeg")
        self.assertEqual(strip_jpeg_metadata(b""), b"")

    def test_malformed_structure_degrades_to_original(self):
        # 段长字段谎报（比剩余字节还大）→ 解析异常 → 原样返回，不阻断上传
        jpeg = _SOI + b"\xff\xe1\xff\xff" + b"garbage" + _EOI
        self.assertEqual(strip_jpeg_metadata(jpeg), jpeg)

    def test_truncated_soi_only(self):
        self.assertEqual(strip_jpeg_metadata(_SOI), _SOI)


if __name__ == "__main__":
    unittest.main()
