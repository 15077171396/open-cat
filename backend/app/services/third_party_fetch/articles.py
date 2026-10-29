"""文章类平台适配器（国外 + 国内社区）：与仓库类平台并列，共享管线
（html2md 转换 + common 图片转存/语言探测/大小校验）；各平台只负责 URL 校验、
页面拉取（国外可走 ARTICLE_PROXY 出站代理）、正文选择器与返回结构适配。

地址格式（兼容省略协议）：
国外：X (x.com/{user}/status/{id} | /i/article/{id})、Reddit (…/comments/{id})、
Medium (medium.com/@user/slug | {pub}.medium.com/slug)、Dev.to、
Stack Overflow/Exchange (…/questions/{id})、Hacker News (item?id=)
国内：知乎 (zhuanlan.zhihu.com/p/{id} | question/{qid}/answer/{aid})、掘金 (post/{id})、
CSDN (blog.csdn.net/{user}/article/details/{id})、博客园、简书、
微信公众号 (/s/... | /s?__biz=...)、SegmentFault (/a/{id})、开源中国、V2EX (/t/{id})
统一返回结构与 third_party_fetch/__init__.py 一致（resource_type="article"）。
"""

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from typing import Awaitable, Callable, Optional, Union
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.core.executors import run_heavy
from app.core.exceptions import AppException
from app.services.third_party_fetch import html2md
from app.services.third_party_fetch.common import (
    MAX_RESPONSE_SIZE,
    TRANSFERABLE_IMAGE_HOSTS,
    CappedBody,
    build_source_line,
    check_content_size,
    clean_tags,
    detect_content_language,
    fetch_capped,
    fetch_capped_following_redirects,
    transfer_images_to_cos,
)
from app.services.third_party_fetch.html2md import (
    MIN_CONTENT_CHARS,
    clean_author,
    extract_article_from_root,
    extract_author,
    extract_tags,
    extract_title,
    find_all,
    find_first,
    html_to_markdown,
    node_text,
    node_to_markdown,
    parse_html,
    rewrite_page_relative_urls,
)

_TIMEOUT = httpx.Timeout(20.0, connect=10.0)

# 浏览器 UA：文章平台普遍对非浏览器 UA 拦截（知乎 / CSDN / V2EX 等）
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
_JSON_HEADERS = {"User-Agent": _HEADERS["User-Agent"], "Accept": "application/json"}

_RETRYABLE_STATUS = {403, 429}

# 支持的平台提示（供统一入口报错用）
SUPPORTED_FOREIGN = "X (Twitter) / Reddit / Medium / Dev.to / Stack Overflow / Hacker News"
SUPPORTED_DOMESTIC = (
    "知乎 / 掘金 / CSDN / 博客园 / 简书 / 微信公众号 / SegmentFault / 开源中国 / V2EX"
    " / 少数派 / 人人都是产品经理"
)


def _normalize_url(url: str) -> str:
    """补全省略的协议前缀"""
    text = (url or "").strip()
    if text and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        text = "https://" + text
    return text


# 页面拉取（直连 → 代理兜底）


def _headers_with_referer(url: str, headers: Optional[dict] = None) -> dict:
    """请求头补上目标站点同源 Referer

    ⚠️ 部分站点（掘金即典型）**没有 Referer 时返回反爬壳页**（约 2KB 的空
    HTML + argus 脚本，正文选择器必然不命中 → 报「未提取到正文」）；带上
    同源 Referer 才能拿到 100KB 级的 SSR 正文。浏览器请求天然带 Referer，
    服务端不加就等于主动把自己标记成爬虫。
    """
    merged = dict(headers or _HEADERS)
    if not any(key.lower() == "referer" for key in merged):
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            merged["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
    return merged


async def _get_page(
    url: str,
    *,
    headers: Optional[dict] = None,
    proxy_eligible: bool = False,
) -> CappedBody:
    """GET 页面 / 接口

    网络异常或 403 / 429 / 5xx 且配置了 ARTICLE_PROXY 时经出站代理重试一次
    （仅国外平台 proxy_eligible=True；国内平台直连必达，不走代理）。
    全部失败抛 502，代理未配置时附配置提示。

    ⚠️ 走**流式下载**（台账 P2-6）：页面 / 接口返回的体积完全由对方站点决定，
    ``client.get`` 会先把整个响应体读进内存再判断大小 —— 那只是事后报警。
    """
    headers = _headers_with_referer(url, headers)
    failure = ""
    status: Optional[int] = None
    try:
        # 手动跟随重定向并逐跳复检目标地址（第三轮增量审查 P1-46②）：
        # httpx 的 follow_redirects=True 会把我们带到对方指定的**任意**地址，
        # 包括内网服务与云元数据端点 —— SSRF 的经典跳板。
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            body = await fetch_capped_following_redirects(
                client, url, MAX_RESPONSE_SIZE, headers=headers
            )
        if body is None:
            failure = "网络异常"
        elif body.over_limit:
            # 超限走代理也是同一份内容，重试无意义，直接给可操作的提示
            raise AppException(
                code=400,
                message=f"页面内容过大（超过 {MAX_RESPONSE_SIZE // (1024 * 1024)}MB），"
                "请手动摘录后粘贴",
            )
        elif body.status_code not in _RETRYABLE_STATUS and body.status_code < 500:
            return body
        else:
            status = body.status_code
            failure = f"HTTP {body.status_code}"
    except httpx.HTTPError as exc:
        failure = exc.__class__.__name__

    if proxy_eligible and settings.ARTICLE_PROXY:
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, follow_redirects=False, proxy=settings.ARTICLE_PROXY
            ) as client:
                body = await fetch_capped_following_redirects(
                    client, url, MAX_RESPONSE_SIZE, headers=headers
                )
            if body is not None:
                if body.over_limit:
                    raise AppException(
                        code=400,
                        message=f"页面内容过大（超过 {MAX_RESPONSE_SIZE // (1024 * 1024)}MB），"
                        "请手动摘录后粘贴",
                    )
                return body
        except httpx.HTTPError:
            pass

    if status in _RETRYABLE_STATUS:
        # 403 / 429 几乎总是对方站点的 **IP 风控**（机房 IP 被拦），而不是我们的
        # 网络故障。说清楚这一点，用户才不会反复重试或把它当成系统 bug。
        raise AppException(
            code=502,
            message=f"页面请求失败（{failure}，目标站点对服务器 IP 有访问限制），"
            "请稍后重试或改用其他来源",
        )

    hint = "，如服务器无法直连该站点，可在 .env 配置 ARTICLE_PROXY 出站代理" if proxy_eligible else ""
    raise AppException(code=502, message=f"页面请求失败（{failure}）{hint}")


