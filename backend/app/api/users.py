"""用户中心路由"""

from fastapi import APIRouter, Depends, Query, Response, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.config import settings
from app.utils.upload_validation import is_allowed_image, validate_image_pixels
from app.core.exceptions import success_response, AppException
from app.core.rate_limit import check_user_daily_quota
from app.schemas.common import ApiResponse, KEYWORD_MAX_LENGTH
from app.schemas.content import ItemsResult, PageResult
from app.schemas.user_content import (
    ContinueLearningItem,
    FavoriteItem,
    InviteInfo,
    LearningRecordItem,
    MessageItem,
    PointTransactionItem,
)
from app.schemas.auth import UserInfoData
from app.services.storage import StorageError, storage
from app.services.invite_poster import build_invite_poster
from app.services.auth_service import build_user_profile
from app.services.favorite_service import (
    add_favorite,
    list_favorites,
    remove_favorite,
    check_favorite,
)
from app.services.message_service import (
    list_messages,
    get_message,
    mark_read,
    mark_all_read,
    delete_message,
    get_unread_count,
)
from app.services.learning_service import (
    upsert_learning_record,
    list_learning_records,
    list_continue_learning,
    get_learning_stats,
)
from app.services.tutorial_service import list_purchased_tutorials
from app.services.point_service import list_user_transactions
from app.services.redemption_service import redeem_code
from app.services.invite_service import get_invite_info, get_invite_settings, list_invitees
from app.services.user_settings_service import (
    update_profile as svc_update_profile,
    update_avatar,
    change_password,
    change_email,
    get_bindings,
    get_notification_settings,
    update_notification_settings,
    delete_account,
    get_user_level,
    get_user_stats,
)
from app.schemas.user_content import InviteeItem
from app.schemas.admin import ActionAck, CreatedResult, DeletedResult, StatsResult, UploadResult
from typing import Any

router = APIRouter()

# 头像每日上传上限：头像与评论配图是同一类写入口（任意注册用户可写、COS 对象永不复用、
# 不产生业务行），评论侧早已加配额，头像侧此前没有 —— 单账号可 300 次/分钟持续写 2MB 对象。
# 20 次足够正常人反复换图，又挡住了把存储当图床刷的行为。
MAX_AVATAR_UPLOADS_PER_USER_PER_DAY = 20


# 请求模型

class FavoriteRequest(BaseModel):
    target_type: str  # tutorial/resource/plugin/node/workflow
    target_id: int


class ProfileUpdateRequest(BaseModel):
    nickname: str | None = None
    bio: str | None = None


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str


class NotificationSettingsRequest(BaseModel):
    system: bool | None = None
    comment: bool | None = None
    like: bool | None = None
    private: bool | None = None
    email: bool | None = None


class EmailChangeRequest(BaseModel):
    # max_length 与 DB 列 String(100) 对齐，防超长合法格式邮箱写库 DataError 500；
    # code 与注册端验证码口径一致（长度约束，字符集由 verify_code 前置判定兜底）
    new_email: str = Field(..., min_length=3, max_length=100)
    code: str = Field(..., min_length=4, max_length=6)


class LearningUpsertRequest(BaseModel):
    progress: int | None = Field(None, ge=0, le=100)
    chapter: str | None = Field(None, max_length=200)


class RedeemCodeRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)


# 个人中心信息

@router.get("/profile", response_model=ApiResponse[UserInfoData])
async def get_profile(
    current_user: User = Depends(get_current_user),
):
    """个人中心信息（复用 auth_service.build_user_profile，与 /auth/me、/auth/login 字段口径一致；响应收紧为 UserInfoData 统一裁剪）"""
    return success_response(build_user_profile(current_user))


