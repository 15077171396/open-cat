"""素材库查询：统一浏览图片、附件、AI 生成图与历史导入图。

素材本身由上传/导入/生成链路写入 `image_assets`；本模块只负责后台浏览、
筛选、统计和使用计数，不新建第二张素材表。
"""

from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.image_asset import ImageAsset
from app.utils.sanitize import escape_like

MATERIAL_KINDS = ("image", "video", "audio", "document", "archive", "other")


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def serialize_material(asset: ImageAsset) -> dict:
    return {
        "id": asset.id,
        "kind": asset.kind,
        "source": asset.source,
        "title": asset.title,
        "filename": asset.filename,
        "alt": asset.alt,
        "author": asset.author,
        "mime_type": asset.mime_type,
        "width": asset.width,
        "height": asset.height,
        "size_bytes": asset.size_bytes,
        "cos_url": asset.cos_url,
        "thumb_url": asset.thumb_url or (asset.cos_url if asset.kind == "image" else ""),
        "license": asset.license,
        "page_url": asset.page_url or "",
        "usage_count": asset.usage_count,
        "last_used_at": _iso(asset.last_used_at),
        "created_at": _iso(asset.created_at),
    }


def _conditions(
    *, keyword: str = "", kind: str = "", source: str = "",
) -> list:
    conditions = [ImageAsset.is_active.is_(True)]
    if kind and kind in MATERIAL_KINDS:
        conditions.append(ImageAsset.kind == kind)
    if source:
        conditions.append(ImageAsset.source == source)
    term = (keyword or "").strip()
    if term:
        pattern = escape_like(term[:100])
        conditions.append(or_(
            ImageAsset.title.contains(pattern, escape="\\"),
            ImageAsset.filename.contains(pattern, escape="\\"),
            ImageAsset.alt.contains(pattern, escape="\\"),
            ImageAsset.query.contains(pattern, escape="\\"),
            ImageAsset.tags.contains(pattern, escape="\\"),
        ))
    return conditions


async def list_materials(
    db: AsyncSession,
    *,
    keyword: str = "",
    kind: str = "",
    source: str = "",
    page: int = 1,
    page_size: int = 24,
) -> dict:
    conditions = _conditions(keyword=keyword, kind=kind, source=source)
    total = int((await db.execute(
        select(func.count()).select_from(ImageAsset).where(*conditions)
    )).scalar() or 0)
    rows = (await db.execute(
        select(ImageAsset)
        .where(*conditions)
        .order_by(ImageAsset.created_at.desc(), ImageAsset.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()
    return {
        "items": [serialize_material(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def material_stats(db: AsyncSession) -> dict:
    rows = (await db.execute(
        select(ImageAsset.kind, func.count())
        .where(ImageAsset.is_active.is_(True))
        .group_by(ImageAsset.kind)
    )).all()
    counts = {str(kind): int(count) for kind, count in rows}
    total = int((await db.execute(
        select(func.count()).select_from(ImageAsset)
        .where(ImageAsset.is_active.is_(True))
    )).scalar() or 0)
    return {"total": total, "kinds": counts}


async def touch_material(db: AsyncSession, material_id: int) -> ImageAsset:
    asset = (await db.execute(
        select(ImageAsset).where(ImageAsset.id == material_id)
    )).scalar_one_or_none()
    if asset is None or not asset.is_active:
        raise AppException(code=404, message="素材不存在")
    asset.usage_count = int(asset.usage_count or 0) + 1
    asset.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(asset)
    return asset
