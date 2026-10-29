"""SEO 路由 - IndexNow 密钥文件

IndexNow 协议要求密钥文件位于站点根路径 /{key}.txt（响应体即密钥本身）。
nginx 用正则 location `^/[a-f0-9]{32}\\.txt$` 将该路径转发到本路由，
密钥只在 .env 配置一处，避免 nginx.conf 与 .env 双处维护不同步。
"""

from fastapi import APIRouter
from fastapi.responses import Response

from app.config import settings
from app.core.exceptions import AppException

router = APIRouter()


@router.get("/indexnow-key", include_in_schema=False)
async def indexnow_key():
    """输出 IndexNow 密钥（未配置时 404，等同密钥文件不存在）"""
    if not settings.INDEXNOW_KEY:
        # 走 AppException 而非裸 HTTPException：后者会被 handler 重包一层，绕过 AppException.data 通道
        raise AppException(code=404, message="IndexNow key not configured")
    return Response(content=settings.INDEXNOW_KEY, media_type="text/plain; charset=utf-8")
