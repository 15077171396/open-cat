"""第三方资料拉取公共服务：各平台 fetcher 共用的正文后处理能力（单一事实来源）。

1. 相对链接/图片重写（resolver 回调适配各平台 URL 结构）；
2. 图片转存 COS（单图失败保留原 URL 降级）；
3. 文件列表按目录分组渲染为 Markdown 附在正文之后；
4. 大小校验：拉取内容上限 2MB；
5. 流式下载护栏 fetch_capped / MAX_RESPONSE_SIZE：所有第三方响应边读边计数、超限即断连。
"""

import asyncio
import ipaddress
import json
import re
from datetime import datetime
from typing import Callable, Optional
from urllib.parse import unquote, urljoin, urlparse

import httpx
from loguru import logger

from app.config import settings
from app.core.exceptions import AppException

from app.utils.upload_validation import is_allowed_image
from app.services.storage import storage, get_file_ext

# Markdown / HTML 链接与图片识别正则

# Markdown 行内链接 / 图片：[text](url "title")、![alt](<url>)
MD_URL_RE = re.compile(r"(!?\[[^\]]*\]\()(<[^>]+>|[^)\s]+)([^)]*\))")

# Markdown 引用式链接定义（行首）：[ref]: url "title"
MD_REF_RE = re.compile(r"^(\s{0,3}\[([^\]]+)\]:\s*)(<[^>]+>|\S+)(.*)$", re.MULTILINE)

# Markdown 引用式图片用法：![alt][ref]（其 ref 定义需按图片重写为 raw 直链）
MD_IMG_REF_RE = re.compile(r"!\[[^\]]*\]\[([^\]]+)\]")

# HTML 标签属性：src="..." / href='...'（负向断言排除 data-src 等）
HTML_ATTR_RE = re.compile(r"""(?<![\w-])(src|href)(\s*=\s*)(["'])(.*?)\3""", re.IGNORECASE)

# 无需重写的地址前缀
SKIP_PREFIXES = ("http://", "https://", "//", "data:", "#", "mailto:", "tel:")

# jsDelivr 主域名（GitHub 仓库文件的国内可达 CDN，图片下载兜底源）
JSDELIVR_HOST = "cdn.jsdelivr.net"

# GitHub 图片资源域名（公开常量）：README 转存与 /images/proxy 代理共用此白名单。
# 含 jsDelivr 域名——jsDelivr 上的 gh 路径就是 GitHub 仓库文件，转存/代理同理。
# （原属 github_readme_service，迁移至此保持单一来源，由原模块 re-export 兼容旧引用）
GITHUB_IMAGE_HOSTS = {
    "github.com",
    "raw.githubusercontent.com",
    "user-images.githubusercontent.com",
    "private-user-images.githubusercontent.com",
    "avatars.githubusercontent.com",
    "objects.githubusercontent.com",
    "camo.githubusercontent.com",
    "media.githubusercontent.com",
    "opengraph.githubassets.com",
    JSDELIVR_HOST,
}

# 各平台拉取共用的图片转存域名白名单。README 经常跨平台引用图片
# （如 Gitee 仓库 README 引用 GitHub badge），因此用统一白名单而非按平台隔离
TRANSFERABLE_IMAGE_HOSTS = GITHUB_IMAGE_HOSTS | {
    "gitee.com",
    "gitcode.com",
    "raw.gitcode.com",
    "api.gitcode.com",
    "modelscope.cn",
    "www.modelscope.cn",
    "huggingface.co",
    "hf-mirror.com",
}

# 拉取内容大小上限（2MB），防止异常超大文件拖垮管理端
MAX_CONTENT_SIZE = 2 * 1024 * 1024

# 单个第三方响应的内存护栏（8MB）——与 MAX_CONTENT_SIZE（产品限制：正文超 2MB 拒绝导入）是两件事；
# 取 8MB 刻意宽松，只把「无上限」变成「有上限」，峰值内存 ≈ 上限 + 一个分块
MAX_RESPONSE_SIZE = 8 * 1024 * 1024

# 单次拉取最多转存多少张图片：图片数量由对方仓库决定，不设上限等于把出站带宽与
# COS 成本交给对方控制；超出部分保留原 URL（与单图失败同一降级策略）
MAX_TRANSFER_IMAGES = 60

# 流式下载的读取块大小
_STREAM_CHUNK_SIZE = 64 * 1024

# 从上游响应头里**必须丢掉**的字段（见 _strip_applied_headers）
_APPLIED_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})


def _strip_applied_headers(raw: dict) -> dict:
    """剔除「httpx 已处理过、再带上就会重复处理」的响应头。

    ⚠️ aiter_bytes() 返回的是已按 Content-Encoding 解压过的字节，而 CappedBody.text
    会用 httpx.Response 重建响应——若原样带 content-encoding: gzip 会被再解压一次，
    DecodingError 抛在 .text 里（fetch_capped 已 return 之后），冒到全局处理器变 500，
    表现为「凡带 gzip/br 的站点全部拉取失败」。content-length 是压缩后长度同样失真；
    transfer-encoding 是逐跳头，重建时带上无意义。
    """
    return {k: v for k, v in raw.items() if k.lower() not in _APPLIED_HEADERS}


