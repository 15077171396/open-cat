"""文章类第三方拉取单测（html2md 转换 / 正文抽取 / 平台分发与适配）。

全部离线：HTML 转换与抽取用内联样例；网络相关 fetcher 用桩函数替换
articles._get_page，不发起真实 HTTP 请求。既有仓库类平台（GitHub 等）
不在本文件范围，其行为不应受文章类扩展影响（分发注册表隔离）。
"""

import contextlib
import json
import unittest
from unittest import mock

from app.core.exceptions import AppException
from app.services.third_party_fetch import articles as articles_mod
from app.services.third_party_fetch import common as common_mod
from app.services.third_party_fetch import html2md
from app.services.third_party_fetch import modelscope as modelscope_mod
from app.services.third_party_fetch.articles import (
    _SSPAI,
    _WOSHIPM,
    _clean_title,
    _compose_article_markdown,
    fetch_reddit,
    fetch_twitter,
    fetch_web_article,
    resolve_article_fetcher,
)
from app.services.third_party_fetch.common import (
    ZH_README_CANDIDATES,
    CappedBody,
    collect_image_urls,
)


class HtmlToMarkdownTests(unittest.TestCase):
    """HTML → Markdown 转换器"""

    def test_headings_and_inline(self):
        md = html2md.html_to_markdown(
            "<h1>大标题</h1><p>这是<strong>加粗</strong>与<em>斜体</em>，"
            '还有<a href="https://a.com/x">链接</a>。</p>'
        )
        self.assertIn("# 大标题", md)
        self.assertIn("**加粗**", md)
        self.assertIn("*斜体*", md)
        self.assertIn("[链接](https://a.com/x)", md)

    def test_cjk_join_without_space(self):
        md = html2md.html_to_markdown("<p>中文<span>拼接</span>English words here</p>")
        self.assertIn("中文拼接English words here", md)

    def test_pre_block_keeps_code_and_lang(self):
        md = html2md.html_to_markdown(
            '<pre><code class="language-python">def f():\n    return 1\n</code></pre>'
        )
        self.assertIn("```python", md)
        self.assertIn("def f():\n    return 1", md)

    def test_inline_code_with_backtick(self):
        md = html2md.html_to_markdown("<p>code <code>a`b</code></p>")
        self.assertIn("`` a`b ``", md)

    def test_pre_block_with_highlight_spans_keeps_order(self):
        """代码块内带语法高亮 <span> 时必须保持原文顺序（掘金 / 高亮库的常态）

        回归：旧实现顶层不反转、嵌套才反转，再在结尾 `"".join(reversed(parts))`
        补救 —— 只有「整段纯文本单个文本节点」的代码块顺序正确；一旦出现
        `<span class="hljs-keyword">` 就会整段按节点逆序输出，表现为
        「代码被截断 / 乱码 / 上下颠倒」（2026-10-19 用户反馈）。
        """
        html = (
            '<pre><code class="hljs language-js">'
            '<span class="hljs-keyword">const</span> image = '
            '<span class="hljs-keyword">new</span> '
            '<span class="hljs-title class_">Image</span>();\n'
            'image.<span class="hljs-property">src</span> = imageUrl;'
            "</code></pre>"
        )
        code = html2md.html_to_markdown(html).split("```js\n", 1)[1].split("\n```", 1)[0]
        self.assertEqual(code, "const image = new Image();\nimage.src = imageUrl;")

    def test_pre_block_order_with_multiple_top_level_nodes(self):
        """<pre> 下多个顶层子节点也要按文档顺序拼接（首节点不能跑到末尾）"""
        html = "<pre>a<span>b</span>c</pre>"
        code = html2md.html_to_markdown(html).split("\n", 1)[1].rsplit("\n", 1)[0]
        self.assertEqual(code, "abc")

    def test_inline_code_with_nested_spans_keeps_order(self):
        md = html2md.html_to_markdown("<p><code>x<span>y</span>z</code></p>")
        self.assertIn("`xyz`", md)

    def test_code_block_content_not_rewritten(self):
        """代码块内的 src="..." 属于代码内容，不能被相对链接重写成绝对地址

        回归：掘金 Vue 示例 `:src="url"` 曾被改成
        `:src="https://juejin.cn/post/url"`，正文凭空多出字符、示例代码是错的。
        """
        md = '```vue\n<img :src="url" />\n```\n\n正文 ![图](pic.png)'
        rewritten = common_mod.rewrite_relative_urls(
            md, lambda u, is_image: f"https://cdn.example.com/{u}"
        )
        self.assertIn(':src="url"', rewritten)
        self.assertIn("https://cdn.example.com/pic.png", rewritten)

    def test_image_collect_and_apply_skip_code_block(self):
        """代码块里的图片语法既不被收集、也不被替换"""
        md = (
            "```md\n![示例](https://p3-xtjj-sign.byteimg.com/a.webp)\n```\n\n"
            "![正文](https://p3-xtjj-sign.byteimg.com/b.webp)"
        )
        urls = collect_image_urls(md, set(), (".byteimg.com",))
        self.assertEqual(urls, ["https://p3-xtjj-sign.byteimg.com/b.webp"])
        replaced = common_mod.apply_cos_urls(
            md, {"https://p3-xtjj-sign.byteimg.com/a.webp": "https://cos/a.webp"}
        )
        self.assertIn("![示例](https://p3-xtjj-sign.byteimg.com/a.webp)", replaced)

    def test_lists_nested(self):
        md = html2md.html_to_markdown(
            "<ul><li>一</li><li>二<ol><li>子一</li><li>子二</li></ol></li></ul>"
        )
        self.assertIn("- 一", md)
        self.assertIn("- 二", md)
        self.assertIn("  1. 子一", md)
        self.assertIn("  2. 子二", md)

    def test_blockquote_and_hr(self):
        md = html2md.html_to_markdown("<blockquote><p>引用</p></blockquote><hr>")
        self.assertIn("> 引用", md)
        self.assertIn("---", md)

    def test_table_gfm_with_pipe_escape(self):
        md = html2md.html_to_markdown(
            "<table><thead><tr><th>A</th><th>B</th></tr></thead>"
            "<tbody><tr><td>1</td><td>a|b</td></tr></tbody></table>"
        )
        self.assertIn("| A | B |", md)
        self.assertIn("| 1 | a\\|b |", md)

    def test_image_lazy_attrs(self):
        # 默认顺序：src 优先
        md = html2md.html_to_markdown(
            '<img data-src="https://a.com/lazy.png" src="https://a.com/real.png" alt="图">'
        )
        self.assertIn('![图](https://a.com/real.png)', md)
        # 覆盖顺序（如知乎 data-original 优先）
        md2 = html2md.html_to_markdown(
            '<img data-original="https://a.com/full.png" src="https://a.com/thumb.png">',
            img_src_attrs=("data-original", "src"),
        )
        self.assertIn("https://a.com/full.png", md2)

    def test_data_uri_image_skipped(self):
        md = html2md.html_to_markdown('<img src="data:image/svg+xml;base64,xxx" alt="x">')
        self.assertEqual(md, "")

    def test_anchor_and_relative_link(self):
        md = html2md.html_to_markdown('<p><a href="#sec">锚点</a><a href="/rel">相对</a></p>')
        self.assertIn("锚点", md)
        self.assertIn("[相对](/rel)", md)

    def test_markdown_special_chars_escaped(self):
        md = html2md.html_to_markdown("<p>1 * 2 &lt;div&gt; [x]</p>")
        self.assertIn("1 \\* 2 \\<div\\> \\[x\\]", md)

    def test_unclosed_list_items_auto_closed(self):
        # 未闭合的 li 不应互相嵌套
        md = html2md.html_to_markdown("<ul><li>一<li>二<li>三</ul>")
        self.assertEqual(md.count("- "), 3)

    def test_br_renders_line_break(self):
        md = html2md.html_to_markdown("<p>第一行<br>第二行</p>")
        self.assertIn("第一行\n第二行", md)

    def test_script_style_ignored(self):
        md = html2md.html_to_markdown("<div><style>p{color:red}</style><script>var a=1;</script>正文</div>")
        self.assertEqual(md, "正文")