@router.put("/profile", response_model=ApiResponse[ActionAck])
async def update_profile_api(
    req: ProfileUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新个人资料（昵称、简介）"""
    result = await svc_update_profile(db, current_user, req.nickname, req.bio)
    return success_response(result, message="资料已更新")


@router.post("/avatar", response_model=ApiResponse[UploadResult])
async def upload_avatar(
    avatar: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """上传头像（multipart/form-data 字段名 avatar，写入腾讯云 COS），返回 { avatar_url }"""
    # 配额必须在读文件与写 COS 之前：放在上传之后计数，等于每次浪费都免费
    await check_user_daily_quota(
        current_user.id,
        scope="avatar",
        limit=MAX_AVATAR_UPLOADS_PER_USER_PER_DAY,
        message=f"今日头像上传次数已达上限（{MAX_AVATAR_UPLOADS_PER_USER_PER_DAY} 次），请明天再试",
    )

    # content_type 归一化小写比较（客户端可能传 IMAGE/PNG），
    # 且对白名单配置逐项 strip（兼容 "image/jpeg, image/png" 带空格写法）
    allowed_types = {t.strip().lower() for t in settings.AVATAR_ALLOWED_TYPES.split(",") if t.strip()}
    content_type = (avatar.content_type or "").lower()
    if content_type not in allowed_types:
        raise AppException(code=400, message="仅支持 JPG/PNG/WEBP/GIF 格式")

    data = await avatar.read(settings.AVATAR_MAX_SIZE + 1)
    if len(data) > settings.AVATAR_MAX_SIZE:
        raise AppException(
            code=400,
            message=f"头像大小不能超过 {settings.AVATAR_MAX_SIZE // 1024 // 1024}MB",
        )

    # 内容类型与实际魔数一致性校验 (防伪造扩展名)
    if not is_allowed_image(content_type, data):
        raise AppException(code=400, message="Invalid image content")

    # 像素上限：魔数与体积都合格的图仍可能是解压炸弹（12000×12000 纯色 PNG 只有几百 KB，
    # 但解码成位图要几百 MB）。头像会被邀请海报用 Pillow 真正解码，必须在入口就拦住。
    try:
        validate_image_pixels(data)
    except ValueError as exc:
        raise AppException(code=400, message=str(exc))

    # 本函数此后的 COS 上传是 0.5~5s 的网络 I/O：先结束鉴权阶段打开的事务，
    # 免得整段时间占着连接池（15 条池，5~10 个并发上传就能让其他接口排队）。
    # 回滚会让 current_user 过期，故先取出主键，写库前重新加载。
    user_id = current_user.id
    await db.rollback()

    ext_map = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    ext = ext_map.get(content_type, ".jpg")

    try:
        url = await storage.upload_bytes(
            data,
            folder="avatars",
            ext=ext,
            content_type=avatar.content_type,
        )
    except StorageError as exc:
        logger.error(f"头像上传失败（driver={storage.driver}）: {exc}")
        raise AppException(code=500, message="头像存储失败, 请稍后重试")

    user = await db.get(User, user_id)
    if user is None:  # 上传期间账号被删：不再写库，但也不必把已上传的对象回滚
        raise AppException(code=401, message="登录状态已失效，请重新登录")
    result = await update_avatar(db, user, url)
    logger.info(f"用户 {user_id} 更新头像: {url}")
    return success_response(result, message="头像上传成功")


# 密码 / 绑定 / 注销

@router.put("/password", response_model=ApiResponse[ActionAck])
async def change_password_api(
    req: PasswordChangeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """修改密码"""
    await change_password(db, current_user, req.old_password, req.new_password)
    return success_response({"message": "密码修改成功"})


@router.get("/bindings", response_model=ApiResponse[ActionAck])
async def get_bindings_api(
    current_user: User = Depends(get_current_user),
):
    """获取账号绑定信息"""
    result = await get_bindings(current_user)
    return success_response(result)


@router.put("/email", response_model=ApiResponse[ActionAck])
async def change_email_api(
    req: EmailChangeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更换邮箱（需先向新邮箱发送验证码）"""
    result = await change_email(db, current_user, req.new_email, req.code)
    logger.info(f"用户 {current_user.id} 更换邮箱成功")
    return success_response(result, message="邮箱更换成功")


@router.post("/delete-account", response_model=ApiResponse[ActionAck])
async def delete_account_api(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """注销账号"""
    user_id = current_user.id
    await delete_account(db, current_user)
    logger.warning(f"用户 {user_id} 已注销账号")
    return success_response({"message": "账号已注销"})


# 通知设置

@router.get("/notification-settings", response_model=ApiResponse[ActionAck])
async def get_notification_settings_api(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取通知设置"""
    result = await get_notification_settings(db, current_user)
    return success_response(result)


@router.put("/notification-settings", response_model=ApiResponse[ActionAck])
async def update_notification_settings_api(
    req: NotificationSettingsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新通知设置"""
    updates = req.model_dump(exclude_none=True)
    result = await update_notification_settings(db, current_user, updates)
    return success_response(result, message="通知设置已更新")


# 统计 / 等级

@router.get("/stats", response_model=ApiResponse[StatsResult])
async def user_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """个人中心顶部 4 个统计：收藏/学习/下载/消息"""
    result = await get_user_stats(db, current_user)
    return success_response(result)


@router.get("/level", response_model=ApiResponse[ActionAck])
async def user_level(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """用户等级与成就"""
    result = await get_user_level(db, current_user)
    return success_response(result)


# 收藏

@router.post("/favorites", response_model=ApiResponse[CreatedResult])
async def add_favorite_api(
    req: FavoriteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """添加收藏"""
    result = await add_favorite(db, current_user, req.target_type, req.target_id)
    return success_response(result, message=result.get("message", "收藏成功"))


@router.get("/favorites", response_model=ApiResponse[PageResult[FavoriteItem]])
async def list_favorites_api(
    page: int = Query(1, ge=1),
    page_size: int = Query(6, ge=1, le=50),
    category: str = Query(None, description="tutorial/resource/plugin/node/workflow/all"),
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """收藏列表"""
    result = await list_favorites(db, current_user, page, page_size, category, keyword)
    return success_response(result)


@router.delete("/favorites/{favorite_id}", response_model=ApiResponse[DeletedResult])
async def remove_favorite_api(
    favorite_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """取消收藏"""
    await remove_favorite(db, current_user, favorite_id)
    return success_response({"message": "已取消收藏"})


@router.get("/favorites/check", response_model=ApiResponse[ActionAck])
async def check_favorite_api(
    target_type: str = Query(...),
    target_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """检查是否已收藏"""
    result = await check_favorite(db, current_user, target_type, target_id)
    return success_response(result)


# 学习记录

@router.get("/learning", response_model=ApiResponse[PageResult[LearningRecordItem]])
async def list_learning_api(
    page: int = Query(1, ge=1),
    page_size: int = Query(6, ge=1, le=50),
    status: str = Query(None, description="in_progress/completed/all"),
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """学习记录列表"""
    result = await list_learning_records(db, current_user, page, page_size, status, keyword)
    return success_response(result)


@router.get("/learning/continue", response_model=ApiResponse[ItemsResult[ContinueLearningItem]])
async def continue_learning_api(
    limit: int = Query(2, ge=1, le=10),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """继续学习 - 取最近在学的 N 条

    ⚠️ 响应是 `{"items": [...]}` 而**不是**裸数组：前端 `Profile.tsx` 与小程序
    `profile.js` 都读 `data.items`。此前这里声明成 `ApiResponse[list[...]]`，
    与返回的 dict 形状不符 → 序列化抛 `ResponseValidationError` → 个人中心
    「继续学习」整块 500（顶部横幅「部分数据加载失败」就是它）。
    """
    items = await list_continue_learning(db, current_user, limit)
    return success_response({"items": items})


@router.get("/learning/stats", response_model=ApiResponse[StatsResult])
async def learning_stats_api(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """学习记录统计"""
    result = await get_learning_stats(db, current_user)
    return success_response(result)


@router.get("/purchases", response_model=ApiResponse[PageResult[Any]])
async def list_purchases_api(
    page: int = Query(1, ge=1),
    page_size: int = Query(6, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """我购买的教程（积分购买记录，购买后永久可读；购买时间倒序）"""
    result = await list_purchased_tutorials(db, current_user.id, page, page_size)
    return success_response(result)


@router.post("/learning/{tutorial_id}", response_model=ApiResponse[ActionAck])
async def upsert_learning_api(
    tutorial_id: int,
    req: LearningUpsertRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """新增或更新学习记录（显式上报进度/章节）"""
    record = await upsert_learning_record(
        db,
        current_user,
        tutorial_id,
        progress=req.progress,
        current_chapter=req.chapter,
    )
    # 显式提交：upsert_learning_record 仅 flush（被 track_learning 的 savepoint 复用），
    # 路由直调时需在此提交（get_db teardown 的 commit 晚于响应发送）
    await db.commit()
    return success_response(
        {
            "tutorial_id": record.tutorial_id,
            "progress": record.progress,
            "chapter": record.current_chapter,
            "status": record.status,
            "last_study_at": record.last_study_at.isoformat() if record.last_study_at else None,
        }
    )


# 消息

@router.get("/messages", response_model=ApiResponse[PageResult[MessageItem]])
async def list_messages_api(
    page: int = Query(1, ge=1),
    page_size: int = Query(6, ge=1, le=50),
    type: str = Query(None, description="system/comment/like/private/all"),
    keyword: str = Query(None, max_length=KEYWORD_MAX_LENGTH),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """消息列表"""
    result = await list_messages(db, current_user, page, page_size, type, keyword)
    return success_response(result)


@router.get("/messages/unread-count", response_model=ApiResponse[StatsResult])
async def unread_count_api(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """未读消息数"""
    result = await get_unread_count(db, current_user)
    return success_response(result)


@router.get("/messages/{message_id}", response_model=ApiResponse[MessageItem])
async def get_message_api(
    message_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """消息详情"""
    result = await get_message(db, current_user, message_id)
    return success_response(result)


@router.post("/messages/{message_id}/read", response_model=ApiResponse[ActionAck])
async def mark_read_api(
    message_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """标记单条消息已读"""
    await mark_read(db, current_user, message_id)
    return success_response({"message": "已标记为已读"})


@router.post("/messages/read-all", response_model=ApiResponse[ActionAck])
async def mark_all_read_api(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """全部已读"""
    count = await mark_all_read(db, current_user)
    return success_response({"updated": count, "message": f"已将 {count} 条消息标记为已读"})


@router.delete("/messages/{message_id}", response_model=ApiResponse[DeletedResult])
async def delete_message_api(
    message_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除消息"""
    await delete_message(db, current_user, message_id)
    return success_response({"message": "消息已删除"})


# 积分明细

@router.get("/points/transactions", response_model=ApiResponse[PageResult[PointTransactionItem]])
async def points_transactions(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """我的积分明细（时间倒序，含工具消耗与管理员调整）"""
    result = await list_user_transactions(db, current_user.id, page, page_size)
    return success_response(result)


@router.post("/points/redeem", response_model=ApiResponse[ActionAck])
async def points_redeem(
    req: RedeemCodeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """兑换码充值积分（并发安全：原子核销名额 + 唯一约束防重复兑换）"""
    result = await redeem_code(db, current_user, req.code)
    return success_response(result, message=f"兑换成功，获得 {result['points']} 积分")


# 邀请返利

@router.get("/invite/info", response_model=ApiResponse[InviteInfo])
async def invite_info(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """我的邀请信息：专属邀请码 + 奖励配置 + 已邀请人数/累计奖励统计"""
    result = await get_invite_info(db, current_user)
    return success_response(result)


@router.get("/invite/poster")
async def invite_poster(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """我的邀请海报（1080×1440 PNG）：二维码内容即邀请注册链接

    服务端合成而不是前端 canvas：海报会被大量转发，必须保证字体/间距/二维码
    清晰度一致，也不能依赖用户设备字体。图片含个人邀请码，故只允许本人获取。
    """
    code = (current_user.invite_code or "").strip()
    if not code:
        raise AppException(code=400, message="邀请码尚未生成，请刷新后重试")
    # 先把要画进图里的标量取出来：渲染含「下载头像 + Pillow 合成」，耗时 0.3~3s，
    # 期间不该占着数据库连接（15 条池）。回滚会过期 ORM 对象，故必须先快照。
    nickname = current_user.nickname or ""
    avatar_url = current_user.avatar_url
    await db.rollback()
    # 奖励积分数来自后台设置（与邀请页展示同源），海报上不能写死 50
    _, reward_points = await get_invite_settings()
    png = await build_invite_poster(
        invite_code=code,
        nickname=nickname,
        avatar_url=avatar_url,
        reward_points=reward_points,
    )
    return Response(
        content=png,
        media_type="image/png",
        # 头像 / 昵称随时会改，这张图必须每次都是最新的：不缓存
        # （曾用 max-age=300，结果用户改完资料看到的还是旧海报）
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/invite/invitees", response_model=ApiResponse[PageResult[InviteeItem]])
async def invite_invitees(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """我邀请的用户明细（时间倒序，账号脱敏）"""
    result = await list_invitees(db, current_user, page, page_size)
    return success_response(result)
