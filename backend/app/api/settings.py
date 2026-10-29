"""系统设置公共路由

- GET /settings               前端公共设置（无需认证，Redis 缓存 5 分钟）
- GET /settings/legal/{type}  法律文档：隐私政策/用户协议/免责声明（无需认证）

管理端读写接口见 admin.py（GET/PUT /admin/settings）。
"""

from fastapi import APIRouter

from app.core.exceptions import AppException, success_response
from app.schemas.common import ApiResponse
from app.services.setting_service import get_legal_document, get_public_settings
from app.schemas.admin import LegalDocument, SettingsPayload

router = APIRouter()


@router.get("", response_model=ApiResponse[SettingsPayload])
async def public_settings():
    """前端公共设置：默认值 + 管理端覆盖值（未配置项回退默认）"""
    return success_response(await get_public_settings())


@router.get("/legal/{doc_type}", response_model=ApiResponse[LegalDocument])
async def public_legal_document(doc_type: str):
    """法律文档（privacy=隐私政策 / agreement=用户协议 / disclaimer=免责声明）；
    内容为管理端富文本 HTML，前台渲染端负责 DOMPurify 白名单清洗"""
    try:
        doc = await get_legal_document(doc_type)
    except ValueError as exc:
        raise AppException(code=404, message=str(exc))
    return success_response(doc)