class CappedBody:
    """流式下载的结果。over_limit=True 表示主动中止（content 为空，应提示「内容过大」而非网络故障）。

    刻意提供与 httpx.Response 同名的 text / json() / url 成员，调用点替换为
    fetch_capped 后其余用法无需改动。⚠️ headers 是过滤后的（见 _strip_applied_headers）。
    """

    __slots__ = (
        "status_code", "headers", "content", "encoding", "content_type", "over_limit", "url",
    )

    def __init__(
        self,
        status_code: int,
        headers: dict,
        content: bytes = b"",
        encoding: Optional[str] = None,
        content_type: str = "",
        over_limit: bool = False,
        url: str = "",
    ) -> None:
        self.status_code = status_code
        self.headers = headers
        self.content = content
        self.encoding = encoding
        self.content_type = content_type
        self.over_limit = over_limit
        # 最终 URL（跟随重定向之后）—— 正文里的相对链接重写与 source_url 都依赖它
        self.url = url

    @property
    def text(self) -> str:
        """按 httpx 的同一套规则解码（复用 Response.text 的 charset 处理，避免自己造轮子）"""
        return httpx.Response(
            self.status_code, headers=self.headers, content=self.content
        ).text

    def json(self):
        """与 ``httpx.Response.json()`` 同语义（按 content 解析，失败抛 ValueError）"""
        return json.loads(self.content)


def _visited_redirect_targets(requested_url: str, resp) -> list[str]:
    """httpx 自动跟随后实际访问过的每一跳地址（resp.history 各次 3xx + 最终落点），
    逐个复检即可覆盖「白名单主机 302 到内网」这条跳板"""
    hops = [str(r.url) for r in getattr(resp, "history", ()) or ()]
    final = str(resp.url)
    if final != requested_url:
        hops.append(final)
    # 去掉与初始请求相同的那一跳（它已由调用侧的白名单校验过）
    return [h for h in hops if h != requested_url]


async def fetch_capped(
    client: httpx.AsyncClient,
    url: str,
    limit: int,
    *,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    method: str = "GET",
    json_body: Optional[dict] = None,
    error_body_limit: int = 0,
) -> Optional[CappedBody]:
    """流式请求，边读边计数，累计超过 limit 立即中止（不能用 client.get 全量读完再检查——
    500MB 响应会先把进程内存打满）。返回 None 表示网络异常（与超限区分：前者可重试，后者应提示精简）。
    非 200 默认不读 body；error_body_limit >0 时非 200 也读至多该字节数（部分平台用 5xx 表达「文件不存在」，
    需从错误体判断语义）。"""
    try:
        async with client.stream(
            method, url, headers=headers, params=params, json=json_body
        ) as resp:
            # 逐跳复检：白名单主机一个 302 就能把我们带到内网服务或云元数据端点，
            # 不复检的话响应体会被转存进本站 COS 并以本站域名发布（SSRF 跳板）。
            # 返回 None 而非抛错：单源不安全不应让整篇导入失败（换候选/保留原 URL 降级）
            unsafe_hop = next(
                (hop for hop in _visited_redirect_targets(url, resp)
                 if not is_safe_redirect_target(hop)),
                None,
            )
            if unsafe_hop is not None:
                logger.warning(f"重定向落在不安全地址，丢弃该响应并换候选: {url} → {unsafe_hop}")
                return None

            encoding = resp.encoding
            content_type = (
                (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            )
            common = dict(
                status_code=resp.status_code,
                # ⚠️ 必须过滤：body 已被 aiter_bytes 解压，原样带 content-encoding
                # 会让 CappedBody.text 二次解压 → DecodingError → 500（见该函数注释）
                headers=_strip_applied_headers(dict(resp.headers)),
                encoding=encoding,
                content_type=content_type,
                # 跟随重定向后的最终 URL（正文里的相对链接重写依赖它）
                url=str(resp.url),
            )
            if resp.status_code != 200:
                if not error_body_limit:
                    return CappedBody(**common)
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes(_STREAM_CHUNK_SIZE):
                    total += len(chunk)
                    if total > error_body_limit:
                        break
                    chunks.append(chunk)
                return CappedBody(content=b"".join(chunks), **common)
            # Content-Length 预告就超限时连一个字节都不必读
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > limit:
                return CappedBody(over_limit=True, **common)
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes(_STREAM_CHUNK_SIZE):
                total += len(chunk)
                if total > limit:
                    # 主动中止：连接随 with 退出关闭，已读内容丢弃
                    return CappedBody(over_limit=True, **common)
                chunks.append(chunk)
            return CappedBody(content=b"".join(chunks), **common)
    except httpx.HTTPError as exc:
        # 不在这里吞掉原因：调用方多数只需要「网络异常」这一结论，
        # 但排障时要能从日志看出到底是超时还是连接被拒。
        logger.warning(f"流式请求失败（{exc.__class__.__name__}）: {url}")
        return None


async def fetch_capped_following_redirects(
    client: httpx.AsyncClient,
    url: str,
    limit: int,
    *,
    headers: Optional[dict] = None,
    max_redirects: int = 5,
) -> Optional[CappedBody]:
    """流式 GET + 手动逐跳跟随重定向，每一跳校验目标不是内网/保留地址（SSRF 防护）。

    不用 httpx 的 follow_redirects=True：那样对方站点可以把我们带到任意地址
    （内网服务与云元数据端点是经典跳板）。返回语义与 fetch_capped 一致（None = 网络异常）；
    重定向目标不安全时抛 AppException(400)——「地址本身不可用」与「网络抖动可重试」要区分开。
    """
    current = url
    for _ in range(max_redirects + 1):
        body = await fetch_capped(client, current, limit, headers=headers)
        if body is None:
            return None
        if body.status_code not in _REDIRECT_STATUS:
            return body
        location = body.headers.get("location")
        if not location:
            return body
        target = str(httpx.URL(body.url or current).join(location))
        if not is_safe_redirect_target(target):
            logger.warning(f"拒绝跟随重定向到不安全地址: {current} → {target}")
            raise AppException(
                code=400, message="该地址的重定向目标不被允许（内网或非 http(s) 地址）"
            )
        current = target
    # 跳数超限：按失败处理（与 httpx 的 too_many_redirects 同语义）
    logger.warning(f"重定向跳数超过 {max_redirects}: {url}")
    return None


# 重定向状态码 / 跳数上限（与 httpx 默认一致）
_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})

