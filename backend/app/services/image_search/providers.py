"""多图源适配器：官方 API、搜索引擎页面与表情包仓库统一归一化成 ImageHit。

日志刻意不打印请求 URL 或异常详情：Pixabay/GIPHY 的 Key 在 query string 里，
异常文本可能带出。网站抓取只解析公开页面，不绕过验证码或登录。
"""

import asyncio
import hashlib
import html
import json
import re
from urllib.parse import quote

import httpx
from loguru import logger

from app.core.exceptions import AppException
from app.services.image_search.base import ImageHit, SearchPage, SearchQuery

_SOURCE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TIMEOUT = httpx.Timeout(connect=3.0, read=8.0, write=3.0, pool=3.0)
_MAX_PER_PAGE = 40
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _source_id(value) -> str:
    text = str(value or "").strip()
    if not _SOURCE_ID.fullmatch(text):
        raise AppException(code=400, message="图片 ID 不合法")
    return text


def _hash_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _text(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


# 百度等图源会在 JSON 字符串里吐出非法转义（如 `\x`、`\'`），标准解析器直接
# 报 "Invalid \escape"。这不是我们的 bug，只能把非法转义补成字面反斜杠再解析一次。
_INVALID_ESCAPE = re.compile(r'\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})')


def _loads_lenient(text: str):
    """先按标准 JSON 解析，失败时修复非法转义后重试。仍失败则抛出原异常。"""
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(_INVALID_ESCAPE.sub(lambda _: "\\\\", text))


class _BaseProvider:
    name = ""
    license_name = ""
    license_url = ""
    requires_attribution = False
    requires_key = True
    image_hosts: tuple[str, ...] = ()
    types: tuple[str, ...] = ("photo",)
    base_url = ""
    headers: dict[str, str] = {}

    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = (api_key or "").strip()
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=_TIMEOUT,
            follow_redirects=False,
            transport=transport,
        )

    async def close(self) -> None:
        await self.client.aclose()

    def _require_key(self) -> None:
        if self.requires_key and not self.api_key:
            raise AppException(code=503, message=f"{self.name} 未配置 API Key，请联系管理员")

    async def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        self._require_key()
        try:
            return await self.client.get(path, params=params)
        except httpx.TimeoutException as exc:
            logger.warning(f"{self.name} 请求超时: {type(exc).__name__}")
            raise AppException(code=504, message=f"{self.name} 请求超时，请稍后重试") from exc
        except httpx.HTTPError as exc:
            logger.warning(f"{self.name} 网络错误: {type(exc).__name__}")
            raise AppException(code=502, message=f"{self.name} 暂时不可用") from exc

    def _check_status(self, response: httpx.Response, *, allow_404: bool = False) -> bool:
        status = response.status_code
        if status in (401, 403):
            logger.warning(f"{self.name} 拒绝访问: HTTP {status}")
            if self.requires_key:
                raise AppException(code=503, message=f"{self.name} API Key 无效或未授权")
            raise AppException(code=503, message=f"{self.name} 拒绝访问，请稍后重试或关闭该图源")
        if status == 429:
            logger.warning(f"{self.name} 额度耗尽: HTTP 429")
            raise AppException(code=429, message=f"{self.name} 请求额度已用完，请稍后重试")
        if status == 402:
            # memecub 的额度用尽语义（官方文档：402 insufficient_credits）
            logger.warning(f"{self.name} 额度耗尽: HTTP 402")
            raise AppException(code=402, message=f"{self.name} 额度已用完，请稍后重试")
        if status == 404 and allow_404:
            return False
        if status >= 500:
            logger.warning(f"{self.name} 上游错误: HTTP {status}")
            raise AppException(code=502, message=f"{self.name} 暂时不可用")
        if 300 <= status < 400:
            # 客户端不跟随重定向（避免被带到任意页面），所以这里必须显式报错。
            # 页面抓取类图源被重定向通常意味着反爬：跳图形验证码或地域页。
            location = (response.headers.get("location") or "")[:120]
            logger.warning(f"{self.name} 被重定向: HTTP {status} -> {location}")
            raise AppException(
                code=503, message=f"{self.name} 触发了安全验证，请稍后重试或关闭该图源",
            )
        if status >= 400:
            logger.warning(f"{self.name} 请求被拒绝: HTTP {status}")
            raise AppException(code=502, message=f"{self.name} 请求失败")
        return True

    async def _get_json(
        self, path: str, params: dict | None = None, *, allow_404: bool = False,
    ) -> dict | None:
        response = await self._get(path, params)
        if not self._check_status(response, allow_404=allow_404):
            return None
        try:
            payload = response.json()
        except ValueError as exc:
            # 百度的 acjson 会返回带非法转义的 JSON（标题里的反斜杠没转义），
            # 标准解析器一碰就炸，所以这里再宽松解析一次。
            try:
                payload = _loads_lenient(response.text)
            except ValueError:
                # 页面抓取类图源在反爬/限流时返回的是 HTML 验证页。直接说「格式异常」
                # 会让人以为是解析代码坏了，所以按 content-type 补一句真实原因。
                html = "html" in (response.headers.get("content-type") or "").lower()
                hint = "（站点返回了网页，可能触发了访问限制）" if html else ""
                logger.warning(f"{self.name} 返回非法 JSON{hint}")
                raise AppException(code=502, message=f"{self.name} 返回格式异常{hint}") from exc
        if not isinstance(payload, dict):
            raise AppException(code=502, message=f"{self.name} 返回格式异常")
        return payload

    async def _get_text(self, path: str, params: dict | None = None) -> str:
        response = await self._get(path, params)
        self._check_status(response)
        return response.text

    @staticmethod
    def _paging(page: int, per_page: int) -> tuple[int, int]:
        return max(1, page), min(max(1, per_page), _MAX_PER_PAGE)


