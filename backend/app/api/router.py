"""路由聚合"""

from fastapi import APIRouter, Response

from app.core.exceptions import success_response
from app.schemas.common import ApiResponse
from app.services import health_service
from app.api.auth import router as auth_router
from app.api.tutorials import router as tutorials_router
from app.api.users import router as users_router
from app.api.categories import router as categories_router
from app.api.admin import router as admin_router
from app.api.admin_audit import router as admin_audit_router
from app.api.admin_rbac import router as admin_rbac_router
from app.api.stats import router as stats_router
from app.api.home import router as home_router
from app.api.announcements import router as announcements_router
from app.api.uploads import router as uploads_router
from app.api.images import router as images_router
from app.api.sitemap import router as sitemap_router
from app.api.llms_txt import router as llms_txt_router
from app.api.indexnow_key import router as indexnow_key_router
from app.api.settings import router as settings_router
from app.api.feedbacks import router as feedbacks_router
from app.api.reports import router as reports_router
from app.api.comments import router as comments_router
from app.api.payments import router as payments_router
from app.api.setup import router as setup_router
from app.schemas.admin import ActionAck

api_router = APIRouter(prefix="/api/v1")

# 注册各模块路由
api_router.include_router(auth_router, prefix="/auth", tags=["认证"])
api_router.include_router(tutorials_router, prefix="/tutorials", tags=["教程"])
api_router.include_router(users_router, prefix="/users", tags=["用户中心"])
api_router.include_router(categories_router, prefix="/categories", tags=["分类"])
api_router.include_router(admin_router, prefix="/admin", tags=["管理后台"])
api_router.include_router(admin_rbac_router, prefix="/admin", tags=["权限管理"])
api_router.include_router(admin_audit_router, prefix="/admin", tags=["操作审计"])
api_router.include_router(stats_router, prefix="/stats", tags=["统计"])
api_router.include_router(home_router, prefix="/home", tags=["首页聚合"])
api_router.include_router(announcements_router, prefix="/announcements", tags=["公告"])
api_router.include_router(uploads_router, prefix="/uploads", tags=["文件上传"])
api_router.include_router(settings_router, prefix="/settings", tags=["系统设置"])
api_router.include_router(feedbacks_router, prefix="/feedbacks", tags=["问题反馈"])
api_router.include_router(reports_router, prefix="/reports", tags=["内容举报"])
api_router.include_router(comments_router, prefix="/comments", tags=["内容评论"])
api_router.include_router(payments_router, prefix="/payments", tags=["支付"])
# 安装向导：必须在 /admin 之外（admin 路由有 fail-closed 权限依赖，
# 系统里还没有管理员时会把自己锁死）
api_router.include_router(setup_router, prefix="/setup", tags=["安装向导"])
api_router.include_router(images_router, tags=["图片代理"])
api_router.include_router(sitemap_router, tags=["SEO"])
api_router.include_router(llms_txt_router, tags=["SEO"])
api_router.include_router(indexnow_key_router, tags=["SEO"])


# 健康检查两级设计：/health 浅检查（仅进程存活，容器编排与 nginx 用）；
# /health/deep 深检查（实际探测 MySQL + Redis，供 uptime 监控告警用）。探测实现见 services/health_service


@api_router.get("/health", tags=["系统"], response_model=ApiResponse[ActionAck])
async def health_check():
    """浅健康检查：仅证明进程存活，不触碰外部依赖（依赖故障时返回失败会让编排器反复重启容器，把下游抖动放大成服务不可用）"""
    return success_response({"status": "ok"})


@api_router.get("/health/deep", tags=["系统"])
async def health_deep_check(response: Response):
    """深度健康检查：实际探测 MySQL 与 Redis，依赖异常 → 503 degraded（uptime 据此告警）。
    MySQL 附加校验会话时区 UTC 与 alembic 版本 head（读不到数据只标记「未知」不判失败，详见 health_service.probe_mysql）"""
    payload, ok = await health_service.get_deep_health()
    response.status_code = 200 if ok else 503
    return success_response(payload)