class ExtractArticleTests(unittest.TestCase):
    """正文与标题抽取"""

    SAMPLE = """
    <html><head><meta property="og:title" content="文章标题 - 知乎"><title>页面 title</title></head>
    <body>
      <nav>导航 导航导航</nav>
      <div class="Post-RichTextContainer"><p>知乎正文内容，这里有足够多的可见文本字符用来通过阈值判断。</p></div>
      <article><h1>回退标题</h1><p>article 回退正文，同样有足够多的可见文本字符来通过最小阈值判断逻辑。</p></article>
    </body></html>
    """

    def test_specific_selector_first(self):
        title, node = html2md.extract_article(self.SAMPLE, (".Post-RichTextContainer",))
        self.assertEqual(title, "文章标题 - 知乎")
        md = html2md.node_to_markdown(node)
        self.assertIn("知乎正文内容", md)
        self.assertNotIn("article 回退正文", md)

    def test_fallback_to_article(self):
        _, node = html2md.extract_article(self.SAMPLE, (".not-exist",))
        self.assertIn("article 回退正文", html2md.node_to_markdown(node))

    def test_allow_fallback_false_raises_none(self):
        _, node = html2md.extract_article(
            self.SAMPLE, (".not-exist",), allow_fallback=False
        )
        self.assertIsNone(node)

    def test_title_fallback_chain(self):
        html = "<html><body><h1>H1 标题</h1><p>正文正文正文正文</p></body></html>"
        title, _ = html2md.extract_article(html, ("body",))
        self.assertEqual(title, "H1 标题")
        html2 = "<html><head><title>Title 标签</title></head><body><p>正文正文正文正文</p></body></html>"
        title2, _ = html2md.extract_article(html2, ("body",))
        self.assertEqual(title2, "Title 标签")