class PexelsProvider(_BaseProvider):
    name = "Pexels"
    license_name = "Pexels License"
    license_url = "https://www.pexels.com/license/"
    image_hosts = ("images.pexels.com",)
    types = ("photo",)
    base_url = "https://api.pexels.com"

    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = (api_key or "").strip()
        self.headers = {"Authorization": self.api_key}
        super().__init__(api_key, transport=transport)

    async def search(self, query: SearchQuery) -> SearchPage:
        if query.image_type in {"illustration", "meme"}:
            raise AppException(code=422, message="Pexels 仅支持照片，请切换到其他图源")
        page, per_page = self._paging(query.page, query.per_page)
        params: dict[str, str | int] = {
            "query": query.q, "page": page, "per_page": per_page,
        }
        if query.orientation != "all":
            params["orientation"] = query.orientation
        payload = await self._get_json("/v1/search", params)
        photos = payload.get("photos")
        if not isinstance(photos, list):
            raise AppException(code=502, message="Pexels 返回格式异常")
        items = [hit for photo in photos if (hit := self._map(photo)) is not None]
        total = payload.get("total_results")
        total = total if isinstance(total, int) else None
        has_more = bool(payload.get("next_page")) or (
            total is not None and page * per_page < total
        )
        return SearchPage(items=items, total=total, has_more=has_more)

    async def get(self, source_id: str) -> ImageHit | None:
        item_id = _source_id(source_id)
        payload = await self._get_json(f"/v1/photos/{item_id}", allow_404=True)
        return self._map(payload) if payload else None

    def _map(self, photo) -> ImageHit | None:
        if not isinstance(photo, dict):
            return None
        src = photo.get("src") if isinstance(photo.get("src"), dict) else {}
        thumb = src.get("medium") or src.get("large") or src.get("original")
        download = src.get("large2x") or src.get("large") or src.get("original")
        if not thumb or not download:
            return None
        try:
            source_id = _source_id(photo.get("id"))
            width = int(photo.get("width") or 0)
            height = int(photo.get("height") or 0)
        except (AppException, TypeError, ValueError):
            return None
        alt = _text(photo.get("alt"), 300)
        return ImageHit(
            source="pexels",
            source_id=source_id,
            thumb_url=str(thumb),
            download_url=str(download),
            width=width,
            height=height,
            alt=alt,
            title=alt or f"Pexels 图片 {source_id}",
            author=_text(photo.get("photographer"), 200),
            author_url=_text(photo.get("photographer_url"), 1000),
            page_url=_text(photo.get("url"), 1000),
            license=self.license_name,
            license_url=self.license_url,
            requires_attribution=self.requires_attribution,
        )


