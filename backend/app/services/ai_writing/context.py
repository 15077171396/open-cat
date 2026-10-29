"""上下文组装：HTML→纯文本、预算截断、素材标记。

内容里可能包含「忽略以上指令」之类的句子；它只能作为素材，不能改变系统行为。
因此正文始终包在 <article_context>/<selection> 标签中，系统提示另行声明。
"""

import re
from html.parser import HTMLParser

from app.schemas.ai_writing import WritingRunRequest
from app.services.ai_writing.prompts import PromptDefault, render_template

_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "table", "section", "article",
}
_SKIP_TAGS = {"script", "style", "noscript"}
TRUNCATE_MARK = "\n…[内容已截断]…\n"


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self.skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self.skip:
            self.skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    if not html:
        return ""
    parser = _Extractor()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# 只有真的出现标签时才走解析：纯文本里的 `<T>` 或比较运算符不该被当成 HTML 丢掉。
_TAG_RE = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")


def as_plain_text(text: str) -> str:
    """把可能来自 Quill 的 HTML 转成纯文本；纯文本原样返回（不做二次解析）。"""
    if not text or not _TAG_RE.search(text):
        return text
    return html_to_text(text)


def truncate_middle(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    head = int(limit * 0.7)
    tail = max(0, limit - head)
    return text[:head] + TRUNCATE_MARK + (text[-tail:] if tail else "")


def build_messages(prompt: PromptDefault, req: WritingRunRequest) -> list[dict]:
    selection = (req.selection.text if req.selection else "")[:4000]
    values = {
        "title": req.context.title,
        "category": req.context.category,
        "tags": "、".join(req.context.tags),
        "chapter_title": req.context.chapter_title,
        "outline": "\n".join(req.context.outline),
        # 编辑器提交的是 Quill HTML：全文/前后文必须先转纯文本再截断，
        # 否则模型会收到 `<p style=...>` 这类标签并可能把它们抄进输出。
        # 先转文本再截断也很重要：截在标签中间只会留下半截 HTML。
        "article": truncate_middle(
            as_plain_text(req.context.article_text), prompt.max_input_chars
        ),
        "before": truncate_middle(as_plain_text(req.context.before[-4000:]), 2000),
        "after": truncate_middle(as_plain_text(req.context.after[:4000]), 2000),
        "selection": selection,
        "instruction": req.instruction or "无额外要求",
        "target_length": str(req.target_length or ""),
    }
    messages = [{"role": "system", "content": prompt.system_prompt}]
    # 从最新一轮往回取：预算不足时丢最旧的轮次，保住「接着刚才那段」所需的近期上下文；
    # 每轮按正序前插，最终消息顺序仍是时间正序。
    history: list[dict] = []
    history_chars = 0
    for turn in reversed(req.history[-6:]):
        if history_chars + len(turn.content) > 4000:
            break
        history_chars += len(turn.content)
        history.insert(0, {"role": turn.role, "content": turn.content})
    messages.extend(history)
    messages.append({"role": "user", "content": render_template(prompt.user_template, values)})
    return messages
