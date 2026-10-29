"""GitHub 图片代理：国内直连不可达的 GitHub 图片经服务端多源中转拉取
（原始 URL → raw 域转 jsDelivr → GITHUB_MIRROR_PREFIX 镜像），全部失败 302 回源兜底。
安全：严格域名白名单防开放代理（SSRF）、仅 http(s)、手动逐跳校验重定向、流式限制大小。"""

import asyncio
import mimetypes

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, RedirectResponse, Response
from loguru import logger

from app.core.exceptions import AppException, error_response
from app.services import cos_service
from app.services.image_search import thumb as image_search_thumb
from app.services.image_proxy import (
    SAFE_IMAGE_TYPES as _SAFE_IMAGE_TYPES,
    fetch_candidates as _fetch_candidates,
    is_allowed_url as _is_allowed_url,
    own_cos_object_key as _own_cos_key,
)
from app.services.third_party_fetch.common import MAX_RESPONSE_SIZE

router = APIRouter()

# 主机白名单、MIME 精确集、候选源构造已下沉到 services/image_proxy（行为由 test_image_proxy_policy 钉住）。
# 单张大小上限取 MAX_RESPONSE_SIZE（8MB）：本端点免鉴权且整包读进内存（峰值≈上限×2），
# 4 并发×40MB 就能顶爆 768m 容器；超限时走既有 302 回源降级路径。
_MAX_IMAGE_SIZE = MAX_RESPONSE_SIZE

# 并发闸门 + 排队上限（P1-39）：饱和即降级为 302 回源，不排队、不 503
_MAX_CONCURRENT_PROXY = 4
_PROXY_QUEUE_TIMEOUT = 0.5
_proxy_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_PROXY)

# 图库缩略图一次加载很多张，单独放宽并发；仍是 2MB/张的短连接。
_MAX_CONCURRENT_SEARCH_THUMB = 8
_SEARCH_THUMB_QUEUE_TIMEOUT = 3.0
_search_thumb_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_SEARCH_THUMB)

# 重定向跟随上限（防重定向环）
_MAX_REDIRECTS = 5

# 共享 AsyncClient（连接池复用，避免每请求完整 TCP+TLS 握手）；
# follow_redirects=False：重定向手动跟随以逐跳校验白名单
_client: httpx.AsyncClient | None = None
# 惰性单例锁：启动后首批并发请求同时通过 None 判断会各自创建 client，
# 多余实例永不关闭（连接/TLS 会话泄漏），双重检查加锁防竞态
_client_lock = asyncio.Lock()


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                _client = httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False)
    return _client


async def close_image_client() -> None:
    """应用关闭时释放共享连接池（main.lifespan 调用）"""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _fetch_image(client: httpx.AsyncClient, target: str) -> tuple[bytes | None, str | None]:
    """单源拉取图片，返回 (bytes, content_type)：手动逐跳重定向校验白名单、
    仅接受 200 + MIME 精确白名单、Content-Length 预检 + 流式累计限大小。"""
    current = target
    for _ in range(_MAX_REDIRECTS + 1):
        try:
            async with client.stream("GET", current) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        return None, None
                    # 相对/绝对跳转统一解析为绝对 URL 后再校验（防重定向 SSRF）
                    next_url = str(httpx.URL(current).join(location))
                    if not _is_allowed_url(next_url):
                        return None, None
                    current = next_url
                    continue
                if resp.status_code != 200:
                    return None, None
                content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if content_type not in _SAFE_IMAGE_TYPES:
                    return None, None
                # 预告超限时一个字节都不读（与 fetch_capped 同一取舍）
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > _MAX_IMAGE_SIZE:
                    return None, None
                # 流式读取限制大小：client.get() 会先整包读入内存再检查，
                # 恶意超大文件可瞬间占满内存（DoS 向量）
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > _MAX_IMAGE_SIZE:
                        return None, None
                return bytes(buf), content_type
        except httpx.HTTPError:
            return None, None
    return None, None