class NonTechDomainSitesTests(unittest.TestCase):
    """少数派 / 人人都是产品经理：补「效率办公」「产品运营」两个内容方向的导入源。

    两站正文容器类名都容易写错，必须锁住：
    - 少数派是 wangEditor 产物，节点带 Vue scope 属性（data-v-xxxx），按 class 仍应命中
    - 人人都是产品经理是 BEM modifier **双横线** article--content，写成单横线会静默抓不到正文
    """

    SSPAI_SAMPLE = """
    <html><head><title>点餐助手怎么选 - 少数派</title></head><body>
      <nav>导航导航</nav>
      <div class="article__main__wrapper">
        <div class="article__main__content wangEditor-txt" data-v-c065c440>
          <p>少数派正文段落，这里有足够多的可见文本字符用来通过最小可见字符数阈值判断。</p>
        </div>
      </div>
    </body></html>
    """

    WOSHIPM_SAMPLE = """
    <html><head><title>产品经理的 AI 方法论 | 人人都是产品经理</title></head><body>
      <div class="article--wrapper">
        <div class="article--content grap">
          <p>人人都是产品经理正文段落，同样有足够多的可见文本字符通过最小阈值判断逻辑。</p>
        </div>
      </div>
    </body></html>
    """

    def test_sspai_body_extracted(self):
        title, node = html2md.extract_article(
            self.SSPAI_SAMPLE, _SSPAI.content_selectors, allow_fallback=False
        )
        self.assertIsNotNone(node, "少数派正文容器未命中")
        self.assertIn("少数派正文段落", html2md.node_to_markdown(node))
        self.assertNotIn("导航", html2md.node_to_markdown(node), "不应把导航抓进正文")
        self.assertEqual(_SSPAI.title_strip[0].sub("", title).strip(), "点餐助手怎么选")

    def test_woshipm_body_extracted(self):
        title, node = html2md.extract_article(
            self.WOSHIPM_SAMPLE, _WOSHIPM.content_selectors, allow_fallback=False
        )
        self.assertIsNotNone(node, "人人都是产品经理正文容器未命中")
        self.assertIn("人人都是产品经理正文段落", html2md.node_to_markdown(node))
        self.assertEqual(_WOSHIPM.title_strip[0].sub("", title).strip(), "产品经理的 AI 方法论")

    def test_woshipm_selector_uses_double_dash(self):
        """回归守卫：类名是 article--content（双横线），不是 article-content"""
        self.assertIn(".article--content", _WOSHIPM.content_selectors)
        self.assertNotIn(".article-content", _WOSHIPM.content_selectors)

    def test_image_hosts_cover_cdn_subdomains(self):
        """两站配图分布在多个 CDN 子域，后缀白名单必须覆盖，否则图片不转存会留下外链/裂图"""
        self.assertIn(".sspai.com", _SSPAI.image_host_suffixes)
        self.assertIn(".woshipm.com", _WOSHIPM.image_host_suffixes)
        self.assertIn(".yunyingpai.com", _WOSHIPM.image_host_suffixes)

    def test_url_patterns(self):
        self.assertTrue(_SSPAI.patterns[0].match("https://sspai.com/post/115241"))
        self.assertFalse(_SSPAI.patterns[0].match("https://sspai.com/matrix"))
        self.assertTrue(_WOSHIPM.patterns[0].match("https://www.woshipm.com/it/5997158.html"))
        self.assertFalse(_WOSHIPM.patterns[0].match("https://www.woshipm.com/it/"))


class DispatcherTests(unittest.IsolatedAsyncioTestCase):
    """统一入口分发：仓库类平台不受影响，文章类按域名解析"""

    def test_repo_platforms_unchanged(self):
        from app.services.third_party_fetch import _PLATFORM_FETCHERS
        from app.services.third_party_fetch.github import fetch_github_doc

        self.assertIs(_PLATFORM_FETCHERS["github.com"], fetch_github_doc)
        for host in ("gitee.com", "gitcode.com", "modelscope.cn", "huggingface.co", "hf-mirror.com"):
            self.assertIn(host, _PLATFORM_FETCHERS)

    def test_article_hosts_resolved(self):
        for host in (
            "x.com", "twitter.com", "www.reddit.com", "news.ycombinator.com",
            "stackoverflow.com", "zhuanlan.zhihu.com", "www.zhihu.com", "juejin.cn",
            "blog.csdn.net", "mp.weixin.qq.com", "www.cnblogs.com", "www.jianshu.com",
            "segmentfault.com", "my.oschina.net", "www.v2ex.com", "dev.to", "medium.com",
            "sspai.com", "www.sspai.com", "woshipm.com", "www.woshipm.com",
        ):
            self.assertIsNotNone(resolve_article_fetcher(host), host)

    def test_suffix_routes(self):
        for host in ("blog.stackexchange.com", "pub.medium.com", "user.blog.csdn.net"):
            self.assertIsNotNone(resolve_article_fetcher(host), host)

    def test_unknown_host_none(self):
        self.assertIsNone(resolve_article_fetcher("example.org"))

    def test_site_fetcher_callable_signature(self):
        """按站点解析出的 fetcher 必须能被 (url, include_files) 直接调用

        回归：早期用 functools.partial(fetch_web_article, site=site) 适配，
        调用时 site 与位置参数冲突抛 TypeError，导致所有 HTML 文章平台不可用。
        """
        import asyncio

        from app.services.third_party_fetch import articles as articles_mod

        fetcher = resolve_article_fetcher("juejin.cn")
        self.assertIsNotNone(fetcher)

        async def _fake_get_page(url, **kwargs):
            return _FakeHtmlResponse(
                "<html><body><article class='markdown-body'><p>"
                "这是一段足够长的正文内容，用来验证站点 fetcher 的参数绑定是否正确无误，"
                "避免 functools.partial 关键字绑定与位置参数冲突导致的 TypeError。"
                "</p></article></body></html>",
                final_url="https://juejin.cn/post/1",
            )

        original = articles_mod._get_page
        articles_mod._get_page = _fake_get_page
        try:
            result = asyncio.run(fetcher("https://juejin.cn/post/1", False))
        finally:
            articles_mod._get_page = original
        self.assertEqual(result["platform"], "juejin")

    async def test_unsupported_url_rejected(self):
        from app.services.third_party_fetch import fetch_third_party_content

        with self.assertRaises(AppException) as ctx:
            await fetch_third_party_content("https://example.org/some/article", False)
        self.assertIn("暂不支持该平台", ctx.exception.message)


