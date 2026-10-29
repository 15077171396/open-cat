"""SEO 路由 - 站点地图

两个入口（nginx 把根路径反代到这里）：
    /api/v1/sitemap.xml      → 入口：URL 未超上限时是 urlset，超过时是 sitemapindex
    /api/v1/sitemap-{n}.xml  → 分片
"""

import asyncio
import time

from fastapi import APIRouter, Depends, Path
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.sitemap_service import build_sitemap

router = APIRouter()

# 进程内缓存（PERF）：sitemap 生成需 6 个全表查询，搜索引擎每次抓取都触发
# 全表扫描形成放大；内容变更频率远低于爬虫抓取频率，TTL 10 分钟足够。
# 按「页」缓存：分片模式下每片单独缓存，避免抓一片就把其它片挤出缓存。
_SITEMAP_TTL = 600
# 缓存条数上界：每片是整份 XML（≤45,000 条 <url>，可达数 MB），分片号范围到 1000，
# 只增不删会让常驻进程内存 GB 级；真分片数远小于上界，超出时淘汰最旧的
_MAX_CACHED_SITEMAP_PAGES = 8
_sitemap_cache: dict[str, tuple[float, str]] = {}
# 单飞锁：TTL 到点瞬间 N 个爬虫并发 miss 会各跑一遍 6 条全表查询
_render_lock = asyncio.Lock()


def _prune(now: float) -> None:
    """写入前清掉过期项并压回上界（只在重建路径调用，命中缓存时零开销）"""
    for stale in [k for k, (ts, _) in _sitemap_cache.items() if now - ts > _SITEMAP_TTL]:
        _sitemap_cache.pop(stale, None)
    while len(_sitemap_cache) >= _MAX_CACHED_SITEMAP_PAGES:
        oldest = min(_sitemap_cache, key=lambda k: _sitemap_cache[k][0])
        _sitemap_cache.pop(oldest, None)


async def _render(key: str, db: AsyncSession, page: int | None) -> str:
    now = time.time()
    cached = _sitemap_cache.get(key)
    if cached and now - cached[0] <= _SITEMAP_TTL:
        return cached[1]
    async with _render_lock:
        # 锁内复检：排队等锁期间，第一个请求已经把这一份重建好了
        now = time.time()
        cached = _sitemap_cache.get(key)
        if cached and now - cached[0] <= _SITEMAP_TTL:
            return cached[1]
        content = await build_sitemap(db, page)
        _prune(now)
        _sitemap_cache[key] = (now, content)
        return content


@router.get("/sitemap.xml", include_in_schema=False)
async def sitemap(db: AsyncSession = Depends(get_db)):
    """站点地图入口（由 nginx 在根路径 /sitemap.xml 反代到本路由）"""
    content = await _render("index", db, None)
    return Response(content=content, media_type="application/xml; charset=utf-8")


@router.get("/sitemap-{page}.xml", include_in_schema=False)
async def sitemap_page(
    page: int = Path(..., ge=1, description="分片序号，从 1 开始"),
    db: AsyncSession = Depends(get_db),
):
    """站点地图分片（由 nginx 在根路径 /sitemap-N.xml 反代到本路由）

    分片号越界返回 404：失效链接应明确报错，而不是回一份空 urlset
    （空 urlset 会被当成「该片确实为空」，可能触发内容与索引不符的告警）。
    """
    content = await _render(f"page-{page}", db, page)
    return Response(content=content, media_type="application/xml; charset=utf-8")