# 内网 / 保留地址段：重定向目标命中即拒绝（SSRF 防护）
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
        "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16",
        "198.18.0.0/15", "::1/128", "fc00::/7", "fe80::/10",
    )
)


def is_safe_redirect_target(url: str) -> bool:
    """重定向目标是否允许：仅 http(s)，且主机不能是字面内网/保留地址。
    只查字面 IP 不做 DNS 解析（挡不住 DNS rebinding）——目标是挡掉明显的 SSRF 探测，并非完备防护。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").strip()
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # 域名：交给 DNS（见上方取舍）
    return not any(ip in net for net in _PRIVATE_NETWORKS)


def capped_reason(body: Optional[CappedBody]) -> str:
    """把流式下载结果归纳成一句可读的失败原因（供降级路径日志使用，需区分网络异常/超限/错误码）"""
    if body is None:
        return "网络异常"
    if body.over_limit:
        return f"响应超过 {MAX_RESPONSE_SIZE // (1024 * 1024)}MB"
    return f"HTTP {body.status_code}"


async def fetch_text_or_error(
    client: httpx.AsyncClient,
    url: str,
    *,
    limit: int = MAX_RESPONSE_SIZE,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    method: str = "GET",
    json_body: Optional[dict] = None,
    label: str = "内容",
    error_body_limit: int = 0,
) -> CappedBody:
    """流式下载 + 统一错误映射，返回 CappedBody（状态码交给调用方判断）。
    只统一与平台无关的两类失败：网络异常 → 502；超限 → 400（提示精简而非重试）。
    非 200 原样返回：各平台对 404/403 的语义差别很大，在这里统一只会抹平有用信息。"""
    body = await fetch_capped(
        client, url, limit,
        headers=headers, params=params, method=method, json_body=json_body,
        error_body_limit=error_body_limit,
    )
    if body is None:
        raise AppException(code=502, message=f"{label}拉取失败，请稍后重试")
    if body.over_limit:
        raise AppException(
            code=400,
            message=f"{label}过大（超过 {limit // (1024 * 1024)}MB），请手动精简后粘贴",
        )
    return body


# 中文版 README 常见命名（拉取时优先探测，命中则用户端直接展示中文，
# 未命中再回退默认 README + 管理端「翻译为中文」按需翻译）
ZH_README_CANDIDATES = (
    "README.zh-CN.md",
    "README_zh-CN.md",
    "README.zh.md",
    "README_zh.md",
    "README.zh-Hans.md",
    "README_CN.md",
)


def is_zh_readme(path: Optional[str]) -> bool:
    """仓库内文件路径是否为中文版 README"""
    lower = (path or "").lower()
    return "zh" in lower or "cn" in lower or "chinese" in lower


def detect_content_language(markdown: str) -> str:
    """按正文内容粗略判断语言（文件名探测未命中时的兜底）

    中文字符数量不低于拉丁字母数量的 1/3 视为中文，否则视为英文（用于管理端
    「翻译为中文」提示，仅影响提示不影响内容）。无中文直接判英文；短正文只要
    以中文为主同样判中文，避免把中文短帖（X 帖子 / V2EX 主题等）误报为
    「暂无中文版内容」。
    """
    zh = len(re.findall(r"[\u4e00-\u9fff]", markdown))
    if zh == 0:
        return "en"
    latin = len(re.findall(r"[A-Za-z]", markdown))
    return "zh" if zh * 3 >= latin else "en"


# 元信息补全（标题 / 简介 / 标签 / 出处）

# 平台标识 → 展示名。与 articles.ArticleSite.label 保持一致；
# 仓库类平台直接取品牌名。供管理端展示「出处：掘金 · 张三」等提示。
PLATFORM_LABELS: dict[str, str] = {
    "github": "GitHub",
    "gitee": "Gitee",
    "gitcode": "GitCode",
    "modelscope": "ModelScope",
    "huggingface": "HuggingFace",
    "twitter": "X (Twitter)",
    "reddit": "Reddit",
    "hackernews": "Hacker News",
    "stackoverflow": "Stack Overflow",
    "medium": "Medium",
    "devto": "Dev.to",
    "zhihu": "知乎",
    "juejin": "掘金",
    "csdn": "CSDN",
    "cnblogs": "博客园",
    "jianshu": "简书",
    "weixin": "微信公众号",
    "segmentfault": "SegmentFault",
    "oschina": "开源中国",
    "v2ex": "V2EX",
    "sspai": "少数派",
    "woshipm": "人人都是产品经理",
}

# 自动简介长度上限（超出截断加省略号）
SUMMARY_MAX_CHARS = 150
# 自动标签数量 / 单标签长度上限
TAG_MAX_COUNT = 8
TAG_MAX_LEN = 20

# Markdown 语法清洗（生成简介用）：行内链接/图片保留文字，去掉标记符号
_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MD_REF_LINK_RE = re.compile(r"!?\[([^\]]*)\]\[[^\]]*\]")
_MD_MARKS_RE = re.compile(r"[*_`>#~]+")
# 生成简介时跳过的行前缀（标题 / 引用 / 表格 / 列表 / 代码围栏 / 图片 / 分隔线）
_SUMMARY_SKIP_PREFIXES = ("#", ">", "|", "-", "*", "!", "```", "~~~", "---")


def markdown_to_plain(markdown: str) -> str:
    """Markdown → 纯文本（去链接与图片语法、去强调标记、折叠空白）

    仅用于生成简介，不做完整 Markdown 解析——简介只需可读的一句话。
    """
    text = _MD_LINK_RE.sub(r"\1", markdown)
    text = _MD_REF_LINK_RE.sub(r"\1", text)
    text = _MD_MARKS_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def build_summary(markdown: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    """从正文自动提取一句话简介（取首个实质段落，超长截断加省略号）

    跳过标题 / 引用 / 表格 / 列表 / 代码块等非叙述行（README 首屏常是徽章与
    目录），取第一段有实质内容的文字；正文过短或全是结构行时返回空串，
    由调用方决定是否回填。
    """
    for raw_line in markdown.split("\n"):
        line = raw_line.strip()
        if not line or line.startswith(_SUMMARY_SKIP_PREFIXES):
            continue
        plain = markdown_to_plain(line)
        if len(plain) < 10:
            continue
        return plain[:limit].rstrip() + "…" if len(plain) > limit else plain
    return ""


def clean_tags(raw, limit: int = TAG_MAX_COUNT) -> list[str]:
    """清洗标签列表：去空白与包裹符号、去超长、按不区分大小写去重、限量

    接受任意可迭代输入（meta keywords 切分结果、页面标签文本等），
    保持原始顺序，避免把 SEO 堆砌的长句当成标签。
    """
    result: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        tag = re.sub(r"\s+", " ", str(item or "")).strip(" #,，、;；|")
        if not tag or len(tag) > TAG_MAX_LEN:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
        if len(result) >= limit:
            break
    return result


def build_source_line(platform_label: str, author: str, url: str) -> str:
    """构造文章正文末尾的「来源」引用行（出处归因，与既有各平台格式统一）

    形如 ``> 来源：[掘金 · 张三](https://juejin.cn/post/123)``；无作者时
    省略作者段。各平台 fetcher 统一调用，保证导入内容出处格式一致。
    """
    label = (platform_label or "").strip() or "原文"
    who = f" · {author.strip()}" if (author or "").strip() else ""
    link = (url or "").strip()
    if not link:
        return f"> 来源：{label}{who}"
    return f"> 来源：[{label}{who}]({link})"

# 可转存到 COS 的图片 MIME 类型（与 /uploads/image 编辑器上传白名单一致；
# SVG 可含脚本不在白名单内，保留原 URL 走代理）
IMAGE_MIME_ALLOWED = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# README 图片并发转存上限，避免大量图片同时下载打满带宽
TRANSFER_CONCURRENCY = 5


def check_content_size(content: str) -> None:
    """拉取内容大小校验（超过 2MB 上限直接抛出，避免拖垮管理端）"""
    if len(content.encode("utf-8", errors="ignore")) > MAX_CONTENT_SIZE:
        raise AppException(
            code=400, message="拉取内容超过 2MB 上限，请手动精简后粘贴"
        )


# 相对 URL 解析回调：接收 (原始相对地址, 是否为图片)，返回绝对地址或 None（不重写）
RelativeUrlResolver = Callable[[str, bool], Optional[str]]


def is_image_prefix(prefix: str) -> bool:
    """Markdown 行内语法前缀是否为图片

    覆盖普通 ![alt](url) 与嵌套 [![alt](url)](url)（GitHub 徽章
    常用形式：内层图片的前缀是 "[![" 而非 "!["）。
    """
    return prefix.startswith("!") or prefix.startswith("[![")


def unwrap_angle(url: str) -> str:
    """去掉 Markdown 尖括号包裹形式 <url> 的尖括号"""
    u = url.strip()
    if u.startswith("<") and u.endswith(">"):
        return u[1:-1].strip()
    return u


# 围栏代码块起始行：最多 3 个前导空格 + ``` 或 ~~~（CommonMark 允许 info string）
_FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")


def split_markdown_fences(markdown: str) -> list[tuple[bool, str]]:
    """按围栏代码块把 Markdown 切成 ``[(是否代码块, 片段)]``

    **为什么需要**：URL 重写 / 图片收集是**全文正则**，代码块里出现的
    ``src="url"``、``[text](url)`` 属于**代码内容**而非链接，改写会把示例代码
    改坏。实测掘金文章里的 Vue 示例 ``:src="url"`` 被改成
    ``:src="https://juejin.cn/post/url"``（正文凭空多出 23 个字符，读者看到的
    示例代码是错的）。

    切分只按行进行，片段之间用 ``"\\n"`` 拼回可**逐字节还原**原文（空行不丢）。
    未闭合的围栏视为「剩余全是代码」——宁可少改，不可改坏代码。
    """
    segments: list[tuple[bool, str]] = []
    buf: list[str] = []
    fence = ""
    for line in markdown.split("\n"):
        if not fence:
            m = _FENCE_OPEN_RE.match(line)
            if m:
                if buf:
                    segments.append((False, "\n".join(buf)))
                    buf = []
                fence = m.group(1)
            buf.append(line)
            continue
        buf.append(line)
        stripped = line.strip()
        # 结束围栏：同种字符、长度不短于开启围栏、其后无其它内容
        if stripped and len(stripped) >= len(fence) and set(stripped) == {fence[0]}:
            segments.append((True, "\n".join(buf)))
            buf = []
            fence = ""
    if buf:
        segments.append((bool(fence), "\n".join(buf)))
    return segments


def transform_outside_fences(markdown: str, transform: Callable[[str], str]) -> str:
    """只对**代码块之外**的片段执行 transform，代码块原样保留"""
    return "\n".join(
        chunk if is_code else transform(chunk)
        for is_code, chunk in split_markdown_fences(markdown)
    )


def make_raw_file_resolver(
    file_raw_url: str,
    path_in_repo: str = "",
    raw_page_pairs: tuple[tuple[str, str], ...] = (),
) -> RelativeUrlResolver:
    """构造「raw 直链式」平台的相对 URL 解析器（GitHub / Gitee / GitCode / HF）

    :param file_raw_url: README 的 raw 直链（或等价可访问地址，如 jsDelivr URL），
        相对路径基于其所在目录解析；以 / 开头的路径基于仓库根解析
    :param path_in_repo: README 在仓库内的路径（用于从 file_raw_url 推导仓库根）
    :param raw_page_pairs: 链接解析后 (raw 前缀, 页面前缀) 替换对——README 内
        指向其他 Markdown 的 raw 链接转为仓库页面（.md 可在线阅读），
        如 GitHub 的 raw.githubusercontent.com/o/r/ → github.com/o/r/blob/
    """
    if not file_raw_url:
        # 无 raw 直链（拉取源未返回 download_url 等）时无从解析相对路径
        return lambda url, is_image: None

    dir_base = file_raw_url.rsplit("/", 1)[0]
    repo_root = None
    if path_in_repo:
        du = unquote(file_raw_url)
        if du.endswith("/" + path_in_repo):
            repo_root = du[: len(du) - len(path_in_repo) - 1]

    def _resolve(url: str, is_image: bool) -> Optional[str]:
        u = unwrap_angle(url)
        if not u or u.startswith(SKIP_PREFIXES):
            return None
        if u.startswith("/"):
            if not repo_root:
                return None
            absolute = urljoin(repo_root + "/", u.lstrip("/"))
        else:
            absolute = urljoin(dir_base + "/", u)
        if not is_image:
            for raw_prefix, page_prefix in raw_page_pairs:
                if absolute.startswith(raw_prefix):
                    absolute = page_prefix + absolute[len(raw_prefix):]
                    break
        return absolute

    return _resolve


def rewrite_relative_urls(markdown: str, resolver: RelativeUrlResolver) -> str:
    """把 README 中的相对图片 / 链接重写为绝对地址（resolver 决定解析规则）

    覆盖三种语法上下文：Markdown 行内 ![](...) / [](...)、引用式
    ![alt][ref] 对应的 [ref]: url 定义、HTML src / href 属性。
    ⚠️ **围栏代码块内不改写**（见 `split_markdown_fences`）：代码里的
    ``src="url"`` 是示例代码，不是页面资源地址。
    """

    def _md_repl(m: re.Match) -> str:
        resolved = resolver(m.group(2), is_image_prefix(m.group(1)))
        return m.group(0) if resolved is None else f"{m.group(1)}{resolved}{m.group(3)}"

    # 被图片引用的 ref 定义重写为直链，其余重写为页面地址
    image_ref_ids = set(MD_IMG_REF_RE.findall(markdown))

    def _ref_repl(m: re.Match) -> str:
        resolved = resolver(m.group(3), m.group(2) in image_ref_ids)
        return m.group(0) if resolved is None else f"{m.group(1)}{resolved}{m.group(4)}"

    def _html_repl(m: re.Match) -> str:
        resolved = resolver(m.group(4), m.group(1).lower() == "src")
        return (
            m.group(0)
            if resolved is None
            else f"{m.group(1)}{m.group(2)}{m.group(3)}{resolved}{m.group(3)}"
        )

    def _transform(chunk: str) -> str:
        result = MD_URL_RE.sub(_md_repl, chunk)
        result = MD_REF_RE.sub(_ref_repl, result)
        return HTML_ATTR_RE.sub(_html_repl, result)

    return transform_outside_fences(markdown, _transform)


# 图片转存 COS


def _match_host(hostname: str, hosts: set[str], host_suffixes: tuple[str, ...]) -> bool:
    """域名是否命中白名单：精确域名或点分后缀匹配（".juejin.byteimg.com" 前导点仅书写约定）。
    ⚠️ 不要加「以 - 后缀结尾」分支：连字符匹配会放行 attacker-byteimg.com 这类可注册仿冒域名。"""
    if hostname in hosts:
        return True
    for suffix in host_suffixes:
        base = suffix.lstrip(".")
        if not base:
            continue
        if hostname == base or hostname.endswith("." + base):
            return True
    return False


def is_image_url(
    url: str, hosts: set[str], host_suffixes: tuple[str, ...] = ()
) -> bool:
    """是否为指定域名白名单内的 http(s) 图片地址"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and _match_host(
        (parsed.hostname or ""), hosts, host_suffixes
    )


