"""本站素材：已上传/已导入的 COS 图片，按标题、Alt、检索词、标签搜索。"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.image_asset import ImageAsset
from app.services.image_search.base import ImageHit
from app.utils.sanitize import escape_like


def asset_to_hit(asset: ImageAsset) -> ImageHit:
    return ImageHit(
        source="local",
        source_id=str(asset.id),
        thumb_url=asset.thumb_url or asset.cos_url,
        download_url=asset.cos_url,
        width=asset.width,
        height=asset.height,
        alt=asset.alt or asset.title,
        title=asset.title or asset.alt,
        author=asset.author,
        page_url=asset.page_url or asset.cos_url,
        license="本站素材",
        requires_attribution=False,
    )


async def search_local(
    db: AsyncSession, query: str, page: int, per_page: int,
) -> dict:
    conditions = [ImageAsset.is_active.is_(True), ImageAsset.kind == "image"]
    keyword = (query or "").strip()
    if keyword:
        pattern = escape_like(keyword)
        conditions.append(or_(
            ImageAsset.title.contains(pattern, escape="\\"),
            ImageAsset.alt.contains(pattern, escape="\\"),
            ImageAsset.query.contains(pattern, escape="\\"),
            ImageAsset.tags.contains(pattern, escape="\\"),
        ))
    stmt = (
        select(ImageAsset)
        .where(*conditions)
        .order_by(ImageAsset.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page + 1)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > per_page
    items = [asset_to_hit(asset).public_dict() for asset in rows[:per_page]]
    return {
        "source": "local", "items": items, "has_more": has_more,
        "total": None, "cached": False,
    }


async def get_asset(db: AsyncSession, source_id: str) -> ImageAsset | None:
    try:
        asset_id = int(str(source_id))
    except (TypeError, ValueError):
        return None
    stmt = select(ImageAsset).where(ImageAsset.id == asset_id, ImageAsset.is_active.is_(True))
    return (await db.execute(stmt)).scalar_one_or_none()