class TwitterFetchTests(unittest.IsolatedAsyncioTestCase):
    """X (Twitter) fetcher（桩 API 响应）"""

    async def test_tweet_to_markdown(self):
        captured = {}

        async def _fake_get_page(url, **kwargs):
            captured["url"] = url
            return _FakeResponse({
                "code": 200,
                "tweet": {
                    "text": "第一段\n\n第二段",
                    "author": {"name": "某人", "screen_name": "someone"},
                    "created_at": "Wed Oct 07 12:00:00 +0000 2026",
                    "media": {"all": [{"type": "photo", "url": "https://pbs.twimg.com/media/1.png"}]},
                },
            })

        self._patch(_fake_get_page)
        try:
            result = await fetch_twitter("https://x.com/someone/status/123456", False)
        finally:
            self._unpatch()

        self.assertIn("api.fxtwitter.com/status/123456", captured["url"])
        self.assertEqual(result["platform"], "twitter")
        self.assertEqual(result["resource_type"], "article")
        # 段落分隔 + 硬换行保留 + 图片 + 来源行
        self.assertIn("第一段", result["content"])
        self.assertIn("![图片 1](https://pbs.twimg.com/media/1.png)", result["content"])
        self.assertIn("@someone", result["content"])
        self.assertIn("> 来源：[某人 (@someone)](https://x.com/someone/status/123456)", result["content"])

    async def test_invalid_url_rejected(self):
        with self.assertRaises(AppException) as ctx:
            await fetch_twitter("https://x.com/someone", False)
        self.assertIn("仅支持", ctx.exception.message)

    async def test_article_title_used(self):
        async def _fake_get_page(url, **kwargs):
            return _FakeResponse({
                "code": 200,
                "tweet": {
                    "text": "摘要",
                    "article": {"title": "X 长文标题", "text": "长文正文"},
                    "author": {"name": "N", "screen_name": "h"},
                },
            })

        self._patch(_fake_get_page)
        try:
            result = await fetch_twitter("https://x.com/h/status/9", False)
        finally:
            self._unpatch()
        self.assertIn("# X 长文标题", result["content"])
        self.assertEqual(result["title"], "X 长文标题")

    def _patch(self, fake):
        self._orig = articles_mod._get_page
        articles_mod._get_page = fake

    def _unpatch(self):
        articles_mod._get_page = self._orig


class RedditFetchTests(unittest.IsolatedAsyncioTestCase):
    """Reddit fetcher（桩 .json 响应）"""

    async def test_selftext_post(self):
        async def _fake_get_page(url, **kwargs):
            self.assertTrue(url.endswith(".json"))
            return _FakeResponse([{
                "data": {"children": [{"data": {
                    "title": "Reddit 标题",
                    "subreddit": "programming",
                    "author": "alice",
                    "selftext": "正文 **markdown** 内容",
                    "created_utc": 1728000000,
                    "url": "https://www.reddit.com/r/programming/comments/abc/",
                }}]},
            }, {"data": {"children": []}}])

        self._orig = articles_mod._get_page
        articles_mod._get_page = _fake_get_page
        try:
            result = await fetch_reddit("https://www.reddit.com/r/programming/comments/abc/some_slug/", False)
        finally:
            articles_mod._get_page = self._orig

        self.assertEqual(result["platform"], "reddit")
        self.assertIn("# Reddit 标题", result["content"])
        self.assertIn("正文 **markdown** 内容", result["content"])
        self.assertIn("> 来源：[r/programming · u/alice]", result["content"])

    async def test_removed_post_rejected(self):
        async def _fake_get_page(url, **kwargs):
            return _FakeResponse([{
                "data": {"children": [{"data": {
                    "title": "T", "subreddit": "x", "author": "a",
                    "selftext": "[removed]", "url": "about:blank",
                }}]},
            }, {"data": {"children": []}}])

        self._orig = articles_mod._get_page
        articles_mod._get_page = _fake_get_page
        try:
            with self.assertRaises(AppException):
                await fetch_reddit("https://www.reddit.com/r/x/comments/abc/t/", False)
        finally:
            articles_mod._get_page = self._orig