# 平台配置


@dataclass(frozen=True)
class ArticleSite:
    """HTML 文章类平台的抽取配置"""

    platform: str                       # 返回值 platform 字段
    label: str                          # 中文名（报错提示 / 出处展示用）
    patterns: tuple[re.Pattern, ...]    # 合法文章地址（全部不匹配则 400）
    example: str                        # 示例地址（报错提示用）
    foreign: bool = False               # 国外平台（拉取 / 图片转存可走代理）
    content_selectors: tuple[str, ...] = ()
    allow_fallback: bool = True         # 选择器未命中时是否回退 article/body
    min_chars: int = MIN_CONTENT_CHARS  # 正文最少可见字符数（低于视为抽取失败）
    img_src_attrs: tuple[str, ...] = html2md.DEFAULT_IMG_SRC_ATTRS
    image_hosts: frozenset[str] = frozenset()   # 图片转存额外精确域名
    image_host_suffixes: tuple[str, ...] = ()   # 图片转存额外后缀域名
    title_strip: tuple[re.Pattern, ...] = ()    # 标题清理（去 "- 知乎" 等后缀）
    author_selectors: tuple[str, ...] = ()      # 作者选择器（meta 未命中时兜底）
    tag_selectors: tuple[str, ...] = ()         # 标签选择器（meta 未命中时兜底）


def _validate_url(url: str, site: ArticleSite) -> str:
    text = _normalize_url(url)
    if not any(p.match(text) for p in site.patterns):
        raise AppException(
            code=400, message=f"仅支持{site.label}文章地址，如 {site.example}"
        )
    return text


def _clean_title(title: str, site: ArticleSite) -> str:
    """按平台配置去掉标题尾部的站点后缀（如 "- 知乎"、" - Stack Overflow"）"""
    for pattern in site.title_strip:
        title = pattern.sub("", title)
    return title.strip()


def _escape_heading(text: str) -> str:
    """标题进 Markdown 标题行的转义（防 # / [] 等破坏结构）"""
    return re.sub(r"([\\`*_\[\]<>#])", r"\\\1", text).strip()


def _compose_article_markdown(title: str, body: str) -> str:
    """组装文章 Markdown：正文未自带同名 h1 时追加标题行"""
    title = (title or "").strip()
    if not title:
        return body
    if body.startswith("# "):
        first = body.split("\n", 1)[0][2:].strip()
        if first == _escape_heading(title):
            return body
    return f"# {_escape_heading(title)}\n\n{body}"


# HTML 文章类平台通用拉取


def _extract_article_sync(
    html_text: str, final_url: str, site: ArticleSite
) -> Optional[dict]:
    """把「HTML → Markdown」整段 **CPU 工作**抽成同步函数，供 `run_heavy` 在线程池里调用。

    为什么必须抽出来（第三轮增量审查 P1-38）：这一整段是纯 CPU —— stdlib
    html.parser 建树、遍历渲染、`detect_content_language` 的全文档 `re.findall`、
    `rewrite_page_relative_urls` 的三次全文档正则、`check_content_size` 的
    `len(content.encode())` 二次全量拷贝。原先它们**直接写在 `async def` 里同步执行**，
    会阻塞整个事件循环；生产 gunicorn 只有 `-w 2`，**两个并发导入即全站停顿**
    （首页/搜索/评论一起卡住，且无报错、无部分输出）。同仓库 `uploads.py` /
    `security.py` 已有 `to_thread` 的正确范式。

    返回 `None` 表示「没抽到正文」（选择器未命中、或正文过短）——两种情况给同一提示。
    `check_content_size` 超限会抛 `AppException(400)`，异常可正常穿过线程边界。
    """
    root = parse_html(html_text)
    raw_title, node = extract_article_from_root(
        root, site.content_selectors, site.allow_fallback
    )
    if node is None:
        return None

    markdown = node_to_markdown(node, site.img_src_attrs)
    if len(markdown) < site.min_chars:
        return None

    # 作者 / 标签从同一 DOM 抽取（meta 优先，平台选择器兜底），供管理端回填与标注出处
    author = extract_author(root, site.author_selectors)
    tags = clean_tags(extract_tags(root, site.tag_selectors))

    title = _clean_title(raw_title, site)
    markdown = _compose_article_markdown(title, markdown)
    # 相对图片 / 链接基于最终页面地址（跟随重定向后）补全为绝对地址
    markdown = rewrite_page_relative_urls(markdown, final_url)
    # 出处归因（与 X / Reddit / HN 等平台格式一致）
    markdown = f"{markdown}\n\n{build_source_line(site.label, author, final_url)}"
    check_content_size(markdown)
    return {"markdown": markdown, "title": title, "author": author, "tags": tags}