class PixabayProvider(_BaseProvider):
    name = "Pixabay"
    license_name = "Pixabay Content License"
    license_url = "https://pixabay.com/service/license-summary/"
    requires_attribution = True
    image_hosts = ("cdn.pixabay.com",)
    types = ("photo", "illustration")
    base_url = "https://pixabay.com"

    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = (api_key or "").strip()
        self.headers = {}
        super().__init__(api_key, transport=transport)

    async def search(self, query: SearchQuery) -> SearchPage:
        page, per_page = self._paging(query.page, query.per_page)
        params: dict[str, str | int] = {
            "key": self.api_key, "q": query.q, "page": page, "per_page": per_page,
            "safesearch": "true",
        }
        if query.image_type == "photo":
            params["image_type"] = "photo"
        elif query.image_type == "illustration":
            params["image_type"] = "illustration"
        elif query.image_type == "meme":
            raise AppException(code=422, message="Pixabay 不支持表情包，请切换图源")
        if query.orientation in {"landscape", "portrait"}:
            params["orientation"] = "horizontal" if query.orientation == "landscape" else "vertical"
        payload = await self._get_json("/api/", params)
        hits = payload.get("hits")
        if not isinstance(hits, list):
            raise AppException(code=502, message="Pixabay 返回格式异常")
        items = [hit for row in hits if (hit := self._map(row)) is not None]
        total = payload.get("totalHits")
        total = total if isinstance(total, int) else None
        has_more = total is not None and page * per_page < total
        return SearchPage(items=items, total=total, has_more=has_more)

    async def get(self, source_id: str) -> ImageHit | None:
        item_id = _source_id(source_id)
        payload = await self._get_json(
            "/api/", {"key": self.api_key, "id": item_id, "safesearch": "true"},
            allow_404=True,
        )
        if payload is None:
            return None
        hits = payload.get("hits")
        if not isinstance(hits, list) or not hits:
            return None
        return self._map(hits[0])

    def _map(self, row) -> ImageHit | None:
        if not isinstance(row, dict):
            return None
        thumb = row.get("webformatURL")
        download = row.get("largeImageURL") or row.get("webformatURL")
        if not thumb or not download:
            return None
        try:
            source_id = _source_id(row.get("id"))
            width = int(row.get("imageWidth") or 0)
            height = int(row.get("imageHeight") or 0)
        except (AppException, TypeError, ValueError):
            return None
        tags = _text(row.get("tags"), 300)
        author = _text(row.get("user"), 200)
        user_id = row.get("user_id")
        author_url = ""
        if author and user_id:
            author_url = f"https://pixabay.com/users/{quote(author)}-{user_id}/"
        return ImageHit(
            source="pixabay",
            source_id=source_id,
            thumb_url=str(thumb),
            download_url=str(download),
            width=width,
            height=height,
            alt=tags.replace(",", "，")[:300],
            title=tags[:300] or f"Pixabay 图片 {source_id}",
            author=author,
            author_url=author_url,
            page_url=_text(row.get("pageURL"), 1000),
            license=self.license_name,
            license_url=self.license_url,
            requires_attribution=self.requires_attribution,
        )


class GiphyProvider(_BaseProvider):
    name = "GIPHY"
    license_name = "GIPHY"
    license_url = "https://developers.giphy.com/"
    image_hosts = (
        "media.giphy.com", "i.giphy.com",
        "media0.giphy.com", "media1.giphy.com", "media2.giphy.com",
        "media3.giphy.com", "media4.giphy.com",
    )
    types = ("meme",)
    base_url = "https://api.giphy.com"

    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = (api_key or "").strip()
        self.headers = {}
        super().__init__(api_key, transport=transport)

    async def search(self, query: SearchQuery) -> SearchPage:
        if query.image_type not in {"all", "meme"}:
            raise AppException(code=422, message="GIPHY 仅支持表情包")
        page, per_page = self._paging(query.page, query.per_page)
        payload = await self._get_json("/v1/gifs/search", {
            "api_key": self.api_key, "q": query.q, "limit": per_page,
            "offset": (page - 1) * per_page, "rating": "pg-13", "lang": "zh-CN",
        })
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise AppException(code=502, message="GIPHY 返回格式异常")
        items = [hit for row in rows if (hit := self._map(row)) is not None]
        pagination = payload.get("pagination")
        total = pagination.get("total_count") if isinstance(pagination, dict) else None
        try:
            has_more = bool(total) and page * per_page < int(total)
        except (TypeError, ValueError):
            has_more = False
        return SearchPage(items=items, total=total if isinstance(total, int) else None,
                          has_more=has_more)

    async def get(self, source_id: str) -> ImageHit | None:
        payload = await self._get_json(
            f"/v1/gifs/{_source_id(source_id)}", {"api_key": self.api_key}, allow_404=True,
        )
        return self._map(payload.get("data")) if payload and isinstance(payload.get("data"), dict) else None

    def _map(self, row) -> ImageHit | None:
        if not isinstance(row, dict):
            return None
        images = row.get("images") if isinstance(row.get("images"), dict) else {}
        fixed = images.get("fixed_height") if isinstance(images.get("fixed_height"), dict) else {}
        small = images.get("fixed_height_small") if isinstance(images.get("fixed_height_small"), dict) else {}
        downsized = images.get("downsized") if isinstance(images.get("downsized"), dict) else {}
        original = images.get("original") if isinstance(images.get("original"), dict) else {}
        thumb = small.get("url") or fixed.get("url") or original.get("url")
        download = downsized.get("url") or fixed.get("url") or original.get("url")
        if not thumb or not download:
            return None
        source_id = _text(row.get("id"), 64)
        if not source_id:
            return None
        fallback = original.get("url") if original.get("url") and original.get("url") != download else ""
        user = row.get("user") if isinstance(row.get("user"), dict) else {}
        return ImageHit(
            source="giphy",
            source_id=source_id,
            thumb_url=str(thumb),
            download_url=str(download),
            width=_int(fixed.get("width") or original.get("width")),
            height=_int(fixed.get("height") or original.get("height")),
            alt=_text(row.get("title"), 300),
            title=_text(row.get("title"), 300) or "GIPHY",
            author=_text(user.get("display_name") or user.get("username"), 200),
            author_url=_text(user.get("profile_url"), 1000),
            page_url=_text(row.get("url"), 1000),
            license=self.license_name,
            license_url=self.license_url,
            requires_attribution=self.requires_attribution,
            fallback_urls=(fallback,) if fallback else (),
        )


