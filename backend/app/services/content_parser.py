"""教程正文章节切分。

与前端 ``frontend/src/utils/content.ts`` 的 ``parseSections`` 保持完全一致的切分规则，
使后端按章节返回正文时 section_id 与前端目录树对齐，支持章节懒加载。
"""

import re

# 与前端 slugify 对齐：保留 ASCII 单词字符与中文，其余替换为 "-"
_SLUGIFY_RE = re.compile(r"[^\w\u4e00-\u9fa5]+", re.ASCII)
_SLUGIFY_TRIM_RE = re.compile(r"^-|-$")

# Markdown 标题行：^(#{1,6})\s+(.+)$
_MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")

# HTML 标题标签：<(h[1-6])([^>]*)>(.*?)</\1>，不跨行（与 JS 默认一致，无 s flag）
_HTML_HEADING_RE = re.compile(r"<(h[1-6])([^>]*)>(.*?)</\1>", re.IGNORECASE)

# isHTMLContent：以常见块级标签开头的视为 HTML
_HTML_DETECT_RE = re.compile(
    r"^\s*<(p|h[1-6]|ul|ol|div|blockquote|pre|table|img|figure|hr)\b",
    re.IGNORECASE,
)


def is_html_content(content: str) -> bool:
    """与前端 ``isHTMLContent`` 对齐"""
    if not content:
        return False
    return bool(_HTML_DETECT_RE.match(content))


# 与前端 cleanHeadingText 对齐：常见 HTML 实体 → 字符
_HTML_ENTITY_MAP = {
    "&nbsp;": " ",
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
}
# Unicode 不间断空格（\u00a0）与全角空格统一折叠
_NBSP_RE = re.compile("[ \u00a0\u3000]+")
_MULTI_SPACE_RE = re.compile(r"\s+")


def clean_heading_text(text: str) -> str:
    """与前端 ``cleanHeadingText`` 对齐：解码常见 HTML 实体并压缩空白。

    保证后端抽取的目录标题/id 与前端 parseSections 一致
    （如 ``Midjourney&nbsp;完全指南`` → ``Midjourney 完全指南``）。
    """
    if not text:
        return ""
    for entity, char in _HTML_ENTITY_MAP.items():
        text = text.replace(entity, char)
    return _MULTI_SPACE_RE.sub(" ", _NBSP_RE.sub(" ", text)).strip()


def slugify(text: str) -> str:
    """与前端 ``slugify`` 对齐：小写化，非单词/中文字符替换为 -，去首尾 -"""
    result = _SLUGIFY_RE.sub("-", text.lower())
    result = _SLUGIFY_TRIM_RE.sub("", result)
    return result or "heading"


def _unique_id(base: str, used: set[str]) -> str:
    """与前端 ``uniqueId`` 对齐：同名标题追加 -1, -2 ... 去重"""
    sid = base
    i = 1
    while sid in used:
        sid = f"{base}-{i}"
        i += 1
    used.add(sid)
    return sid


def _parse_markdown_sections(content: str) -> list[dict]:
    """与前端 ``parseMarkdownSections`` 对齐：按 # 标题切分"""
    lines = content.split("\n")
    sections: list[dict] = []
    current: dict | None = None
    buffer: list[str] = []
    used_ids: set[str] = set()

    def flush() -> None:
        if not current:
            return
        current["content"] = "\n".join(buffer)
        sections.append(current)

    for line in lines:
        m = _MARKDOWN_HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            sid = _unique_id(slugify(title), used_ids)
            current = {"id": sid, "title": title, "level": level, "content": ""}
            buffer = [line]
        else:
            buffer.append(line)
    flush()
    return sections


def _parse_html_sections(content: str) -> list[dict]:
    """与前端 ``parseHTMLSections`` 对齐：按 <h1-6> 标签切分"""
    sections: list[dict] = []
    current: dict | None = None
    last_index = 0
    used_ids: set[str] = set()

    for match in _HTML_HEADING_RE.finditer(content):
        if current:
            current["content"] = content[last_index:match.start()]
            sections.append(current)
        level = int(match.group(1)[1])
        title = clean_heading_text(re.sub(r"<[^>]+>", "", match.group(3)))
        sid = _unique_id(slugify(title), used_ids)
        current = {"id": sid, "title": title, "level": level, "content": ""}
        last_index = match.start()

    if current:
        current["content"] = content[last_index:]
        sections.append(current)

    return sections


def parse_sections(content: str) -> list[dict]:
    """与前端 ``parseSections`` 对齐：按一级标题切分正文为多个章节。

    返回 ``[{id, title, level, content}]``，不含 sub_headings（前端目录树未使用）。
    """
    if not content:
        return []
    if is_html_content(content):
        return _parse_html_sections(content)
    return _parse_markdown_sections(content)
