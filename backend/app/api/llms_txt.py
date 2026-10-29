"""SEO/GEO 路由 - llms.txt（LLM 爬虫站点内容说明）"""

import asyncio
import time

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.content_cache import llms_generation
from app.services.llms_txt_service import build_llms_txt

router = APIRouter()

# 进程内缓存（PERF）：生成需 5 个带排序的查询，与 sitemap 相同的 TTL 策略。
# 缓存值里额外记「内容代数」：内容上下架时 content_cache 会 INCR 该键，
# 各 worker 发现代数变化立即重建 —— 只靠 TTL 会让已下架内容在 llms.txt 里多活 10 分钟。
_LLMS_TTL = 600
_llms_cache: tuple[float, int, str] = (0.0, -1, "")
# 单飞锁：LLM 爬虫是成批来的（同一时刻多家引擎抓取），无锁时每个爬虫各跑一遍全量重建
_llms_lock = asyncio.Lock()


@router.get("/llms.txt", include_in_schema=False)
async def llms_txt(db: AsyncSession = Depends(get_db)):
    """llms.txt（由 nginx 在根路径 /llms.txt 反代到本路由，供 AI 引擎发现站点内容）"""
    global _llms_cache
    now = time.time()
    gen = await llms_generation()
    if now - _llms_cache[0] <= _LLMS_TTL and gen == _llms_cache[1]:
        return Response(content=_llms_cache[2], media_type="text/markdown; charset=utf-8")
    async with _llms_lock:
        # 锁内复检：等锁期间第一个爬虫已经重建好了
        now = time.time()
        gen = await llms_generation()
        if now - _llms_cache[0] > _LLMS_TTL or gen != _llms_cache[1]:
            _llms_cache = (now, gen, await build_llms_txt(db))
    return Response(content=_llms_cache[2], media_type="text/markdown; charset=utf-8")
