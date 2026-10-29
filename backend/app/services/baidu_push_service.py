"""百度搜索资源平台「普通收录」API 推送：缩短爬虫发现新 URL 的时间（不保证收录）。
单次最多 20 条、配额当日有效；推送是旁路能力，任何失败只记日志不抛异常。
⚠️ 凭据硬约束：必须走 HTTPS（token 在查询串里，明文即泄露）；异常只记 type(e).__name__
（httpx 错误串带完整 URL，会把令牌写进宿主挂载的日志文件）。"""

import asyncio
from urllib.parse import urlparse

import httpx
from loguru import logger

from app.config import settings

_PUSH_ENDPOINT = "https://data.zz.baidu.com/urls"
_BATCH_SIZE = 20
# 整批推送总时限：批量日可推上千条，超时会让 APScheduler（max_instances=1）次日直接报错不推；
# 配额当日有效可重试，宁可截断也不占住调度器
_TOTAL_DEADLINE_SECONDS = 120


async def push_urls_to_baidu(urls: list[str]) -> dict:
    """批量推送 URL 到百度普通收录。

    返回 {"success": 成功条数, "remain": 当日剩余配额, "skipped": 跳过条数}；
    未配置 token 或推送失败时均不抛异常。
    """
    if not settings.BAIDU_PUSH_TOKEN:
        logger.debug("未配置 BAIDU_PUSH_TOKEN，跳过百度推送")
        return {"success": 0, "remain": None, "skipped": len(urls)}
    if not urls:
        return {"success": 0, "remain": None, "skipped": 0}

    site = urlparse(settings.SITE_BASE_URL).netloc
    # token 经 params 传入而不是拼进 URL 字符串：httpx 抛错时错误串仍会带查询串，
    # 所以真正防泄漏的是下面的「只记异常类型」，这里只是让代码里少一处明文拼接
    params = {"site": site, "token": settings.BAIDU_PUSH_TOKEN}

    success = 0
    remain = None
    pushed = 0
    # 客户端在循环外创建复用连接池：原先每批次各建一次 TCP+TLS 握手，
    # 批量更新日白白增加延迟与对端连接压力（PERF）
    try:
        async with asyncio.timeout(_TOTAL_DEADLINE_SECONDS):
            async with httpx.AsyncClient(timeout=10) as client:
                for i in range(0, len(urls), _BATCH_SIZE):
                    batch = urls[i:i + _BATCH_SIZE]
                    try:
                        resp = await client.post(
                            _PUSH_ENDPOINT,
                            params=params,
                            content="\n".join(batch),
                            headers={"Content-Type": "text/plain"},
                        )
                        data = resp.json()
                        success += int(data.get("success", 0))
                        remain = data.get("remain")
                        logger.info(
                            f"百度推送: 本批 {len(batch)} 条, 成功 {data.get('success', 0)}, "
                            f"失败 {data.get('error', 0) or 0}, 剩余配额 {data.get('remain')}"
                        )
                    except Exception as e:  # noqa: BLE001 推送失败不影响主流程
                        # 只记异常类型：见模块头的硬约束 2
                        logger.warning(
                            f"百度推送批次异常（已忽略）: {type(e).__name__} / "
                            f"HTTP {getattr(getattr(e, 'response', None), 'status_code', '-')}"
                        )
                    pushed += len(batch)
    except TimeoutError:
        logger.warning(
            f"百度推送超过总时限 {_TOTAL_DEADLINE_SECONDS}s，放弃剩余 "
            f"{len(urls) - pushed} 条（旁路能力，次日调度会重新收集）"
        )
    return {"success": success, "remain": remain}