async def fetch_web_article(
    url: str, site: ArticleSite, include_files: bool = False
) -> dict:
    """通用文章拉取：拉页面 → 选择器抽正文 → HTML 转 Markdown → 图片转存"""
    text = _validate_url(url, site)
    resp = await _get_page(text, proxy_eligible=site.foreign)
    # ⚠️ 这里刻意**不再**判 403 / 429：`_get_page` 已把它们当失败处理并直接抛
    # 502（且带「目标站点对服务器 IP 有访问限制」的提示）。原先此处还留着一个
    # 「{site.label} 拒绝了本次请求」的分支，但 `_get_page` 永远不会把 403/429
    # 的 body 返回给调用方 —— 那个分支是**死代码**，会让人误以为风控提示已生效。
    if resp.status_code != 200:
        raise AppException(
            code=502, message=f"{site.label} 页面请求失败（HTTP {resp.status_code}）"
        )
    content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type and "html" not in content_type:
        raise AppException(code=400, message=f"该地址返回的不是网页（{content_type}）")

    # 纯 CPU 阶段走线程池，避免阻塞事件循环（见 _extract_article_sync 的说明）
    extracted = await run_heavy(
        _extract_article_sync, resp.text, str(resp.url), site
    )
    if extracted is None:
        raise AppException(
            code=400,
            message=f"未能从{site.label}页面中提取到正文：文章可能需要登录查看、"
            "已被删除或为动态渲染，可打开原文确认后手动复制",
        )
    markdown = extracted["markdown"]
    title = extracted["title"]
    author = extracted["author"]
    tags = extracted["tags"]

    final_host = (urlparse(str(resp.url)).hostname or "").lower()
    hosts = TRANSFERABLE_IMAGE_HOSTS | set(site.image_hosts) | ({final_host} if final_host else set())
    # 国外平台图片（miro.medium.com / media.dev.to 等）在国内服务器常无法直连，
    # 配置 ARTICLE_PROXY 时以代理作为下载兜底通道（COS 上传始终直连，不走代理）
    proxy_client = (
        httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True, proxy=settings.ARTICLE_PROXY
        )
        if site.foreign and settings.ARTICLE_PROXY
        else None
    )
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            image_stats: dict[str, int] = {}
            markdown, images_transferred = await transfer_images_to_cos(
                client,
                markdown,
                hosts=hosts,
                host_suffixes=site.image_host_suffixes,
                proxy_client=proxy_client,
                proxy_hosts=hosts,
                # 图片来源页面：掘金 / 头条系 CDN 会校验 Referer
                referer=str(resp.url),
                stats=image_stats,
            )
    finally:
        if proxy_client is not None:
            await proxy_client.aclose()
    return {
        "content": markdown,
        "title": title or text,
        "platform": site.platform,
        "resource_type": "article",
        "language": detect_content_language(markdown),
        "source": "web",
        "source_url": str(resp.url),
        "images_transferred": images_transferred,
        # 正文里待转存的图片总数：与 images_transferred 一起让管理端能提示
        # 「3/4 张成功，1 张保留了会过期的原链」
        "images_total": image_stats.get("total", images_transferred),
        "files_included": False,
        "author": author,
        "tags": tags,
    }


# 国外平台：X (Twitter)

_TWITTER_URL_RE = re.compile(
    r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/"
    r"(?:[^/?]+/status|i/web/status|i/article)/(\d+)",
    re.IGNORECASE,
)


