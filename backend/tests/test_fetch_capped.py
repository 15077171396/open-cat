"""第三方拉取的「流式限流」回归（缺口台账 P2-6 / 代码审查 D4）。

问题：拉取正文与图片时先 ``await client.get(url)`` 把响应**完整读进内存**，
再判断 ``len(resp.content) > 上限`` —— 那只是「事后报警」：一个 500MB 的
raw README / 图片会在检查之前就把进程内存打满。另外 README 里的图片数量
完全由对方仓库决定，转存**没有数量上限**。

本文件把三条契约钉死：
1. 超限必须**边读边中止**（不是读完再判）——用「实际读了多少个分块」证明；
2. 「超限」与「网络异常」必须可区分——前者提示精简内容，后者提示重试；
3. 单次转存图片数量有上限，且截断顺序稳定（每次都转存正文最靠前的 N 张）。

全部离线：用桩 client 模拟流式响应，不发起真实 HTTP 请求。
"""

import gzip
import unittest
from unittest import mock

import httpx

from app.core.exceptions import AppException
from app.services.github_readme_service import _fetch_raw
from app.services.storage import storage
from app.services.third_party_fetch import common


class _StreamResponse:
    """桩流式响应：记录被消费了多少个分块，用于证明「提前中止」。"""

    def __init__(self, chunks, status_code=200, headers=None, encoding="utf-8", url="http://x/"):
        self._chunks = chunks
        self.status_code = status_code
        self.headers = headers if headers is not None else {}
        self.encoding = encoding
        self.url = url
        self.yielded = 0

    async def aiter_bytes(self, chunk_size=None):
        for chunk in self._chunks:
            self.yielded += 1
            yield chunk


class _StreamCtx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc_info):
        return False


class _FakeStreamClient:
    """桩 client：只实现 fetch_capped 用到的 stream()。

    记录最后一次调用的参数，用于断言 method / params / json body 是否被正确透传。
    """

    def __init__(self, resp=None, error=None):
        self._resp = resp
        self._error = error
        self.requests = 0
        self.last_call: dict = {}

    def stream(self, method, url, headers=None, params=None, json=None):
        self.requests += 1
        self.last_call = {
            "method": method, "url": url,
            "headers": headers, "params": params, "json": json,
        }
        if self._error is not None:
            raise self._error
        return _StreamCtx(self._resp)