class TenorCompatProvider(_BaseProvider):
    """Tenor 官方 API 已停服；这里走 GIPHY 的 /v2 Tenor 兼容端点。"""

    name = "Tenor 兼容（GIPHY）"
    license_name = "Tenor / GIPHY"
    license_url = "https://developers.giphy.com/docs/api/tenor-migration/"
    image_hosts = GiphyProvider.image_hosts
    types = ("meme",)
    base_url = "https://api.giphy.com"

    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = (api_key or "").strip()
        self.headers = {}
        super().__init__(api_key, transport=transport)

    async def search(self, query: SearchQuery) -> SearchPage:
        if query.image_type not in {"all", "meme"}:
            raise AppException(code=422, message="Tenor 兼容源仅支持表情包")
        page, per_page = self._paging(query.page, query.per_page)
        payload = await self._get_json("/v2/search", {
            "key": self.api_key, "q": query.q, "limit": per_page,
            "pos": str((page - 1) * per_page),
        })
        rows = payload.get("results")
        if not isinstance(rows, list):
            raise AppException(code=502, message="Tenor 返回格式异常")
        items = [hit for row in rows if (hit := self._map(row)) is not None]
        return SearchPage(items=items, total=None, has_more=bool(payload.get("next")))

    async def get(self, source_id: str) -> ImageHit | None:
        return None

    def _map(self, row) -> ImageHit | None:
        if not isinstance(row, dict):
            return None
        media = row.get("media_formats") if isinstance(row.get("media_formats"), dict) else {}
        gif = media.get("gif") if isinstance(media.get("gif"), dict) else {}
        tiny = media.get("tinygif") if isinstance(media.get("tinygif"), dict) else {}
        preview = media.get("preview") if isinstance(media.get("preview"), dict) else {}
        thumb = tiny.get("url") or preview.get("url") or gif.get("url")
        download = gif.get("url") or thumb
        if not thumb or not download:
            return None
        dims = gif.get("dims") if isinstance(gif.get("dims"), list) else []
        source_id = _text(row.get("id"), 64)
        if not source_id:
            return None
        return ImageHit(
            source="tenor",
            source_id=source_id,
            thumb_url=str(thumb),
            download_url=str(download),
            width=_int(dims[0]) if len(dims) > 0 else 0,
            height=_int(dims[1]) if len(dims) > 1 else 0,
            alt=_text(row.get("content_description"), 300),
            title=_text(row.get("h1_title") or row.get("content_description"), 300) or "Tenor",
            page_url=_text(row.get("itemurl"), 1000),
            license=self.license_name,
            license_url=self.license_url,
            requires_attribution=self.requires_attribution,
        )


