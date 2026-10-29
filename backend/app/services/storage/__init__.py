"""存储门面：上层只依赖这里，不直接碰具体驱动。

```python
from app.services.storage import StorageError, get_file_ext, storage

url = await storage.upload_bytes(data, folder="images", ext=".png", content_type="image/png")
```

驱动由 `STORAGE_DRIVER` 决定：
- `local`（默认）：写本机磁盘，零配置，开源用户开箱可用；
- `cos`：腾讯云对象存储，生产环境/CDN 场景用。

新增驱动（S3 / OSS / MinIO）只需再写一个模块并在 `_load_provider` 里登记，
业务代码一行不用改——这是这一层存在的唯一理由。
"""

from __future__ import annotations

from functools import lru_cache
from types import ModuleType

from app.config import settings

from .base import StorageError, generate_key, get_file_ext

__all__ = [
    "StorageError",
    "driver_name",
    "generate_key",
    "get_file_ext",
    "is_configured",
    "public_url_prefixes",
    "storage",
]


@lru_cache(maxsize=1)
def _load_provider() -> ModuleType:
    name = (settings.STORAGE_DRIVER or "local").strip().lower()
    if name == "local":
        from . import local_provider as provider
    elif name == "cos":
        from . import cos_provider as provider
    else:
        # 拼错的驱动名不能静默回落到 local：那会把「配了 COS 却没生效」变成
        # 一个只有翻文件系统才能发现的隐性问题。
        raise StorageError(
            f"未知的 STORAGE_DRIVER: {name!r}（可选 local / cos）"
        )
    return provider


def driver_name() -> str:
    return (settings.STORAGE_DRIVER or "local").strip().lower()


def is_configured() -> bool:
    return _load_provider().is_configured()


def public_url_prefixes() -> tuple[str, ...]:
    """当前驱动可能产出的公开 URL 前缀（校验用户提交的图片是否来自本站上传）。"""
    return _load_provider().public_url_prefixes()


class _StorageFacade:
    """门面：转发到当前驱动。

    用对象而不是散装函数，是为了让调用点统一写成 `storage.xxx()`，
    一眼能看出「这是存储层的调用」；同时给未来加 `sign_url` / `delete` 留位置。
    """

    @property
    def driver(self) -> str:
        return driver_name()

    def is_configured(self) -> bool:
        return _load_provider().is_configured()

    def public_url_prefixes(self) -> tuple[str, ...]:
        """当前驱动可能产出的公开 URL 前缀（校验图片是否来自本站上传）。"""
        return _load_provider().public_url_prefixes()

    async def upload_bytes(
        self, data: bytes, *, folder: str, ext: str, content_type: str
    ) -> str:
        return await _load_provider().upload_bytes(
            data=data, folder=folder, ext=ext, content_type=content_type
        )

    async def download_object_bytes(self, key: str, max_size: int) -> tuple[bytes, str]:
        return await _load_provider().download_object_bytes(key, max_size)


storage = _StorageFacade()
