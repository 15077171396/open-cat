"""本地磁盘存储驱动（开源默认）。

为什么需要它：原实现所有上传都走腾讯云 COS，开源使用者拿不到云账号，
第一次上传就报错——「跑起来了但什么都传不了」比跑不起来更让人放弃。

落盘位置由 `STORAGE_LOCAL_DIR` 决定（容器内是挂卷的 `/app/uploads`），
对外 URL 形如 `{STORAGE_PUBLIC_BASE}/uploads/{key}`，由后端 StaticFiles 托管
（见 `app/main.py` 的挂载与 nginx 的 `/uploads/` 反代）。

⚠️ 安全边界：只按 key 读文件，且 key 必须过 `_safe_path` 校验。
用户可控的 key 若含 `..` 就能读到 `/etc/passwd`——文件下载接口会用 key 回源，
所以这里必须挡住路径穿越，而不是指望上游校验。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from app.config import settings

from .base import StorageError, generate_key


def _root() -> Path:
    return Path(settings.STORAGE_LOCAL_DIR).resolve()


def _public_base() -> str:
    """对外访问根地址：优先显式配置，否则回落到站点根地址。"""
    base = (settings.STORAGE_PUBLIC_BASE or settings.SITE_BASE_URL or "").rstrip("/")
    return base


def _safe_path(key: str) -> Path:
    """把对象 key 解析成绝对路径，并确保它仍在存储根目录内。"""
    root = _root()
    target = (root / key).resolve()
    if root != target and root not in target.parents:
        # 不把用户输入回显到日志里（可能含控制字符）
        raise StorageError("非法的对象路径")
    return target


def is_configured() -> bool:
    return True


def public_url_prefixes() -> tuple[str, ...]:
    """本驱动产出的公开 URL 前缀（用于校验「图片确实来自本站上传」）。"""
    base = _public_base()
    # base 为空时退化成相对前缀，仍能匹配 `/uploads/...` 形式的 URL
    return (f"{base}/uploads/", "/uploads/")


def _write_sync(key: str, data: bytes) -> str:
    path = _safe_path(key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError as exc:
        raise StorageError(f"本地存储写入失败: {exc}") from exc
    url = f"{_public_base()}/uploads/{key}"
    logger.info(f"本地存储写入成功: key={key}, size={len(data)} bytes")
    return url


def _read_sync(key: str, max_size: int) -> tuple[bytes, str]:
    path = _safe_path(key)
    if not path.is_file():
        raise StorageError(f"对象不存在: {key}")
    size = path.stat().st_size
    if size > max_size:
        raise StorageError(f"对象超过读取上限（{size} > {max_size}）")
    data = path.read_bytes()
    return data, _guess_content_type(path.suffix.lower())


async def upload_bytes(*, data: bytes, folder: str, ext: str, content_type: str) -> str:
    key = generate_key(folder, ext)
    return await asyncio.to_thread(_write_sync, key, data)


async def download_object_bytes(key: str, max_size: int) -> tuple[bytes, str]:
    return await asyncio.to_thread(_read_sync, key, max_size)


def _guess_content_type(ext: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".zip": "application/zip",
    }.get(ext, "application/octet-stream")
