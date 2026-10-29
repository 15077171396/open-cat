"""图库图片导入：下载白名单 → 校验 → WebP/动图归一化 → COS → image_assets 去重落库。"""

import hashlib
from dataclasses import dataclass
from io import BytesIO

import httpx
from loguru import logger
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import AppException
from app.core.executors import run_heavy
from app.models.image_asset import ImageAsset

from app.services.image_search.base import ImageHit
from app.services.storage import storage, StorageError
from app.services.image_search.config import SOURCE_META
from app.services.image_search.local import get_asset
from app.services.image_search.policy import is_allowed_https_url, is_allowed_url
from app.utils.upload_validation import (
    MAX_DECODE_PIXELS, is_allowed_image, validate_image_pixels,
)

_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
_SNIFF_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
_HEADER_OPTIONAL_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})
_EXT = {
    "image/jpeg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/gif": ".gif",
}
_MAX_REDIRECTS = 3
_THUMB_MAX_EDGE = 2400
_MAX_ANIMATION_FRAMES = 300

# qpic 的“暂时无法查看”返回 HTTP 200 和有效 PNG；精确匹配内容，不能按 120×120 尺寸误杀正常小图。
_UNAVAILABLE_IMAGE_SHA256 = frozenset({
    "1b8214ac4449461450d94a808d42e658d6aaac13581554e6776a8e2b83d75125",
})


@dataclass(frozen=True)
class PreparedImage:
    data: bytes
    width: int
    height: int
    ext: str
    content_type: str


async def download_image(
    url: str,
    *,
    allowed_hosts=(),
    max_bytes: int,
    timeout: float = 15.0,
    transport: httpx.AsyncBaseTransport | None = None,
    allow_public: bool = False,
    allow_http: bool = False,
    headers: dict[str, str] | None = None,
) -> tuple[bytes, str]:
    def allowed(target: str) -> bool:
        if allow_public:
            return is_allowed_url(
                target, (), allow_public=True, allow_http=allow_http,
            )
        return is_allowed_https_url(target, allowed_hosts)

    if not allowed(url):
        raise AppException(code=502, message="图片地址来源不可信")
    current = url
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=4.0, read=timeout, write=4.0, pool=4.0),
        follow_redirects=False,
        transport=transport,
    ) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            try:
                async with client.stream("GET", current, headers=headers) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise AppException(code=502, message="图片下载跳转无效")
                        current = str(httpx.URL(current).join(location))
                        if not allowed(current):
                            raise AppException(code=502, message="图片下载跳转到了不可信域名")
                        continue
                    if response.status_code != 200:
                        # 区分「图没了」和「我们这边出问题」：上游 404/403 是图源侧失效，
                        # 不该报成 502（那是网关错误，会让人以为后端挂了）。
                        status = response.status_code
                        if status in (401, 403):
                            raise AppException(code=403, message="原图拒绝访问，可能已开启防盗链")
                        if status in (404, 410):
                            raise AppException(code=404, message="原图已失效")
                        raise AppException(code=502, message="图片下载失败")
                    content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
                    # 搜狗 CDN 等站点会省略 Content-Type；此时先读内容再按魔数判定，
                    # 但只有空/octet-stream 才允许走这条兜底，避免 HTML 伪装成图片。
                    sniff_type = content_type in _HEADER_OPTIONAL_TYPES
                    if content_type not in _CONTENT_TYPES and not sniff_type:
                        raise AppException(code=502, message="图片格式不受支持")
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_bytes:
                        raise AppException(code=502, message="图片超过大小限制")
                    buffer = bytearray()
                    async for chunk in response.aiter_bytes():
                        buffer.extend(chunk)
                        if len(buffer) > max_bytes:
                            raise AppException(code=502, message="图片超过大小限制")
                    if not buffer:
                        raise AppException(code=502, message="图片内容为空")
                    data = bytes(buffer)
                    if hashlib.sha256(data).hexdigest() in _UNAVAILABLE_IMAGE_SHA256:
                        raise AppException(code=404, message="原图已失效")
                    if content_type not in _CONTENT_TYPES:
                        content_type = next(
                            (item for item in _SNIFF_TYPES if is_allowed_image(item, data)),
                            "",
                        )
                        if not content_type:
                            raise AppException(code=502, message="图片格式不受支持")
                    return data, content_type
            except httpx.TimeoutException as exc:
                raise AppException(code=504, message="图片下载超时") from exc
            except httpx.HTTPError as exc:
                raise AppException(code=502, message="图片下载失败") from exc
    raise AppException(code=502, message="图片下载跳转次数过多")