class FetchCappedTests(unittest.IsolatedAsyncioTestCase):
    async def test_content_length_precheck_skips_body_entirely(self):
        """Content-Length 预告超限时连一个分块都不该读"""
        resp = _StreamResponse(
            [b"x" * 10] * 5, headers={"content-length": "999999", "content-type": "text/plain"}
        )
        body = await common.fetch_capped(_FakeStreamClient(resp), "http://x/", 100)
        self.assertIsNotNone(body)
        self.assertTrue(body.over_limit)
        self.assertEqual(resp.yielded, 0, "预告超限却仍读取了响应体")
        self.assertEqual(body.content, b"")

    async def test_aborts_mid_stream_without_reading_rest(self):
        """分块累计超限必须**立刻**中止，而不是读完再判断"""
        resp = _StreamResponse([b"x" * 10] * 10, headers={"content-type": "text/plain"})
        body = await common.fetch_capped(_FakeStreamClient(resp), "http://x/", 25)
        self.assertTrue(body.over_limit)
        # 10 → 20 → 30 > 25，第 3 个分块就该停手
        self.assertEqual(resp.yielded, 3, f"应读 3 个分块即中止，实际 {resp.yielded}")
        self.assertEqual(body.content, b"")

    async def test_within_limit_returns_body_and_decodes_text(self):
        resp = _StreamResponse(
            ["你好".encode("utf-8")],
            headers={"content-type": "text/plain; charset=utf-8"},
            encoding="utf-8",
        )
        body = await common.fetch_capped(_FakeStreamClient(resp), "http://x/", 100)
        self.assertFalse(body.over_limit)
        self.assertEqual(body.status_code, 200)
        self.assertEqual(body.text, "你好")
        self.assertEqual(body.content_type, "text/plain")

    async def test_non_200_returns_status_without_reading_body(self):
        """错误页内容没有用处，不该读进内存（也不该误判成超限）"""
        resp = _StreamResponse([b"x" * 10] * 5, status_code=404, headers={})
        body = await common.fetch_capped(_FakeStreamClient(resp), "http://x/", 10)
        self.assertEqual(body.status_code, 404)
        self.assertFalse(body.over_limit)
        self.assertEqual(resp.yielded, 0)

    async def test_error_body_read_only_when_requested(self):
        """默认不读错误体；明确要求时读一小段，供调用方从**业务码**判断语义。

        为什么需要：部分平台（ModelScope）用 5xx 表达「文件不存在」，
        只看状态码会把「文件不存在」误判成「服务故障」。
        """
        payload = b'{"Code": 10990101007, "Message": "file empty"}'
        resp = _StreamResponse(
            [payload], status_code=500, headers={"content-type": "application/json"}
        )
        body = await common.fetch_capped(_FakeStreamClient(resp), "http://x/", 1024)
        self.assertEqual(body.content, b"")
        self.assertEqual(resp.yielded, 0, "默认不应读取错误体")

        resp2 = _StreamResponse(
            [payload], status_code=500, headers={"content-type": "application/json"}
        )
        body2 = await common.fetch_capped(
            _FakeStreamClient(resp2), "http://x/", 1024, error_body_limit=4096
        )
        self.assertFalse(body2.over_limit)
        self.assertEqual(body2.json(), {"Code": 10990101007, "Message": "file empty"})

    async def test_error_body_respects_its_own_limit(self):
        """错误体读取同样有上限（不能因为「只是错误页」就无界读）"""
        resp = _StreamResponse(
            [b"x" * 5000], status_code=500, headers={"content-type": "text/plain"}
        )
        body = await common.fetch_capped(
            _FakeStreamClient(resp), "http://x/", 1024, error_body_limit=100
        )
        self.assertEqual(body.content, b"")
        self.assertEqual(resp.yielded, 1)

    async def test_network_error_returns_none(self):
        """网络异常返回 None，与「超限」区分开（调用方要给不同的提示）"""
        client = _FakeStreamClient(error=httpx.ConnectError("boom"))
        self.assertIsNone(await common.fetch_capped(client, "http://x/", 10))