class BaiduProvider(_BaseProvider):
    name = "百度图片"
    license_name = "网络图片（百度）"
    requires_key = False
    types = ("photo", "illustration", "meme")
    base_url = "https://image.baidu.com"
    headers = {
        "User-Agent": _UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://image.baidu.com/",
    }

    async def search(self, query: SearchQuery) -> SearchPage:
        page, per_page = self._paging(query.page, query.per_page)
        # 预热页面拿 Cookie；拿不到也继续试 JSON，失败时给出明确错误。
        try:
            await self._get_text("/search/index", {"tn": "baiduimage", "word": query.q})
        except AppException:
            pass
        payload = await self._get_json("/search/acjson", {
            "tn": "resultjson_com", "ipn": "rj", "word": query.q,
            "pn": (page - 1) * per_page, "rn": per_page,
        })
        if payload.get("antiFlag"):
            raise AppException(code=503, message="百度图片触发了安全验证，请稍后重试或关闭该图源")
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise AppException(code=502, message="百度图片返回格式异常")
        items = [hit for row in rows if (hit := self._map(row, query.q)) is not None][:per_page]
        return SearchPage(items=items, total=None, has_more=len(items) >= per_page)

    async def get(self, source_id: str) -> ImageHit | None:
        return None

    def _map(self, row, query: str) -> ImageHit | None:
        if not isinstance(row, dict):
            return None
        thumb = _text(row.get("thumbURL") or row.get("hoverURL"), 1000)
        download = _text(row.get("middleURL") or row.get("thumbURL"), 1000)
        if not download:
            return None
        source_id = _hash_id(download + str(row.get("width")) + str(row.get("height")))
        title = _text(row.get("fromPageTitle") or query, 300)
        return ImageHit(
            source="baidu",
            source_id=source_id,
            thumb_url=thumb or download,
            download_url=download,
            width=_int(row.get("width")),
            height=_int(row.get("height")),
            alt=title,
            title=title,
            page_url=f"https://image.baidu.com/search/index?tn=baiduimage&word={quote(query)}",
            license=self.license_name,
            requires_attribution=self.requires_attribution,
            fallback_urls=(thumb,) if thumb and thumb != download else (),
        )


class SogouProvider(_BaseProvider):
    name = "搜狗图片"
    license_name = "网络图片（搜狗）"
    requires_key = False
    types = ("photo", "illustration", "meme")
    base_url = "https://pic.sogou.com"
    headers = {
        "User-Agent": _UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://pic.sogou.com/",
    }

    async def search(self, query: SearchQuery) -> SearchPage:
        page, per_page = self._paging(query.page, query.per_page)
        text = await self._get_text("/pics", {
            "query": query.q, "start": (page - 1) * per_page,
        })
        match = re.search(r"window\.__INITIAL_STATE__=(\{.*?\})</script>", text, re.S)
        if not match:
            raise AppException(code=502, message="搜狗图片页面结构已变化")
        try:
            payload = json.loads(match.group(1))
        except ValueError as exc:
            raise AppException(code=502, message="搜狗图片返回格式异常") from exc
        rows = payload.get("searchList", {}).get("searchList", [])
        if not isinstance(rows, list):
            raise AppException(code=502, message="搜狗图片返回格式异常")
        items = [hit for row in rows if (hit := self._map(row, query.q)) is not None][:per_page]
        return SearchPage(items=items, total=None, has_more=len(items) >= per_page)

    async def get(self, source_id: str) -> ImageHit | None:
        return None

    def _map(self, row, query: str) -> ImageHit | None:
        if not isinstance(row, dict):
            return None
        pic = _text(row.get("picUrl"), 1000)
        thumb = _text(row.get("thumbUrl") or row.get("locImageLink"), 1000)
        if not pic:
            return None
        source_id = _text(row.get("docId") or row.get("mf_id"), 64) or _hash_id(pic)
        title = _text(row.get("title") or query, 300)
        return ImageHit(
            source="sogou",
            source_id=source_id,
            thumb_url=thumb or pic,
            download_url=pic,
            width=_int(row.get("width")),
            height=_int(row.get("height")),
            alt=title,
            title=title,
            page_url=_text(row.get("url"), 1000),
            license=self.license_name,
            requires_attribution=self.requires_attribution,
            fallback_urls=(thumb,) if thumb and thumb != pic else (),
        )


class So360Provider(_BaseProvider):
    """360 图片：国内直连快，公开 JSON 接口，无需 Key。

    注意它不认 `rn`，一次固定回 108 条，所以必须自己截断到 per_page，
    否则一页就能把前端网格撑爆。
    """

    name = "360 图片"
    license_name = "网络图片（360）"
    requires_key = False
    types = ("photo", "illustration", "meme")
    base_url = "https://image.so.com"
    # Referer 是硬要求：不带会被判定为异常访问，返回「360图片_访问异常」的 HTML
    # 而不是 JSON，表现为「返回格式异常」。
    headers = {
        "User-Agent": _UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://image.so.com/",
    }

    async def search(self, query: SearchQuery) -> SearchPage:
        page, per_page = self._paging(query.page, query.per_page)
        payload = await self._get_json("/j", {
            "q": query.q, "src": "srp", "correct": query.q,
            "adstar": 0, "tab": "all",
            "pn": (page - 1) * per_page, "rn": per_page,
        })
        rows = payload.get("list")
        if not isinstance(rows, list):
            raise AppException(code=502, message="360 图片返回格式异常")
        items = [
            hit for row in rows if (hit := self._map(row, query.q)) is not None
        ][:per_page]
        return SearchPage(items=items, total=None, has_more=not payload.get("end"))

    async def get(self, source_id: str) -> ImageHit | None:
        return None

    def _map(self, row, query: str) -> ImageHit | None:
        if not isinstance(row, dict):
            return None
        thumb = _text(row.get("thumb") or row.get("thumb_bak"), 1000)
        original = _text(row.get("img"), 1000)
        if not thumb and not original:
            return None
        source_id = _text(row.get("id"), 64) or _hash_id(thumb or original)
        title = _text(row.get("title"), 300) or query
        return ImageHit(
            source="so360",
            source_id=source_id,
            thumb_url=thumb or original,
            download_url=original or thumb,
            width=_int(row.get("width")),
            height=_int(row.get("height")),
            alt=title,
            title=title,
            page_url=f"https://image.so.com/i?q={quote(query)}",
            license=self.license_name,
            requires_attribution=self.requires_attribution,
            # 原图是 http 且 https 原图不通；下载失败时回退到 https 缩略图。
            fallback_urls=(thumb,) if thumb and thumb != original else (),
        )


