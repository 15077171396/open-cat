"""Bing IndexNow 即时收录推送

- IndexNow 协议（indexnow.org）：Bing / Yandex / Naver / Seznam 共用的新 URL 即时推送
- 需在站点根路径部署密钥文件 https://host/{key}.txt（内容由 nginx 转发到
  后端 /api/v1/indexnow-key 动态输出，密钥只需在 .env 配置一处）
- 推送是旁路能力：任何失败只记日志、不抛异常，不影响业务主流程
"""

from urllib.parse import urlparse

import httpx
from loguru import logger

from app.config import settings

_PUSH_ENDPOINT = "https://api.indexnow.org/IndexNow"
# 官方单次请求上限 10000 条 URL
_BATCH_SIZE = 10000


async def push_urls_to_indexnow(urls: list[str]) -> dict:
    """批量推送 URL 到 IndexNow。

    返回 {"success": 接受条数, "skipped": 跳过条数}；
    未配置密钥或推送失败时均不抛异常。
    """
    if not settings.INDEXNOW_KEY:
        logger.debug("未配置 INDEXNOW_KEY，跳过 IndexNow 推送")
        return {"success": 0, "skipped": len(urls)}
    if not urls:
        return {"success": 0, "skipped": 0}

    host = urlparse(settings.SITE_BASE_URL).netloc
    success = 0
    async with httpx.AsyncClient(timeout=10) as client:
        for i in range(0, len(urls), _BATCH_SIZE):
            batch = urls[i:i + _BATCH_SIZE]
            payload = {"host": host, "key": settings.INDEXNOW_KEY, "urlList": batch}
            try:
                resp = await client.post(_PUSH_ENDPOINT, json=payload)
                # 协议约定：200 = 全部接受，202 = 部分接受/待校验，其余为错误
                if resp.status_code in (200, 202):
                    success += len(batch)
                    logger.info(f"IndexNow 推送: 本批 {len(batch)} 条, 状态 {resp.status_code}")
                else:
                    logger.warning(
                        f"IndexNow 推送失败: 状态 {resp.status_code}, 响应 {resp.text[:200]}"
                    )
            except Exception as e:  # noqa: BLE001 推送失败不影响主流程
                logger.warning(f"IndexNow 推送批次异常（已忽略）: {e}")
    return {"success": success}
