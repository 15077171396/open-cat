"""Validation helpers for uploaded binary files."""

import io

from PIL import Image

# 单张图允许解码的像素上限。20M 像素 ≈ 5000×4000，远超头像/配图的实际需要；
# 与 utils/image_convert.py 的转码上限保持一致，两处都拦在「解码成位图」之前。
MAX_DECODE_PIXELS = 20_000_000

IMAGE_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (),
}


def is_allowed_image(content_type: str, content: bytes) -> bool:
    """Check that an allowed image matches its declared media type."""
    if content_type == "image/webp":
        return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return any(content.startswith(signature) for signature in IMAGE_SIGNATURES.get(content_type, ()))


def validate_image_pixels(content: bytes, max_pixels: int = MAX_DECODE_PIXELS) -> tuple[int, int]:
    """只读文件头拿尺寸，超过像素上限就拒绝（不解码像素数据）。

    为什么必须单独拦：魔数 + 体积都合格的图仍可能是「解压炸弹」——
    一张 12000×12000 的纯色 PNG 压缩后只有几百 KB，而 `convert("RGB")` 要真分配约 430MB。
    Pillow 默认只在 >89M 像素告警、>178M 抛错，**89M–178M 这一段既不报错又要吃掉几百 MB**，
    容器 mem_limit 768m 时足以 OOM。

    返回 (width, height)；不是可解析的图片或超限时抛 ValueError。
    """
    try:
        with Image.open(io.BytesIO(content)) as img:
            width, height = img.size
    except Image.DecompressionBombError as exc:  # >178M 像素：Pillow 自己就拒绝
        raise ValueError("图片尺寸过大，请压缩后再上传") from exc
    except Exception as exc:  # noqa: BLE001 头部损坏/格式不支持，统一按「不是有效图片」处理
        raise ValueError("图片无法解析，请更换文件") from exc

    if width <= 0 or height <= 0:
        raise ValueError("图片尺寸无效")
    if width * height > max_pixels:
        raise ValueError(
            f"图片像素过大（{width}×{height}），请压缩到 "
            f"{max_pixels // 1_000_000}M 像素以内再上传"
        )
    return width, height