def collect_image_urls(
    markdown: str, hosts: set[str], host_suffixes: tuple[str, ...] = ()
) -> list[str]:
    """收集 README 中所有白名单域名图片 URL（去重且保持正文出现顺序——转存上限截断时
    才能保证每次都转存最靠前的几张；覆盖行内/引用式/HTML img 三种形态）
    ⚠️ 跳过围栏代码块（代码里的 ``![x](y)`` 是示例，不是正文配图）"""
    seen: set[str] = set()
    ordered: list[str] = []

    def _add(candidate: str) -> None:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)

    for is_code, chunk in split_markdown_fences(markdown):
        if is_code:
            continue
        # 行内图片 ![alt](url) 与嵌套 [![alt](url)](url)
        for m in MD_URL_RE.finditer(chunk):
            if is_image_prefix(m.group(1)):
                _add(unwrap_angle(m.group(2)))

        # 引用式图片：仅收集被 ![...][ref] 引用的定义
        image_ref_ids = set(MD_IMG_REF_RE.findall(chunk))
        for m in MD_REF_RE.finditer(chunk):
            if m.group(2) in image_ref_ids:
                _add(unwrap_angle(m.group(3)))

        # HTML img src
        for m in HTML_ATTR_RE.finditer(chunk):
            if m.group(1).lower() == "src":
                _add(m.group(4))

    return [u for u in ordered if is_image_url(u, hosts, host_suffixes)]


