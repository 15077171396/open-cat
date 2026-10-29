"""GEO llms.txt 生成服务（llmstxt.org 规范）

面向 GPTBot/ClaudeBot/PerplexityBot 等 LLM 爬虫的站点内容说明文件，
Markdown 格式：H1 站点名 + blockquote 简介 + 分组链接列表。
内容由数据库动态生成（热门内容按热度取 top N），由 nginx `location = /llms.txt`
反代到后端 /api/v1/llms.txt。
"""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.node import Node, Plugin
from app.models.resource import Resource
from app.models.tool import Tool
from app.models.tutorial import Tutorial

# 各板块取热门内容的条数上限（控制文件体积，LLM 上下文友好）
_TUTORIAL_LIMIT = 30
_PLUGIN_LIMIT = 20
_NODE_LIMIT = 20
_RESOURCE_LIMIT = 20
_TOOL_LIMIT = 20

_DESC_MAX = 120

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_desc(desc: str | None) -> str:
    """HTML 片段转纯文本并截断，用作链接描述"""
    if not desc:
        return ""
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", desc)).strip()
    if len(text) > _DESC_MAX:
        text = text[:_DESC_MAX].rstrip() + "…"
    return text


def _link(name: str, url: str, desc: str) -> str:
    """输出 `- [name](url): desc` 行；描述为空时省略冒号段"""
    return f"- [{name}]({url}): {desc}" if desc else f"- [{name}]({url})"


async def build_llms_txt(db: AsyncSession) -> str:
    """汇总站点核心板块与热门内容，生成 llms.txt 文档"""
    base = settings.SITE_BASE_URL.rstrip("/")
    lines: list[str] = [
        "# AI学习猫",
        "",
        "> AI学习猫（www.wdmao.cn）是综合学习与实践平台，提供实用教程、知识百科、"
        "在线工具、精选资源与扩展，帮助你更高效地学习、探索与解决问题。",
        "",
        "## 栏目导航",
        "",
        _link("实用教程", f"{base}/tutorials", "循序渐进的章节化图文教程，覆盖软件使用、开发技术、AI 应用、数字创作等"),
        _link("知识百科", f"{base}/nodes", "各领域知识、概念、术语与技术的解释、原理与应用场景"),
        _link("扩展推荐", f"{base}/plugins", "精选实用扩展、浏览器扩展和功能组件，覆盖学习、开发、设计等场景"),
        _link("资源下载", f"{base}/resources", "软件、模型、模板、工作流、源码等精选数字资源"),
        _link("在线工具", f"{base}/tools", "文本处理、图片处理、开发辅助等在线工具，打开即可使用"),
        "",
    ]

    # 热门教程（按学习次数）
    tutorials = (await db.execute(
        select(Tutorial.id, Tutorial.title, Tutorial.description)
        .where(Tutorial.is_published == True)  # noqa: E712
        .order_by(Tutorial.view_count.desc())
        .limit(_TUTORIAL_LIMIT)
    )).all()
    if tutorials:
        lines += ["## 热门教程", ""]
        lines += [_link(t.title, f"{base}/tutorials/{t.id}", _clean_desc(t.description)) for t in tutorials]
        lines.append("")

    # 热门扩展（按预览量；原按 install_count，该列已随迁移 0040 删除）
    plugins = (await db.execute(
        select(Plugin.id, Plugin.name, Plugin.description)
        # 必须过滤发布态：/llms.txt 免鉴权且结果被 LLM 爬虫长期抓走，草稿/下架内容一旦
        # 进了热门榜就会外泄标题与简介，下架也收不回来
        .where(Plugin.is_published == True)  # noqa: E712
        .order_by(Plugin.view_count.desc(), Plugin.id.desc())
        .limit(_PLUGIN_LIMIT)
    )).all()
    if plugins:
        lines += ["## 热门扩展", ""]
        lines += [_link(p.name, f"{base}/plugins/{p.id}", _clean_desc(p.description)) for p in plugins]
        lines.append("")

    # 热门节点（按浏览次数）
    nodes = (await db.execute(
        select(Node.id, Node.name, Node.description)
        .where(Node.is_published == True)  # noqa: E712
        .order_by(Node.view_count.desc())
        .limit(_NODE_LIMIT)
    )).all()
    if nodes:
        lines += ["## 热门节点", ""]
        lines += [_link(n.name, f"{base}/nodes/{n.id}", _clean_desc(n.description)) for n in nodes]
        lines.append("")

    # 热门资源（按下载次数）
    resources = (await db.execute(
        select(Resource.id, Resource.name, Resource.description)
        .where(Resource.is_published == True)  # noqa: E712
        .order_by(Resource.download_count.desc())
        .limit(_RESOURCE_LIMIT)
    )).all()
    if resources:
        lines += ["## 热门资源", ""]
        lines += [_link(r.name, f"{base}/resources/{r.id}", _clean_desc(r.description)) for r in resources]
        lines.append("")

    # 在线工具（按使用次数）
    tools = (await db.execute(
        select(Tool.id, Tool.name, Tool.description)
        .where(Tool.is_published == True)  # noqa: E712
        .order_by(Tool.usage_count.desc())
        .limit(_TOOL_LIMIT)
    )).all()
    if tools:
        lines += ["## 在线工具", ""]
        lines += [_link(t.name, f"{base}/tools/{t.id}", _clean_desc(t.description)) for t in tools]
        lines.append("")

    lines += [
        "## 参考",
        "",
        _link("站点地图", f"{base}/sitemap.xml", "全站可索引 URL 清单"),
        "",
    ]
    return "\n".join(lines)
