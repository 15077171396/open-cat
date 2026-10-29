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