# 图片下载请求头：不少 CDN 对「无 UA / 非浏览器 UA」的请求直接 403，
# 而 `<img>` 在用户浏览器里是带完整 UA 的 —— 服务端转存不带同款头，就会出现
# 「用户能看到原图、我们转存失败」的诡异现象（失败即保留会过期的签名 URL）。
_IMAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}

# 单张图片最多尝试次数：图片转存是「尽力而为」的旁路，重试 2 次已能吃掉绝大多数
# 瞬时抖动（CDN 偶发 5xx / 连接重置），再多只是拖长导入耗时
_IMAGE_FETCH_ATTEMPTS = 2
# 重试退避基数（秒）：第 n 次重试前等 0.3 × n
_IMAGE_RETRY_BACKOFF = 0.3


async def _fetch_image_body(
    client: httpx.AsyncClient, url: str, referer: str = ""
) -> Optional["CappedBody"]:
    """下载单张图片：带浏览器 UA / 可选 Referer，网络异常或 5xx 时短暂重试

    403 / 404 不重试 —— 那是「对方明确拒绝或不存在」，重试只会白等。
    """
    headers = dict(_IMAGE_HEADERS)
    if referer:
        headers["Referer"] = referer
    body: Optional[CappedBody] = None
    for attempt in range(_IMAGE_FETCH_ATTEMPTS):
        body = await fetch_capped(
            client, url, settings.COS_IMAGE_MAX_SIZE, headers=headers
        )
        if body is not None and (body.over_limit or body.status_code < 500):
            return body
        if attempt + 1 < _IMAGE_FETCH_ATTEMPTS:
            await asyncio.sleep(_IMAGE_RETRY_BACKOFF * (attempt + 1))
    return body