async def fetch_twitter(url: str, include_files: bool = False) -> dict:
    """X (Twitter) 帖子 / 长文拉取（经 fxtwitter / vxtwitter 公共 JSON API）

    X 官方 API 需要鉴权且页面为全动态渲染，公共镜像 API 是匿名拉取唯一
    可行途径：优先 fxtwitter（含长文 article 字段），失败回退 vxtwitter。
    仅拉取单条帖子（线程 / 会话不展开）。
    """
    text = _normalize_url(url)
    m = _TWITTER_URL_RE.match(text)
    if not m:
        raise AppException(
            code=400,
            message="仅支持 X (Twitter) 帖子 / 长文地址，如 https://x.com/user/status/123456789",
        )
    tweet_id = m.group(1)

    data: dict = {}
    source = ""
    failure = ""
    endpoints = (
        (f"https://api.fxtwitter.com/status/{tweet_id}", "fxtwitter"),
        (f"https://api.vxtwitter.com/Twitter/status/{tweet_id}", "vxtwitter"),
    )
    for endpoint, name in endpoints:
        try:
            resp = await _get_page(endpoint, headers=_JSON_HEADERS, proxy_eligible=True)
        except AppException as exc:
            failure = exc.message or "请求失败"
            continue
        if resp.status_code == 404:
            failure = "帖子不存在或已被删除"
            continue
        if resp.status_code != 200:
            failure = f"HTTP {resp.status_code}"
            continue
        try:
            payload = resp.json()
        except ValueError:
            failure = "返回内容无法解析"
            continue
        if isinstance(payload, dict) and (payload.get("tweet") or payload.get("text")):
            data = payload
            source = name
            break
        failure = "返回内容缺少正文"
    if not data:
        raise AppException(
            code=404 if "不存在" in failure else 502,
            message=f"X 帖子拉取失败：{failure or '未知错误'}",
        )

    tweet = data.get("tweet") or {}
    article = tweet.get("article") or {}
    body_text = (article.get("text") or tweet.get("text") or data.get("text") or "").strip()
    if not body_text:
        raise AppException(code=404, message="该帖子没有可拉取的文字内容（可能仅含媒体）")

    author = tweet.get("author") or {}
    author_name = (author.get("name") or data.get("user_name") or "").strip()
    handle = (author.get("screen_name") or data.get("handle") or "").strip()
    created = (tweet.get("created_at") or data.get("created_at") or "").strip()

    media = (tweet.get("media") or {}).get("all") or []
    images = [
        m.get("url") for m in media
        if isinstance(m, dict) and m.get("type") == "photo" and m.get("url")
    ]

    # 纯文本 → Markdown：段落间空行，行内换行转硬换行（避免 marked 折叠成一行）
    paragraphs = [
        p.strip().replace("\n", "  \n")
        for p in re.split(r"\n{2,}", body_text)
        if p.strip()
    ]
    body = "\n\n".join(paragraphs)
    if images:
        body += "\n\n" + "\n\n".join(
            f"![图片 {i + 1}]({u})" for i, u in enumerate(images)
        )

    article_title = (article.get("title") or "").strip()
    if article_title:
        body = _compose_article_markdown(article_title, body)
    if handle:
        source_line = f"> 来源：[{author_name} (@{handle})]({text})" if author_name else f"> 来源：[@{handle}]({text})"
    else:
        source_line = f"> 来源：[X]({text})"
    if created:
        source_line += f" · {created}"
    body = f"{body}\n\n{source_line}"

    title = article_title or (
        f"{author_name}：{body_text[:40]}…" if author_name else f"X 帖子 {tweet_id}"
    )
    check_content_size(body)
    # 出处展示名：优先「昵称 (@handle)」，仅有其一时退化
    if author_name and handle:
        author_display = f"{author_name} (@{handle})"
    else:
        author_display = f"@{handle}" if handle else author_name
    return {
        "content": body,
        "title": title,
        "platform": "twitter",
        "resource_type": "article",
        "language": detect_content_language(body),
        "source": source,
        "source_url": text,
        "images_transferred": 0,
        "files_included": False,
        "author": author_display,
        # 帖子正文里的 #话题 作为标签候选
        "tags": clean_tags(re.findall(r"#([\w\u4e00-\u9fff]+)", body_text)),
    }


# 国外平台：Reddit

_REDDIT_URL_RE = re.compile(
    r"^https?://(?:www\.|old\.|new\.|np\.)?reddit\.com/r/[^/\s]+/comments/([a-z0-9]+)",
    re.IGNORECASE,
)