class FetchRawCappedTests(unittest.IsolatedAsyncioTestCase):
    """`_fetch_raw`：超限 → 400（提示精简）；网络/非 200 → 502（提示重试）"""

    async def test_ok_returns_text(self):
        resp = _StreamResponse(
            ["# 标题".encode("utf-8")],
            headers={"content-type": "text/plain; charset=utf-8"},
            encoding="utf-8",
        )
        self.assertEqual(await _fetch_raw(_FakeStreamClient(resp), "http://x/"), "# 标题")

    async def test_over_limit_raises_400_with_actionable_message(self):
        resp = _StreamResponse(
            [b"x" * (common.MAX_CONTENT_SIZE // 2 + 10)] * 3,
            headers={"content-type": "text/plain"},
        )
        with self.assertRaises(AppException) as ctx:
            await _fetch_raw(_FakeStreamClient(resp), "http://x/")
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("2MB", ctx.exception.message)

    async def test_non_200_raises_502(self):
        resp = _StreamResponse([b"nope"], status_code=500)
        with self.assertRaises(AppException) as ctx:
            await _fetch_raw(_FakeStreamClient(resp), "http://x/")
        self.assertEqual(ctx.exception.code, 502)

    async def test_network_error_raises_502(self):
        client = _FakeStreamClient(error=httpx.ConnectTimeout("boom"))
        with self.assertRaises(AppException) as ctx:
            await _fetch_raw(client, "http://x/")
        self.assertEqual(ctx.exception.code, 502)


class ImageTransferLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_over_limit_image_is_skipped_without_uploading(self):
        """单图超限：放弃该图（保留原 URL），绝不把超大 body 传给 COS"""
        resp = _StreamResponse(
            [b"x" * 1000] * 5, headers={"content-type": "image/png"}
        )
        with mock.patch.object(common.storage, "upload_bytes") as upload:
            result = await common.download_and_upload_image(
                _FakeStreamClient(resp), "https://github.com/a.png"
            )
        self.assertIsNone(result)
        upload.assert_not_called()

    async def test_collect_image_urls_preserves_document_order(self):
        """顺序稳定是「截断保留正文最靠前的 N 张」成立的前提"""
        md = (
            "![c](https://github.com/c.png)\n"
            "![a](https://github.com/a.png)\n"
            "![b](https://github.com/b.png)\n"
            "![a](https://github.com/a.png)\n"  # 重复项只保留首次出现位置
        )
        urls = common.collect_image_urls(md, {"github.com"})
        self.assertEqual(
            urls,
            [
                "https://github.com/c.png",
                "https://github.com/a.png",
                "https://github.com/b.png",
            ],
        )

    async def test_transfer_caps_number_of_images(self):
        """超出上限的图片保留原 URL，只转存前 N 张"""
        total = common.MAX_TRANSFER_IMAGES + 5
        md = "\n".join(f"![i{i}](https://github.com/i{i}.png)" for i in range(total))
        calls: list[str] = []

        async def fake_download(client, url, **kwargs):
            calls.append(url)
            return None  # 全部「下载失败」，只关心被尝试了几次

        with mock.patch.object(common.storage, "is_configured", return_value=True), \
             mock.patch.object(common, "download_and_upload_image", side_effect=fake_download):
            _md, transferred = await common.transfer_images_to_cos(object(), md)

        self.assertEqual(len(calls), common.MAX_TRANSFER_IMAGES)
        self.assertEqual(transferred, 0)
        # 截断的是「后 5 张」，前 N 张必须被尝试过
        self.assertNotIn(f"https://github.com/i{total - 1}.png", calls)
        self.assertIn("https://github.com/i0.png", calls)


class FetchCappedHttpIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """用**真实 httpx + 本地 HTTP 服务**验证 fetch_capped（不是桩）。

    为什么必须有这一层：桩测试只能证明「我按假设调用」，无法证明
    ``client.stream()`` / ``resp.encoding`` / ``aiter_bytes()`` 的真实语义符合假设
    —— 比如 Content-Length 预告、无 Content-Length 的分块响应、提前断连后
    连接是否被正确释放，都只有在真实实现上才能验出来。
    只绑 127.0.0.1 且用系统分配的端口，完全离线、无端口冲突。
    """

    BODY = b"x" * (512 * 1024)  # 512KB
    SMALL = "小文件内容 hello".encode("utf-8")
    GZIP_TEXT = (
        "<html><head><title>压缩页</title></head><body>"
        + "正文内容" * 500
        + "</body></html>"
    )

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        body, small = cls.BODY, cls.SMALL
        gzip_text = cls.GZIP_TEXT

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/redirect":
                    # 302 → /small：验证 CappedBody.url 记录的是**重定向后的最终地址**
                    # （正文相对链接重写依赖它，记成跳转前的地址会让导入的链接全错）
                    self.send_response(302)
                    self.send_header("Location", "/small")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path == "/redirect-private":
                    # 302 → 内网地址：SSRF 探针，必须被拒绝（P1-46②）
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:1/evil")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path == "/redirect-self":
                    # 自指重定向：跳数超限后按失败处理（不能无限跟）
                    self.send_response(302)
                    self.send_header("Location", "/redirect-self")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path == "/small":
                    payload, ctype, with_length = small, "text/plain; charset=utf-8", True
                elif self.path == "/gzip":
                    # 真实站点（掘金 / CSDN / GitHub API…）普遍返回 gzip —— 这里必须
                    # 真发压缩体 + 真带 Content-Encoding，否则测不出「二次解压」回归
                    payload = gzip.compress(gzip_text.encode("utf-8"))
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Encoding", "gzip")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                elif self.path == "/nope":
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"not found")
                    return
                elif self.path == "/chunked":
                    # 不发 Content-Length：逼出「边读边计数」这条路径
                    payload, ctype, with_length = body, "application/octet-stream", False
                else:
                    payload, ctype, with_length = body, "application/octet-stream", True

                self.send_response(200)
                self.send_header("Content-Type", ctype)
                if with_length:
                    self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                for i in range(0, len(payload), 64 * 1024):
                    try:
                        self.wfile.write(payload[i : i + 64 * 1024])
                    except (ConnectionError, OSError):
                        return  # 客户端提前断连是预期行为

            def do_POST(self):
                # 回显收到的 JSON，用于验证 method / json body 真的被透传了
                length = int(self.headers.get("Content-Length") or 0)
                received = self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                payload = b'{"echo":' + received + b"}"
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        # 端口 0 = 由系统分配，避免与其它进程冲突
        cls._server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls._base = f"http://127.0.0.1:{cls._server.server_address[1]}"
        cls._thread = threading.Thread(target=cls._server.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls._server.shutdown()
        cls._server.server_close()

    async def test_small_body_returned_and_decoded(self):
        async with httpx.AsyncClient(timeout=10) as client:
            body = await common.fetch_capped(client, f"{self._base}/small", 4096)
        self.assertEqual(body.status_code, 200)
        self.assertFalse(body.over_limit)
        self.assertEqual(body.text, self.SMALL.decode())

    async def test_content_length_precheck_aborts(self):
        """有 Content-Length 且预告超限 → 一个字节都不读"""
        async with httpx.AsyncClient(timeout=10) as client:
            body = await common.fetch_capped(client, f"{self._base}/big", 1024)
        self.assertTrue(body.over_limit)
        self.assertEqual(body.content, b"")

    async def test_gzip_body_text_is_not_decoded_twice(self):
        """gzip 响应：``.text`` 必须等于原文，绝不能二次解压（线上 500 回归）。

        httpx 的 ``aiter_bytes()`` 返回的是**已解压**的字节；若把原始
        ``content-encoding: gzip`` 一起塞回重建的 ``httpx.Response``，
        ``.text`` 会对已解压的明文再解压一次：

            httpx.DecodingError: Error -3 while decompressing data:
            incorrect header check

        该异常抛在 ``fetch_capped`` return 之后，拦不住，最终变成 500 ——
        现象是「凡带压缩的站点全部拉取失败，未压缩的正常」，极易被误判成
        对方风控。桩测试与未压缩的本地服务都测不出来，必须真发压缩体。
        """
        async with httpx.AsyncClient(timeout=10) as client:
            body = await common.fetch_capped(client, f"{self._base}/gzip", 4 * 1024 * 1024)
        self.assertEqual(body.status_code, 200)
        self.assertFalse(body.over_limit)
        self.assertEqual(body.text, self.GZIP_TEXT)
        self.assertNotIn("content-encoding", body.headers)

    async def test_chunked_aborts_while_streaming(self):
        """无 Content-Length 时必须靠「边读边计数」中止（真实分块响应）"""
        async with httpx.AsyncClient(timeout=10) as client:
            body = await common.fetch_capped(client, f"{self._base}/chunked", 1024)
        self.assertTrue(body.over_limit)
        self.assertEqual(body.content, b"")

    async def test_generous_limit_reads_whole_body(self):
        async with httpx.AsyncClient(timeout=10) as client:
            body = await common.fetch_capped(client, f"{self._base}/big", 8 * 1024 * 1024)
        self.assertFalse(body.over_limit)
        self.assertEqual(len(body.content), len(self.BODY))

    async def test_404_returns_status_without_body(self):
        async with httpx.AsyncClient(timeout=10) as client:
            body = await common.fetch_capped(client, f"{self._base}/nope", 1024)
        self.assertEqual(body.status_code, 404)
        self.assertFalse(body.over_limit)
        self.assertEqual(body.content, b"")

    async def test_literal_loopback_redirect_is_dropped(self):
        """真实 httpx 跟随链路：最终落点是字面内网地址时必须丢弃（P1-53②）

        本用例同时证明了两件事，缺一件都测不到这条防护：
        1. `follow_redirects=True` 时 httpx 把 `resp.url` 置为**最终**地址 ——
           「正文相对链接重写靠它」这个既有依赖正是靠这一点成立的；
        2. 因此 `fetch_capped` 能在读任何字节之前就看清落点，把
           白名单主机一次 302 指向 `127.0.0.1` / `169.254.169.254` 的跳板挡掉。
        """
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            body = await common.fetch_capped(client, f"{self._base}/redirect", 4096)
        self.assertIsNone(body, f"重定向落在 127.0.0.1 却返回了内容: {body}")

    async def test_post_with_json_body_over_real_http(self):
        """POST + JSON body 走真实 httpx（ModelScope 译文接口就是 POST）。"""
        async with httpx.AsyncClient(timeout=10) as client:
            body = await common.fetch_capped(
                client, f"{self._base}/echo", 4096,
                method="POST", json_body={"preferLanguage": "zh_CN"},
            )
        self.assertEqual(body.status_code, 200)
        self.assertEqual(body.json(), {"echo": {"preferLanguage": "zh_CN"}})

    async def test_private_redirect_target_is_refused(self):
        """真实 HTTP：302 指向内网 → 抛 400（而不是去访问它）（P1-46②）"""
        async with httpx.AsyncClient(timeout=10) as client:
            with self.assertRaises(AppException) as ctx:
                await common.fetch_capped_following_redirects(
                    client, f"{self._base}/redirect-private", 4096
                )
        self.assertEqual(ctx.exception.code, 400)

    async def test_redirect_loop_is_bounded(self):
        """自指重定向：跳数超限后返回 None（失败），不得无限跟

        注：本地测试服务就在 127.0.0.1 上，而安全判定会拒绝回环地址 ——
        这里把判定打桩放行，只验证「跟随与跳数上限」这段逻辑
        （判定本身由 SafeRedirectTests 的两个纯函数用例覆盖）。
        """
        with mock.patch.object(common, "is_safe_redirect_target", return_value=True):
            async with httpx.AsyncClient(timeout=10) as client:
                body = await common.fetch_capped_following_redirects(
                    client, f"{self._base}/redirect-self", 4096, max_redirects=3
                )
        self.assertIsNone(body)

    async def test_normal_redirect_still_followed(self):
        """回归保护：正常重定向必须照常跟随（否则会把所有站点的跳转全掐掉）"""
        with mock.patch.object(common, "is_safe_redirect_target", return_value=True):
            async with httpx.AsyncClient(timeout=10) as client:
                body = await common.fetch_capped_following_redirects(
                    client, f"{self._base}/redirect", 4096
                )
        self.assertIsNotNone(body)
        self.assertEqual(body.status_code, 200)
        self.assertTrue(body.url.endswith("/small"))

    async def test_unreachable_host_is_reported_as_failure(self):
        """连不上时要么返回 None（传输异常），要么返回非 200 状态。

        两种都算「失败」；**绝不允许**返回 200 的正常 body —— 否则调用方会
        把失败当成成功内容存库。
        """
        async with httpx.AsyncClient(timeout=5) as client:
            body = await common.fetch_capped(client, "http://127.0.0.1:1/x", 1024)
        self.assertTrue(
            body is None or body.status_code >= 400,
            f"连接失败却返回了 {body.status_code if body else 'ok'}",
        )


class SafeRedirectTests(unittest.TestCase):
    """重定向目标的判定口径（第三轮增量审查 P1-46②）。

    真实 HTTP 链路（内网目标被拒 / 自指环不无限跟 / 正常跳转照常跟随）
    在 FetchCappedHttpIntegrationTests 里覆盖。
    """

    def test_private_and_non_http_targets_are_rejected(self):
        bad = [
            "http://127.0.0.1:8000/api/v1/settings",
            "http://169.254.169.254/latest/meta-data/",
            "http://192.168.1.1/",
            "http://10.0.0.5/",
            "http://[::1]/",
            "ftp://example.com/x",
            "file:///etc/passwd",
            "not a url",
            "http:///no-host",
        ]
        for url in bad:
            with self.subTest(url=url):
                self.assertFalse(common.is_safe_redirect_target(url))

    def test_public_http_targets_are_allowed(self):
        for url in (
            "https://example.com/x",
            "http://example.com/x",
            "https://raw.githubusercontent.com/a/b/main/README.md",
            "https://8.8.8.8/x",
        ):
            with self.subTest(url=url):
                self.assertTrue(common.is_safe_redirect_target(url))

class CappedBodyTests(unittest.IsolatedAsyncioTestCase):
    """CappedBody 刻意提供与 httpx.Response 同名的成员，替换 client.get 时才能不改调用方。"""

    async def test_url_is_recorded(self):
        resp = _StreamResponse([b"hi"], url="https://example.com/final")
        body = await common.fetch_capped(_FakeStreamClient(resp), "http://x/", 1024)
        self.assertEqual(body.url, "https://example.com/final")

    async def test_json_parses_content(self):
        resp = _StreamResponse([b'{"a": 1}'], headers={"content-type": "application/json"})
        body = await common.fetch_capped(_FakeStreamClient(resp), "http://x/", 1024)
        self.assertEqual(body.json(), {"a": 1})

    async def test_json_raises_value_error_on_bad_payload(self):
        """与 httpx.Response.json() 同语义：坏 JSON 抛 ValueError，调用方现有 except 不用改。"""
        resp = _StreamResponse([b"not json"])
        body = await common.fetch_capped(_FakeStreamClient(resp), "http://x/", 1024)
        with self.assertRaises(ValueError):
            body.json()

    async def test_applied_headers_are_stripped(self):
        """已被 httpx 处理过的头必须剔除（body 已解压 / 长度已变），其余原样保留。"""
        resp = _StreamResponse(
            [b"hi"],
            headers={
                "content-encoding": "gzip",
                "content-length": "26",
                "transfer-encoding": "chunked",
                "content-type": "text/html; charset=utf-8",
                "x-custom": "keep",
            },
        )
        body = await common.fetch_capped(_FakeStreamClient(resp), "http://x/", 1024)
        self.assertNotIn("content-encoding", body.headers)
        self.assertNotIn("content-length", body.headers)
        self.assertNotIn("transfer-encoding", body.headers)
        self.assertEqual(body.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(body.headers["x-custom"], "keep")

    def test_capped_reason_distinguishes_causes(self):
        """降级路径的日志必须能区分「网络抖动 / 体积超限 / 对方错误码」。"""
        self.assertEqual(common.capped_reason(None), "网络异常")
        self.assertIn("MB", common.capped_reason(common.CappedBody(200, {}, over_limit=True)))
        self.assertEqual(common.capped_reason(common.CappedBody(404, {})), "HTTP 404")


class FetchCappedRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_defaults_to_get(self):
        client = _FakeStreamClient(_StreamResponse([b"ok"]))
        await common.fetch_capped(client, "http://x/", 1024, params={"a": "1"})
        self.assertEqual(client.last_call["method"], "GET")
        self.assertEqual(client.last_call["params"], {"a": "1"})
        self.assertIsNone(client.last_call["json"])

    async def test_supports_post_with_json_body(self):
        """ModelScope 译文接口是 POST —— 必须也能走流式（否则它仍是「先下载后校验」）。"""
        client = _FakeStreamClient(_StreamResponse([b"{}"]))
        await common.fetch_capped(
            client, "http://x/", 1024, method="POST", json_body={"k": "v"}
        )
        self.assertEqual(client.last_call["method"], "POST")
        self.assertEqual(client.last_call["json"], {"k": "v"})


class FetchTextOrErrorTests(unittest.IsolatedAsyncioTestCase):
    """统一错误映射：网络异常 → 502（可重试），超限 → 400（应精简，重试无意义）。"""

    async def test_network_error_raises_502(self):
        client = _FakeStreamClient(error=httpx.ConnectError("boom"))
        with self.assertRaises(AppException) as ctx:
            await common.fetch_text_or_error(client, "http://x/", label="README")
        self.assertEqual(ctx.exception.code, 502)
        self.assertIn("README", ctx.exception.message)

    async def test_over_limit_raises_400_with_size_hint(self):
        resp = _StreamResponse([b"x" * 100], headers={"content-length": "999999"})
        with self.assertRaises(AppException) as ctx:
            await common.fetch_text_or_error(
                _FakeStreamClient(resp), "http://x/", limit=10, label="README"
            )
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("过大", ctx.exception.message)

    async def test_non_200_is_returned_not_raised(self):
        """404 / 403 的提示语各平台差别很大，必须留给调用方判断，不能被统一抹平。"""
        resp = _StreamResponse([], status_code=404)
        body = await common.fetch_text_or_error(_FakeStreamClient(resp), "http://x/")
        self.assertEqual(body.status_code, 404)

    async def test_ok_returns_body(self):
        resp = _StreamResponse([b"hello"])
        body = await common.fetch_text_or_error(_FakeStreamClient(resp), "http://x/")
        self.assertEqual(body.text, "hello")


class NoUnboundedDownloadTests(unittest.TestCase):
    """静态守卫：第三方拉取层不得再出现 `client.get(...)` 这类无界下载。

    这类回归**不会报错**：功能照常工作，只是在遇到超大响应时才把内存打满。
    用 AST 而不是正则扫描 —— 正则会把文档字符串里提到的 ``client.get`` 也算进来。
    """

    TARGETS = [
        "backend/app/services/third_party_fetch/articles.py",
        "backend/app/services/third_party_fetch/gitee.py",
        "backend/app/services/third_party_fetch/gitcode.py",
        "backend/app/services/third_party_fetch/huggingface.py",
        "backend/app/services/third_party_fetch/modelscope.py",
        "backend/app/services/github_readme_service.py",
    ]
    HTTP_METHODS = {"get", "post", "put", "patch", "delete", "request"}

    def test_no_direct_http_calls_remain(self):
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        offenders: list[str] = []
        for rel in self.TARGETS:
            tree = ast.parse((root / rel).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute) or func.attr not in self.HTTP_METHODS:
                    continue
                target = func.value
                if isinstance(target, ast.Name) and "client" in target.id.lower():
                    offenders.append(f"{rel}:{node.lineno} {target.id}.{func.attr}()")
        self.assertEqual(
            offenders, [],
            "以下调用会先把整个响应体读进内存，请改用 fetch_capped / fetch_text_or_error：\n"
            + "\n".join(offenders),
        )

    def test_fetch_capped_is_actually_used(self):
        """反向守卫：确认上面那条不是因为「文件里根本没做网络请求」而空过。"""
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        for rel in self.TARGETS:
            src = (root / rel).read_text(encoding="utf-8")
            self.assertTrue(
                "fetch_capped(" in src or "fetch_text_or_error(" in src,
                f"{rel} 未见任何流式下载调用，守卫可能已失效",
            )


class RedirectSafetyGuardTests(unittest.IsolatedAsyncioTestCase):
    """自动跟随的重定向必须逐跳复检（第四轮全量审查 P1-53②）

    `follow_redirects=True` 的 client 会在 httpx 内部一路跟到对方 Location 指定的
    任意地址。P1-46② 只给**页面抓取**路径做了手动跟随 + 逐跳校验，而图片转存、
    README、各家 API 都共用 `fetch_capped`，此前完全没有这道检查 —— 于是一个
    白名单主机（或它的一次 302）就能把 `169.254.169.254` / `127.0.0.1:8000` 的
    响应体当成「图片」转存进本站 COS 并以本站域名发布。
    """

    async def test_redirect_into_metadata_endpoint_is_dropped_before_reading(self):
        resp = _StreamResponse([b"META-DATA-SECRET"], url="http://169.254.169.254/latest/meta-data/")
        resp.history = [_StreamResponse([], url="https://p1-juejin.byteimg.com/a.png")]
        client = _FakeStreamClient(resp)

        body = await common.fetch_capped(client, "https://p1-juejin.byteimg.com/a.png", 1024)

        self.assertIsNone(body, "重定向落在元数据端点，却返回了响应体")
        self.assertEqual(resp.yielded, 0, "应在读取任何字节之前就丢弃")

    async def test_redirect_into_loopback_service_is_dropped(self):
        resp = _StreamResponse([b"secret"], url="http://127.0.0.1:8000/api/v1/admin/users")
        resp.history = []
        body = await common.fetch_capped(_FakeStreamClient(resp), "https://ok.example.com/a.png", 1024)
        self.assertIsNone(body)

    async def test_external_redirect_is_still_accepted(self):
        """合法跳板（镜像站 302 回官方站）不能被误伤 —— 否则转存会大面积降级"""
        resp = _StreamResponse(
            [b"PNM\n"], url="https://huggingface.co/x.png", headers={"content-type": "image/png"}
        )
        resp.history = [_StreamResponse([], url="https://hf-mirror.com/x.png")]
        body = await common.fetch_capped(_FakeStreamClient(resp), "https://hf-mirror.com/x.png", 1024)
        self.assertIsNotNone(body)
        self.assertEqual(body.status_code, 200)
        # 原 test_redirect_records_final_url 的断言目的（正文相对链接重写靠最终地址）
        self.assertTrue(body.url.endswith("/x.png"), body.url)
        self.assertEqual(body.content, b"PNM\n")

    def test_visited_targets_excludes_the_original_request(self):
        """初始地址已由调用侧的域名白名单校验过，不该在这里重复判"""
        first = _StreamResponse([], url="https://a.example.com/x")
        first.history = []
        self.assertEqual(common._visited_redirect_targets("https://a.example.com/x", first), [])


if __name__ == "__main__":
    unittest.main()