async def download_and_upload_image(
    client: httpx.AsyncClient,
    url: str,
    extra_candidates: Optional[list[str]] = None,
    proxy_client: Optional[httpx.AsyncClient] = None,
    proxy_hosts: Optional[set[str]] = None,
    referer: str = "",
) -> Optional[str]:
    """下载单张白名单域名图片并上传 COS，返回 COS 地址。
    依次尝试多源候选（原始 URL → 平台额外候选）；直连全败且域名在 proxy_hosts 内时经出站代理兜底
    （COS 上传始终直连）。全部失败返回 None，由调用方保留原 URL 降级。
    :param referer: 图片来源页面地址，部分 CDN 校验 Referer 才放行（如掘金 / 头条系）
    """
    attempts: list[tuple[httpx.AsyncClient, str]] = [(client, url)]
    for target in extra_candidates or []:
        if target != url:
            attempts.append((client, target))
    if (
        proxy_client is not None
        and proxy_hosts is not None
        and (urlparse(url).hostname or "") in proxy_hosts
    ):
        attempts.append((proxy_client, url))

    for target_client, target in attempts:
        # 流式下载 + 超限即中止（台账 P2-6）：原实现先 `client.get` 全量读进内存
        # 再比大小，一个超大图片 URL 就能把进程内存打满
        body = await _fetch_image_body(target_client, target, referer)
        if body is None:
            continue
        if body.over_limit:
            # 超限说明「换源结果相同」，直接放弃（与原有语义一致）
            return None
        if body.status_code != 200:
            continue

        content_type = body.content_type
        # MIME 不匹配说明文件本身不是可转存图片（换源结果相同），直接放弃
        if content_type not in IMAGE_MIME_ALLOWED:
            return None
        if not is_allowed_image(content_type, body.content):
            return None

        ext = get_file_ext(None, content_type)
        return await storage.upload_bytes(
            body.content,
            folder="images",
            ext=ext or ".jpg",
            content_type=content_type,
        )
    return None