class WebArticleTests(unittest.IsolatedAsyncioTestCase):
    """HTML 文章类 fetcher（桩页面响应）"""

    def _page(self):
        return """
        <html><head>
        <meta property="og:title" content="示例文章 - 掘金">
        <meta name="author" content="张三">
        <meta name="keywords" content="前端,性能优化">
        </head>
        <body><article class="markdown-body">
        <h1>示例文章</h1><p>这是正文第一段，包含足够多的字符以通过阈值检查。</p>
        <p><img src="/img/relative.png" alt="相对图"></p>
        </article></body></html>
        """

    async def test_fetch_web_article_happy_path(self):
        async def _fake_get_page(url, **kwargs):
            return _FakeHtmlResponse(self._page(), final_url="https://juejin.cn/post/123")

        self._orig = articles_mod._get_page
        articles_mod._get_page = _fake_get_page
        try:
            result = await fetch_web_article("https://juejin.cn/post/123", articles_mod._JUEJIN, False)
        finally:
            articles_mod._get_page = self._orig

        self.assertEqual(result["platform"], "juejin")
        self.assertEqual(result["resource_type"], "article")
        # 标题去站点后缀，且不与正文自带 h1 重复
        self.assertEqual(result["title"], "示例文章")
        self.assertEqual(result["content"].count("# 示例文章"), 1)
        # 相对图片基于页面地址补全
        self.assertIn("https://juejin.cn/img/relative.png", result["content"])
        # 作者 / 标签从 meta 抽取，正文末尾追加出处行
        self.assertEqual(result["author"], "张三")
        self.assertEqual(result["tags"], ["前端", "性能优化"])
        self.assertIn(
            "> 来源：[掘金 · 张三](https://juejin.cn/post/123)", result["content"]
        )

    async def test_invalid_path_rejected(self):
        with self.assertRaises(AppException) as ctx:
            await fetch_web_article("https://juejin.cn/user/123", articles_mod._JUEJIN, False)
        self.assertIn("仅支持", ctx.exception.message)

    async def test_extraction_failure_message(self):
        async def _fake_get_page(url, **kwargs):
            return _FakeHtmlResponse("<html><body><div>登录后查看</div></body></html>",
                                     final_url="https://zhuanlan.zhihu.com/p/1")

        self._orig = articles_mod._get_page
        articles_mod._get_page = _fake_get_page
        try:
            with self.assertRaises(AppException) as ctx:
                await fetch_web_article("https://zhuanlan.zhihu.com/p/1", articles_mod._ZHIHU, False)
        finally:
            articles_mod._get_page = self._orig
        self.assertIn("未能从", ctx.exception.message)


class ModelScopeMissingFileTests(unittest.IsolatedAsyncioTestCase):
    """魔搭用 **HTTP 500** 表达「文件不存在」（线上事故 2026-10-11 回归）。

    原实现只把 404 当「不存在」，其余一律 502。而中文 README 候选
    （``README.zh-CN.md`` 等）排在候选列表最前面 —— 绝大多数仓库没有中文
    README，于是**第一个候选就 500**，整个拉取直接失败：

        ModelScope 请求失败（HTTP 500），请稍后重试

    结果：ModelScope 拉取全线不可用。修法是读错误体里的业务码
    （``Code=10990101007``），而不是只看状态码。
    """

    MISSING_BODY = json.dumps(
        {
            "Code": 10990101007,
            "Message": "获取模型文件失败，文件内容为空",
            "RequestId": "x",
            "Success": False,
        }
    ).encode("utf-8")

    def _fake_fetch_capped(self, status_map):
        """构造桩 fetch_capped：按 FilePath 返回不同状态，且**忠实模拟**
        「不传 error_body_limit 就不读错误体」这一真实行为 —— 否则即使调用方
        漏传该参数，测试也会假绿。"""

        async def _fake(client, url, limit, **kwargs):
            file_path = (kwargs.get("params") or {}).get("FilePath", "")
            status, body = status_map.get(file_path, (404, b""))
            if status != 200 and not kwargs.get("error_body_limit"):
                body = b""
            return CappedBody(
                status, {"content-type": "application/json"}, content=body, url=url
            )

        return _fake

    def _patched(self, status_map):
        """必须同时替换两处 `fetch_capped`：

        - ``common.fetch_capped``：``fetch_text_or_error`` 引用的是本模块全局名；
        - ``modelscope.fetch_capped``：``_fetch_translated_readme`` 用的是本模块导入名。
        漏掉任一处，测试要么测不到目标路径，要么真的发起网络请求（不再离线）。
        """
        fake = self._fake_fetch_capped(status_map)
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(common_mod, "fetch_capped", fake))
        stack.enter_context(mock.patch.object(modelscope_mod, "fetch_capped", fake))
        return stack

    def test_is_file_missing_recognises_business_code(self):
        """404 与「500 + 业务码」都算不存在；真正的 5xx 仍必须算故障。"""
        missing_500 = CappedBody(500, {}, content=self.MISSING_BODY)
        real_error_500 = CappedBody(
            500, {}, content=b'{"Code":500,"Message":"service unavailable"}'
        )
        self.assertTrue(modelscope_mod._is_file_missing(CappedBody(404, {})))
        self.assertTrue(modelscope_mod._is_file_missing(missing_500))
        # 拿不到 / 认不出错误体时必须保守当成故障，不能把真故障说成「文件不存在」
        self.assertFalse(modelscope_mod._is_file_missing(CappedBody(500, {})))
        self.assertFalse(modelscope_mod._is_file_missing(real_error_500))
        self.assertFalse(modelscope_mod._is_file_missing(CappedBody(403, {})))

    async def test_missing_zh_candidate_falls_back_to_readme(self):
        """中文候选 500（不存在）必须跳过，继续回退到 README.md。"""
        status_map = {cand: (500, self.MISSING_BODY) for cand in ZH_README_CANDIDATES}
        status_map["README.md"] = (200, "# 标题\n\n正文内容够长以通过阈值检查。".encode("utf-8"))

        with self._patched(status_map):
            result = await modelscope_mod.fetch_modelscope_doc(
                "https://modelscope.cn/models/owner/name"
            )

        self.assertEqual(result["platform"], "modelscope")
        self.assertIn("正文内容够长", result["content"])

    async def test_real_5xx_still_reported_as_failure(self):
        """真正的服务故障不能被「文件不存在」吞掉，必须如实报错。"""
        status_map = {"README.md": (503, b'{"Code":503,"Message":"busy"}')}

        with self._patched(status_map):
            with self.assertRaises(AppException) as ctx:
                await modelscope_mod.fetch_modelscope_doc(
                    "https://modelscope.cn/models/owner/name"
                )
        self.assertEqual(ctx.exception.code, 502)
        self.assertIn("503", ctx.exception.message)


