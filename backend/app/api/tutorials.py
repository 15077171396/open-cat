"""教程路由"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, get_current_user_optional
from app.models.user import User
from app.core.exceptions import success_response
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.schemas.content import ContentListItem, PageResult, TutorialDetail, TutorialIntro
from app.services.tutorial_service import (
    get_tutorials,
    get_tutorial_intro,
    get_tutorial_detail,
    track_learning,
    check_tutorial_read_access,
    purchase_tutorial,
)
from app.services.tutorial_node_service import get_node_tree, get_node_detail
from app.schemas.admin import ActionAck

router = APIRouter()


@router.get("", response_model=ApiResponse[PageResult[ContentListItem]])
async def list_tutorials(
    # 上限 200：OFFSET 深分页会让 MySQL 扫完前面所有行（page=100000 就是 500 万行）
    # 公开列表不需要翻到那么深，超出的长尾交给搜索/sitemap（第五轮审查 PERF-08）
    page: int = Query(1, ge=1, le=200),
    page_size: int = Query(6, ge=1, le=50),
    # max_length：防超长关键字整体转义后透传给 DB 做 LIKE（带宽/CPU 放大向量）
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    category: str = Query(None, max_length=50),
    # pattern 白名单：任意字符串原先会静默落空排序（等价无 ORDER BY 分页）
    sort: str = Query("latest", pattern="^(latest|hottest)$"),
    db: AsyncSession = Depends(get_db),
):
    """教程列表 (公开；置顶 > 推荐 > 最新/热门)"""
    result = await get_tutorials(db, page, page_size, keyword, category, sort)
    return success_response(result)


@router.get("/{tutorial_id}/intro", response_model=ApiResponse[TutorialIntro])
async def tutorial_intro(
    tutorial_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """教程介绍页 (公开；正文不返回，目录由后端抽取；网盘链接需具备阅读权限)"""
    result = await get_tutorial_intro(db, tutorial_id, current_user)
    return success_response(result)


@router.get("/{tutorial_id}/nodes", response_model=ApiResponse[ActionAck])
async def tutorial_node_tree(
    tutorial_id: int,
    db: AsyncSession = Depends(get_db),
):
    """章节树 (公开，仅导航小字段；供介绍页目录与阅读页左树使用)"""
    result = await get_node_tree(db, tutorial_id)
    return success_response(result)


@router.get("/{tutorial_id}/nodes/{node_id}", response_model=ApiResponse[ActionAck])
async def tutorial_node_detail(
    tutorial_id: int,
    node_id: int,
    count_view: bool = Query(False, description="是否计入浏览次数（阅读页会话首次加载传 true）"),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """单章正文 (需登录；免费教程登录即读，会员教程需 VIP 有效或已积分购买)"""
    await check_tutorial_read_access(db, current_user, tutorial_id)
    result = await get_node_detail(db, tutorial_id, node_id, count_view=count_view)
    return success_response(result)


@router.get("/{tutorial_id}", response_model=ApiResponse[TutorialDetail])
async def tutorial_detail(
    tutorial_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """教程详情 (需登录；免费教程登录即读，会员教程需 VIP 有效或已积分购买；兼容旧整篇阅读)

    权限校验在 get_tutorial_detail 内完成（复用同一次行加载，避免重复投影查询）
    """
    result = await get_tutorial_detail(db, tutorial_id, current_user)
    return success_response(result)


class TrackRequest(BaseModel):
    duration: int = Field(10, ge=0, le=15, description="单次上报学习时长（秒），0-15")


@router.post("/{tutorial_id}/track", response_model=ApiResponse[ActionAck])
async def track_tutorial(
    tutorial_id: int,
    req: TrackRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """上报学习时长（纯统计：学习记录 + 实际学习时长，不再作为阅读门槛）"""
    result = await track_learning(db, current_user, tutorial_id, req.duration)
    return success_response(result)


@router.post("/{tutorial_id}/purchase", response_model=ApiResponse[ActionAck])
async def purchase_tutorial_endpoint(
    tutorial_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """积分购买会员教程（非会员购买后永久可读；幂等，重复购买不扣分）"""
    result = await purchase_tutorial(db, current_user, tutorial_id)
    message = "已购买过该教程" if result.get("already_purchased") else "购买成功，已解锁永久阅读"
    return success_response(result, message)
