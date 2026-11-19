"""上传图片服务端转码：超阈值 PNG 自动转有损 WebP（转码后更小才替换原图；
APNG 多帧动图跳过；任何异常静默降级为原图上传；纯 CPU 同步操作，调用方需经 asyncio.to_thread 移出事件循环）"""

from __future__ import annotations

from io import BytesIO

from loguru import logger
from PIL import Image

# 转码前主动做的尺寸闸。不能只依赖 Pillow 默认 MAX_IMAGE_PIXELS（≈1.79 亿，
# 该区间只告警不抛异常）：约 2 亿像素的纯色 PNG 解码 + RGBA 编码要 ~0.8GB 内存，
# 768m 容器直接 OOM 并 crash loop
MAX_DECODE_PIXELS = 20_000_000


def convert_png_to_webp(data: bytes, quality: int = 85) -> bytes | None:
    """PNG 字节流转有损 WebP；不满足转换条件 / 失败 / 无体积收益时返回 None"""
    try:
        with Image.open(BytesIO(data)) as img:
            # APNG 多帧动图：转换后只剩首帧，跳过保留原样
            if getattr(img, "is_animated", False):
                return None
            # 尺寸闸必须在 decode 之前：`img.size` 只读头部，不触发像素解码
            width, height = img.size
            pixels = width * height
            if pixels > MAX_DECODE_PIXELS:
                logger.warning(
                    f"PNG 尺寸 {width}x{height}（{pixels / 1_000_000:.1f}M 像素）"
                    f"超过转码上限 {MAX_DECODE_PIXELS // 1_000_000}M，保留原图上传"
                )
                return None
            buf = BytesIO()
            img.save(buf, "WEBP", quality=quality, method=4)
            out = buf.getvalue()
        # 无体积收益（极罕见，如纯噪点图）时保留原图
        if len(out) >= len(data):
            return None
        logger.info(f"PNG→WebP 转码: {len(data)} -> {len(out)} bytes "
                    f"({len(out) * 100 // len(data)}%)")
        return out
    except Exception as exc:  # noqa: BLE001 - 转码是纯优化，任何失败都不阻断上传
        logger.warning(f"PNG→WebP 转码失败，保留原图上传: {exc}")
        return None


# 要从 JPEG 里整段切除的段类型（2026-11-20，对标文档 S8 收尾项）：
# - APP1：EXIF（含 GPS 定位、拍摄设备序列号）与 XMP —— 用户配图不该把拍照地点带上网；
# - APP13：Photoshop IRB（8BIM，可能携带完整编辑历史与缩略图）；
# - COM：任意注释（可能含作者信息）。
# 刻意保留 APP2（ICC 色彩配置）：剥掉它会导致偏色，且不含隐私。
_JPEG_DROP_MARKERS = frozenset({0xE1, 0xED, 0xFE})  # APP1 / APP13 / COM


def strip_jpeg_metadata(data: bytes) -> bytes:
    """无损切除 JPEG 的隐私元数据段（二进制段拼接，不重编码、画质零损失）。

    解析失败 / 结构异常时**返回原字节**：剥离是隐私加固，不是上传的前置条件 ——
    宁可带着 EXIF 上传，也不能让一次解析意外打断正常配图（与转码的降级语义一致）。
    PNG/WebP 走转码管线时天然重编码（元数据已丢），无需处理。
    """
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return data
    try:
        out = bytearray(data[:2])  # SOI
        i = 2
        n = len(data)
        while i < n:
            if data[i] != 0xFF:
                # 段间填充字节（罕见）：原样保留并继续扫描
                out.append(data[i])
                i += 1
                continue
            marker = data[i + 1] if i + 1 < n else 0x00
            if marker == 0xD9:  # EOI
                out += data[i:]
                break
            if marker == 0xDA:  # SOS：其后是压缩数据（含 0xFF 填充），原样保留到结尾
                out += data[i:]
                break
            if 0xD0 <= marker <= 0xD7 or marker == 0x01:  # 独立段（RSTn/TEM），无长度
                out += data[i : i + 2]
                i += 2
                continue
            if i + 3 >= n:
                raise ValueError("截断的段头")
            seg_len = int.from_bytes(data[i + 2 : i + 4], "big")
            if seg_len < 2 or i + 2 + seg_len > n:
                raise ValueError("非法段长")
            if marker in _JPEG_DROP_MARKERS or marker == 0xFE:
                # 整段切除（含 0xFF marker 与两字节长度）
                i += 2 + seg_len
                continue
            out += data[i : i + 2 + seg_len]
            i += 2 + seg_len
        return bytes(out)
    except (ValueError, IndexError) as exc:  # 段长谎报/数据截断 —— 降级为原图上传
        logger.warning(f"JPEG 元数据剥离失败，保留原图上传: {exc}")
        return data