class HackerNewsFetchTests(unittest.IsolatedAsyncioTestCase):
    """Hacker News fetcher（桩 Firebase 条目接口）"""

    async def test_story_with_link_and_comments(self):
        from app.services.third_party_fetch.articles import fetch_hackernews

        items = {
            "1": {
                "type": "story",
                "title": "HN 标题",
                "url": "https://example.com/post",
                "text": "<p>条目自带的 <b>HTML</b> 正文</p>",
                "kids": [11, 12],
            },
            "11": {"by": "alice", "text": "<p>第一条评论</p>"},
            "12": {"by": "bob", "text": "<p>第二条评论</p>"},
        }

        async def _fake_hn_item(client, item_id):
            return items.get(str(item_id))

        original = articles_mod._hn_item
        articles_mod._hn_item = _fake_hn_item
        try:
            result = await fetch_hackernews("https://news.ycombinator.com/item?id=1", False)
        finally:
            articles_mod._hn_item = original

        self.assertEqual(result["platform"], "hackernews")
        self.assertIn("# HN 标题", result["content"])
        self.assertIn("**HTML**", result["content"])
        self.assertIn("[原文链接](https://example.com/post)", result["content"])
        self.assertIn("## 评论区", result["content"])
        self.assertIn("**alice**", result["content"])

    async def test_invalid_url_rejected(self):
        from app.services.third_party_fetch.articles import fetch_hackernews

        with self.assertRaises(AppException) as ctx:
            await fetch_hackernews("https://news.ycombinator.com/newest", False)
        self.assertIn("仅支持", ctx.exception.message)


class StackOverflowFetchTests(unittest.IsolatedAsyncioTestCase):
    """Stack Overflow / Stack Exchange fetcher（桩页面响应）"""

    PAGE = """
    <html><head><title>如何提问 - Stack Overflow</title></head>
    <body>
      <div id="question">
        <div class="js-post-body"><p>问题正文，包含足够多的字符以通过最小长度阈值检查。</p></div>
        <div class="user-details" itemprop="author">
          <a href="/users/1/alice">alice</a>
          <span class="reputation-score">1,234</span>
          <span class="user-action-time">asked yesterday</span>
        </div>
      </div>
      <div class="post-taglist"><a class="post-tag">python</a><a class="post-tag">pandas</a></div>
      <div id="answers">
        <div class="answer accepted-answer"><div class="js-post-body"><p>采纳的回答内容。</p></div></div>
        <div class="answer"><div class="js-post-body"><p>其他回答内容。</p></div></div>
      </div>
    </body></html>
    """

    async def test_question_and_answers(self):
        from app.services.third_party_fetch.articles import fetch_stackoverflow

        async def _fake_get_page(url, **kwargs):
            return _FakeHtmlResponse(self.PAGE, final_url="https://stackoverflow.com/questions/1/x")

        original = articles_mod._get_page
        articles_mod._get_page = _fake_get_page
        try:
            result = await fetch_stackoverflow("https://stackoverflow.com/questions/1/x", False)
        finally:
            articles_mod._get_page = original

        self.assertEqual(result["platform"], "stackoverflow")
        self.assertEqual(result["title"], "如何提问")  # 站点后缀被清理
        self.assertIn("## 问题", result["content"])
        self.assertIn("## 采纳回答", result["content"])
        self.assertIn("采纳的回答内容", result["content"])
        # 作者取提问者链接文本（而非含 "asked yesterday" 的整块文本），标签取 post-tag
        self.assertEqual(result["author"], "alice")
        self.assertEqual(result["tags"], ["python", "pandas"])
        self.assertIn(
            "> 来源：[Stack Overflow · alice](https://stackoverflow.com/questions/1/x)",
            result["content"],
        )

    async def test_non_question_path_rejected(self):
        from app.services.third_party_fetch.articles import fetch_stackoverflow

        with self.assertRaises(AppException) as ctx:
            await fetch_stackoverflow("https://stackoverflow.com/tags", False)
        self.assertIn("仅支持", ctx.exception.message)


class LanguageDetectTests(unittest.TestCase):
    """正文语言探测（管理端「翻译为中文」提示用）"""

    def test_detect(self):
        from app.services.third_party_fetch.common import detect_content_language

        # 中文为主（含短正文）→ zh
        self.assertEqual(detect_content_language("这是一段完全由中文组成的正文内容，没有任何拉丁字母。"), "zh")
        self.assertEqual(detect_content_language("短中文正文。"), "zh")
        # 纯英文 / 无中文 → en
        self.assertEqual(detect_content_language("This is a plain English article body."), "en")
        self.assertEqual(detect_content_language("12345 !!!"), "en")
        # 少量中文夹在大量英文中 → en
        self.assertEqual(detect_content_language("Hello world " * 40 + "中文"), "en")


