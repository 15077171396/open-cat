"""通用网页文章 HTML → Markdown 转换与正文抽取：基于标准库 html.parser（零新增依赖），
供文章类平台（知乎/掘金/CSDN/公众号/Medium 等）复用，与 README 类拉取（common.py）相互独立。
- parse_html / find_all：容错 DOM 树（自动闭合隐式结束标签、跳过 script/style/svg），tag#id.class 简单选择器
- extract_article：按平台选择器找正文容器（过短视为登录墙继续尝试），未命中回退 article/main/body，并抽标题
- extract_author / extract_tags：抽取作者与候选标签，供管理端自动回填
- node_to_markdown：覆盖标题/段落/链接/图片(含 data-src)/代码块/列表/引用/GFM 表格/强调，CJK 免加空格
"""

import re
from html.parser import HTMLParser
from typing import Iterator, Optional
from urllib.parse import urljoin

from loguru import logger

from app.services.third_party_fetch.common import (
    SKIP_PREFIXES,
    rewrite_relative_urls,
)

# DOM 树构建


class _Node:
    """轻量 DOM 节点：tag + 属性 + 子节点（子节点为 _Node 或 str 文本）"""

    __slots__ = ("tag", "attrs", "children", "blockish")

    def __init__(self, tag: str, attrs: Optional[dict] = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list = []
        # _is_blockish 的记忆化缓存（None = 尚未计算），见该函数说明
        self.blockish: Optional[bool] = None

    def __repr__(self) -> str:  # 便于调试
        return f"<{self.tag} id={self.attrs.get('id')} class={self.attrs.get('class')}>"


# 自闭合标签（无结束标签，不入栈）
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}

# DOM 嵌套深度上限（见 _TreeBuilder.handle_starttag）：防畸形深链把渲染拖成
# O(n²) 或触发 RecursionError。256 远超真实页面，正常抽取不受影响。
_MAX_TREE_DEPTH = 256

# 整棵子树跳过的标签（脚本样式 / 媒体 / 表单控件等，正文不含可读内容）
_IGNORED_TAGS = {
    "script", "style", "noscript", "template", "svg", "iframe", "canvas",
    "object", "embed", "video", "audio", "map", "select", "option",
    "textarea", "datalist", "output", "progress", "meter", "input",
}

# 解析时保留但渲染时跳过的标签（meta/title 在 head 里，标题抽取要用）
_RENDER_SKIP_TAGS = {
    "title", "meta", "link", "base", "head", "nav", "aside", "footer",
    "form", "dialog", "button", "label", "fieldset", "legend", "datalist",
}