async def fetch_reddit(url: str, include_files: bool = False) -> dict:
    """Reddit 帖子拉取（官方 .json 后缀接口，匿名可用）

    文字帖取 selftext（本身即 Markdown）；链接帖 / 媒体帖取外链或图集。
    评论不展开（噪音大且分页复杂），如需讨论内容建议手动摘录。
    """
    text = _normalize_url(url)
    m = _REDDIT_URL_RE.match(text)
    if not m:
        raise AppException(
            code=400,
            message="仅支持 Reddit 帖子地址，如 https://www.reddit.com/r/subreddit/comments/xxxxxx/",
        )

    api_url = text.split("?")[0].rstrip("/") + ".json"
    resp = await _get_page(api_url, headers=_JSON_HEADERS, proxy_eligible=True)
    if resp.status_code == 404:
        raise AppException(code=404, message="Reddit 帖子不存在或已被删除")
    if resp.status_code == 403:
        raise AppException(
            code=502,
            message="Reddit 拒绝了本次请求（HTTP 403，服务器 IP 风控），"
            "可在 .env 配置 ARTICLE_PROXY 出站代理后重试",
        )
    if resp.status_code != 200:
        raise AppException(code=502, message=f"Reddit 请求失败（HTTP {resp.status_code}）")
    try:
        payload = resp.json()
        post = payload[0]["data"]["children"][0]["data"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise AppException(code=502, message="Reddit 返回内容无法解析")

    title = (post.get("title") or "").strip() or "Reddit 帖子"
    subreddit = post.get("subreddit") or ""
    author = post.get("author") or ""
    link_url = (post.get("url") or "").strip()
    permalink = (post.get("permalink") or "").strip()
    # 占位地址（about:blank）与指回本帖的站内地址不是可用外链，删除 / 移除帖
    # 常残留这两类值，若不剔除会把空帖误判为「链接帖」而返回无意义内容
    if link_url and (
        not link_url.lower().startswith(("http://", "https://"))
        or (permalink and link_url.rstrip("/").endswith(permalink.rstrip("/")))
    ):
        link_url = ""
    selftext = (post.get("selftext") or "").strip()
    created_utc = post.get("created_utc")

    parts: list[str] = []
    if selftext and selftext not in ("[removed]", "[deleted]"):
        parts.append(selftext)

    # 图集帖：media_metadata 中的图片按顺序补为 Markdown 图片
    gallery_urls: list[str] = []
    for item in (post.get("media_metadata") or {}).values():
        if not isinstance(item, dict) or item.get("e") != "Image":
            continue
        src = (item.get("s") or {}).get("u") or (item.get("s") or {}).get("gif")
        if src:
            gallery_urls.append(unescape(src))
    if gallery_urls:
        parts.append("\n\n".join(f"![图片 {i + 1}]({u})" for i, u in enumerate(gallery_urls)))

    if not parts:
        if post.get("is_video"):
            parts.append(f"（视频帖：[前往 Reddit 观看]({text})）")
        elif post.get("post_hint") == "image" and link_url:
            parts.append(f"![图片]({link_url})")
        elif link_url:
            parts.append(f"[{link_url}]({link_url})")
        else:
            raise AppException(code=404, message="该帖子没有可拉取的内容（可能已被删除）")

    body = "\n\n".join(parts)
    created_line = ""
    if created_utc:
        try:
            created_line = datetime.fromtimestamp(
                float(created_utc), tz=timezone.utc
            ).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            created_line = ""
    source_line = f"> 来源：[r/{subreddit} · u/{author}]({text})"
    if created_line:
        source_line += f" · {created_line}"
    content = f"# {_escape_heading(title)}\n\n{body}\n\n{source_line}"

    check_content_size(content)
    return {
        "content": content,
        "title": title,
        "platform": "reddit",
        "resource_type": "article",
        "language": detect_content_language(content),
        "source": "reddit_json",
        "source_url": text,
        "images_transferred": 0,
        "files_included": False,
        "author": f"u/{author}" if author else "",
        "tags": clean_tags([f"r/{subreddit}"] if subreddit else []),
    }


# 国外平台：Hacker News

_HN_URL_RE = re.compile(r"^https?://news\.ycombinator\.com/item\?id=(\d+)", re.IGNORECASE)

_HN_ITEM_API = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
_HN_COMMENT_LIMIT = 10  # 仅展开前 10 条顶层评论，避免树状全量拉取


async def _hn_item(client: httpx.AsyncClient, item_id: str) -> Optional[dict]:
    body = await fetch_capped(
        client, _HN_ITEM_API.format(id=item_id), MAX_RESPONSE_SIZE, headers=_JSON_HEADERS
    )
    if body is None or body.over_limit or body.status_code != 200:
        return None
    try:
        return body.json()
    except ValueError:
        return None


async def fetch_hackernews(url: str, include_files: bool = False) -> dict:
    """Hacker News 条目拉取（Firebase 公开 API）

    条目 = 标题 + 正文（部分条目自带 HTML 文本）+ 外链；评论展开前 10 条
    顶层热门评论（HN 的价值很大程度在评论区，全量树状展开则体积失控）。
    Firebase API 位于境外，配置 ARTICLE_PROXY 时经其出站（与其余国外平台一致）。
    """
    text = _normalize_url(url)
    m = _HN_URL_RE.match(text)
    if not m:
        raise AppException(
            code=400,
            message="仅支持 Hacker News 条目地址，如 https://news.ycombinator.com/item?id=12345678",
        )
    item_id = m.group(1)

    async with httpx.AsyncClient(
        timeout=_TIMEOUT, follow_redirects=True, proxy=settings.ARTICLE_PROXY or None
    ) as client:
        item = await _hn_item(client, item_id)
        if not item or item.get("type") not in ("story", "poll"):
            raise AppException(code=404, message="Hacker News 条目不存在或已被删除")
        title = (item.get("title") or "").strip() or f"Hacker News {item_id}"
        link = (item.get("url") or "").strip()
        text_html = (item.get("text") or "").strip()

        parts: list[str] = []
        if text_html:
            md = html_to_markdown(text_html)
            if md:
                parts.append(md)
        if link:
            parts.append(f"[原文链接]({link})")
        if not parts:
            raise AppException(code=404, message="该条目没有可拉取的内容（可能仅剩链接）")

        kids = [str(k) for k in (item.get("kids") or [])][: _HN_COMMENT_LIMIT]
        comments = await asyncio.gather(*(_hn_item(client, k) for k in kids))
        comment_parts: list[str] = []
        for comment in comments:
            if not comment or not (comment.get("text") or "").strip():
                continue
            author = comment.get("by") or "匿名"
            comment_md = html_to_markdown(comment["text"])
            if not comment_md:
                continue
            quoted = "\n".join(
                f"> {line}" if line else ">" for line in comment_md.split("\n")
            )
            comment_parts.append(f"**{author}**：\n\n{quoted}")
        if comment_parts:
            parts.append("## 评论区（前 {} 条）\n\n{}".format(len(comment_parts), "\n\n".join(comment_parts)))

    source_line = build_source_line("Hacker News", (item.get("by") or "").strip(), text)
    content = f"# {_escape_heading(title)}\n\n" + "\n\n".join(parts) + f"\n\n{source_line}"
    check_content_size(content)
    return {
        "content": content,
        "title": title,
        "platform": "hackernews",
        "resource_type": "article",
        "language": detect_content_language(content),
        "source": "hn_firebase",
        "source_url": text,
        "images_transferred": 0,
        "files_included": False,
        "author": (item.get("by") or "").strip(),
        "tags": [],
    }


# 国外平台：Stack Overflow / Stack Exchange

_SO_URL_RE = re.compile(
    r"^https?://(?:www\.)?(?:stackoverflow\.com|[\w.-]+\.stackexchange\.com)/questions/(\d+)",
    re.IGNORECASE,
)
_SO_ANSWER_LIMIT = 3  # 除采纳回答外最多附带的回答数


def _extract_stackoverflow_sync(html_text: str, final_url: str) -> Optional[dict]:
    """Stack Overflow 问答页的「HTML → Markdown」纯 CPU 段（供 to_thread 调用）。

    与 `_extract_article_sync` 同一个理由（第三轮增量审查 P1-38）：建树 + 多个
    回答的递归渲染 + 三次全文档正则都是纯 CPU，不能在事件循环里同步跑。
    """
    root = parse_html(html_text)
    title = extract_title(root)
    title = re.sub(r"\s*[-—–]\s*Stack\s*Overflow\s*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*[-—–]\s*.+\s+Stack\s+Exchange\s*$", "", title, flags=re.IGNORECASE)

    question_node = None
    question_container = find_first(root, "#question")
    if question_container is not None:
        question_node = find_first(question_container, ".js-post-body")
    if question_node is None:
        question_node = find_first(root, ".js-post-body")
    if question_node is None:
        return None

    parts: list[str] = ["## 问题", "", node_to_markdown(question_node)]

    # 作者取提问者（用户卡片内的首个链接，节点文本含「asked at ...」等噪音，
    # 故不能整块取文本）；标签取问题下方的 post-tag 胶囊
    author = ""
    if question_container is not None:
        user_node = find_first(question_container, ".user-details")
        if user_node is not None:
            link_node = find_first(user_node, "a")
            if link_node is not None:
                author = clean_author(node_text(link_node))
    tags = clean_tags([node_text(n) for n in find_all(root, ".post-tag")])

    answers: list[tuple[bool, str]] = []  # (是否采纳, Markdown)
    answers_container = find_first(root, "#answers")
    answer_nodes = find_all(answers_container, ".answer") if answers_container else find_all(root, ".answer")
    for answer in answer_nodes:
        body_node = find_first(answer, ".js-post-body")
        if body_node is None:
            continue
        accepted = "accepted-answer" in (answer.attrs.get("class") or "")
        answers.append((accepted, node_to_markdown(body_node)))
    accepted = next((md for is_accepted, md in answers if is_accepted), None)
    if accepted:
        parts += ["", "## 采纳回答", "", accepted]
    others = [md for is_accepted, md in answers if not is_accepted][: _SO_ANSWER_LIMIT]
    for i, md in enumerate(others, 1):
        parts += ["", f"## 回答 {i}", "", md]

    content = f"# {_escape_heading(title)}\n\n" + "\n".join(parts)
    content = rewrite_page_relative_urls(content, final_url)
    content = f"{content}\n\n{build_source_line('Stack Overflow', author, final_url)}"
    check_content_size(content)
    return {"content": content, "title": title.strip(), "author": author, "tags": tags}


async def fetch_stackoverflow(url: str, include_files: bool = False) -> dict:
    """Stack Overflow / Stack Exchange 问答拉取（服务端渲染页面抽取）

    抽取问题正文 + 采纳回答 + 其他高票回答（上限 3 个），代码块保留语言标记。
    """
    text = _normalize_url(url)
    if not _SO_URL_RE.match(text):
        raise AppException(
            code=400,
            message="仅支持 Stack Overflow / Stack Exchange 问答地址，"
            "如 https://stackoverflow.com/questions/123456/...",
        )
    resp = await _get_page(text, proxy_eligible=True)
    if resp.status_code != 200:
        raise AppException(
            code=502, message=f"页面请求失败（HTTP {resp.status_code}），请稍后重试"
        )

    # 纯 CPU 段走线程池（见 _extract_stackoverflow_sync 的说明）
    extracted = await run_heavy(
        _extract_stackoverflow_sync, resp.text, str(resp.url)
    )
    if extracted is None:
        raise AppException(
            code=400,
            message="未能从页面中提取到问题正文，请确认地址为问答页且问题公开可见",
        )
    return {
        "content": extracted["content"],
        "title": extracted["title"],
        "platform": "stackoverflow",
        "resource_type": "article",
        "language": detect_content_language(extracted["content"]),
        "source": "web",
        "source_url": str(resp.url),
        "images_transferred": 0,
        "files_included": False,
        "author": extracted["author"],
        "tags": extracted["tags"],
    }


# 平台配置注册

_ZHIHU = ArticleSite(
    platform="zhihu",
    label="知乎",
    patterns=(
        re.compile(r"^https?://zhuanlan\.zhihu\.com/p/\d+", re.IGNORECASE),
        re.compile(r"^https?://(?:www\.)?zhihu\.com/question/\d+(?:/(?:answer|answers)/\d+)?", re.IGNORECASE),
    ),
    example="https://zhuanlan.zhihu.com/p/123456789",
    content_selectors=(".Post-RichTextContainer", ".RichContent-inner", ".RichText", "article"),
    allow_fallback=False,
    img_src_attrs=("data-original", "data-actualsrc", "src"),
    image_host_suffixes=(".zhimg.com",),
    title_strip=(re.compile(r"\s*[-—–|]\s*知乎\s*$"),),
    author_selectors=(".AuthorInfo-name",),
    tag_selectors=(".TopicTag",),
)

_JUEJIN = ArticleSite(
    platform="juejin",
    label="掘金",
    patterns=(re.compile(r"^https?://juejin\.cn/post/\d+", re.IGNORECASE),),
    example="https://juejin.cn/post/7123456789012345678",
    content_selectors=(".markdown-body", "article"),
    # 掘金正文图片实际落在**头条系 CDN 的多个子域**上，不只是 *.juejin.byteimg.com：
    #   p1-juejin.byteimg.com / p9-xtjj-sign.byteimg.com（实测）/ p3-sign.toutiaoimg.com
    # 原白名单只写 `.juejin.byteimg.com` → `p9-xtjj-sign.byteimg.com` 的图片不被转存，
    # 正文里留下带 `x-expires` 签名的原始链接（约 3 天有效期）。签名过期后 CDN 返回
    # **403 + text/html**（2026-10-11 实测），浏览器对跨域 <img> 的非图片响应直接
    # ERR_BLOCKED_BY_ORB 拦截 —— 表现为用户控制台报错 + 裂图。
    # 覆盖整个 byteimg.com / toutiaoimg.com 家族，避免再漏子域。
    image_host_suffixes=(".byteimg.com", ".toutiaoimg.com", ".juejin.cn"),
    title_strip=(re.compile(r"\s*[-—–|]\s*掘金\s*$"),),
    author_selectors=(".author-name", ".username"),
)

_CSDN = ArticleSite(
    platform="csdn",
    label="CSDN",
    patterns=(
        re.compile(r"^https?://(?:\w+\.)?blog\.csdn\.net/[\w.%~-]+/article/details/\d+", re.IGNORECASE),
    ),
    example="https://blog.csdn.net/username/article/details/123456789",
    content_selectors=("#content_views", "article"),
    allow_fallback=False,
    image_host_suffixes=(".csdnimg.cn",),
    title_strip=(re.compile(r"[_\-—–|]\s*CSDN博客.*$"),),
    author_selectors=(".profile-intro-name",),
)

_CNBLOGS = ArticleSite(
    platform="cnblogs",
    label="博客园",
    patterns=(
        re.compile(r"^https?://(?:www\.)?cnblogs\.com/[\w.%~-]+/p/[\w.]+", re.IGNORECASE),
    ),
    example="https://www.cnblogs.com/username/p/12345678.html",
    content_selectors=("#cnblogs_post_body", "#post-detail", "article"),
    title_strip=(re.compile(r"\s*[-—–|]\s*博客园\s*$"),),
)

_JIANSHU = ArticleSite(
    platform="jianshu",
    label="简书",
    patterns=(re.compile(r"^https?://(?:www\.)?jianshu\.com/p/[0-9a-f]+", re.IGNORECASE),),
    example="https://www.jianshu.com/p/abcdef123456",
    content_selectors=("article",),
    image_host_suffixes=(".jianshu.io",),
    title_strip=(re.compile(r"\s*[-—–|]\s*简书\s*$"),),
)

_WEIXIN = ArticleSite(
    platform="weixin",
    label="微信公众号",
    patterns=(re.compile(r"^https?://mp\.weixin\.qq\.com/s(?:[/?]|$)", re.IGNORECASE),),
    example="https://mp.weixin.qq.com/s/abcdef1234567890",
    content_selectors=("#js_content", ".rich_media_content"),
    allow_fallback=False,
    img_src_attrs=("data-src", "src"),
    image_hosts=frozenset({"mmbiz.qpic.cn"}),
    image_host_suffixes=(".mmbiz.qpic.cn",),
    # 公众号文章没有作者 meta，页面上的公众号名（#js_name）就是实际出处
    author_selectors=("#js_name",),
)

_SEGMENTFAULT = ArticleSite(
    platform="segmentfault",
    label="SegmentFault",
    patterns=(re.compile(r"^https?://(?:www\.)?segmentfault\.com/a/\d+", re.IGNORECASE),),
    example="https://segmentfault.com/a/1190000012345678",
    content_selectors=(".article-content", ".article", "article"),
    title_strip=(re.compile(r"\s*[-—–|]\s*(?:SegmentFault\s*)?思否\s*$"),),
)

_OSCHINA = ArticleSite(
    platform="oschina",
    label="开源中国",
    patterns=(
        re.compile(r"^https?://(?:my\.|www\.)?oschina\.net/(?:u/[^/?]+/)?blog/\d+", re.IGNORECASE),
    ),
    example="https://my.oschina.net/u/123456/blog/7890123",
    content_selectors=("#articleContent", "article"),
    title_strip=(re.compile(r"\s*[-—–|]\s*开源中国社区?\s*$"),),
)

_V2EX = ArticleSite(
    platform="v2ex",
    label="V2EX",
    patterns=(re.compile(r"^https?://(?:www\.)?v2ex\.com/t/\d+", re.IGNORECASE),),
    example="https://www.v2ex.com/t/1234567",
    content_selectors=(".topic_content",),
    min_chars=10,
)

_SSPAI = ArticleSite(
    platform="sspai",
    label="少数派",
    patterns=(re.compile(r"^https?://sspai\.com/post/\d+", re.IGNORECASE),),
    example="https://sspai.com/post/115241",
    # 正文是 wangEditor 产物，节点带 Vue scope 属性（data-v-xxxx），按 class 命中不受影响；
    # 外层 .article__main__wrapper 作为兜底（少数文章无 .article__main__content）
    content_selectors=(".article__main__content", ".article__main__wrapper"),
    allow_fallback=False,
    # 图片分布在 cdnfile / cdn-static / rssfile 三个子域，统一按后缀转存
    image_host_suffixes=(".sspai.com",),
    title_strip=(re.compile(r"\s*[-—–|]\s*少数派\s*$"),),
)

_WOSHIPM = ArticleSite(
    platform="woshipm",
    label="人人都是产品经理",
    patterns=(
        re.compile(r"^https?://(?:www\.)?woshipm\.com/[\w-]+/\d+\.html", re.IGNORECASE),
    ),
    example="https://www.woshipm.com/it/5997158.html",
    # ⚠️ 类名是**双横线** article--content（BEM modifier 风格），不是单横线；grap 是排版样式类
    content_selectors=(".article--content",),
    allow_fallback=False,
    # 配图可能落在 image.woshipm.com 与 image.yunyingpai.com（同集团站点）
    image_host_suffixes=(".woshipm.com", ".yunyingpai.com"),
    title_strip=(re.compile(r"\s*[-—–|]\s*人人都是产品经理\s*$"),),
)

_MEDIUM = ArticleSite(
    platform="medium",
    label="Medium",
    patterns=(re.compile(r"^https?://(?:[\w-]+\.)*medium\.com/\S+", re.IGNORECASE),),
    example="https://medium.com/@user/article-slug",
    foreign=True,
    content_selectors=("article", "main"),
    image_hosts=frozenset({"miro.medium.com"}),
    image_host_suffixes=(".miro.medium.com",),
)

_DEVTO = ArticleSite(
    platform="devto",
    label="Dev.to",
    patterns=(re.compile(r"^https?://dev\.to/[\w@.-]+/[\w-]+", re.IGNORECASE),),
    example="https://dev.to/username/article-slug",
    foreign=True,
    content_selectors=(".crayons-article__body", "article"),
    image_hosts=frozenset({"media2.dev.to", "media.dev.to", "dev-to-uploads.s3.amazonaws.com"}),
    image_host_suffixes=(".dev.to",),
)

# 域名（小写）→ 配置 / 专属 fetcher。注意与 URL 校验正则保持一致
_REGISTERED_SITES: dict[str, ArticleSite] = {}


def _register_site(hosts: tuple[str, ...], site: ArticleSite) -> None:
    for host in hosts:
        _REGISTERED_SITES[host.lower()] = site


_register_site(("zhuanlan.zhihu.com", "www.zhihu.com", "zhihu.com"), _ZHIHU)
_register_site(("juejin.cn",), _JUEJIN)
_register_site(("blog.csdn.net",), _CSDN)
_register_site(("cnblogs.com", "www.cnblogs.com"), _CNBLOGS)
_register_site(("jianshu.com", "www.jianshu.com"), _JIANSHU)
_register_site(("mp.weixin.qq.com",), _WEIXIN)
_register_site(("segmentfault.com", "www.segmentfault.com"), _SEGMENTFAULT)
_register_site(("oschina.net", "www.oschina.net", "my.oschina.net"), _OSCHINA)
_register_site(("v2ex.com", "www.v2ex.com"), _V2EX)
_register_site(("sspai.com", "www.sspai.com"), _SSPAI)
_register_site(("woshipm.com", "www.woshipm.com"), _WOSHIPM)
_register_site(("medium.com", "www.medium.com"), _MEDIUM)
_register_site(("dev.to", "www.dev.to"), _DEVTO)

_SPECIAL_FETCHERS: dict[str, Callable[[str, bool], Awaitable[dict]]] = {}


def _register_special(hosts: tuple[str, ...], fetcher) -> None:
    for host in hosts:
        _SPECIAL_FETCHERS[host.lower()] = fetcher


_register_special(
    ("x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.x.com", "mobile.twitter.com"),
    fetch_twitter,
)
_register_special(
    ("reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com", "np.reddit.com"),
    fetch_reddit,
)
_register_special(("news.ycombinator.com",), fetch_hackernews)
_register_special(("stackoverflow.com", "www.stackoverflow.com"), fetch_stackoverflow)

# 后缀路由：子域无法穷举的平台（*.stackexchange.com / *.medium.com / *.blog.csdn.net）
_SUFFIX_ROUTES: list[tuple[str, Union[ArticleSite, Callable[[str, bool], Awaitable[dict]]]]] = [
    (".stackexchange.com", fetch_stackoverflow),
    (".medium.com", _MEDIUM),
    (".blog.csdn.net", _CSDN),
]


def _site_fetcher(site: ArticleSite) -> Callable[[str, bool], Awaitable[dict]]:
    """把 ArticleSite 适配为统一 fetcher 签名 (url, include_files)

    不能用 functools.partial(fetch_web_article, site=site)：fetch_web_article
    的第二个位置参数就是 site，partial 只绑定关键字，调用时 fetcher(url, flag)
    会把 flag 塞给 site 而报 "got multiple values for argument 'site'"。
    """
    async def _fetch(url: str, include_files: bool = False) -> dict:
        return await fetch_web_article(url, site, include_files)

    return _fetch


def resolve_article_fetcher(host: str) -> Optional[Callable[[str, bool], Awaitable[dict]]]:
    """按域名解析文章类平台 fetcher（未命中返回 None）"""
    host = (host or "").lower()
    fetcher = _SPECIAL_FETCHERS.get(host)
    if fetcher is not None:
        return fetcher
    site = _REGISTERED_SITES.get(host)
    if site is not None:
        return _site_fetcher(site)
    for suffix, target in _SUFFIX_ROUTES:
        if host.endswith(suffix):
            if isinstance(target, ArticleSite):
                return _site_fetcher(target)
            return target
    return None