class DuitangProvider(_BaseProvider):
    """堆糖：国内图片社区，公开 JSON 接口，支持 start/limit 分页。"""

    name = "堆糖"
    license_name = "网络图片（堆糖）"
    requires_key = False
    types = ("photo", "illustration")
    base_url = "https://www.duitang.com"
    headers = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"}

    async def search(self, query: SearchQuery) -> SearchPage:
        page, per_page = self._paging(query.page, query.per_page)
        payload = await self._get_json("/napi/blog/list/by_search/", {
            "kw": query.q, "start": (page - 1) * per_page, "limit": per_page,
        })
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        rows = data.get("object_list")
        if not isinstance(rows, list):
            raise AppException(code=502, message="堆糖返回格式异常")
        items = [
            hit for row in rows if (hit := self._map(row, query.q)) is not None
        ][:per_page]
        total = data.get("total")
        total = total if isinstance(total, int) else None
        has_more = bool(data.get("next_start")) or (
            total is not None and page * per_page < total
        )
        return SearchPage(items=items, total=total, has_more=has_more)

    async def get(self, source_id: str) -> ImageHit | None:
        return None

    def _map(self, row, query: str) -> ImageHit | None:
        if not isinstance(row, dict):
            return None
        photo = row.get("photo") if isinstance(row.get("photo"), dict) else {}
        url = _text(photo.get("path"), 1000)
        if not url:
            return None
        source_id = _text(row.get("id") or photo.get("id"), 64) or _hash_id(url)
        title = _text(row.get("msg") or query, 300)
        return ImageHit(
            source="duitang",
            source_id=source_id,
            # 堆糖只给一个原图地址，没有独立缩略图；列表靠代理 + 浏览器缓存省流量。
            thumb_url=url,
            download_url=url,
            width=_int(photo.get("width")),
            height=_int(photo.get("height")),
            alt=title,
            title=title,
            page_url=f"https://www.duitang.com/search/?kw={quote(query)}",
            license=self.license_name,
            requires_attribution=self.requires_attribution,
        )


class DoutupkProvider(_BaseProvider):
    name = "斗图啦"
    license_name = "网络图片（斗图啦）"
    requires_key = False
    types = ("meme",)
    image_hosts = ("img.doutupk.com",)
    base_url = "https://www.doutupk.com"
    headers = {
        "User-Agent": _UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.doutupk.com/",
    }

    async def search(self, query: SearchQuery) -> SearchPage:
        page, per_page = self._paging(query.page, query.per_page)
        text = await self._get_text("/search", {
            "type": "photo", "more": 1, "keyword": query.q, "page": page,
        })
        rows = re.findall(
            r'<a[^>]+href="(https://www\.doutupk\.com/photo/[^"]+)"[^>]*>'
            r'\s*<img[^>]+data-original="([^"]+)"',
            text, re.S,
        )
        items: list[ImageHit] = []
        seen: set[str] = set()
        for page_url, image_url in rows:
            image_url = html.unescape(image_url).replace("http://", "https://", 1)
            if image_url in seen:
                continue
            seen.add(image_url)
            photo_id = page_url.rstrip("/").rsplit("/", 1)[-1]
            fallback = ""
            items.append(ImageHit(
                source="doutupk",
                source_id=photo_id or _hash_id(image_url),
                thumb_url=image_url,
                download_url=image_url,
                width=0, height=0,
                alt=query.q,
                title=query.q,
                page_url=page_url,
                license=self.license_name,
                requires_attribution=self.requires_attribution,
                fallback_urls=(fallback,) if fallback else (),
            ))
        items = items[:per_page]
        return SearchPage(items=items, total=None, has_more=len(items) >= per_page)

    async def get(self, source_id: str) -> ImageHit | None:
        return None