class MetaInfoTests(unittest.TestCase):
    """元信息补全：简介生成 / 标签清洗 / 出处行 / 平台名与作者回填"""

    def test_build_summary_skips_structure_lines(self):
        from app.services.third_party_fetch.common import build_summary

        md = (
            "# 项目标题\n\n"
            "![badge](https://img.shields.io/x.svg)\n\n"
            "> 一段引用\n\n"
            "这是项目的第一段实质介绍文字，足够长可以当作简介使用。\n\n"
            "第二段。"
        )
        self.assertEqual(
            build_summary(md), "这是项目的第一段实质介绍文字，足够长可以当作简介使用。"
        )

    def test_build_summary_truncates_and_cleans_markdown(self):
        from app.services.third_party_fetch.common import build_summary

        md = "见 [官方文档](https://a.com/x) 与 **加粗** 说明，" + "内容" * 100
        summary = build_summary(md, limit=20)
        self.assertTrue(summary.endswith("…"))
        self.assertEqual(len(summary), 21)  # 20 字 + 省略号
        self.assertNotIn("](", summary)
        self.assertNotIn("**", summary)

    def test_build_summary_empty_when_no_prose(self):
        from app.services.third_party_fetch.common import build_summary

        self.assertEqual(build_summary("# 标题\n\n![a](b.png)\n\n- 列表"), "")

    def test_clean_tags(self):
        from app.services.third_party_fetch.common import clean_tags

        self.assertEqual(
            clean_tags(["V8", " v8 ", "#前端#", "", "x" * 30, "性能", "性能"]),
            ["V8", "前端", "性能"],
        )
        self.assertEqual(clean_tags(["a", "b", "c"], limit=2), ["a", "b"])

    def test_build_source_line(self):
        from app.services.third_party_fetch.common import build_source_line

        self.assertEqual(
            build_source_line("掘金", "张三", "https://juejin.cn/post/1"),
            "> 来源：[掘金 · 张三](https://juejin.cn/post/1)",
        )
        self.assertEqual(
            build_source_line("Reddit", "", "https://r.com/1"),
            "> 来源：[Reddit](https://r.com/1)",
        )
        self.assertEqual(build_source_line("知乎", "李四", ""), "> 来源：知乎 · 李四")

    def test_extract_author_and_tags_from_meta(self):
        html = (
            '<html><head><meta name="author" content="by 张三">'
            '<meta name="keywords" content="A, B，C">'
            '<meta property="article:tag" content="D">'
            "</head><body></body></html>"
        )
        root = html2md.parse_html(html)
        self.assertEqual(html2md.extract_author(root), "张三")
        self.assertEqual(html2md.extract_tags(root), ["A", " B", "C", "D"])

    def test_extract_author_selector_fallback(self):
        html = (
            '<html><body><div class="AuthorInfo-name">李四</div></body></html>'
        )
        root = html2md.parse_html(html)
        self.assertEqual(
            html2md.extract_author(root, (".AuthorInfo-name",)), "李四"
        )
        self.assertEqual(html2md.extract_author(root), "")  # 无 meta 且未给选择器

    def test_enrich_fills_defaults(self):
        from app.services.third_party_fetch import _enrich

        result = _enrich(
            {
                "content": "# 标题\n\n这是正文的第一段内容，用来生成自动简介。",
                "title": "owner/repo",
                "platform": "github",
                "resource_type": "repo",
            }
        )
        self.assertEqual(result["platform_label"], "GitHub")
        self.assertEqual(result["author"], "owner")  # 仓库类取 owner 段
        self.assertEqual(result["summary"], "这是正文的第一段内容，用来生成自动简介。")
        self.assertEqual(result["tags"], ["GitHub"])  # 无候选标签时回退平台名

    def test_enrich_keeps_fetcher_values(self):
        from app.services.third_party_fetch import _enrich

        result = _enrich(
            {
                "content": "正文",
                "title": "文章",
                "platform": "juejin",
                "resource_type": "article",
                "author": "张三",
                "tags": ["V8"],
            }
        )
        self.assertEqual(result["platform_label"], "掘金")
        self.assertEqual(result["author"], "张三")
        self.assertEqual(result["tags"], ["V8"])


class TitleHelpersTests(unittest.TestCase):
    """标题清理与组装"""

    def test_clean_title(self):
        self.assertEqual(_clean_title("文章 - 知乎", articles_mod._ZHIHU), "文章")
        self.assertEqual(_clean_title("文章 - 掘金", articles_mod._JUEJIN), "文章")
        self.assertEqual(
            _clean_title("Python入门_CSDN博客-某某博客", articles_mod._CSDN), "Python入门"
        )

    def test_compose_avoids_duplicate_h1(self):
        body = "# 标题\n\n正文"
        self.assertEqual(_compose_article_markdown("标题", body), body)
        composed = _compose_article_markdown("另一个", "正文")
        self.assertTrue(composed.startswith("# 另一个\n\n正文"))


