"""素材库入库：上传图片、上传附件与 AI 生成图统一写入 image_assets。

去重键用 sha256（source + source_id）；写入失败只记日志，绝不能影响已经成功的
COS 上传、附件插入或 AI 出图。
"""

import hashlib

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.image_asset import ImageAsset
from app.utils.upload_validation import validate_image_pixels

_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/x-msvideo", "video/webm"}
_AUDIO_TYPES = {"audio/mpeg", "audio/wav", "audio/x-wav", "audio/ogg"}
_DOCUMENT_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain",
}
_ARCHIVE_TYPES = {
    "application/zip", "application/x-zip-compressed",
    "application/x-rar-compressed", "application/vnd.rar",
    "application/x-7z-compressed",
}


def _kind_for(content_type: str, filename: str | None) -> str:
    content_type = (content_type or "").lower()
    suffix = (filename or "").lower().rsplit(".", 1)[-1] if "." in (filename or "") else ""
    if content_type in _IMAGE_TYPES or suffix in {"jpg", "jpeg", "png", "gif", "webp"}:
        return "image"
    if content_type in _VIDEO_TYPES or suffix in {"mp4", "mov", "avi", "webm"}:
        return "video"
    if content_type in _AUDIO_TYPES or suffix in {"mp3", "wav", "ogg"}:
        return "audio"
    if content_type in _DOCUMENT_TYPES or suffix in {
        "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt",
    }:
        return "document"
    if content_type in _ARCHIVE_TYPES or suffix in {"zip", "rar", "7z"}:
        return "archive"
    return "other"


async def _record_upload(
    db: AsyncSession,
    *,
    url: str,
    data: bytes,
    content_type: str,
    filename: str | None,
    admin_id: int,
    kind: str,
) -> ImageAsset | None:
    digest = hashlib.sha256(data).hexdigest()
    try:
        existing = (await db.execute(select(ImageAsset).where(
            ImageAsset.source == "upload",
            ImageAsset.source_id == digest,
        ))).scalar_one_or_none()
        if existing is not None:
            return existing
        width = height = 0
        if kind == "image":
            try:
                width, height = validate_image_pixels(data)
            except ValueError:
                width, height = 0, 0
        asset = ImageAsset(
            source="upload",
            source_id=digest,
            sha256=digest,
            kind=kind,
            mime_type=(content_type or "")[:100],
            filename=(filename or "")[:300],
            title=(filename or "上传文件")[:300],
            alt="",
            cos_url=url,
            thumb_url=url if kind == "image" else None,
            width=width,
            height=height,
            size_bytes=len(data),
            license="本站上传",
            created_by=admin_id,
            is_active=True,
        )
        db.add(asset)
        await db.commit()
        return asset
    except IntegrityError:
        await db.rollback()
        return None
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.warning(f"上传素材写入素材库失败（不影响上传）: {type(exc).__name__}")
        return None


async def record_upload_asset(
    db: AsyncSession,
    *,
    url: str,
    data: bytes,
    content_type: str,
    filename: str | None,
    admin_id: int,
) -> ImageAsset | None:
    return await _record_upload(
        db, url=url, data=data, content_type=content_type,
        filename=filename, admin_id=admin_id, kind="image",
    )


async def record_file_asset(
    db: AsyncSession,
    *,
    url: str,
    data: bytes,
    content_type: str,
    filename: str | None,
    admin_id: int,
) -> ImageAsset | None:
    return await _record_upload(
        db, url=url, data=data, content_type=content_type,
        filename=filename, admin_id=admin_id,
        kind=_kind_for(content_type, filename),
    )


async def record_generated_asset(
    *,
    url: str,
    data: bytes,
    content_type: str,
    width: int,
    height: int,
    prompt: str,
    alt: str,
    admin_id: int,
) -> None:
    """AI 生成图落素材库；独立短会话，失败不影响出图。"""
    digest = hashlib.sha256(data).hexdigest()
    try:
        async with async_session() as db:
            existing = (await db.execute(select(ImageAsset).where(
                ImageAsset.source == "ai",
                ImageAsset.source_id == digest,
            ))).scalar_one_or_none()
            if existing is not None:
                return
            db.add(ImageAsset(
                source="ai",
                source_id=digest,
                sha256=digest,
                kind="image",
                mime_type=(content_type or "")[:100],
                filename="",
                title=(prompt or "AI 生成图片")[:300],
                alt=(alt or "")[:300],
                cos_url=url,
                thumb_url=url,
                width=max(0, width),
                height=max(0, height),
                size_bytes=len(data),
                query=(prompt or "")[:200],
                license="AI 生成",
                created_by=admin_id,
                is_active=True,
            ))
            await db.commit()
    except IntegrityError:
        return
    except SQLAlchemyError as exc:
        logger.warning(f"AI 图片写入素材库失败（不影响出图）: {type(exc).__name__}")
