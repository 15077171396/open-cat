"""SEO 站点地图生成服务：内容全部来自数据库（SPA 构建期拿不到动态内容），
由 nginx 把根路径 /sitemap.xml 及分片 /sitemap-N.xml 反代到后端。
搜索引擎对单个 sitemap 有 5 万条 URL / 50MB 硬上限（超限整份丢弃），
故数据量大时切成「索引文件 + 分片」。⚠️ 分片靠 offset/limit 切分，
所有实体查询必须带 ORDER BY id 保证顺序稳定，否则同一 URL 会跨片重复或丢失。"""

from datetime import datetime, timezone
from math import ceil

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.config import settings
from app.core.exceptions import AppException
from app.models.node import Node, Plugin
from app.models.resource import Resource
from app.models.tool import Tool
from app.models.tutorial import Tutorial
from app.models.tutorial_node import TutorialNode

# 静态栏目页（其余 URL 全部来自数据库动态内容）
# /feedback 刻意不在列：表单页无正文，且已加 noindex
_STATIC_PATHS = ("/", "/tutorials", "/plugins", "/nodes", "/resources", "/tools", "/about", "/contact")

# 单文件 URL 上限。搜索引擎的硬上限是 50000，这里留 10% 余量：
# ① 留出「上限调整 / 统计口径差异」的缓冲；② 单文件体积也要留出 50MB 余量。
SITEMAP_MAX_URLS_PER_FILE = 45000

# 分片页数上界：用于在「收集全部 URL」之前快速拒绝伪造页码（见 build_sitemap）。
# 1000 页 × 45000 条 = 4500 万 URL，远超本站可预期规模。
_MAX_SITEMAP_PAGES = 1000

# 触发告警的比例：用量达到上限的这个比例时打 warning，便于在真正分片前先扩容或清理
_SITEMAP_WARN_RATIO = 0.8


def _entity_query(model, *columns, where=None):
    """构造「按主键升序、只加载指定列」的实体查询。

    - `load_only` 只取需要的列：全站 URL 收集要遍历全部内容行，多取一个正文字段
      就是几十 MB 的额外内存。
    - ⚠️ `ORDER BY` 不是可选项，见模块头「顺序稳定」说明。所有实体查询都经此函数，
      这样「漏写 ORDER BY」在代码里不可能悄悄发生。
    """
    stmt = select(model).options(load_only(*columns))
    if where is not None:
        stmt = stmt.where(where)
    return stmt.order_by(model.id)


def _entry(loc: str, lastmod: str | None = None) -> str:
    if lastmod:
        return f"  <url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>"
    return f"  <url><loc>{loc}</loc></url>"


def _date(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d") if dt else None


async def collect_sitemap_entries(db: AsyncSession) -> list[tuple[str, str | None]]:
    """汇总全站可索引 URL，返回 ``[(loc, lastmod), ...]``，顺序稳定。

    顺序约定：静态栏目页在最前，其后依次是教程 / 章节 / 扩展 / 节点 / 资源 / 工具，
    每个实体内部按 id 升序。
    """
    base = settings.SITE_BASE_URL.rstrip("/")
    entries: list[tuple[str, str | None]] = []

    # 1. 静态栏目页
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entries += [(f"{base}{p}", today) for p in _STATIC_PATHS]

    # 2. 已发布教程详情页
    tutorials = (await db.execute(
        _entity_query(
            Tutorial, Tutorial.id, Tutorial.updated_at,
            where=Tutorial.is_published == True,  # noqa: E712
        )
    )).scalars().all()
    entries += [(f"{base}/tutorials/{t.id}", _date(t.updated_at)) for t in tutorials]

    # 3. 教程章节页（长尾落地页，仅统计已发布教程的章节）
    #    刻意用「先取已发布 id 再 IN」而不是 JOIN：JOIN 的 ON 条件在本项目的内存
    #    会话仿真里无法正确求值，用 IN 既能测、语义也更直白。
    published_ids = [t.id for t in tutorials]
    if published_ids:
        chapters = (await db.execute(
            _entity_query(
                TutorialNode,
                TutorialNode.id, TutorialNode.tutorial_id, TutorialNode.updated_at,
                where=TutorialNode.tutorial_id.in_(published_ids),
            )
        )).scalars().all()
        entries += [
            (f"{base}/tutorials/{c.tutorial_id}/c/{c.id}", _date(c.updated_at))
            for c in chapters
        ]

    # 4. 扩展 / 节点 / 资源 / 已发布工具详情页
    plugins = (await db.execute(
        _entity_query(Plugin, Plugin.id, Plugin.updated_at, where=Plugin.is_published == True)  # noqa: E712
    )).scalars().all()
    entries += [(f"{base}/plugins/{p.id}", _date(p.updated_at)) for p in plugins]

    nodes = (await db.execute(
        _entity_query(Node, Node.id, Node.updated_at, where=Node.is_published == True)  # noqa: E712
    )).scalars().all()
    entries += [(f"{base}/nodes/{n.id}", _date(n.updated_at)) for n in nodes]

    resources = (await db.execute(
        _entity_query(Resource, Resource.id, Resource.updated_at, where=Resource.is_published == True)  # noqa: E712
    )).scalars().all()
    entries += [(f"{base}/resources/{r.id}", _date(r.updated_at)) for r in resources]

    tools = (await db.execute(
        _entity_query(
            Tool, Tool.id, Tool.updated_at,
            where=Tool.is_published == True,  # noqa: E712
        )
    )).scalars().all()
    entries += [(f"{base}/tools/{t.id}", _date(t.updated_at)) for t in tools]

    return entries


def sitemap_page_count(total_urls: int) -> int:
    """分片总数（至少 1：URL 再少也有 /sitemap-1.xml 可用，口径统一）。"""
    return max(1, ceil(total_urls / SITEMAP_MAX_URLS_PER_FILE))


def _build_urlset(entries: list[tuple[str, str | None]]) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(_entry(loc, lastmod) for loc, lastmod in entries)
        + "\n</urlset>\n"
    )


