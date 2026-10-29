"""管理端图片搜索接口：图源选项、检索、导入与缩略图代理。

权限复用内容编辑权限：图片搜索是编辑器内的动作，不新增权限码。
导入只接受 source + source_id，下载地址由服务端按图源重新解析，客户端无法指定 URL。
"""

from urllib.parse import quote

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import AppException, success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.image_search import ImageImportRequest
from app.services.image_search import importer as importer_service
from app.services.image_search import service as search_service
from app.services.image_search import thumb as thumb_service
from app.services.image_search.config import SOURCE_META


@router.get("/image-search/options")
async def image_search_options(
    db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin),
):
    return success_response(await search_service.build_options(db))


@router.get("/image-search/search")
async def image_search(
    source: str = Query(..., min_length=1, max_length=20),
    q: str = Query("", max_length=80),
    page: int = Query(1, ge=1, le=50),
    per_page: int = Query(24, ge=1, le=40),
    image_type: str = Query("all", alias="type"),
    orientation: str = Query("all"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    payload = await search_service.search(
        db, admin_id=admin.id, source=source, q=q, page=page,
        per_page=per_page, image_type=image_type, orientation=orientation,
    )
    for item in payload.get("items", []):
        if item.get("source") == "local":
            item["thumb_proxy_url"] = item.get("thumb_url") or ""
            item["preview_proxy_url"] = item.get("preview_url") or ""
            continue
        source = str(item.get("source") or "")
        item["thumb_proxy_url"] = _proxy_url(item.get("thumb_url"), source)
        item["preview_proxy_url"] = _proxy_url(item.get("preview_url"), source)
    return success_response(payload)


def _proxy_url(url: str | None, source: str) -> str:
    if not url:
        return ""
    token = thumb_service.sign_thumb(url, source=source)
    return "/api/v1/images/search-thumb?token=" + quote(token, safe="")


@router.post("/image-search/import")
async def import_image(
    req: ImageImportRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    cfg = await search_service.load_config(db)
    if not cfg.enabled:
        raise AppException(code=503, message="图片搜索未开启，请联系管理员")

    if req.source == "local":
        asset = await search_service.resolve_local_asset(db, req.source_id)
        if asset is None:
            raise AppException(code=404, message="素材不存在")
        deduped = True
    else:
        existing = await importer_service.find_asset(db, req.source, req.source_id)
        if existing is not None:
            asset, deduped = existing, True
        else:
            await search_service.check_import_quota(cfg, admin.id)
            hit = await search_service.resolve_hit(
                db, source=req.source, source_id=req.source_id,
            )
            asset = await importer_service.import_hit(
                db, hit, alt=req.alt, query=req.query or "", admin_id=admin.id,
            )
            deduped = False
            await search_service.record_import_success(admin.id)

    if not deduped:
        await search_service.audit_import(db, admin, asset)

    return success_response({
        "url": asset.cos_url,
        "thumb_url": asset.thumb_url,
        "width": asset.width,
        "height": asset.height,
        "alt": asset.alt,
        "asset_id": asset.id,
        "deduped": deduped,
        "credit": _credit(asset),
    })


def _credit(asset) -> dict:
    label = SOURCE_META.get(asset.source, {}).get("name", "本站素材")
    if asset.source in {"upload", "ai", "local"}:
        label = "本站素材"
    text = f"图片：{asset.author} / {label}" if asset.author else f"图片：{label}"
    return {
        "text": text,
        "url": asset.page_url or "",
        "license": asset.license,
        "license_url": asset.license_url or "",
        "requires_attribution": bool(asset.requires_attribution),
    }