async def _proxy_own_cos_object(key: str, fallback_url: str) -> Response:
    """本站 COS 对象中转：服务端凭证读取 → 同源回传

    失败一律 302 回源（浏览器直连兜底），与本端点既有的降级策略一致：
    中转失败不应该让图片彻底消失。
    """
    try:
        await asyncio.wait_for(_proxy_semaphore.acquire(), timeout=_PROXY_QUEUE_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning(f"图片代理并发已满（{_MAX_CONCURRENT_PROXY}），本站图片降级为 302 回源: {key}")
        return RedirectResponse(fallback_url)

    try:
        content, content_type = await cos_service.download_object_bytes(key, _MAX_IMAGE_SIZE)
    except Exception as exc:  # noqa: BLE001 - 任何读取失败都只是降级，不该 500
        logger.warning(f"本站 COS 图片中转失败，302 回源: {key} ({exc})")
        return RedirectResponse(fallback_url)
    finally:
        _proxy_semaphore.release()

    # MIME 精确白名单：COS 上可能存着 SVG / PDF，同源回传 SVG 等于交出登录态
    if content_type not in _SAFE_IMAGE_TYPES:
        guessed = (mimetypes.guess_type(key)[0] or "").lower()
        if guessed not in _SAFE_IMAGE_TYPES:
            logger.warning(f"本站 COS 对象不是可同源回传的图片类型（{content_type}），302 回源: {key}")
            return RedirectResponse(fallback_url)
        content_type = guessed

    # 对象 key 含随机 UUID、内容不可变，可放心长缓存（减少重复中转）
    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/images/search-thumb")
async def image_search_thumbnail(token: str = Query(..., min_length=10, max_length=4096)):
    """图库缩略图签名代理（公开但必须携带 HMAC 签名）。

    `<img>` 请求不会带 Authorization 头，所以不能复用管理端 JWT；token 由管理端搜索
    接口签发，包含图片 URL、来源与过期时间。fetch 内仍做主机白名单/公网 IP 校验。
    """
    try:
        target = image_search_thumb.verify_thumb(token)
    except AppException as exc:
        return JSONResponse(status_code=exc.code, content=error_response(exc.message))

    try:
        await asyncio.wait_for(
            _search_thumb_semaphore.acquire(), timeout=_SEARCH_THUMB_QUEUE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return JSONResponse(status_code=503, content=error_response("图片代理繁忙，请稍后重试"))
    try:
        content, content_type = await image_search_thumb.fetch_thumb(target)
    except AppException as exc:
        return JSONResponse(status_code=exc.code, content=error_response(exc.message))
    finally:
        _search_thumb_semaphore.release()
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=604800",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/images/proxy")
async def proxy_image(url: str = Query(..., min_length=10, max_length=2000)):
    """图片中转

    两条通道：
    1. **本站 COS 对象**：用服务端凭证按 key 读取后回传。存储桶侧的匿名访问可能被
       内容审核等策略拦成 403（对象完好、鉴权可读），浏览器直连就会裂图；前台在
       `<img>` 报错时会自动把 src 换成本端点，从而绕开匿名限制。
    2. **GitHub 图片资源**：多源中转（原始 URL → jsDelivr → 镜像）解决国内不可达。
    """
    own_key = _own_cos_key(url)
    if own_key:
        return await _proxy_own_cos_object(own_key, url)

    if not _is_allowed_url(url):
        return JSONResponse(
            status_code=400,
            content=error_response("仅支持代理本站图片与 GitHub 图片资源"),
        )

    # 并发闸门：本端点免鉴权、单请求峰值内存≈图片上限×2，不设闸门约 20 并发即 OOM 全站。
    # 饱和时降级为 302 回源（排队把内存压力变延迟、503 让图片裂开，回源由浏览器直连兜底）。
    try:
        await asyncio.wait_for(_proxy_semaphore.acquire(), timeout=_PROXY_QUEUE_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning(
            f"图片代理并发已满（{_MAX_CONCURRENT_PROXY}），本次降级为 302 回源: {url}"
        )
        return RedirectResponse(url)

    try:
        client = await _get_client()
        content: bytes | None = None
        content_type: str | None = None
        for target in _fetch_candidates(url):
            content, content_type = await _fetch_image(client, target)
            if content is not None:
                break
    finally:
        _proxy_semaphore.release()

    # 所有源均失败（不可达 / 非图片 / 超限）：302 回源，浏览器直连兜底
    if content is None:
        return RedirectResponse(url)

    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=86400",
            # 深度防御：即使未来白名单被误改，sandbox CSP 也阻止脚本执行
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )
