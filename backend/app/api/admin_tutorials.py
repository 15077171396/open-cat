"""管理后台 - 教程与章节管理"""

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Literal, Optional

from app.database import get_db
from app.core.permissions import require_admin
from app.models.user import User
from app.core.exceptions import success_response
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.schemas.content import ContentListItem, PageResult
from app.api._admin_shared import router

from app.services.admin_tutorial_service import (
    list_tutorials,
    get_tutorial,
    create_tutorial,
    update_tutorial,
    toggle_publish as toggle_tutorial_publish,
    delete_tutorial,
    batch_delete_tutorials,
    restore_tutorial,
    batch_restore_tutorials,
    batch_publish_tutorials,
)
from app.services.tutorial_node_service import (
    admin_get_tree as get_tutorial_node_tree,
    admin_create_node as create_tutorial_node,
    admin_update_node as update_tutorial_node,
    admin_move_node as move_tutorial_node,
    admin_delete_node as delete_tutorial_node,
)
from app.api._admin_shared import NetdiskLinkItem
from app.schemas.content import TutorialDetail
from app.schemas.admin import ActionAck, AffectedResult, CreatedResult, DeletedResult


# 教程管理

class TutorialCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1)
    introduction: Optional[str] = None
    category: str = Field(..., min_length=1, max_length=50)
    # 章节化改造：正文改走 /admin/tutorials/{id}/nodes 章节接口，
    # 此字段仅为兼容旧请求保留（传入时会写入 tutorials.content 备份字段）
    content: Optional[str] = Field(None, min_length=1)
    cover_image: Optional[str] = Field(None, max_length=500)
    status: Optional[Literal["updating", "completed"]] = "updating"
    # 阅读权限（均需登录）：free=免费阅读 / vip=会员专享（非会员可积分购买）
    access_mode: Optional[Literal["free", "vip"]] = "free"
    # 会员教程积分购买价：0=不支持积分购买；免费模式下会被归一为 0
    points_price: Optional[int] = Field(None, ge=0, le=1_000_000)
    is_pinned: Optional[bool] = False
    is_recommended: Optional[bool] = False
    # SEO 信息：仅写入页面 meta 标签供搜索引擎抓取，前台页面不展示
    seo_title: Optional[str] = Field(None, max_length=200)
    seo_description: Optional[str] = Field(None, max_length=500)
    seo_keywords: Optional[str] = Field(None, max_length=500)
    netdisk_links: Optional[list[NetdiskLinkItem]] = None


class TutorialUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, min_length=1)
    introduction: Optional[str] = None
    category: Optional[str] = Field(None, min_length=1, max_length=50)
    content: Optional[str] = Field(None, min_length=1)
    cover_image: Optional[str] = Field(None, max_length=500)
    status: Optional[Literal["updating", "completed"]] = None
    access_mode: Optional[Literal["free", "vip"]] = None
    points_price: Optional[int] = Field(None, ge=0, le=1_000_000)
    is_pinned: Optional[bool] = None
    is_recommended: Optional[bool] = None
    # SEO 信息：允许清空（传空串 = 清除，前台回落默认标题/简介）
    seo_title: Optional[str] = Field(None, max_length=200)
    seo_description: Optional[str] = Field(None, max_length=500)
    seo_keywords: Optional[str] = Field(None, max_length=500)
    netdisk_links: Optional[list[NetdiskLinkItem]] = None


@router.get("/tutorials", response_model=ApiResponse[PageResult[ContentListItem]])
async def admin_list_tutorials(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    category: str = Query(None, max_length=50),
    status: Literal["updating", "completed"] = Query(None),
    is_published: Optional[bool] = Query(None),
    access_mode: Literal["free", "vip"] = Query(None),
    deleted: Literal["active", "deleted", "all"] = Query(
        "active", description="回收站视图：active=正常内容（默认）/ deleted=回收站 / all=全部"
    ),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """教程列表（支持标题/简介搜索与分类/更新状态/发布状态/阅读权限筛选）

    `deleted` 是**视图**而非筛选项：默认只看正常内容，回收站里的内容不会混进日常列表。
    """
    result = await list_tutorials(
        db, page, page_size, keyword, category, status, is_published, access_mode, deleted
    )
    return success_response(result)


@router.post("/tutorials", response_model=ApiResponse[CreatedResult])
async def admin_create_tutorial(
    req: TutorialCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """发布教程"""
    result = await create_tutorial(db, req.model_dump(), admin)
    return success_response(result, "教程发布成功")


# 批量操作（批量路由必须定义在 /tutorials/{tutorial_id} 之前，
# 否则 "batch-delete" 会被当作路径参数解析为 int 而返回 422）

class TutorialBatchDeleteRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=100)


class TutorialBatchPublishRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=100)
    publish: bool = True