class ImageHostTests(unittest.TestCase):
    """图片白名单：后缀匹配与既有精确匹配并存"""

    def test_suffix_matching(self):
        urls = [
            "https://p1-juejin.byteimg.com/a.png",
            "https://pic1.zhimg.com/b.png",
            "https://evil-juejin.byteimg.com/c.png",  # 后缀匹配会命中（属预期语义）
            "https://github.com/d.png",
        ]
        # 与 collect_image_urls 不同，这里直接验证 is_image_url 语义
        from app.services.third_party_fetch.common import is_image_url

        # 白名单一律写**父域**后缀（与 articles.py:_JUEJIN 一致），点分匹配即可覆盖
        # `p1-juejin.byteimg.com` 这类连字符子域。原先这条断言依赖
        # `endswith("-" + base)` 去迁就窄后缀 `.juejin.byteimg.com`，而该分支同时
        # 放行了 `attacker-byteimg.com` —— 已删除（第四轮审查 P1-53①）。
        self.assertTrue(is_image_url(urls[0], set(), (".byteimg.com",)))
        self.assertTrue(is_image_url(urls[1], set(), (".zhimg.com",)))
        self.assertTrue(is_image_url(urls[3], {"github.com"}))
        self.assertFalse(is_image_url("https://other.com/e.png", set(), (".zhimg.com",)))

    def test_collect_with_suffixes(self):
        md = "![a](https://p3-juejin.byteimg.com/x.png) ![b](https://unknown.host/y.png)"
        collected = collect_image_urls(md, set(), (".byteimg.com",))
        self.assertEqual(collected, ["https://p3-juejin.byteimg.com/x.png"])

    def test_hyphen_lookalike_domains_are_rejected(self):
        """连字符后缀匹配会放行**可注册**的仿冒域名（第四轮全量审查 P1-53①）

        `hostname.endswith("-" + base)` 当初是为兼容 `p1-juejin.byteimg.com` 这类
        子域加的，但掘金/知乎/CSDN 的真实主机都落在 `.byteimg.com` / `.zhimg.com` /
        `.csdnimg.cn` 这类**父域**后缀下，点分分支已经覆盖到（上面几个用例就是证据）。
        连字符分支的净效果只有：`attacker-byteimg.com`、`evil-juejin.cn` 这类任何人都
        能注册的域名也被当作白名单 —— 第三方作者只要在热门文章里埋一个这种图片地址，
        就能让本站服务端发起一次由他指定目标的出站请求，并把结果以本站 COS/CDN
        域名发布出去（看起来像站方内容）。
        """
        from app.services.third_party_fetch.common import is_image_url

        suffixes = articles_mod._JUEJIN.image_host_suffixes

        for bad in (
            "https://attacker-byteimg.com/x.png",
            "https://evil-juejin.cn/x.png",
            "https://x-toutiaoimg.com/y.png",
        ):
            with self.subTest(url=bad):
                self.assertFalse(is_image_url(bad, set(), suffixes))

        # 真实子域形态必须仍然命中 —— 且只靠点分父域，不依赖连字符匹配
        for good in (
            "https://p1-juejin.byteimg.com/a.png",
            "https://p9-xtjj-sign.byteimg.com/b.png",
            "https://p3-sign.toutiaoimg.com/c.png",
        ):
            with self.subTest(url=good):
                self.assertTrue(is_image_url(good, set(), suffixes))

    def test_juejin_covers_bytedance_cdn_subdomains(self):
        """掘金正文图片的实际域名必须被转存白名单覆盖（2026-10-11 线上回归）。

        现象：用户端控制台报 `ERR_BLOCKED_BY_ORB` —— 掘金图片实际落在
        `p9-xtjj-sign.byteimg.com`（头条系 CDN，非 `*.juejin.byteimg.com`），
        原后缀 `.juejin.byteimg.com` **匹配不到** → 图片未被转存到 COS →
        正文里留着带 `x-expires` 签名的原始链接（约 3 天有效期）。
        签名过期后 CDN 返回 **403 + text/html**（实测），浏览器对跨域 `<img>`
        的非图片响应直接 ORB 拦截：图片变成裂图且控制台报错。

        本用例把「真实会被导入的域名形态」钉死，避免后缀白名单再被收窄。
        """
        from app.services.third_party_fetch.common import is_image_url

        suffixes = articles_mod._JUEJIN.image_host_suffixes
        matched = [
            "https://p9-xtjj-sign.byteimg.com/tos-cn-i-73owjymdk6/x~tplv-q75.awebp",
            "https://p1-juejin.byteimg.com/a.png",
            "https://p3-sign.toutiaoimg.com/tos-cn-i-abc/y.webp",
            "https://juejin.cn/img/z.png",
        ]
        for url in matched:
            with self.subTest(url=url):
                self.assertTrue(
                    is_image_url(url, set(), suffixes),
                    f"{url} 未被掘金转存白名单覆盖 → 该图不会进 COS，签名过期后必成裂图",
                )
        self.assertFalse(is_image_url("https://evil.example.com/e.png", set(), suffixes))

    def test_collect_picks_bytedance_cdn_images(self):
        """正文里出现头头条系 CDN 图片时必须被收集到（转存的前置条件）"""
        md = (
            "![a](https://p9-xtjj-sign.byteimg.com/tos-cn-i-1/x.awebp)"
            " ![b](https://unknown.host/y.png)"
        )
        collected = collect_image_urls(md, set(), articles_mod._JUEJIN.image_host_suffixes)
        self.assertEqual(
            collected,
            ["https://p9-xtjj-sign.byteimg.com/tos-cn-i-1/x.awebp"],
            "头条系 CDN 图片未被收集 → 转存流程不会处理它",
        )


class _FakeResponse:
    """桩 httpx.Response：JSON 响应"""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}
        self.url = "https://stub.local/"

    def json(self):
        return self._payload


class _FakeHtmlResponse:
    """桩 httpx.Response：HTML 响应"""

    def __init__(self, text, final_url):
        self.text = text
        self.status_code = 200
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self.url = final_url

    def json(self):
        raise ValueError("not json")


if __name__ == "__main__":
    unittest.main()