def normalize_image(
    data: bytes, content_type: str, allow_animation: bool = False,
) -> PreparedImage:
    """静态图统一转 WebP；动图按来源开关直通（保留 GIF/WebP 动画）。"""
    try:
        image = Image.open(BytesIO(data))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("图片无法解析") from exc
    with image:
        width, height = image.size
        if width <= 0 or height <= 0:
            raise ValueError("图片尺寸无效")
        if width * height > MAX_DECODE_PIXELS:
            raise ValueError("图片像素过大")
        animated = bool(getattr(image, "is_animated", False))
        if animated:
            if not allow_animation:
                raise ValueError("该图源不支持动图")
            try:
                frames = int(image.n_frames)
            except (AttributeError, OSError):
                frames = 1
            if frames > _MAX_ANIMATION_FRAMES:
                raise ValueError("动图帧数过多")
            return PreparedImage(
                data=data, width=width, height=height,
                ext=_EXT.get(content_type, ".gif"), content_type=content_type,
            )
        if content_type == "image/gif" and allow_animation:
            return PreparedImage(
                data=data, width=width, height=height,
                ext=".gif", content_type="image/gif",
            )
        image = ImageOps.exif_transpose(image)
        image.thumbnail((_THUMB_MAX_EDGE, _THUMB_MAX_EDGE), Image.LANCZOS)
        has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
        image = image.convert("RGBA" if has_alpha else "RGB")
        output = BytesIO()
        try:
            image.save(output, "WEBP", quality=85, method=4)
        except OSError as exc:
            raise ValueError("图片处理失败") from exc
        result = output.getvalue()
        if not result:
            raise ValueError("图片处理失败")
        return PreparedImage(
            data=result, width=image.width, height=image.height,
            ext=".webp", content_type="image/webp",
        )


async def find_asset(db: AsyncSession, source: str, source_id: str) -> ImageAsset | None:
    return (await db.execute(select(ImageAsset).where(
        ImageAsset.source == source,
        ImageAsset.source_id == source_id,
    ))).scalar_one_or_none()


async def import_hit(
    db: AsyncSession,
    hit: ImageHit,
    *,
    alt: str | None,
    query: str,
    admin_id: int,
) -> ImageAsset:
    if hit.source == "local":
        asset = await get_asset(db, hit.source_id)
        if asset is None:
            raise AppException(code=404, message="素材不存在")
        return asset
    meta = SOURCE_META.get(hit.source)
    if meta is None:
        raise AppException(code=400, message="不支持的图片图源")
    existing = await find_asset(db, hit.source, hit.source_id)
    if existing is not None:
        return existing
    if not storage.is_configured():
        raise AppException(code=503, message="COS 未配置，无法保存图片")

    headers = {"Referer": meta["referer"]} if meta.get("referer") else None
    prepared: PreparedImage | None = None
    last_error: AppException | None = None
    for url in (hit.download_url, *hit.fallback_urls):
        if not url:
            continue
        try:
            data, content_type = await download_image(
                url,
                allowed_hosts=meta["image_hosts"],
                max_bytes=settings.COS_IMAGE_MAX_SIZE,
                allow_public=bool(meta["web_images"]),
                allow_http=bool(meta["allow_http"]),
                headers=headers,
            )
            if not is_allowed_image(content_type, data):
                raise AppException(code=502, message="图片内容与类型不一致")
            try:
                validate_image_pixels(data)
            except ValueError as exc:
                raise AppException(code=502, message=str(exc)) from exc
            try:
                prepared = await run_heavy(
                    normalize_image, data, content_type, bool(meta["allow_animation"]),
                )
            except ValueError as exc:
                raise AppException(code=502, message=str(exc)) from exc
            break
        except AppException as exc:
            last_error = exc
    if prepared is None:
        raise last_error or AppException(code=502, message="图片下载失败")

    digest = hashlib.sha256(prepared.data).hexdigest()
    try:
        cos_url = await storage.upload_bytes(
            prepared.data, folder=f"images/{hit.source}", ext=prepared.ext,
            content_type=prepared.content_type,
        )
    except StorageError as exc:
        logger.error(f"图库图片转存 COS 失败: source={hit.source} id={hit.source_id} ({exc})")
        raise AppException(code=503, message="图片存储失败，请稍后重试") from exc

    alt_text = (alt if alt is not None else hit.alt or hit.title or "").strip()[:300]
    asset = ImageAsset(
        source=hit.source,
        source_id=hit.source_id,
        sha256=digest,
        title=(hit.title or alt_text or f"{hit.source} 图片")[:300],
        alt=alt_text,
        author=(hit.author or "")[:200],
        author_url=hit.author_url or None,
        page_url=hit.page_url or None,
        license=(hit.license or "")[:60],
        license_url=hit.license_url or None,
        requires_attribution=hit.requires_attribution,
        width=prepared.width,
        height=prepared.height,
        size_bytes=len(prepared.data),
        cos_url=cos_url,
        thumb_url=cos_url,
        query=(query or "").strip()[:200],
        created_by=admin_id,
        is_active=True,
    )
    db.add(asset)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await find_asset(db, hit.source, hit.source_id)
        if existing is not None:
            return existing
        raise
    await db.refresh(asset)
    return asset
