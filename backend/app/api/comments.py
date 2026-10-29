"""内容评论路由：
- GET/POST /comments 评论列表（公开）与发布/回复（需登录）
- POST /comments/images 配图上传（需登录）
- DELETE /comments/{id} 删除自己的评论（软删除，删顶层连带软删回复）

目标用 (target_type, target_id) 表达，取值见 COMMENT_TARGET_TYPES。
单独挂 /comments 而非 /tutorials/{id}/comments：教程路由已有 GET /{tutorial_id} 路径参数端点，
子路径声明顺序一旦被调整就静默 404，且评论不属于任何单一资源。
⚠️ /images 必须声明在 /{comment_id} 之前：路径参数路由放前面会吃掉将来新增的 POST 子路径。
"""

from fastapi import APIRouter, Depends, File, Query, UploadFile
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models.comment import (
    COMMENT_MAX_IMAGES,
    COMMENT_MAX_LENGTH,
    COMMENT_TARGET_TUTORIAL,
    COMMENT_TARGET_TYPES,
    COMMENT_TARGET_TYPE_MAX_LENGTH,
    MAX_COMMENT_IMAGES_PER_USER_PER_DAY,
)
from app.core.rate_limit import check_user_daily_quota
from app.core.legacy_metrics import record_legacy_usage
from app.models.user import User
from app.core.executors import run_heavy
from app.core.exceptions import AppException, success_response
from app.schemas.common import ApiResponse
from app.services import comment_service, cos_service
from app.utils.image_convert import convert_png_to_webp
from app.utils.upload_validation import is_allowed_image, validate_image_pixels
from app.schemas.content import PageResult
from app.schemas.admin import AdminCommentItem, CreatedResult, DeletedResult, UploadResult

router = APIRouter()

# 评论配图允许的 MIME 类型（与编辑器图片一致；不支持 SVG —— 可承载脚本）
_IMAGE_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# 取值由注册表派生：OpenAPI 是唯一对外文档，手写取值列表会漏新类型导致文档与实际不符
_TARGET_TYPE_DESC = "目标类型，取值：" + " / ".join(comment_service.registered_comment_targets())


def uses_legacy_comment_param(
    target_type: str | None, target_id: int | None, tutorial_id: int | None
) -> bool:
    """这条请求是否走的是兼容分支（只给旧 tutorial_id）；单独成函数让「路由记指标」与
    「service 归一参数」共用同一条件，避免两处判定漂移"""
    return target_id is None and tutorial_id is not None


async def note_legacy_comment_param(
    target_type: str | None, target_id: int | None, tutorial_id: int | None
) -> None:
    """命中兼容分支时留日志 + 计数（`legacy_metrics` 是可查询的那一份，见 P2-55 之六）。"""
    if not uses_legacy_comment_param(target_type, target_id, tutorial_id):
        return
    logger.bind(event="comment_legacy_param", param="tutorial_id").info(
        "评论请求仍使用 tutorial_id（旧 bundle 流量，兼容分支）"
    )
    await record_legacy_usage("comment_legacy_param")


def resolve_comment_target(
    target_type: str | None, target_id: int | None, tutorial_id: int | None
) -> tuple[str, int]:
    """把请求参数归一成 (target_type, target_id) 并校验类型合法（GET 与 POST 共用的目标契约，故为公开函数）。

    tutorial_id 是过渡期兼容参数：旧 bundle 用户长时间不刷新会一直发老参数（表现为评论列表空、发评论报错），
    这里翻译成新参数后即刻丢弃；默认值 tutorial 刻意保留（改成必填会把旧 bundle 打成 400），
    命中兼容分支由 note_legacy_comment_param 留痕计数。本函数无副作用（日志/计数在路由层），纯函数用例不需要 Redis。
    """
    if uses_legacy_comment_param(target_type, target_id, tutorial_id):
        target_type = target_type or COMMENT_TARGET_TUTORIAL
        target_id = tutorial_id
    if target_id is None or not target_type:
        raise AppException(code=400, message="缺少 target_type / target_id 参数")
    if target_type not in COMMENT_TARGET_TYPES:
        raise AppException(code=400, message="不支持的内容类型")
    return target_type, target_id


class CommentCreateRequest(BaseModel):
    target_type: str = Field(
        COMMENT_TARGET_TUTORIAL,
        max_length=COMMENT_TARGET_TYPE_MAX_LENGTH,
        description=_TARGET_TYPE_DESC,
    )
    target_id: int | None = Field(None, description="目标内容ID")
    tutorial_id: int | None = Field(
        None, description="[已废弃] 教程ID，等价于 target_type=tutorial + target_id"
    )
    # 允许只发图不写字（截图提问很常见），故不设 min_length；空值由 service 校验
    content: str = Field("", max_length=COMMENT_MAX_LENGTH, description="评论正文")
    parent_id: int | None = Field(None, description="父评论ID（回复时传，仅两级）")
    images: list[str] | None = Field(
        None, max_length=COMMENT_MAX_IMAGES, description="配图URL列表（须来自本站上传）"
    )


