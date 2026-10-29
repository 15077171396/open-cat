"""腾讯云 COS 驱动：把既有的 `cos_service` 适配到统一的存储接口上。

刻意**不改** `cos_service` 的内部实现（ACL 补设、域名回退等细节都是线上踩出来的），
这一层只做接口对齐，出问题时能一眼区分「适配层错了」还是「COS 逻辑错了」。
"""

from __future__ import annotations

from .base import StorageError


async def upload_bytes(*, data: bytes, folder: str, ext: str, content_type: str) -> str:
    # 延迟导入：驱动没启用时不必加载 COS SDK，也不要求运行环境装过它
    from app.services import cos_service

    try:
        return await cos_service.upload_bytes(
            data, folder=folder, ext=ext, content_type=content_type
        )
    except cos_service.CosError as exc:
        raise StorageError(str(exc)) from exc


async def download_object_bytes(key: str, max_size: int) -> tuple[bytes, str]:
    from app.services import cos_service

    try:
        return await cos_service.download_object_bytes(key, max_size)
    except cos_service.CosError as exc:
        raise StorageError(str(exc)) from exc


def is_configured() -> bool:
    from app.services import cos_service

    return cos_service.is_configured()


def public_url_prefixes() -> tuple[str, ...]:
    from app.services import cos_service

    return cos_service.public_url_prefixes()
