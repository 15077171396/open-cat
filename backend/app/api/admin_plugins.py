"""管理后台 - 扩展管理（含第三方导入与翻译任务）"""

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Literal, Optional

from app.database import get_db
from app.core.permissions import require_admin
from app.models.user import User
from app.core.exceptions import AppException, success_response
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.schemas.content import ContentListItem, PageResult
from app.api._admin_shared import router

from app.services.admin_plugin_service import (
    list_plugins,
    get_plugin,
    create_plugin,
    update_plugin,
    delete_plugin,
    batch_delete_plugins,
)
from app.services.third_party_fetch import fetch_third_party_content
from app.services.translate_service import submit_translate_task, get_translate_task
from app.api._admin_shared import PluginSourceItem
from app.schemas.content import PluginDetail
from app.schemas.admin import ActionAck, CreatedResult, DeletedResult


# 扩展管理

class PluginCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    category: str = Field(..., min_length=1, max_length=50)
    # platform / version / rating / install_count 已删列，请求模型不再接收（多传被 pydantic 忽略）
    # 发布作者：**没有手工输入 UI**（2026-10-12 需求：不该让运营手填）。
    # 仅「第三方导入」会自动带上原文/仓库作者；缺省时由发布账号兜底（见 content_author）。
    author_name: Optional[str] = Field(None, max_length=100)
    author_avatar_url: Optional[str] = Field(None, max_length=500)
    description: str = Field(..., min_length=1)
    content: Optional[str] = None
    sources: Optional[list[PluginSourceItem]] = None
    # SEO 信息（台账 P1-17）：仅写入详情页 meta 标签供搜索引擎抓取，前台不展示
    seo_title: Optional[str] = Field(None, max_length=200)
    seo_description: Optional[str] = Field(None, max_length=500)
    seo_keywords: Optional[str] = Field(None, max_length=500)
    # 发布状态：False=草稿，前台列表/详情/首页/sitemap 均不可见。默认 True（保存即上线）
    is_published: Optional[bool] = True


class PluginUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    category: Optional[str] = Field(None, min_length=1, max_length=50)
    description: Optional[str] = Field(None, min_length=1)
    content: Optional[str] = None
    sources: Optional[list[PluginSourceItem]] = None
    # 发布作者：同 Create —— 无手工 UI；缺省时由发布账号在「作者为空」时兜底
    author_name: Optional[str] = Field(None, max_length=100)
    author_avatar_url: Optional[str] = Field(None, max_length=500)
    # SEO 信息：允许清空（传空串 = 清除，回落默认标题）
    seo_title: Optional[str] = Field(None, max_length=200)
    seo_description: Optional[str] = Field(None, max_length=500)
    seo_keywords: Optional[str] = Field(None, max_length=500)
    # 发布状态：显式传值才改；None=不改动（草稿/下架都走这里，漏声明会被 pydantic 静默丢弃）
    is_published: Optional[bool] = None


@router.get("/plugins", response_model=ApiResponse[PageResult[ContentListItem]])
async def admin_list_plugins(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    category: str = Query(None),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """扩展列表"""
    result = await list_plugins(db, page, page_size, keyword, category)
    return success_response(result)


# 扩展批量操作
# 注意：批量路由必须定义在 /plugins/{plugin_id} 之前，
# 否则 "batch-delete" 会被当作路径参数解析为 int 而返回 422。

class PluginBatchDeleteRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=100)


@router.post("/plugins/batch-delete", response_model=ApiResponse[DeletedResult])
async def admin_batch_delete_plugins(
    req: PluginBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """批量删除扩展（来源链接一并清除，不可恢复）"""
    result = await batch_delete_plugins(db, req.ids, admin)
    return success_response(result, f"已删除 {result['deleted']} 个扩展")


@router.get("/fetch-resource", response_model=ApiResponse[ActionAck])
async def admin_fetch_third_party_resource(
    url: str = Query(..., min_length=3, max_length=500),
    include_files: bool = Query(False, description="是否附带文件列表（ModelScope / HuggingFace 等平台支持）"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """根据资源网址从第三方平台拉取资料（用于正文内容导入，按 URL 域名自动识别平台）。

    - 仓库/模型类（GitHub/Gitee/GitCode/ModelScope/HuggingFace）：仓库拉 README、模型拉介绍卡片，
      include_files=true 附文件列表；优先探测中文版（README.zh-CN.md 等），非中文返回 language="en" 可再调翻译接口
    - 文章类（resource_type=article，X/Reddit/Medium/HN/知乎/掘金/CSDN 等）：抽取正文转 Markdown，
      国外平台无法直连时可配 ARTICLE_PROXY
    返回含 title/platform/author/summary/tags/source_url 等元信息供自动回填表单，文章类自动追加出处行。
    """
    # 抓取路径可达分钟级且全程不写库 → 先释放鉴权事务，否则一条慢抓取长期占住池连接
    await db.rollback()

    result = await fetch_third_party_content(url, include_files)
    return success_response(result)


class TranslateContentRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=300000)
    format: Literal["markdown", "html"] = "html"


@router.post("/translate-content", response_model=ApiResponse[ActionAck])
async def admin_translate_content(
    req: TranslateContentRequest,
    admin: User = Depends(require_admin),
):
    """提交 AI 翻译任务（异步）：长文翻译需 1-2 分钟，同步等待会被 CDN/网关超时掐断（524），
    故立即返回 task_id 由前端轮询 GET /translate-content/{task_id}"""
    task_id = await submit_translate_task(req.content, req.format)
    return success_response({"task_id": task_id}, "翻译任务已提交")


@router.get("/translate-content/{task_id}", response_model=ApiResponse[ActionAck])
async def admin_get_translate_task(
    task_id: str,
    admin: User = Depends(require_admin),
):
    """查询翻译任务进度/结果（status: pending | running | completed | failed）"""
    task = await get_translate_task(task_id)
    if task is None:
        raise AppException(code=404, message="翻译任务不存在或已过期")
    return success_response(task)


@router.get("/plugins/{plugin_id}", response_model=ApiResponse[PluginDetail])
async def admin_get_plugin(
    plugin_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """扩展详情"""
    result = await get_plugin(db, plugin_id)
    return success_response(result)


@router.post("/plugins", response_model=ApiResponse[CreatedResult])
async def admin_create_plugin(
    req: PluginCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """创建扩展"""
    result = await create_plugin(db, req.model_dump(), admin)
    return success_response(result, "扩展创建成功")


@router.put("/plugins/{plugin_id}", response_model=ApiResponse[None])
async def admin_update_plugin(
    plugin_id: int,
    req: PluginUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """更新扩展"""
    await update_plugin(db, plugin_id, req.model_dump(exclude_unset=False), admin)
    return success_response(message="扩展已更新")


@router.delete("/plugins/{plugin_id}", response_model=ApiResponse[None])
async def admin_delete_plugin(
    plugin_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """删除扩展"""
    await delete_plugin(db, plugin_id, admin)
    return success_response(message="扩展已删除")