# 隐式结束标签：开始标签 T 出现时，栈顶若为对应集合内的标签则先闭合
# （浏览器 HTML 解析的 implied end tags 子集，修复未闭合的 p/li/td 等）
_AUTO_CLOSE = {
    "p": {"p"},
    "li": {"li"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
    "td": {"td", "th"},
    "th": {"td", "th"},
    "tr": {"tr", "td", "th"},
    "thead": {"td", "th"},
    "tbody": {"td", "th"},
    "tfoot": {"td", "th"},
    "ul": {"p"},
    "ol": {"p"},
    "blockquote": {"p"},
    "pre": {"p"},
    "div": {"p"},
    "section": {"p"},
    "article": {"p"},
    "h1": {"p"}, "h2": {"p"}, "h3": {"p"},
    "h4": {"p"}, "h5": {"p"}, "h6": {"p"},
}


class _TreeBuilder(HTMLParser):
    """容错 HTML 树构建器

    html.parser 不做隐式闭合（未闭合的 <li> 会嵌进上一个 <li>），这里按
    implied end tags 规则补齐；对游离结束标签直接忽略。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root")
        self.stack: list[_Node] = [self.root]
        self._skip_depth = 0  # >0 表示正处于被忽略的子树内

    def handle_starttag(self, tag: str, attrs):
        if self._skip_depth:
            if tag in _IGNORED_TAGS and tag not in _VOID_TAGS:
                self._skip_depth += 1
            return
        if tag in _IGNORED_TAGS:
            if tag not in _VOID_TAGS:
                self._skip_depth = 1
            return

        node_attrs: dict[str, str] = {}
        for key, value in attrs:
            node_attrs.setdefault(key, value or "")

        # 隐式结束：新块级开始前闭合栈顶未显式结束的标签
        auto_close = _AUTO_CLOSE.get(tag)
        if auto_close:
            while len(self.stack) > 1 and self.stack[-1].tag in auto_close:
                self.stack.pop()

        node = _Node(tag, node_attrs)
        self.stack[-1].children.append(node)
        # 深度上限（第三轮增量审查 P1-38）：超过即**不再下钻** —— 节点仍挂在当前
        # 父节点上，后续兄弟内容也落在同一层，等价于「自动闭合」。嵌套深度完全由
        # 对方页面决定，而递归渲染与 `_is_blockish` 都受 Python 递归深度约束：
        # 畸形/恶意深链足以把一次导入拖成 O(n²) 甚至 RecursionError。
        # 256 远超任何真实页面（常见站点 < 40 层），不会影响正常抽取。
        if tag not in _VOID_TAGS and len(self.stack) < _MAX_TREE_DEPTH:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs):
        if self._skip_depth or tag in _IGNORED_TAGS:
            return
        node_attrs: dict[str, str] = {}
        for key, value in attrs:
            node_attrs.setdefault(key, value or "")
        self.stack[-1].children.append(_Node(tag, node_attrs))

    def handle_endtag(self, tag: str):
        if self._skip_depth:
            if tag in _IGNORED_TAGS:
                self._skip_depth -= 1
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return
        # 游离结束标签：忽略

    def handle_data(self, data: str):
        if self._skip_depth or not data:
            return
        self.stack[-1].children.append(data)


def parse_html(html: str) -> _Node:
    """HTML 文本 → 轻量 DOM 树（根节点 tag 为 "#root"）"""
    builder = _TreeBuilder()
    try:
        builder.feed(html)
        builder.close()
    except Exception as exc:  # noqa: BLE001 - 页面 HTML 异常时返回已解析部分
        # 只 debug：对方页面 HTML 畸形是常态，打 warning 会把导入日志淹掉
        logger.debug(f"HTML 解析中断，返回已解析部分：{exc}")
    return builder.root


# 简单选择器

_SELECTOR_RE = re.compile(r"^(?:([a-zA-Z][a-zA-Z0-9-]*)?)?(?:#([\w-]+))?(?:\.([\w.-]+))?$")


def _parse_selector(selector: str) -> tuple[str, str, set[str]]:
    """解析 ``tag#id.class1.class2`` 形式的简单选择器（三者可选）"""
    m = _SELECTOR_RE.match(selector.strip())
    if not m:
        return "", "", set()
    classes = {c for c in (m.group(3) or "").split(".") if c}
    return m.group(1) or "", m.group(2) or "", classes


def _node_matches(node: _Node, tag: str, id_: str, classes: set[str]) -> bool:
    if tag and node.tag != tag:
        return False
    if id_ and node.attrs.get("id") != id_:
        return False
    if classes and not classes <= set((node.attrs.get("class") or "").split()):
        return False
    return True


def find_all(node: _Node, selector: str) -> Iterator[_Node]:
    """深度优先检索所有匹配简单选择器的节点（不含自身）"""
    tag, id_, classes = _parse_selector(selector)
    if not (tag or id_ or classes):
        return
    stack = list(reversed(node.children))
    while stack:
        current = stack.pop()
        if isinstance(current, _Node):
            if _node_matches(current, tag, id_, classes):
                yield current
            stack.extend(reversed(current.children))


def find_first(node: _Node, selector: str) -> Optional[_Node]:
    """检索第一个匹配选择器的节点（广度优先：浅层匹配优先）"""
    tag, id_, classes = _parse_selector(selector)
    if not (tag or id_ or classes):
        return None
    queue = list(node.children)
    while queue:
        current = queue.pop(0)
        if not isinstance(current, _Node):
            continue
        if _node_matches(current, tag, id_, classes):
            return current
        queue.extend(current.children)
    return None


# 正文与标题抽取

# 正文回退选择器（平台选择器全部未命中或内容过短时依次尝试）
FALLBACK_CONTENT_SELECTORS = ("article", "main", "body")

# 正文最少可见字符数（低于该值视为登录墙 / 抽取失败，继续尝试下一候选）
# 取 40：登录墙 / 删文页的可见提示通常不足 30 字符，而短公告类正文可能只有
# 50~60 字符，阈值过高会把正常短文误判为抽取失败。
MIN_CONTENT_CHARS = 40

# 纯空白 / 装饰文本
_WHITESPACE_RE = re.compile(r"\s+")


def _node_plain_text(node: _Node) -> str:
    """节点内纯文本（拼接 + 空白折叠，无 Markdown 转义），用于标题抽取"""
    parts: list[str] = []
    stack = list(node.children)
    while stack:
        child = stack.pop()
        if isinstance(child, str):
            parts.append(_WHITESPACE_RE.sub(" ", child))
        elif child.tag not in _IGNORED_TAGS and child.tag not in _RENDER_SKIP_TAGS:
            stack.extend(child.children)
    return _WHITESPACE_RE.sub(" ", " ".join(reversed(parts))).strip()


def extract_title(root: _Node) -> str:
    """抽取文章标题：og:title → twitter:title → 第一个 h1 → <title>"""
    for node in find_all(root, "meta"):
        key = (node.attrs.get("property") or node.attrs.get("name") or "").lower()
        if key in ("og:title", "twitter:title"):
            content = (node.attrs.get("content") or "").strip()
            if content:
                return _WHITESPACE_RE.sub(" ", content)
    for node in find_all(root, "h1"):
        text = _node_plain_text(node)
        if text:
            return text
    for node in find_all(root, "title"):
        text = _node_plain_text(node)
        if text:
            return text
    return ""


def extract_author(root: _Node, selectors: tuple[str, ...] = ()) -> str:
    """抽取作者 / 用户名：meta 标签 → 平台专属选择器

    优先 meta（author / article:author / og:article:author / twitter:creator，
    各站点命名不一，取首个非空值），未命中再按平台选择器取节点文本。
    统一去掉前导 @ 与「作者：」等前缀，取不到返回空串。
    """
    for node in find_all(root, "meta"):
        key = (node.attrs.get("property") or node.attrs.get("name") or "").lower()
        if key in ("author", "article:author", "og:article:author", "twitter:creator"):
            content = (node.attrs.get("content") or "").strip()
            if content:
                return clean_author(content)
    for selector in selectors:
        node = find_first(root, selector)
        if node is not None:
            text = node_text(node)
            if text:
                return clean_author(text)
    return ""


_AUTHOR_PREFIX_RE = re.compile(r"^\s*(?:作者|撰文|文\s*/?\s*|by|@)\s*[:：]?\s*", re.IGNORECASE)
_AUTHOR_MAX_LEN = 50


def clean_author(text: str) -> str:
    """作者名清洗：去「作者：」/「by」/前导 @ 等前缀，折叠空白并限长"""
    cleaned = _AUTHOR_PREFIX_RE.sub("", _WHITESPACE_RE.sub(" ", text or "").strip())
    cleaned = cleaned.strip(" -–—·|")
    return cleaned[:_AUTHOR_MAX_LEN].strip()


def node_text(node: _Node) -> str:
    """节点内纯文本（拼接 + 空白折叠，无 Markdown 转义）"""
    return _node_plain_text(node)


def extract_tags(
    root: _Node, selectors: tuple[str, ...] = ()
) -> list[str]:
    """抽取候选标签（未清洗，交由 common.clean_tags 去重限量）

    来源：meta keywords（中英文逗号/分号切分）→ meta article:tag / og:article:tag
    / news_keywords（可多个）→ 平台专属选择器的节点文本。
    """
    raw: list[str] = []
    for node in find_all(root, "meta"):
        key = (node.attrs.get("property") or node.attrs.get("name") or "").lower()
        content = (node.attrs.get("content") or "").strip()
        if not content:
            continue
        if key == "keywords":
            raw.extend(re.split(r"[,，;；]", content))
        elif key in ("article:tag", "og:article:tag", "news_keywords"):
            raw.append(content)
    for selector in selectors:
        for node in find_all(root, selector):
            text = _node_plain_text(node)
            if text:
                raw.append(text)
    return raw


def extract_article(
    html: str,
    content_selectors: tuple[str, ...] = (),
    allow_fallback: bool = True,
) -> tuple[str, Optional[_Node]]:
    """从页面 HTML 抽取标题与正文容器（内部解析后委托 extract_article_from_root）

    :param content_selectors: 平台正文选择器（按优先级排列）
    :param allow_fallback: 平台选择器全部未命中时，是否回退 article / main /
        body。默认开启（与 ArticleSite.allow_fallback 默认值一致）；对正文
        容器明确且回退到 body 只会引入骨架噪音的平台（知乎 / CSDN /
        微信公众号等）由平台配置显式关闭
    :return: (标题, 正文节点)；正文未抽到时节点为 None
    """
    return extract_article_from_root(
        parse_html(html), content_selectors, allow_fallback
    )


def extract_article_from_root(
    root: _Node,
    content_selectors: tuple[str, ...] = (),
    allow_fallback: bool = True,
) -> tuple[str, Optional[_Node]]:
    """从已解析的 DOM 抽取标题与正文容器（供需复用 root 抽作者 / 标签的调用方使用）

    选择器按优先级取第一个命中的节点（信任平台配置的顺序，不做逐节点
    内容量比较——登录墙 / 删文场景交给调用方按最终正文长度判定）。
    """
    title = extract_title(root)
    selectors = (*content_selectors, *(FALLBACK_CONTENT_SELECTORS if allow_fallback else ()))
    for selector in selectors:
        node = find_first(root, selector)
        if node is not None:
            return title, node
    return title, None


# HTML → Markdown 渲染

# 行内标签：在段落内渲染，不打断块结构
_INLINE_TAGS = {
    "a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data", "del",
    "dfn", "em", "i", "img", "ins", "kbd", "mark", "q", "s", "samp",
    "small", "span", "strong", "sub", "sup", "time", "u", "var", "wbr",
    "font", "tt", "big", "strike", "ruby", "rt", "rp", "acronym",
}

# 透明块容器：不产生额外结构，直接展开渲染子节点
_TRANSPARENT_BLOCKS = {
    "div", "section", "article", "main", "figure", "figcaption", "address",
    "details", "summary", "center", "dl", "dt", "dd", "td", "th", "caption",
    "#root", "html", "body", "hgroup",
}

# Markdown 语法字符转义（文本节点；代码块 / 行内代码 / URL 不转义）
_MD_ESCAPE_RE = re.compile(r"([\\`*_\[\]<>~])")

# CJK 统一表意文字 + 假名 + 谚文 + 全角符号（拼接时无需补空格）
_CJK_RE = re.compile(r"[\u1100-\u11ff\u2e80-\ua4cf\ua960-\ua97f\uac00-\ud7ff"
                     r"\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef\u3000-\u303f]")

# 懒加载图片属性候选（默认顺序；平台可用 _Ctx.src_attrs 覆盖）
DEFAULT_IMG_SRC_ATTRS = ("src", "data-src", "data-original", "data-actualsrc", "data-lazy-src")


def _escape_md(text: str) -> str:
    return _MD_ESCAPE_RE.sub(r"\\\1", text)


def _smart_join(left: str, right: str) -> str:
    """拼接两段文本：CJK 与拉丁字符之间补空格，CJK 相邻不补"""
    if not left:
        return right
    if not right:
        return left
    a, b = left[-1], right[0]
    if a.isspace() or b.isspace() or _CJK_RE.match(a) or _CJK_RE.match(b):
        return left + right
    return left + " " + right


class _Ctx:
    """渲染上下文：代码块内 / 链接嵌套深度 / 表格单元格 / 图片属性候选"""

    __slots__ = ("link_depth", "in_cell", "img_src_attrs")

    def __init__(
        self,
        link_depth: int = 0,
        in_cell: bool = False,
        img_src_attrs: tuple[str, ...] = DEFAULT_IMG_SRC_ATTRS,
    ):
        self.link_depth = link_depth
        self.in_cell = in_cell
        self.img_src_attrs = img_src_attrs


def _collapse(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def _is_blockish(node: _Node) -> bool:
    """节点是否会产生块级结构（未知标签按是否含块级后代判定）

    ⚠️ 结果**记忆化在节点上**（`_Node.blockish`）：未知标签要递归遍历整棵子树，
    而调用点（`_render_block` / `_render_inline` / `_render_blocks`）会自底向上
    对同一批节点反复求值 —— 无记忆化时总代价退化为 O(n × 深度)，
    畸形深链 HTML 能把一次导入拖到数十秒（第三轮增量审查 P1-38）。
    配合 `_TreeBuilder` 的 `_MAX_TREE_DEPTH` 一起把最坏情况压回线性。
    """
    if node.blockish is not None:
        return node.blockish

    tag = node.tag
    if tag in _INLINE_TAGS:
        node.blockish = False
    elif tag in _TRANSPARENT_BLOCKS or tag in (
        "p", "hr", "pre", "blockquote", "ul", "ol", "li", "table", "tbody",
        "thead", "tfoot", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    ):
        node.blockish = True
    else:
        # 未知标签（自定义元素等）：含块级后代视为块，否则视为行内
        node.blockish = any(
            isinstance(child, _Node)
            and child.tag not in _IGNORED_TAGS
            and _is_blockish(child)
            for child in node.children
        )
    return node.blockish


def _render_inline(children: list, ctx: _Ctx) -> str:
    """把行内子节点序列渲染为单段 Markdown 文本"""
    out = ""
    for child in children:
        if isinstance(child, str):
            text = _collapse(child)
            if text:
                out = _smart_join(out, _escape_md(text))
            continue

        tag = child.tag
        if tag in _IGNORED_TAGS or tag in _RENDER_SKIP_TAGS:
            continue

        if tag == "br":
            # 表格单元格内换行退化为空格；其余渲染为 Markdown 硬换行
            out = out.rstrip() + ("\n" if not ctx.in_cell else " ")
            continue
        if tag == "img":
            out = _smart_join(out, _render_img(child, ctx))
            continue
        if tag == "a":
            out = _smart_join(out, _render_link(child, ctx))
            continue
        if tag == "code":
            code_text = _collapse(_inline_code_text(child))
            if code_text:
                out = _smart_join(out, _wrap_inline_code(code_text))
            continue

        inner = _render_inline(child.children, ctx)
        if not inner.strip():
            continue
        if tag in ("strong", "b"):
            out = _smart_join(out, f"**{inner.strip()}**")
        elif tag in ("em", "i"):
            out = _smart_join(out, f"*{inner.strip()}*")
        elif tag in ("del", "s", "strike"):
            out = _smart_join(out, f"~~{inner.strip()}~~")
        else:
            # span / u / mark / sub 等其余行内标签透明处理
            out = _smart_join(out, inner)
    return out


def _wrap_inline_code(text: str) -> str:
    """行内代码围栏：含反引号时按 CommonMark 规则用双反引号包裹"""
    if "`" in text:
        return f"`` {text} ``"
    return f"`{text}`"


def _inline_code_text(node: _Node) -> str:
    """行内代码纯文本（不转义，保留原样）

    遍历方式与 `_render_pre` 完全一致（反转压栈 → 顺序 pop），避免嵌套
    ``<span>`` 时文本被逆序拼接。
    """
    parts: list[str] = []
    stack = list(reversed(node.children))
    while stack:
        child = stack.pop()
        if isinstance(child, str):
            parts.append(child)
        elif child.tag not in _IGNORED_TAGS:
            stack.extend(reversed(child.children))
    return _WHITESPACE_RE.sub(" ", "".join(parts)).strip()


def _render_img(node: _Node, ctx: _Ctx) -> str:
    """图片 → ![alt](src)；无有效 src（懒加载占位 / data URI）时跳过"""
    src = ""
    for attr in ctx.img_src_attrs:
        value = (node.attrs.get(attr) or "").strip()
        if value and not value.startswith("data:"):
            src = value
            break
    if not src:
        return ""
    alt = _escape_md(_collapse(node.attrs.get("alt") or ""))
    if re.search(r"[\s()]", src):
        src = f"<{src.replace('<', '%3C').replace('>', '%3E')}>"
    return f"![{alt}]({src})"


def _render_link(node: _Node, ctx: _Ctx) -> str:
    """链接 → [text](href)；锚点 / 无 href 退化为纯文本；嵌套链接只保留外层"""
    href = (node.attrs.get("href") or "").strip()
    text = _render_inline(
        node.children, _Ctx(ctx.link_depth + 1, ctx.in_cell, ctx.img_src_attrs)
    ).strip()
    if not href or href.startswith(("#", "javascript:", "data:")) or ctx.link_depth > 0:
        return text
    if re.search(r"[\s()]", href):
        href = f"<{href.replace('<', '%3C').replace('>', '%3E')}>"
    if not text:
        return href
    if text == href:
        return href
    return f"[{text}]({href})"


def _render_pre(node: _Node, ctx: _Ctx) -> str:
    """代码块 → ```fenced code```，语言从 class / lang / data-lang 属性识别

    ⚠️ 遍历顺序必须与 `_inline_code_text` 保持一致：**先把子节点整体反转压栈**，
    再逐个 pop —— 这样 pop 出来的就是文档顺序（先首个子节点，遇到嵌套节点时
    把它的子节点反转压栈，仍是先首个子节点）。此前这里写成了「顶层不反转、
    嵌套时才反转」，再在结尾用 ``"".join(reversed(parts))`` 去补救，结果只有
    **单节点**（整段纯文本）的代码块顺序正确；只要代码里出现语法高亮的
    ``<span>``（掘金 / 高亮库的常态，如 ``<span class="hljs-keyword">const</span>``），
    整段代码就会按节点逆序输出 —— 表现为「代码被截断 / 乱码 / 上下颠倒」。
    """
    lang = _code_lang(node)
    parts: list[str] = []
    stack = list(reversed(node.children))
    while stack:
        child = stack.pop()
        if isinstance(child, str):
            parts.append(child)
        elif child.tag == "br":
            parts.append("\n")
        elif child.tag not in _IGNORED_TAGS:
            lang = lang or _code_lang(child)
            stack.extend(reversed(child.children))
    text = "".join(parts).replace("\u00a0", " ").replace("\u200b", "")
    text = text.strip("\n")
    fence = "```"
    while fence in text:
        fence += "`"
    return f"{fence}{lang}\n{text}\n{fence}"


_CODE_LANG_RE = re.compile(r"(?:language|lang)[-_]([\w+#.-]+)")


def _code_lang(node: _Node) -> str:
    """从 pre/code 的 class / lang / data-lang 中提取代码语言标识"""
    candidates = [node.attrs.get("class") or ""] + [
        node.attrs.get(k) or "" for k in ("lang", "data-lang", "data-info")
    ]
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        m = _CODE_LANG_RE.search(candidate)
        if m:
            return m.group(1).lower()
    return ""


def _render_table(node: _Node, ctx: _Ctx) -> str:
    """表格 → GFM 管道表格（无表头行时补空表头；单元格换行折叠为空格）"""
    rows: list[tuple[list[str], bool]] = []

    def _collect(table: _Node) -> None:
        for child in table.children:
            if not isinstance(child, _Node) or child.tag in _IGNORED_TAGS:
                continue
            if child.tag in ("thead", "tbody", "tfoot"):
                _collect(child)
            elif child.tag == "tr":
                cells = [
                    _render_inline(c.children, _Ctx(0, True, ctx.img_src_attrs)).strip()
                    for c in child.children
                    if isinstance(c, _Node) and c.tag in ("td", "th")
                ]
                is_header = any(
                    isinstance(c, _Node) and c.tag == "th" for c in child.children
                )
                rows.append(([c.replace("|", "\\|").replace("\n", " ") for c in cells], is_header))

    _collect(node)
    if not rows:
        return ""

    header_idx = next((i for i, (_, is_header) in enumerate(rows) if is_header), None)
    if header_idx is None:
        body = rows
        header = [""] * max(len(cells) for cells, _ in rows)
    else:
        body = rows[:header_idx] + rows[header_idx + 1:]
        header = rows[header_idx][0]

    cols = max([len(header)] + [len(cells) for cells, _ in body] + [1])
    header = (header + [""] * cols)[:cols]

    lines = [
        "| " + " | ".join(h or " " for h in header) + " |",
        "| " + " | ".join(["---"] * cols) + " |",
    ]
    for cells, _ in body:
        cells = (cells + [""] * cols)[:cols]
        lines.append("| " + " | ".join(c or " " for c in cells) + " |")
    return "\n".join(lines)


def _render_list(node: _Node, ordered: bool, ctx: _Ctx, indent: int = 0) -> str:
    """列表 → Markdown 列表（支持嵌套；缩进按父级标记宽度对齐）"""
    lines: list[str] = []
    index = 1
    for child in node.children:
        if not isinstance(child, _Node) or child.tag in _IGNORED_TAGS:
            continue
        if child.tag in ("ul", "ol"):
            # 列表直接嵌套列表（不规范 HTML）：作为上一项的子列表渲染
            if lines:
                sub = _render_list(child, child.tag == "ol", ctx, indent + 2)
                lines.extend(sub)
            continue
        if child.tag != "li":
            continue

        marker = f"{index}. " if ordered else "- "
        index += 1

        # 项内容剔除直接嵌套的列表，其余按块渲染（首行接标记，后续行缩进）
        nested = [c for c in child.children if isinstance(c, _Node) and c.tag in ("ul", "ol")]
        rest_children = [c for c in child.children if not (isinstance(c, _Node) and c.tag in ("ul", "ol"))]
        blocks = _render_blocks(rest_children, ctx)
        item_text = "\n".join(blocks).strip()
        pad = " " * (indent + len(marker))
        if item_text:
            item_lines = item_text.split("\n")
            lines.append(" " * indent + marker + item_lines[0])
            lines.extend(pad + line for line in item_lines[1:] if line.strip())
        else:
            lines.append(" " * indent + marker.rstrip())

        for sub_list in nested:
            sub = _render_list(sub_list, sub_list.tag == "ol", ctx, indent + len(marker))
            lines.extend(line for line in sub.split("\n") if line.strip())

    return "\n".join(lines)


def _render_block(node, ctx: _Ctx) -> str:
    """渲染单个块级节点为 Markdown 片段（可含多行）"""
    if isinstance(node, str):
        text = _collapse(node)
        return _escape_md(text) if text else ""

    tag = node.tag
    if tag in _IGNORED_TAGS or tag in _RENDER_SKIP_TAGS:
        return ""

    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        text = _render_inline(node.children, ctx).strip()
        return f"{'#' * int(tag[1])} {text}" if text else ""
    if tag == "hr":
        return "---"
    if tag == "pre":
        return _render_pre(node, ctx)
    if tag == "blockquote":
        inner = "\n\n".join(_render_blocks(node.children, ctx))
        if not inner.strip():
            return ""
        return "\n".join(f"> {line}" if line else ">" for line in inner.split("\n"))
    if tag == "ul":
        return _render_list(node, False, ctx)
    if tag == "ol":
        return _render_list(node, True, ctx)
    if tag == "table":
        return _render_table(node, ctx)
    if tag == "figcaption":
        text = _render_inline(node.children, ctx).strip()
        return f"*{text}*" if text else ""
    if tag == "dt" or tag == "summary":
        text = _render_inline(node.children, ctx).strip()
        return f"**{text}**" if text else ""

    # <a> 包裹块级内容（卡片链接等）：Markdown 无法整块包链接，丢弃链接壳
    if tag == "a" and _is_blockish(node):
        return "\n\n".join(_render_blocks(node.children, ctx))

    # 行内标签落在块上下文（<a> 包图片、<span> 独立段落等）
    if tag in _INLINE_TAGS or not _is_blockish(node):
        return _render_inline([node], ctx).strip()

    # p / div / section / article 等透明或普通块容器：展开子块
    return "\n\n".join(_render_blocks(node.children, ctx))


def _render_blocks(children: list, ctx: _Ctx) -> list[str]:
    """渲染块级子节点序列

    连续的行内内容（文本 + 行内标签）归并为同一段落，遇到块级节点分隔，
    避免 ``<div>文本 <b>加粗</b> 续文本</div>`` 被拆成三个段落。
    """
    blocks: list[str] = []
    inline_buf: list = []

    def _flush() -> None:
        if not inline_buf:
            return
        text = _render_inline(inline_buf, ctx).strip()
        if text:
            blocks.append(text)
        inline_buf.clear()

    for child in children:
        if isinstance(child, _Node) and child.tag in _IGNORED_TAGS:
            continue
        if isinstance(child, _Node) and child.tag not in _INLINE_TAGS and _is_blockish(child):
            _flush()
            block = _render_block(child, ctx)
            if block.strip():
                blocks.append(block.strip("\n"))
        else:
            inline_buf.append(child)
    _flush()
    return blocks


def node_to_markdown(
    node: _Node, img_src_attrs: tuple[str, ...] = DEFAULT_IMG_SRC_ATTRS
) -> str:
    """正文容器节点 → Markdown 文本（块间以空行分隔）"""
    blocks = _render_blocks(node.children, _Ctx(img_src_attrs=img_src_attrs))
    return "\n\n".join(b for b in blocks if b.strip()).strip()


def html_to_markdown(
    html: str, img_src_attrs: tuple[str, ...] = DEFAULT_IMG_SRC_ATTRS
) -> str:
    """HTML 片段 → Markdown（用于 API 返回的正文字段，如 HN 评论 HTML）"""
    return node_to_markdown(parse_html(html), img_src_attrs)


def rewrite_page_relative_urls(markdown: str, base_url: str) -> str:
    """把文章 Markdown 中的相对图片 / 链接基于页面地址补全为绝对地址"""
    if not base_url:
        return markdown

    def _resolve(url: str, is_image: bool) -> Optional[str]:  # noqa: ARG001
        u = url.strip()
        if not u or u.startswith(SKIP_PREFIXES):
            return None
        try:
            return urljoin(base_url, u)
        except ValueError:
            return None

    return rewrite_relative_urls(markdown, _resolve)