def apply_cos_urls(markdown: str, mapping: dict[str, str]) -> str:
    """把 markdown 中已转存图片的 URL 替换为 COS 地址

    图片 URL 可能同时出现在链接位置（如 [![img](pic.png)](pic.png)
    点击查看原图、<a href><img></a>），映射命中即替换，保证点击
    行为与展示一致。覆盖行内 / 引用式 / HTML 属性三种上下文。
    ⚠️ 跳过围栏代码块（与 `collect_image_urls` 保持同一范围）。
    """

    def _md_repl(m: re.Match) -> str:
        key = unwrap_angle(m.group(2))
        return f"{m.group(1)}{mapping[key]}{m.group(3)}" if key in mapping else m.group(0)

    def _ref_repl(m: re.Match) -> str:
        key = unwrap_angle(m.group(3))
        return f"{m.group(1)}{mapping[key]}{m.group(4)}" if key in mapping else m.group(0)

    def _html_repl(m: re.Match) -> str:
        key = m.group(4)
        if key in mapping:
            return f"{m.group(1)}{m.group(2)}{m.group(3)}{mapping[key]}{m.group(3)}"
        return m.group(0)

    def _transform(chunk: str) -> str:
        result = MD_URL_RE.sub(_md_repl, chunk)
        result = MD_REF_RE.sub(_ref_repl, result)
        return HTML_ATTR_RE.sub(_html_repl, result)

    return transform_outside_fences(markdown, _transform)


async def transfer_images_to_cos(
    client: httpx.AsyncClient,
    markdown: str,
    hosts: Optional[set[str]] = None,
    extra_candidates_fn: Optional[Callable[[str], list[str]]] = None,
    proxy_client: Optional[httpx.AsyncClient] = None,
    proxy_hosts: Optional[set[str]] = None,
    host_suffixes: tuple[str, ...] = (),
    referer: str = "",
    stats: Optional[dict] = None,
) -> tuple[str, int]:
    """把 README 中的白名单域名图片转存到 COS，返回 (替换后的 markdown, 成功数量)。
    COS 未配置时整体跳过；单张失败保留原 URL 不阻塞；单次上限 MAX_TRANSFER_IMAGES，超出保留原 URL。
    :param extra_candidates_fn: 为单张图片生成额外下载候选（如 jsDelivr / 镜像变体）
    :param host_suffixes: 后缀匹配的额外域名（CDN 子域带编号前缀无法逐一枚举的平台）
    :param referer: 图片来源页面地址（部分 CDN 校验 Referer 才放行）
    :param stats: 可选出参，回填 {"total": 待转存数, "transferred": 成功数}。
        调用方据此把「N/M 张转存成功」告知管理员 —— 失败而保留的签名 URL
        通常 1~3 天即失效，静默失败会变成几天后的「正文图片突然全是裂图」。"""
    if not storage.is_configured():
        logger.warning("COS 未配置，第三方资料图片保留外链不转存")
        if stats is not None:
            stats["total"] = 0
            stats["transferred"] = 0
        return markdown, 0

    hosts = hosts or TRANSFERABLE_IMAGE_HOSTS
    urls = collect_image_urls(markdown, hosts, host_suffixes)
    if not urls:
        if stats is not None:
            stats["total"] = 0
            stats["transferred"] = 0
        return markdown, 0

    # 数量上限：截断保留正文最靠前的 N 张（collect_image_urls 保证顺序稳定）
    if len(urls) > MAX_TRANSFER_IMAGES:
        logger.warning(
            f"第三方资料图片共 {len(urls)} 张，超过单次转存上限 "
            f"{MAX_TRANSFER_IMAGES} 张，仅转存正文前 {MAX_TRANSFER_IMAGES} 张，"
            "其余保留原 URL"
        )
        urls = urls[:MAX_TRANSFER_IMAGES]

    if stats is not None:
        stats["total"] = len(urls)
        stats["transferred"] = 0

    sem = asyncio.Semaphore(TRANSFER_CONCURRENCY)
    mapping: dict[str, str] = {}

    async def _transfer(url: str) -> None:
        async with sem:
            try:
                cos_url = await download_and_upload_image(
                    client,
                    url,
                    extra_candidates=extra_candidates_fn(url) if extra_candidates_fn else None,
                    proxy_client=proxy_client,
                    proxy_hosts=proxy_hosts,
                    referer=referer,
                )
                if cos_url:
                    mapping[url] = cos_url
                    logger.info(f"第三方资料图片已转存 COS: {url} -> {cos_url}")
                else:
                    logger.warning(f"第三方资料图片转存失败，保留原 URL: {url}")
            except Exception as exc:  # noqa: BLE001 - 单图失败降级保留原 URL
                logger.warning(f"第三方资料图片转存失败，保留原 URL: {url} ({exc})")

    await asyncio.gather(*(_transfer(u) for u in urls))

    if stats is not None:
        stats["transferred"] = len(mapping)
    if len(mapping) < len(urls):
        logger.warning(
            f"第三方资料图片部分转存失败：成功 {len(mapping)}/{len(urls)}，"
            "失败项保留原外链（签名链接通常数天后失效，建议重新导入或手动补图）"
        )
    if not mapping:
        return markdown, 0
    return apply_cos_urls(markdown, mapping), len(mapping)