def _build_sitemapindex(base: str, pages: int) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    items = "\n".join(
        f"  <sitemap><loc>{base}/sitemap-{i}.xml</loc><lastmod>{today}</lastmod></sitemap>"
        for i in range(1, pages + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + items
        + "\n</sitemapindex>\n"
    )


async def build_sitemap(db: AsyncSession, page: int | None = None) -> str:
    """生成 sitemap 文档。

    :param page: ``None`` = 入口（``/sitemap.xml``）——URL 未超上限时直接给单份 urlset，
        超过时给 sitemapindex；``page=n`` = 第 n 个分片（``/sitemap-n.xml``）。
    :raises AppException: 分片号越界（404）。失效的分片链接应返回 404，而不是一份
        空 urlset —— 空 urlset 会被搜索引擎当成「该片内容确实为空」，可能触发
        「sitemap 内容与索引不符」的告警。
    """
    # 前置上界（第三轮增量审查 P1-43②）：原实现是「先做 6 次全表收集、之后才校验页码」—— 爬虫遍历
    # /sitemap-500000.xml 每次都触发一轮全量扫描，既浪费也是一条廉价的 DB 放大入口。这里先按页数上界
    # 快速拒绝明显非法的页码，不做任何实体查询。
    # 为什么用固定上界而不是精确总数：拿精确总数要么再跑 6 次 COUNT、要么把实体清单复制进第二个函数
    # —— 而本模块最怕的就是「清单两处漂移」（见模块头注释）。1000 页 × 45000 条 = 4500 万 URL，
    # 远超本站任何可预期的规模，超过它的页码必定是伪造的，返回 404 语义完全正确；
    # 合法范围内的页码仍走下面的精确校验。
    if page is not None and (page < 1 or page > _MAX_SITEMAP_PAGES):
        raise AppException(code=404, message="sitemap 分片不存在")

    entries = await collect_sitemap_entries(db)
    total = len(entries)
    pages = sitemap_page_count(total)
    if total >= SITEMAP_MAX_URLS_PER_FILE * _SITEMAP_WARN_RATIO:
        logger.warning(
            f"sitemap URL 数已达 {total}（单文件上限 {SITEMAP_MAX_URLS_PER_FILE}，"
            f"当前 {pages} 片）——接近搜索引擎硬上限，请关注内容规模"
        )

    if page is None:
        if pages == 1:
            return _build_urlset(entries)
        return _build_sitemapindex(settings.SITE_BASE_URL.rstrip("/"), pages)

    if page < 1 or page > pages:
        raise AppException(code=404, message="sitemap 分片不存在")

    start = (page - 1) * SITEMAP_MAX_URLS_PER_FILE
    return _build_urlset(entries[start : start + SITEMAP_MAX_URLS_PER_FILE])
