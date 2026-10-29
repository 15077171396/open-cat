"""多图源适配器契约：请求参数、字段归一化、错误映射。"""

import json
import unittest

import httpx

from app.core.exceptions import AppException
from app.services.image_search.base import SearchQuery
from app.services.image_search import providers as providers_module
from app.services.image_search.providers import (
    AdoutuProvider, BaiduProvider, DoutupkProvider, DuitangProvider,
    EmojiPackageProvider, GiphyProvider, MemecubProvider, PexelsProvider,
    PixabayProvider, So360Provider, SogouProvider, TenorCompatProvider,
    build_provider,
)


def _client(provider, handler):
    provider.client = httpx.AsyncClient(
        base_url=provider.base_url,
        headers=provider.headers,
        transport=httpx.MockTransport(handler),
    )
    return provider


class PexelsProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_maps_fields_and_params(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["auth"] = request.headers.get("authorization")
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json={
                "total_results": 50,
                "photos": [{
                    "id": 123,
                    "width": 4000,
                    "height": 3000,
                    "url": "https://www.pexels.com/photo/123/",
                    "photographer": "Alice",
                    "photographer_url": "https://www.pexels.com/@alice",
                    "alt": "A cat on a desk",
                    "src": {
                        "medium": "https://images.pexels.com/medium.jpg",
                        "large2x": "https://images.pexels.com/large2x.jpg",
                    },
                }],
            })

        provider = _client(PexelsProvider("pexels-key"), handler)
        page = await provider.search(SearchQuery(
            q="cat", page=2, per_page=12, image_type="photo", orientation="landscape",
        ))
        await provider.close()

        self.assertEqual(seen["path"], "/v1/search")
        self.assertEqual(seen["auth"], "pexels-key")
        self.assertEqual(seen["params"]["query"], "cat")
        self.assertEqual(seen["params"]["page"], "2")
        self.assertEqual(seen["params"]["per_page"], "12")
        self.assertEqual(seen["params"]["orientation"], "landscape")
        self.assertEqual(len(page.items), 1)
        hit = page.items[0]
        self.assertEqual(hit.source, "pexels")
        self.assertEqual(hit.source_id, "123")
        self.assertEqual(hit.author, "Alice")
        self.assertEqual(hit.author_url, "https://www.pexels.com/@alice")
        self.assertEqual(hit.page_url, "https://www.pexels.com/photo/123/")
        self.assertEqual(hit.thumb_url, "https://images.pexels.com/medium.jpg")
        self.assertEqual(hit.download_url, "https://images.pexels.com/large2x.jpg")
        self.assertEqual((hit.width, hit.height), (4000, 3000))
        self.assertEqual(hit.alt, "A cat on a desk")
        self.assertFalse(hit.requires_attribution)
        self.assertTrue(page.has_more)

    async def test_get_single_photo(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/photos/123")
            return httpx.Response(200, json={
                "id": 123, "width": 800, "height": 600,
                "url": "https://www.pexels.com/photo/123/",
                "photographer": "Alice", "photographer_url": "",
                "src": {"medium": "https://images.pexels.com/m.jpg",
                        "large2x": "https://images.pexels.com/l.jpg"},
            })

        provider = _client(PexelsProvider("k"), handler)
        hit = await provider.get("123")
        await provider.close()
        self.assertIsNotNone(hit)
        self.assertEqual(hit.source_id, "123")

    async def test_429_and_401_are_mapped(self):
        provider = _client(PexelsProvider("k"), lambda r: httpx.Response(429))
        with self.assertRaises(AppException) as ctx:
            await provider.search(SearchQuery(q="x", page=1, per_page=12))
        await provider.close()
        self.assertEqual(ctx.exception.code, 429)

        provider = _client(PexelsProvider("k"), lambda r: httpx.Response(401))
        with self.assertRaises(AppException) as ctx:
            await provider.search(SearchQuery(q="x", page=1, per_page=12))
        await provider.close()
        self.assertEqual(ctx.exception.code, 503)
        self.assertIn("Key", ctx.exception.message)

    async def test_missing_key_does_not_request(self):
        provider = PexelsProvider("")
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, json={})

        provider.client = httpx.AsyncClient(
            base_url=provider.base_url, transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(AppException) as ctx:
            await provider.search(SearchQuery(q="x", page=1, per_page=12))
        await provider.close()
        self.assertEqual(ctx.exception.code, 503)
        self.assertFalse(called)

    async def test_illustration_is_rejected_for_pexels(self):
        provider = _client(PexelsProvider("k"), lambda r: httpx.Response(200, json={}))
        with self.assertRaises(AppException) as ctx:
            await provider.search(SearchQuery(
                q="x", page=1, per_page=12, image_type="illustration",
            ))
        await provider.close()
        self.assertEqual(ctx.exception.code, 422)


class PixabayProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_maps_fields_and_params(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json={
                "total": 100,
                "totalHits": 100,
                "hits": [{
                    "id": 456,
                    "pageURL": "https://pixabay.com/photos/456/",
                    "type": "illustration",
                    "tags": "cat, desk, code",
                    "previewURL": "https://cdn.pixabay.com/p.jpg",
                    "webformatURL": "https://cdn.pixabay.com/w.jpg",
                    "largeImageURL": "https://cdn.pixabay.com/l.jpg",
                    "imageWidth": 1920,
                    "imageHeight": 1080,
                    "user": "Bob",
                    "user_id": 7,
                }],
            })

        provider = _client(PixabayProvider("pix-key"), handler)
        page = await provider.search(SearchQuery(
            q="cat", page=1, per_page=12, image_type="illustration",
            orientation="portrait",
        ))
        await provider.close()

        self.assertEqual(seen["path"], "/api/")
        self.assertEqual(seen["params"]["key"], "pix-key")
        self.assertEqual(seen["params"]["image_type"], "illustration")
        self.assertEqual(seen["params"]["orientation"], "vertical")
        self.assertEqual(seen["params"]["safesearch"], "true")
        hit = page.items[0]
        self.assertEqual(hit.source, "pixabay")
        self.assertEqual(hit.source_id, "456")
        self.assertEqual(hit.author, "Bob")
        self.assertEqual(hit.author_url, "https://pixabay.com/users/Bob-7/")
        self.assertEqual(hit.page_url, "https://pixabay.com/photos/456/")
        self.assertEqual(hit.thumb_url, "https://cdn.pixabay.com/w.jpg")
        self.assertEqual(hit.download_url, "https://cdn.pixabay.com/l.jpg")
        self.assertTrue(hit.requires_attribution)
        self.assertTrue(page.has_more)

    async def test_get_by_id(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.params["id"], "456")
            return httpx.Response(200, json={"hits": [{
                "id": 456, "pageURL": "https://pixabay.com/photos/456/",
                "webformatURL": "https://cdn.pixabay.com/w.jpg",
                "largeImageURL": "https://cdn.pixabay.com/l.jpg",
                "imageWidth": 800, "imageHeight": 600,
                "user": "Bob",
            }]})

        provider = _client(PixabayProvider("pix-key"), handler)
        hit = await provider.get("456")
        await provider.close()
        self.assertIsNotNone(hit)
        self.assertEqual(hit.source_id, "456")

    async def test_empty_result(self):
        provider = _client(
            PixabayProvider("pix-key"),
            lambda r: httpx.Response(200, json={"totalHits": 0, "hits": []}),
        )
        page = await provider.search(SearchQuery(q="nothing", page=1, per_page=12))
        await provider.close()
        self.assertEqual(page.items, [])
        self.assertFalse(page.has_more)


class BuildProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_and_unknown(self):
        provider = build_provider("pexels", "k")
        self.assertIsInstance(provider, PexelsProvider)
        await provider.close()
        with self.assertRaises(AppException):
            build_provider("unknown", "k")

    async def test_pixabay_key_not_leaked_in_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        provider = _client(PixabayProvider("super-secret"), handler)
        with self.assertRaises(AppException) as ctx:
            await provider.search(SearchQuery(q="x", page=1, per_page=12))
        await provider.close()
        self.assertNotIn("super-secret", ctx.exception.message)


class GiphyTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_maps_animated_gif(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/gifs/search")
            self.assertEqual(request.url.params["q"], "猫")
            return httpx.Response(200, json={
                "data": [{
                    "id": "abc123",
                    "title": "Cat",
                    "url": "https://giphy.com/gifs/abc123",
                    "user": {"display_name": "Bob", "profile_url": "https://giphy.com/bob"},
                    "images": {
                        "fixed_height": {"url": "https://media.giphy.com/fixed.gif", "width": "200", "height": "150"},
                        "fixed_height_small": {"url": "https://media.giphy.com/small.gif"},
                        "downsized": {"url": "https://media.giphy.com/down.gif"},
                        "original": {"url": "https://media.giphy.com/original.gif"},
                    },
                }],
                "pagination": {"total_count": 100},
            })

        provider = _client(GiphyProvider("giphy-key"), handler)
        page = await provider.search(SearchQuery(q="猫", page=1, per_page=12, image_type="meme"))
        await provider.close()
        hit = page.items[0]
        self.assertEqual(hit.source, "giphy")
        self.assertEqual(hit.download_url, "https://media.giphy.com/down.gif")
        self.assertEqual(hit.fallback_urls, ("https://media.giphy.com/original.gif",))
        self.assertEqual((hit.width, hit.height), (200, 150))
        self.assertEqual(hit.author, "Bob")
        self.assertTrue(page.has_more)


class TenorCompatTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_maps_media_formats(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v2/search")
            return httpx.Response(200, json={
                "results": [{
                    "id": "t1",
                    "content_description": "猫",
                    "h1_title": "Cat",
                    "itemurl": "https://tenor.com/view/t1",
                    "media_formats": {
                        "gif": {"url": "https://media.giphy.com/t1.gif", "dims": [320, 240]},
                        "tinygif": {"url": "https://media.giphy.com/t1-tiny.gif"},
                        "preview": {"url": "https://media.giphy.com/t1-preview.gif"},
                    },
                }],
                "next": "cursor",
            })

        provider = _client(TenorCompatProvider("giphy-key"), handler)
        page = await provider.search(SearchQuery(q="猫", page=1, per_page=12, image_type="meme"))
        await provider.close()
        hit = page.items[0]
        self.assertEqual(hit.source, "tenor")
        self.assertEqual(hit.download_url, "https://media.giphy.com/t1.gif")
        self.assertEqual((hit.width, hit.height), (320, 240))
        self.assertTrue(page.has_more)


class BaiduTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_preheats_cookie_and_maps_fields(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == "/search/index":
                return httpx.Response(200, text="<html>page</html>", headers={"set-cookie": "BAIDUID=x"})
            return httpx.Response(200, json={"data": [{
                "thumbURL": "https://img0.baidu.com/t.jpg",
                "middleURL": "https://img0.baidu.com/m.jpg",
                "fromPageTitle": "猫图",
                "width": 800, "height": 600,
            }]})

        provider = _client(BaiduProvider(""), handler)
        page = await provider.search(SearchQuery(q="猫", page=1, per_page=12))
        await provider.close()
        self.assertEqual(calls, ["/search/index", "/search/acjson"])
        hit = page.items[0]
        self.assertEqual(hit.source, "baidu")
        self.assertEqual(hit.download_url, "https://img0.baidu.com/m.jpg")
        self.assertEqual((hit.width, hit.height), (800, 600))

    async def test_tolerates_invalid_escape_in_json(self):
        """百度的 acjson 会在标题里吐出 `\\x` 这类非法转义，标准 JSON 解析直接失败。

        真实案例：搜索「光头强」时 67KB 响应在第 44 行报 Invalid \\escape。
        """
        body = (
            '{"data":[{"thumbURL":"https://img0.baidu.com/t.jpg",'
            '"middleURL":"https://img0.baidu.com/m.jpg",'
            '"fromPageTitle":"a\\xb 猫图","width":800,"height":600}]}'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/search/index":
                return httpx.Response(200, text="<html/>")
            return httpx.Response(200, text=body, headers={"content-type": "application/json"})

        provider = _client(BaiduProvider(""), handler)
        page = await provider.search(SearchQuery(q="猫", page=1, per_page=12))
        await provider.close()
        self.assertEqual(len(page.items), 1)
        self.assertEqual(page.items[0].download_url, "https://img0.baidu.com/m.jpg")

    async def test_anti_flag_is_explicit(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/search/index":
                return httpx.Response(200, text="<html/>")
            return httpx.Response(200, json={"antiFlag": 1, "message": "Forbid spider access"})

        provider = _client(BaiduProvider(""), handler)
        with self.assertRaises(AppException) as ctx:
            await provider.search(SearchQuery(q="猫", page=1, per_page=12))
        await provider.close()
        self.assertEqual(ctx.exception.code, 503)
        self.assertIn("安全验证", ctx.exception.message)


class So360Tests(unittest.IsolatedAsyncioTestCase):
    async def test_search_truncates_to_per_page(self):
        rows = [{
            "id": f"img-{index}",
            "title": f"猫图 {index}",
            "thumb": f"https://p0.ssl.qhimgs1.com/t{index}.jpg",
            "img": f"http://p0.so.qhimg.com/t{index}.jpg",
            "width": 800, "height": 600,
        } for index in range(20)]

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/j")
            self.assertEqual(request.url.params["q"], "猫")
            return httpx.Response(200, json={"list": rows, "end": False})

        provider = _client(So360Provider(""), handler)
        page = await provider.search(SearchQuery(q="猫", page=1, per_page=12))
        await provider.close()
        # 360 不认 rn、固定回 108 条，必须自己截断到 per_page
        self.assertEqual(len(page.items), 12)
        hit = page.items[0]
        self.assertEqual(hit.source, "so360")
        self.assertEqual(hit.thumb_url, "https://p0.ssl.qhimgs1.com/t0.jpg")
        # 原图只有 http（https 原图不通），缩略图作为下载回退
        self.assertEqual(hit.download_url, "http://p0.so.qhimg.com/t0.jpg")
        self.assertEqual(hit.fallback_urls, ("https://p0.ssl.qhimgs1.com/t0.jpg",))
        self.assertTrue(page.has_more)

    async def test_search_sends_referer(self):
        """360 缺 Referer 会返回「访问异常」的 HTML 而不是 JSON。

        表现为「返回格式异常」，很容易被误判成解析代码坏了，所以在这里钉死。
        """
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["referer"] = request.headers.get("referer", "")
            return httpx.Response(200, json={"list": [], "end": True})

        provider = _client(So360Provider(""), handler)
        await provider.search(SearchQuery(q="猫", page=1, per_page=6))
        await provider.close()
        self.assertEqual(seen["referer"], "https://image.so.com/")

    async def test_bad_payload_is_explicit(self):
        provider = _client(So360Provider(""), lambda r: httpx.Response(200, json={"oops": 1}))
        with self.assertRaises(AppException) as ctx:
            await provider.search(SearchQuery(q="猫", page=1, per_page=12))
        await provider.close()
        self.assertEqual(ctx.exception.code, 502)


class DuitangTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_maps_photo_and_pagination(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/napi/blog/list/by_search/")
            self.assertEqual(request.url.params["start"], "0")
            return httpx.Response(200, json={"status": 1, "data": {
                "total": 720, "next_start": 2,
                "object_list": [
                    {"id": 11, "msg": "猫", "photo": {
                        "path": "https://a-ho-img.dtstatic.com/a.jpeg",
                        "width": 1080, "height": 1920,
                    }},
                    {"id": 12, "msg": "没有图的条目", "photo": {}},
                ],
            }})

        provider = _client(DuitangProvider(""), handler)
        page = await provider.search(SearchQuery(q="猫", page=1, per_page=12))
        await provider.close()
        # 没有 photo.path 的条目必须被丢弃，否则前端会拿到空 src
        self.assertEqual(len(page.items), 1)
        hit = page.items[0]
        self.assertEqual(hit.source, "duitang")
        self.assertEqual(hit.download_url, "https://a-ho-img.dtstatic.com/a.jpeg")
        self.assertEqual((hit.width, hit.height), (1080, 1920))
        self.assertEqual(page.total, 720)
        self.assertTrue(page.has_more)


class AdoutuTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_parses_cards(self):
        body = (
            '<p>共找到 <!-- -->3337<!-- --> 张表情</p>'
            '<a href="/picture/111166"><div><img alt="熊猫头撩妹" loading="lazy" '
            'src="https://img.adoutu.com/picture/a.gif"/></div></a>'
            '<a href="/picture/111143"><div><img alt="老虎熊猫头" loading="lazy" '
            'src="https://img.adoutu.com/picture/b.jpg"/></div></a>'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/search")
            self.assertEqual(request.url.params["keyword"], "熊猫")
            self.assertEqual(request.url.params["type"], "1")
            return httpx.Response(200, text=body, headers={"content-type": "text/html"})

        provider = _client(AdoutuProvider(""), handler)
        page = await provider.search(SearchQuery(q="熊猫", page=1, per_page=24))
        await provider.close()
        self.assertEqual(len(page.items), 2)
        self.assertEqual(page.total, 3337)
        first = page.items[0]
        self.assertEqual(first.source, "adoutu")
        self.assertEqual(first.source_id, "111166")
        self.assertEqual(first.download_url, "https://img.adoutu.com/picture/a.gif")
        self.assertEqual(first.page_url, "https://www.adoutu.com/picture/111166")
        self.assertEqual(first.title, "熊猫头撩妹")
        # 只有 2 条 < per_page，不该再显示「加载更多」
        self.assertFalse(page.has_more)

    async def test_dedupes_repeated_images(self):
        card = (
            '<a href="/picture/{0}"><div><img alt="猫" src="https://img.adoutu.com/picture/same.jpg"/>'
            '</div></a>'
        )
        body = card.format(1) + card.format(2)

        provider = _client(
            AdoutuProvider(""), lambda r: httpx.Response(200, text=body),
        )
        page = await provider.search(SearchQuery(q="猫", page=1, per_page=24))
        await provider.close()
        self.assertEqual(len(page.items), 1)


class MemecubTests(unittest.IsolatedAsyncioTestCase):
    def _row(self, **overrides):
        row = {
            "id": "5z0g0pykd7sg",
            "title": "panda",
            "title_zh": "熊猫",
            "url": "https://gpic.code-nav.cn/memecub/public/a.gif",
            "thumb_url": "https://gpic.code-nav.cn/memecub/public/thumb/a.webp",
            "page_url": "https://memecub.com/meme/5z0g0pykd7sg",
            "width": 190, "height": 190,
            "license": {
                "name": "Web collected", "url": None,
                "commercial_use": False, "attribution_required": False,
            },
            "attribution": None,
        }
        row.update(overrides)
        return row

    async def test_search_maps_fields_and_prefers_chinese_title(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/v1/search")
            self.assertEqual(request.url.params["q"], "熊猫")
            self.assertEqual(request.url.params["limit"], "12")
            return httpx.Response(200, json={"data": [self._row()], "total": 5000})

        provider = _client(MemecubProvider(""), handler)
        page = await provider.search(SearchQuery(q="熊猫", page=1, per_page=12))
        await provider.close()
        hit = page.items[0]
        self.assertEqual(hit.source, "memecub")
        self.assertEqual(hit.title, "熊猫")
        self.assertEqual(hit.download_url, "https://gpic.code-nav.cn/memecub/public/a.gif")
        self.assertEqual(hit.thumb_url, "https://gpic.code-nav.cn/memecub/public/thumb/a.webp")
        self.assertEqual(page.total, 5000)
        self.assertFalse(page.has_more)

    async def test_attribution_is_per_image(self):
        """memecub 每张图许可证不同，requires_attribution 必须逐图取。"""
        row = self._row(license={
            "name": "CC BY", "url": "https://creativecommons.org/licenses/by/4.0/",
            "commercial_use": True, "attribution_required": True,
        }, attribution="© 某作者")
        provider = _client(
            MemecubProvider(""), lambda r: httpx.Response(200, json={"data": [row]}),
        )
        page = await provider.search(SearchQuery(q="猫", page=1, per_page=12))
        await provider.close()
        hit = page.items[0]
        self.assertTrue(hit.requires_attribution)
        self.assertEqual(hit.author, "© 某作者")
        self.assertEqual(hit.license, "CC BY")

    async def test_key_is_optional_but_sent_when_present(self):
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization", "")
            return httpx.Response(200, json={"data": [], "total": 0})

        for key in ("", "mb_test"):
            provider = _client(MemecubProvider(key), handler)
            await provider.search(SearchQuery(q="猫", page=1, per_page=12))
            await provider.close()
            self.assertEqual(seen["auth"], f"Bearer {key}" if key else "")

    async def test_credits_exhausted_is_explicit(self):
        provider = _client(MemecubProvider(""), lambda r: httpx.Response(402, json={}))
        with self.assertRaises(AppException) as ctx:
            await provider.search(SearchQuery(q="猫", page=1, per_page=12))
        await provider.close()
        self.assertEqual(ctx.exception.code, 402)
        self.assertIn("额度", ctx.exception.message)


class SogouTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_parses_initial_state(self):
        state = {"searchList": {"searchList": [{
            "picUrl": "https://pic2.zhimg.com/cat.jpg",
            "thumbUrl": "https://i03piccdn.sogoucdn.com/t",
            "url": "https://example.com/page",
            "title": "猫图",
            "width": 1200, "height": 800,
            "docId": "doc-1",
        }]}}
        body = f"<script>window.__INITIAL_STATE__={json.dumps(state, ensure_ascii=False)}</script>"

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/pics")
            return httpx.Response(200, text=body)

        provider = _client(SogouProvider(""), handler)
        page = await provider.search(SearchQuery(q="猫", page=1, per_page=12))
        await provider.close()
        hit = page.items[0]
        self.assertEqual(hit.source, "sogou")
        self.assertEqual(hit.download_url, "https://pic2.zhimg.com/cat.jpg")
        self.assertEqual(hit.page_url, "https://example.com/page")


class DoutupkTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_parses_photo_cards_and_upgrades_http(self):
        body = (
            '<a href="https://www.doutupk.com/photo/123"><img '
            'data-original="http://img.doutupk.com/a.gif"></a>'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/search")
            return httpx.Response(200, text=body)

        provider = _client(DoutupkProvider(""), handler)
        page = await provider.search(SearchQuery(q="猫", page=1, per_page=12, image_type="meme"))
        await provider.close()
        hit = page.items[0]
        self.assertEqual(hit.source, "doutupk")
        self.assertEqual(hit.download_url, "https://img.doutupk.com/a.gif")
        self.assertEqual(hit.page_url, "https://www.doutupk.com/photo/123")


class EmojiPackageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        providers_module._EMOJI_TREE_CACHE = None

    async def test_search_filters_repo_tree_and_builds_raw_url(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/repos/getActivity/EmojiPackage/git/trees/master")
            return httpx.Response(200, json={"tree": [
                {"type": "blob", "path": "猫/开心.jpg"},
                {"type": "blob", "path": "猫/无语.gif"},
                {"type": "blob", "path": "狗/开心.png"},
                {"type": "blob", "path": "README.md"},
            ]})

        provider = _client(EmojiPackageProvider(""), handler)
        page = await provider.search(SearchQuery(q="猫 开心", page=1, per_page=12, image_type="meme"))
        await provider.close()
        self.assertEqual(len(page.items), 1)
        hit = page.items[0]
        self.assertEqual(hit.source, "emoji")
        self.assertIn("raw.githubusercontent.com", hit.download_url)
        self.assertTrue(hit.download_url.endswith(".jpg"))
        self.assertIn("github.com/getActivity/EmojiPackage", hit.page_url)


class BuildAllProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_sources_build(self):
        for source in (
            "pexels", "pixabay", "giphy", "tenor", "so360", "baidu", "sogou",
            "duitang", "doutupk", "adoutu", "memecub", "emoji",
        ):
            provider = build_provider(source, "k")
            self.assertIsNotNone(provider)
            await provider.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
