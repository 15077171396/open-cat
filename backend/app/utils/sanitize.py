"""富文本净化工具 - 防存储型 XSS（admin.py 与各 admin_*_service 复用，避免规则散落多处）"""

import re

import nh3


# content 富文本白名单标签
CONTENT_TAGS = {
    "p", "br", "hr", "span", "div", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "b", "strong", "i", "em", "u", "del", "blockquote",
    "code", "pre", "a", "img", "table", "thead", "tbody", "tr", "th", "td",
    # 表格列宽定义（quill-table-better 编辑器生成）
    "colgroup", "col",
}

# 各标签允许的属性白名单
CONTENT_ATTRS = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height"},
    "span": {"class"},
    "div": {"class"},
    "code": {"class"},
    # colspan/rowspan/width/height 均为纯数值属性，无 XSS 向量；
    # 用于保留富文本编辑器表格的合并单元格与列宽
    "th": {"align", "colspan", "rowspan", "width", "height"},
    "td": {"align", "colspan", "rowspan", "width", "height"},
    "col": {"width", "span"},
}

# 匹配 src/href 属性中不在安全白名单内的 data: URL。白名单仅含位图格式：
# data:image/svg+xml 被排除（内嵌 <script> 会在文档上下文执行，存储型 XSS 向量）；
# Quill 编辑器图片以 data:image/png 等形式嵌入，位图白名单可覆盖。
_NON_IMAGE_DATA_URL = re.compile(
    r'''((?:src|href)\s*=\s*["']\s*)data:(?!image/(?:png|jpe?g|gif|webp))[^"']*(["'])''',
    re.IGNORECASE,
)


def _strip_non_image_data_urls(content: str) -> str:
    """移除白名单外的 data: URL，仅保留位图 data:image/ 开头的（替换后属性值为空串，合法且无副作用）"""
    return _NON_IMAGE_DATA_URL.sub(r"\1\2", content)


def escape_like(keyword: str) -> str:
    """转义 SQL LIKE 通配符 (%, _, \\)，配合 ``.contains(kw, escape="\\\\")`` 使用。

    防止用户输入 ``%`` / ``_`` 被当作通配符匹配全表 (LIKE 注入)。
    """
    return (
        keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )


def sanitize_plain_text(text):
    """净化纯文本字段（如反馈联系方式）：剥离全部 HTML 标签防存储型 XSS（与富文本白名单不同，不保留任何标签）"""
    if not text:
        return text
    return nh3.clean(text, tags=set(), attributes={})


# 日志注入防护：文件 sink 输出 JSON（换行被转义），但 console/stderr sink 是纯文本——
# 用户可控串含 \n 就能凭空伪造日志行，污染取证时间线。
_LOG_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def scrub_log_value(value, max_len: int = 120) -> str:
    """把用户可控值压成可安全写进**单行**日志的形态：控制字符换空格、空白折叠、截断。"""
    text = _LOG_CONTROL_RE.sub(" ", "" if value is None else str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= max_len else f"{text[:max_len]}…"


def sanitize_content(content):
    """净化富文本 content（None/空串原样返回，避免把 None 写成 ""）。
    data: URL 双重过滤：预过滤解析前的原始串 + 后过滤 nh3 实体解码后的输出（后者为权威兜底，捕获实体编码变体）。"""
    if not content:
        return content
    # 预处理：移除非位图 image/ 的 data: URL（防御 data:text/html 等可执行内容）
    content = _strip_non_image_data_urls(content)
    cleaned = nh3.clean(
        content,
        tags=CONTENT_TAGS,
        attributes=CONTENT_ATTRS,
        link_rel="noopener noreferrer",
        # 允许 data: 协议，Quill 编辑器图片以 base64 data URL 形式嵌入；
        # 危险的 data: 子类型由下方后过滤统一拦截
        url_schemes={"http", "https", "mailto", "data"},
    )
    # 后过滤：净化输出中残留的不安全 data: URL（实体解码后的权威检查）
    return _strip_non_image_data_urls(cleaned)
