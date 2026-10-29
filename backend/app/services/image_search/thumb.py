"""缩略图签名代理：浏览器不直接访问图源 CDN，避免热链限制与 CDN 不可达。"""

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

import httpx

from app.config import settings
from app.core.degrade import degrade_guard
from app.core.exceptions import AppException
from app.redis import redis_client
from app.services.image_search.config import SOURCE_META, all_image_hosts
from app.services.image_search.importer import download_image
from app.services.image_search.policy import is_allowed_https_url, is_allowed_url

THUMB_MAX_BYTES = 2 * 1024 * 1024
_THUMB_TIMEOUT = 10.0

# 失效图负缓存：图源侧已删除/防盗链的地址，短时间内不再返回给前端。
# 目的不是省流量，而是让「搜索 → 请求缩略图 → 502」这一串在网络面板里消失：
# 后端在搜索结果里就把它剔掉，前端压根不会去请求。
DEAD_LINK_TTL = 600
_DEAD_PREFIX = "image_search:dead:"


@dataclass(frozen=True)
class ThumbTarget:
    url: str
    source: str = ""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(body: bytes) -> bytes:
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), body, hashlib.sha256).digest()


def sign_thumb(url: str, *, source: str = "", expires_in: int = 7 * 24 * 3600) -> str:
    body = json.dumps(
        {"u": url, "s": source, "e": int(time.time()) + expires_in},
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{_b64(body)}.{_b64(_sign(body))}"


def verify_thumb(token: str) -> ThumbTarget:
    try:
        body_b64, sig_b64 = (token or "").split(".", 1)
        body = _unb64(body_b64)
        signature = _unb64(sig_b64)
    except (ValueError, TypeError) as exc:
        raise AppException(code=403, message="图片地址签名无效") from exc
    if not hmac.compare_digest(signature, _sign(body)):
        raise AppException(code=403, message="图片地址签名无效")
    try:
        payload = json.loads(body)
        url = str(payload["u"])
        source = str(payload.get("s") or "")
        expires = int(payload["e"])
    except (ValueError, TypeError, KeyError) as exc:
        raise AppException(code=403, message="图片地址签名无效") from exc
    if expires < int(time.time()):
        raise AppException(code=403, message="图片地址已过期，请重新搜索")

    meta = SOURCE_META.get(source)
    if meta is not None:
        allowed = (
            is_allowed_url(
                url, allow_public=True, allow_http=bool(meta["allow_http"]),
            )
            if meta["web_images"]
            else is_allowed_https_url(url, meta["image_hosts"])
        )
    else:
        allowed = is_allowed_https_url(url, all_image_hosts())
    if not allowed:
        raise AppException(code=403, message="图片地址不在允许的图源范围")
    return ThumbTarget(url=url, source=source)


def _dead_key(url: str) -> str:
    return _DEAD_PREFIX + hashlib.sha1(url.encode("utf-8")).hexdigest()


async def mark_dead(url: str) -> None:
    """记录一个已确认失效的图源地址（仅 403/404 这类确定性失败）。"""
    if not url:
        return
    with degrade_guard("image_search.thumb.mark_dead", detail="失效图负缓存写失败，忽略"):
        await redis_client.set(_dead_key(url), "1", ex=DEAD_LINK_TTL)


async def filter_dead_urls(urls: list[str]) -> set[str]:
    """返回其中已知失效的地址集合；Redis 不可用时视为全部可用。"""
    unique = [url for url in dict.fromkeys(urls) if url]
    if not unique:
        return set()
    values: list[str | None] = []
    with degrade_guard("image_search.thumb.filter_dead", detail="读不到负缓存就当作全部可用"):
        values = await redis_client.mget([_dead_key(url) for url in unique])
    return {url for url, value in zip(unique, values) if value}


async def fetch_thumb(
    target: ThumbTarget, *, transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bytes, str]:
    meta = SOURCE_META.get(target.source)
    try:
        if meta is None:
            return await download_image(
                target.url, allowed_hosts=all_image_hosts(), max_bytes=THUMB_MAX_BYTES,
                timeout=_THUMB_TIMEOUT, transport=transport,
            )
        headers = {"Referer": meta["referer"]} if meta.get("referer") else None
        return await download_image(
            target.url,
            allowed_hosts=meta["image_hosts"],
            max_bytes=THUMB_MAX_BYTES,
            timeout=_THUMB_TIMEOUT,
            transport=transport,
            allow_public=bool(meta["web_images"]),
            allow_http=bool(meta["allow_http"]),
            headers=headers,
        )
    except AppException as exc:
        # 只记「确定性失效」；超时/5xx 可能只是抖动，下次还该重试。
        if exc.code in (403, 404):
            await mark_dead(target.url)
        raise