@router.get("", response_model=ApiResponse[PageResult[AdminCommentItem]])
async def list_comments(
    target_type: str = Query(COMMENT_TARGET_TUTORIAL, description=_TARGET_TYPE_DESC),
    target_id: int | None = Query(None, description="目标内容ID"),
    tutorial_id: int | None = Query(None, description="[已废弃] 教程ID"),
    page: int = Query(1, ge=1, le=200),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """评论列表（公开；已删除的不返回，回复内嵌在各自顶层评论下）"""
    await note_legacy_comment_param(target_type, target_id, tutorial_id)
    resolved_type, resolved_id = resolve_comment_target(target_type, target_id, tutorial_id)
    return success_response(
        await comment_service.list_comments(db, resolved_type, resolved_id, page, page_size)
    )


@router.post("", response_model=ApiResponse[CreatedResult])
async def create_comment(
    req: CommentCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """发布评论或回复（需登录；过敏感词机审，命中即留痕）"""
    await note_legacy_comment_param(req.target_type, req.target_id, req.tutorial_id)
    resolved_type, resolved_id = resolve_comment_target(req.target_type, req.target_id, req.tutorial_id)
    result = await comment_service.create_comment(
        db,
        current_user,
        resolved_type,
        resolved_id,
        req.content,
        parent_id=req.parent_id,
        images=req.images,
    )
    return success_response(result, "回复已发布" if req.parent_id else "评论已发布")


@router.post("/images", response_model=ApiResponse[UploadResult])
async def upload_comment_image(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """评论配图上传（登录用户，非管理员）。校验口径与 /uploads/image 一致（白名单→大小→嗅探→PNG转WebP→COS），
    返回 { url }，前端随评论一起提交、服务端再校验来源。⚠️ 按用户日张数上限：这是全站唯一
    「任意注册用户可写、对象永不复用、不产生业务行」的公开入口，无上限时多账号并行即可把 CDN 变成违规图床"""
    # 先把配额要用的标量取出来再回滚：回滚会**过期**同一会话加载出的 ORM 对象
    # （get_current_user 与本路由共用 get_db 那条会话），回滚后读 current_user.id
    # 等于发起一次没有 greenlet 的补载 SELECT → MissingGreenlet → 兜底 500
    user_id = current_user.id
    # 与 /uploads/image 同理：本函数不写库，先释放鉴权事务再去做 I/O
    await db.rollback()

    # 配额必须在**读文件与写 COS 之前**：放在上传之后计费，等于每次浪费都免费
    await check_user_daily_quota(
        user_id,
        scope="comment_image",
        limit=MAX_COMMENT_IMAGES_PER_USER_PER_DAY,
        message=f"今日评论配图已达上限（{MAX_COMMENT_IMAGES_PER_USER_PER_DAY} 张），请明天再来",
    )

    content_type = (file.content_type or "").lower()
    if content_type not in _IMAGE_ALLOWED_TYPES:
        raise AppException(code=400, message="仅支持 JPG/PNG/WEBP/GIF 格式图片")

    data = await file.read(settings.COS_IMAGE_MAX_SIZE + 1)
    if len(data) > settings.COS_IMAGE_MAX_SIZE:
        raise AppException(
            code=400,
            message=f"图片大小不能超过 {settings.COS_IMAGE_MAX_SIZE // 1024 // 1024}MB",
        )
    # 只信内容不信声明：改扩展名绕不过去
    if not is_allowed_image(content_type, data):
        raise AppException(code=400, message="图片内容与声明类型不一致")

    # 像素上限：超大像素图转码会回退原图直接落 COS，最终由访客浏览器解码，
    # 移动端遇到几百 M 像素的图会直接崩渲染进程
    try:
        validate_image_pixels(data)
    except ValueError as exc:
        raise AppException(code=400, message=str(exc))

    ext = cos_service.get_file_ext(file.filename, content_type)

    # 大图 PNG 转 WebP：评论截图普遍是 PNG，转码后体积降 80%~90%
    if (
        content_type == "image/png"
        and settings.COS_IMAGE_WEBP_THRESHOLD > 0
        and len(data) > settings.COS_IMAGE_WEBP_THRESHOLD
    ):
        webp = await run_heavy(
            convert_png_to_webp, data, settings.COS_IMAGE_WEBP_QUALITY
        )
        if webp is not None:
            data = webp
            ext = ".webp"
            content_type = "image/webp"

    try:
        url = await cos_service.upload_bytes(
            data,
            folder="comments",
            ext=ext or ".jpg",
            content_type=content_type,
        )
    except cos_service.CosError as exc:
        logger.error(f"评论配图上传 COS 失败: {exc}")
        raise AppException(code=500, message="图片存储失败, 请稍后重试")

    return success_response({"url": url}, "上传成功")


@router.delete("/{comment_id}", response_model=ApiResponse[DeletedResult])
async def delete_comment(
    comment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除自己的评论（软删除；管理员删他人走后台接口）"""
    result = await comment_service.delete_comment(db, comment_id, current_user.id)
    return success_response(result, "评论已删除")