# 文件列表渲染


def format_file_size(size: Optional[int]) -> str:
    """字节数格式化为人类可读大小（无大小信息返回 '-'）"""
    if size is None or size < 0:
        return "-"
    if size < 1024:
        return f"{size} B"
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1024  # type: ignore[operator]
        if size < 1024:
            return f"{size:.1f} {unit}"
    return f"{size:.1f} PB"


def format_updated_at(value) -> str:
    """文件更新时间格式化为 YYYY-MM-DD（无值 / 无法解析返回 '-'）

    兼容两种输入：unix 秒级时间戳（ModelScope CommittedDate）与
    ISO 8601 字符串（HuggingFace lastCommit.date）。仅保留日期不保留
    时分秒——文件列表以概览为主，避免表格过宽挤压文件名列。
    """
    if value is None:
        return "-"
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return "-"
    text = str(value)
    # ISO 8601：截取日期部分（2025-01-20T12:28:46.000Z → 2025-01-20）
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    return m.group(1) if m else "-"


def render_files_section(
    entries: list[dict], max_rows: int = 200
) -> str:
    """把平台文件清单按顶层目录分组渲染为 Markdown 段落（附在正文之后）。

    每组一个目录标题 + 组内文件表格，文件列显示组内相对路径；根目录文件单独成组；
    目录条目不占表格行（空目录不渲染）。
    :param entries: [{"path", "type", "size", "url", "updated_at"}]，type 为 "file"/"blob" 或其他（目录）
    :param max_rows: 最多渲染条数，超出截断并提示总数（大仓库数千分片会撑爆编辑器）
    """
    # 仅文件条目占表格行（目录条目由分组标题体现）
    files = [e for e in entries if e.get("path") and e.get("type") in ("file", "blob")]
    total = len(files)
    if total == 0:
        return ""

    # 按顶层目录分组（"" 为根目录组），组序与组内顺序保持平台返回的相对顺序
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for e in files:
        path = str(e.get("path", ""))
        top = path.split("/", 1)[0] if "/" in path else ""
        if top not in groups:
            groups[top] = []
            order.append(top)
        groups[top].append(e)

    lines = ["## 文件列表", ""]
    if total > max_rows:
        lines.append(f"共 {total} 个文件，仅展示前 {max_rows} 个：")
    else:
        lines.append(f"共 {total} 个文件：")

    rendered = 0
    for top in order:
        if rendered >= max_rows:
            break
        group = groups[top]
        header = f"**{top}/**" if top else "**（根目录）**"
        lines += [
            "",
            f"{header}（{len(group)} 个文件）",
            "",
            "| 文件 | 大小 | 更新时间 | 下载 |",
            "| --- | --- | --- | --- |",
        ]
        for e in group:
            if rendered >= max_rows:
                break
            path = str(e.get("path", ""))
            # 组内相对路径（去掉顶层目录前缀，更深层级从路径仍可辨识）
            rel = (path[len(top) + 1:] if top else path).replace("|", "\\|")
            size = format_file_size(e.get("size"))
            updated = format_updated_at(e.get("updated_at"))
            url = e.get("url")
            download = f"[下载]({url})" if url else "-"
            lines.append(f"| {rel} | {size} | {updated} | {download} |")
            rendered += 1

    return "\n".join(lines)
