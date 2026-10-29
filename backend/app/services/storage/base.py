"""存储抽象的公共部分：错误类型、对象 key 生成、扩展名推断。

设计目标只有一个：**不配任何云账号也能上传**。
开源版默认 `local`（写本机磁盘，nginx/后端直接托管），需要 CDN 时切 `cos`。
"""

from __future__ import annotations

import uuid
from typing import Optional


class StorageError(Exception):
    """存储层统一异常。

    调用方只捕获这一个类型，不再区分「COS SDK 抛了什么」——
    换驱动时业务代码不需要跟着改 except 子句。
    """


#: 允许的扩展名白名单。不在表里的名字一律丢弃，改由 content_type 推断 ——
#: 文件名是用户输入，`evil.php` / `x.svg` 这类不该有机会落到磁盘上被当成脚本执行。
_ALLOWED_EXTS: frozenset[str] = frozenset(
    {
        ".jpg", ".jpeg", ".png", ".gif", ".webp",
        ".mp4", ".mov", ".avi",
        ".mp3", ".wav",
        ".pdf", ".zip", ".rar", ".7z",
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt",
    }
)

_CT_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/x-rar-compressed": ".rar",
    "text/plain": ".txt",
}


def get_file_ext(filename: Optional[str], content_type: str) -> str:
    """从文件名或 content_type 推断扩展名（含点，未知返回空串）。"""
    if filename and "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
        if ext in _ALLOWED_EXTS:
            return ext
    return _CT_TO_EXT.get(content_type, "")


def generate_key(folder: str, ext: str) -> str:
    """按日期分目录 + UUID 命名生成对象 key。

    例：`images/2026/10/30/a1b2c3d4e5f60718.jpg`

    日期取 **UTC**：服务器时区变化不该让同一天的文件落进不同目录，
    否则「按日期找图」在跨时区部署时会错位。
    """
    from app.utils.time import utcnow

    date_path = utcnow().strftime("%Y/%m/%d")
    filename = f"{uuid.uuid4().hex[:16]}{ext}"
    return f"{folder}/{date_path}/{filename}"