@router.post("/tutorials/batch-delete", response_model=ApiResponse[DeletedResult])
async def admin_batch_delete_tutorials(
    req: TutorialBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """批量删除教程（**移入回收站**，可恢复；章节/购买记录保留）"""
    result = await batch_delete_tutorials(db, req.ids, admin)
    return success_response(result, f"已删除 {result['deleted']} 篇教程（可在回收站恢复）")


@router.post("/tutorials/batch-restore", response_model=ApiResponse[AffectedResult])
async def admin_batch_restore_tutorials(
    req: TutorialBatchDeleteRequest,
    republish: bool = Query(False, description="恢复的同时重新发布（默认恢复为草稿）"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """批量从回收站恢复教程（默认恢复为草稿，需再点一次发布才上线）"""
    result = await batch_restore_tutorials(db, req.ids, admin, republish=republish)
    return success_response(result, f"已恢复 {result['restored']} 篇教程")


@router.post("/tutorials/batch-publish", response_model=ApiResponse[AffectedResult])
async def admin_batch_publish_tutorials(
    req: TutorialBatchPublishRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """批量发布/取消发布教程"""
    result = await batch_publish_tutorials(db, req.ids, req.publish, admin)
    action = "发布" if req.publish else "取消发布"
    return success_response(result, f"已{action} {result['updated']} 篇教程")


@router.get("/tutorials/{tutorial_id}", response_model=ApiResponse[TutorialDetail])
async def admin_get_tutorial(
    tutorial_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """教程详情"""
    result = await get_tutorial(db, tutorial_id)
    return success_response(result)


@router.put("/tutorials/{tutorial_id}", response_model=ApiResponse[None])
async def admin_update_tutorial(
    tutorial_id: int,
    req: TutorialUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """更新教程"""
    # exclude_unset=False → 未传字段保留为 None，service 内用 is not None 判断
    await update_tutorial(db, tutorial_id, req.model_dump(exclude_unset=False), admin)
    return success_response(message="教程已更新")


@router.patch("/tutorials/{tutorial_id}/publish", response_model=ApiResponse[ActionAck])
async def admin_toggle_publish(
    tutorial_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """切换发布状态"""
    result = await toggle_tutorial_publish(db, tutorial_id, admin)
    return success_response(result, "状态已更新")


@router.delete("/tutorials/{tutorial_id}", response_model=ApiResponse[None])
async def admin_delete_tutorial(
    tutorial_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """删除教程（软删除：移入回收站，可在回收站恢复）"""
    await delete_tutorial(db, tutorial_id, admin)
    return success_response(message="教程已移入回收站")


@router.post("/tutorials/{tutorial_id}/restore", response_model=ApiResponse[ActionAck])
async def admin_restore_tutorial(
    tutorial_id: int,
    republish: bool = Query(False, description="恢复的同时重新发布（默认恢复为草稿）"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """从回收站恢复教程。

    默认恢复为**草稿**：内容被删往往有原因，自动重新上线等于让误操作生效两次；
    需要立刻上线时传 `republish=true`。
    """
    result = await restore_tutorial(db, tutorial_id, admin, republish=republish)
    return success_response(result, "教程已恢复" + ("并发布" if republish else "（草稿）"))


# 教程章节管理（正文按章存储，替代原单大字段 content）

class TutorialNodeCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: Optional[str] = None
    parent_id: Optional[int] = Field(None, description="父章节ID，顶级章节不传")


class TutorialNodeUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    content: Optional[str] = None


class TutorialNodeMoveRequest(BaseModel):
    direction: Literal["up", "down"]


@router.get("/tutorials/{tutorial_id}/nodes", response_model=ApiResponse[ActionAck])
async def admin_get_tutorial_nodes(
    tutorial_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """章节树（含正文）"""
    result = await get_tutorial_node_tree(db, tutorial_id)
    return success_response(result)


@router.post("/tutorials/{tutorial_id}/nodes", response_model=ApiResponse[CreatedResult])
async def admin_create_tutorial_node(
    tutorial_id: int,
    req: TutorialNodeCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """新建章节（追加到指定父级同级末尾）"""
    result = await create_tutorial_node(db, tutorial_id, req.model_dump(), admin)
    return success_response(result, "章节已创建")


@router.put("/tutorials/{tutorial_id}/nodes/{node_id}", response_model=ApiResponse[None])
async def admin_update_tutorial_node(
    tutorial_id: int,
    node_id: int,
    req: TutorialNodeUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """更新章节标题/正文"""
    await update_tutorial_node(db, tutorial_id, node_id, req.model_dump(exclude_unset=False), admin)
    return success_response(message="章节已保存")


@router.post("/tutorials/{tutorial_id}/nodes/{node_id}/move", response_model=ApiResponse[ActionAck])
async def admin_move_tutorial_node(
    tutorial_id: int,
    node_id: int,
    req: TutorialNodeMoveRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """同级内上移/下移章节"""
    result = await move_tutorial_node(db, tutorial_id, node_id, req.direction)
    return success_response(result)


@router.delete("/tutorials/{tutorial_id}/nodes/{node_id}", response_model=ApiResponse[None])
async def admin_delete_tutorial_node(
    tutorial_id: int,
    node_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """删除章节（含其子孙章节）"""
    result = await delete_tutorial_node(db, tutorial_id, node_id, admin)
    return success_response(result, "章节已删除")
