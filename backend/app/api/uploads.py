"""文件上传接口：/uploads/image 富文本编辑器图片（管理员, ≤5MB）、
/uploads/file 通用资源文件（管理员, ≤20MB, 超出提示使用网盘）；文件写入腾讯云 COS 返回公开 URL"""

from fastapi import APIRouter, Depends, UploadFile, File
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.degrade import degrade_guard
from app.core.exceptions import AppException, success_response
from app.core.executors import run_heavy
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.common import ApiResponse
from app.services import cos_service
from app.services.image_search.asset_recorder import record_file_asset, record_upload_asset
from app.utils.image_convert import convert_png_to_webp
from app.utils.upload_validation import IMAGE_SIGNATURES, is_allowed_image
from app.schemas.admin import UploadResult

router = APIRouter()


# 编辑器图片允许的 MIME 类型
_IMAGE_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# 资源文件允许的 MIME 类型 (图片 / 视频 / 音频 / 常见文档与压缩包)
_FILE_ALLOWED_TYPES = {
    # 图片
    "image/jpeg", "image/png", "image/gif", "image/webp",
    # 视频
    "video/mp4", "video/quicktime", "video/x-msvideo",
    # 音频
    "audio/mpeg", "audio/wav", "audio/x-wav",
    # 文档
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain",
    # 压缩包
    "application/zip", "application/x-zip-compressed",
    "application/x-rar-compressed", "application/vnd.rar",
    "application/x-7z-compressed",
    # COS/浏览器对部分类型识别不一致, 兜底允许 octet-stream (依赖扩展名校验)
    "application/octet-stream",
}


@router.post("/image", response_model=ApiResponse[UploadResult])
async def upload_image(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """富文本编辑器图片上传（multipart/form-data 字段名 file），返回 { url }"""
    # 必须在 rollback 之前取主键：rollback 会 expire 会话内对象，之后再读 admin.id
    # 会触发同步懒加载，在异步会话里直接 MissingGreenlet。
    admin_id = admin.id
    # 先结束鉴权阶段打开的事务，免得读文件 + 转码 + COS 上传期间一直占住池连接
    await db.rollback()

    content_type = (file.content_type or "").lower()

    if content_type not in _IMAGE_ALLOWED_TYPES:
        raise AppException(code=400, message="仅支持 JPG/PNG/WEBP/GIF 格式图片")

    data = await file.read(settings.COS_IMAGE_MAX_SIZE + 1)
    if len(data) > settings.COS_IMAGE_MAX_SIZE:
        raise AppException(
            code=400,
            message=f"图片大小不能超过 {settings.COS_IMAGE_MAX_SIZE // 1024 // 1024}MB",
        )

    if not is_allowed_image(content_type, data):
        raise AppException(code=400, message="图片内容与声明类型不一致")

    ext = cos_service.get_file_ext(file.filename, content_type)

    # 大图 PNG 服务端转 WebP：截图类 PNG 普遍 1~3MB，转码后体积降 80%~90%；
    # 转码在独立线程执行（纯 CPU），失败/无收益/APNG 时静默回退原图
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
            folder="images",
            ext=ext or ".jpg",
            content_type=content_type,
        )
    except cos_service.CosError as exc:
        logger.error(f"编辑器图片上传 COS 失败: {exc}")
        raise AppException(code=500, message="图片存储失败, 请稍后重试")

    with degrade_guard("upload.asset_record", detail="素材入库失败不影响上传"):
        await record_upload_asset(
            db, url=url, data=data, content_type=content_type,
            filename=file.filename, admin_id=admin_id,
        )

    return success_response({"url": url}, "上传成功")


@router.post("/file", response_model=ApiResponse[UploadResult])
async def upload_file(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """通用资源文件上传（图片/小视频/文档/压缩包等，单文件 20MB 上限，超出提示使用第三方网盘），
    返回 { url, filename, size, content_type }"""
    admin_id = admin.id
    content_type = (file.content_type or "").lower()

    # 先读上限+1 字节以判断是否超限, 避免超大文件全量加载到内存
    max_size = settings.COS_MAX_FILE_SIZE
    data = await file.read(max_size + 1)
    if len(data) > max_size:
        raise AppException(
            code=400,
            message=(
                f"文件大小超过 {max_size // 1024 // 1024}MB 限制, "
                "请使用第三方网盘服务 (如夸克网盘、百度网盘等) 存储, "
                "并在资源网盘链接中填写"
            ),
        )

    # octet-stream 时依赖扩展名校验; 否则要求命中白名单
    ext = cos_service.get_file_ext(file.filename, content_type)
    if content_type != "application/octet-stream":
        if content_type not in _FILE_ALLOWED_TYPES:
            raise AppException(code=400, message="不支持的文件类型")
    elif not ext:
        raise AppException(code=400, message="无法识别文件类型, 请补充文件扩展名")

    # 只信内容不信声明：把 HTML/脚本声明成 image/png 上传时，COS 按声明的 Content-Type
    # 存储，浏览器嗅探后即得攻击者可控的可执行文档；校验「内容与声明一致」堵住改扩展名/Content-Type 的绕过
    if content_type in IMAGE_SIGNATURES or content_type == "image/webp":
        if not is_allowed_image(content_type, data):
            raise AppException(code=400, message="图片内容与声明类型不一致")
    elif content_type == "application/pdf" and not data.startswith(b"%PDF-"):
        raise AppException(code=400, message="文件内容与声明类型不一致")

    try:
        url = await cos_service.upload_bytes(
            data,
            folder="files",
            ext=ext,
            content_type=content_type or "application/octet-stream",
        )
    except cos_service.CosError as exc:
        logger.error(f"资源文件上传 COS 失败: {exc}")
        raise AppException(code=500, message="文件存储失败, 请稍后重试")

    with degrade_guard("upload.file_asset_record", detail="附件入库失败不影响上传"):
        await record_file_asset(
            db, url=url, data=data, content_type=content_type,
            filename=file.filename, admin_id=admin_id,
        )

    return success_response(
        {
            "url": url,
            "filename": file.filename or "",
            "size": len(data),
            "content_type": content_type,
        },
        "上传成功",
    )