_EMOJI_TREE_CACHE: tuple[float, list[str]] | None = None
_EMOJI_TREE_LOCK = asyncio.Lock()
_EMOJI_REPO = "getActivity/EmojiPackage"


_ADOUTU_CARD = re.compile(
    r'href="/picture/(\d+)"[^>]*>.*?<img[^>]+alt="([^"]*)"[^>]+src="(https://img\.adoutu\.com/[^"]+)"',
    re.S,
)
_ADOUTU_TOTAL = re.compile(r"共找到 <!-- -->(\d+)<!-- --> 张")


class AdoutuProvider(_BaseProvider):
    """爱斗图：国内表情包站，Next.js SSR 页面，图片直链在 HTML 里。

    图片域名固定为 img.adoutu.com，所以走精确白名单而不是「任意公网主机」。
    """

    name = "爱斗图"
    license_name = "网络图片（爱斗图）"
    requires_key = False
    types = ("meme",)
    image_hosts = ("img.adoutu.com",)
    base_url = "https://www.adoutu.com"
    headers = {
        "User-Agent": _UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.adoutu.com/",
    }

    async def search(self, query: SearchQuery) -> SearchPage:
        page, per_page = self._paging(query.page, query.per_page)
        text = await self._get_text("/search", {
            "type": 1, "keyword": query.q, "page": page,
        })
        rows = _ADOUTU_CARD.findall(text)
        items: list[ImageHit] = []
        seen: set[str] = set()
        for picture_id, alt, image_url in rows:
            image_url = html.unescape(image_url)
            if image_url in seen:
                continue
            seen.add(image_url)
            title = html.unescape(alt).strip() or query.q
            items.append(ImageHit(
                source="adoutu",
                source_id=picture_id or _hash_id(image_url),
                # 站点只提供一张图，没有独立缩略图；列表靠代理 + 浏览器缓存省流量。
                thumb_url=image_url,
                download_url=image_url,
                width=0, height=0,
                alt=title,
                title=title,
                page_url=f"https://www.adoutu.com/picture/{picture_id}",
                license=self.license_name,
                requires_attribution=self.requires_attribution,
            ))
        total = _ADOUTU_TOTAL.search(text)
        items = items[:per_page]
        return SearchPage(
            items=items,
            total=int(total.group(1)) if total else None,
            has_more=len(rows) >= per_page,
        )

    async def get(self, source_id: str) -> ImageHit | None:
        return None


class MemecubProvider(_BaseProvider):
    """memecub：走官方 REST API（文档见 https://memecub.com/zh/developers）。

    ⚠️ 站点 robots.txt 明确 `Disallow: /zh/search`，所以**不要**去抓搜索页；
    官方文档把 `/api/v1/*` 提供给开发者使用，这里走 API 是官方支持的接入方式。
    Key 可选：不填时每个 IP 每天 20 次搜索，填写后按月额度（402 = 额度耗尽）。
    """

    name = "memecub"
    license_name = "memecub"
    requires_key = False
    types = ("meme", "photo")
    image_hosts = ("gpic.code-nav.cn",)
    base_url = "https://memecub.com"
    headers = {"Accept": "application/json"}

    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = (api_key or "").strip()
        self.headers = {"Accept": "application/json"}
        if self.api_key:
            self.headers["Authorization"] = f"Bearer {self.api_key}"
        super().__init__(api_key, transport=transport)

    async def search(self, query: SearchQuery) -> SearchPage:
        page, per_page = self._paging(query.page, query.per_page)
        payload = await self._get_json("/api/v1/search", {
            "q": query.q, "limit": per_page, "offset": (page - 1) * per_page,
        })
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise AppException(code=502, message="memecub 返回格式异常")
        items = [
            hit for row in rows if (hit := self._map(row)) is not None
        ][:per_page]
        total = payload.get("total")
        total = total if isinstance(total, int) else None
        return SearchPage(
            items=items, total=total,
            has_more=bool(rows) and len(items) >= per_page,
        )

    async def get(self, source_id: str) -> ImageHit | None:
        payload = await self._get_json(
            f"/api/v1/memes/{_source_id(source_id)}", allow_404=True,
        )
        if not payload:
            return None
        data = payload.get("data")
        return self._map(data) if isinstance(data, dict) else None

    def _map(self, row) -> ImageHit | None:
        if not isinstance(row, dict):
            return None
        item_id = _text(row.get("id"), 64)
        thumb = _text(row.get("thumb_url"), 1000)
        original = _text(row.get("url"), 1000)
        if not item_id or (not thumb and not original):
            return None
        license_info = row.get("license") if isinstance(row.get("license"), dict) else {}
        # 中文标题优先；description 是英文长描述，只作兜底
        title = _text(
            row.get("title_zh") or row.get("title") or row.get("description"), 300,
        )
        return ImageHit(
            source="memecub",
            source_id=item_id,
            thumb_url=thumb or original,
            download_url=original or thumb,
            width=_int(row.get("width")),
            height=_int(row.get("height")),
            alt=title,
            title=title or "memecub",
            author=_text(row.get("attribution"), 200),
            page_url=_text(row.get("page_url"), 1000),
            license=_text(license_info.get("name"), 100) or self.license_name,
            license_url=_text(license_info.get("url"), 1000),
            # 逐图判断：memecub 每张图的许可证不同，有些要求署名
            requires_attribution=bool(license_info.get("attribution_required")),
            fallback_urls=(thumb,) if thumb and thumb != original else (),
        )


class EmojiPackageProvider(_BaseProvider):
    name = "EmojiPackage"
    license_name = "GitHub EmojiPackage"
    license_url = "https://github.com/getActivity/EmojiPackage"
    requires_key = False
    types = ("meme",)
    image_hosts = ("raw.githubusercontent.com",)
    base_url = "https://api.github.com"
    headers = {"User-Agent": "ai-cat-image-search", "Accept": "application/vnd.github+json"}

    async def _tree(self) -> list[str]:
        global _EMOJI_TREE_CACHE
        now = asyncio.get_running_loop().time()
        if _EMOJI_TREE_CACHE and now - _EMOJI_TREE_CACHE[0] < 3600:
            return _EMOJI_TREE_CACHE[1]
        async with _EMOJI_TREE_LOCK:
            now = asyncio.get_running_loop().time()
            if _EMOJI_TREE_CACHE and now - _EMOJI_TREE_CACHE[0] < 3600:
                return _EMOJI_TREE_CACHE[1]
            payload = await self._get_json(
                f"/repos/{_EMOJI_REPO}/git/trees/master?recursive=1",
            )
            tree = payload.get("tree") if isinstance(payload, dict) else None
            if not isinstance(tree, list):
                raise AppException(code=502, message="EmojiPackage 仓库结构异常")
            exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
            paths = [
                item["path"] for item in tree
                if isinstance(item, dict) and item.get("type") == "blob"
                and str(item.get("path", "")).lower().endswith(tuple(exts))
            ]
            _EMOJI_TREE_CACHE = (now, paths)
            return paths

    async def search(self, query: SearchQuery) -> SearchPage:
        page, per_page = self._paging(query.page, query.per_page)
        paths = await self._tree()
        tokens = [token.casefold() for token in query.q.split() if token]
        matched = [
            path for path in paths
            if all(token in path.casefold() for token in tokens)
        ]
        start = (page - 1) * per_page
        selected = matched[start:start + per_page]
        items = [hit for path in selected if (hit := self._map(path)) is not None]
        return SearchPage(items=items, total=len(matched), has_more=start + per_page < len(matched))

    async def get(self, source_id: str) -> ImageHit | None:
        return None

    def _map(self, path: str) -> ImageHit | None:
        encoded = quote(path, safe="/")
        download = f"https://raw.githubusercontent.com/{_EMOJI_REPO}/master/{encoded}"
        title = path.rsplit("/", 1)[-1].rsplit(".", 1)[0][:300]
        return ImageHit(
            source="emoji",
            source_id=_hash_id(path),
            thumb_url=download,
            download_url=download,
            width=0, height=0,
            alt=title,
            title=title,
            page_url=f"https://github.com/{_EMOJI_REPO}/blob/master/{encoded}",
            license=self.license_name,
            license_url=self.license_url,
            requires_attribution=self.requires_attribution,
        )


_PROVIDERS = {
    "pexels": PexelsProvider,
    "pixabay": PixabayProvider,
    "giphy": GiphyProvider,
    "tenor": TenorCompatProvider,
    "so360": So360Provider,
    "baidu": BaiduProvider,
    "sogou": SogouProvider,
    "duitang": DuitangProvider,
    "doutupk": DoutupkProvider,
    "adoutu": AdoutuProvider,
    "memecub": MemecubProvider,
    "emoji": EmojiPackageProvider,
}


def build_provider(
    source: str, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None,
) -> _BaseProvider:
    cls = _PROVIDERS.get(source)
    if cls is None:
        raise AppException(code=400, message="不支持的图片图源")
    return cls(api_key, transport=transport)
