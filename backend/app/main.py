"""FastAPI 应用入口"""

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.config import settings
# 导入即完成日志文件 sink 配置，必须在其它模块使用 logger 之前
from app import logging as _logging  # noqa: F401
from app.core.exceptions import register_exception_handlers, success_response
from app.core.monitoring import init_monitoring
from app.core.rate_limit import RateLimitMiddleware
from app.core.request_context import REQUEST_ID_HEADER, RequestIdMiddleware
from app.core.security_headers import SecurityHeadersMiddleware
from app.api.router import api_router
from app.database import dispose_engine
from app.redis import close_redis, redis_client
from app.services.ai.factory import close_all_providers


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"AI 猫平台后端启动中... 环境: {settings.APP_ENV}")
    logger.info(f"数据库: {settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")
    logger.info(f"Redis: {settings.REDIS_HOST}:{settings.REDIS_PORT}")

    # Redis 连通性预热（惰性建连改为启动时发现故障，避免首请求延迟突增）
    try:
        await redis_client.ping()
    except Exception as e:
        logger.warning(f"Redis 启动连通性检查失败（缓存/限流将降级）: {e}")

    # 运行期配置（后台「系统设置」可改项）覆盖到 settings 上。
    # 必须在开始接受请求前完成，否则前几个请求会用 .env 默认值；读不到就保持 env 默认。
    from app.services.runtime_config import apply_runtime_settings, runtime_settings_sync_loop

    try:
        await apply_runtime_settings()
    except Exception:
        logger.exception("运行期配置加载失败，本次启动沿用 .env 默认值")
    # 回收上次进程异常退出时留在 Redis 的浏览量增量：正常情况下调度器每分钟落库，
    # 但进程被 SIGKILL / 容器重建时最后一分钟的增量只在 Redis 里（见 services/view_counter）。
    # 读不到（Redis 不可用）时内部已降级，不影响启动。
    try:
        from app.database import async_session
        from app.services.view_counter import flush_pending_views

        async with async_session() as db:
            await flush_pending_views(db)
    except Exception:
        logger.exception("启动时浏览量落库失败（不影响启动，等待调度器下一轮）")

    # 本地开发没有 scheduler 容器：起一个进程内循环把浏览量增量落库
    # （生产由 scheduler 每分钟做，避免 2 个 worker 各跑一份）
    view_flush_task = None
    if settings.is_dev:
        from app.services.view_counter import flush_loop

        view_flush_task = asyncio.create_task(flush_loop())

    # 轮询：让「另一个 gunicorn worker 保存的设置」在本进程内也能生效（最多 60s 延迟）
    settings_sync_task = asyncio.create_task(runtime_settings_sync_loop())

    yield

    settings_sync_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await settings_sync_task
    if view_flush_task is not None:
        view_flush_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await view_flush_task
    # 关闭顺序必须先排收在飞翻译任务、再关 provider / 线程池：在飞任务还在用它们，
    # 取消并等其结束后再关连接，否则中断状态写不回 Redis
    logger.info("排收在飞翻译任务...")
    from app.services.translate_service import shutdown_translate_tasks
    interrupted = await shutdown_translate_tasks()
    if interrupted:
        logger.warning(f"已中断 {interrupted} 个在飞翻译任务（状态已写回，管理端不再空轮询）")
    logger.info("关闭重活线程池（COS 上传 / 图片转码 / HTML→Markdown）...")
    from app.core.executors import shutdown_heavy_executor
    shutdown_heavy_executor()
    logger.info("关闭 AI Provider 连接...")
    await close_all_providers()
    logger.info("关闭图片代理共享连接池...")
    from app.api.images import close_image_client
    await close_image_client()
    logger.info("关闭 Redis 连接...")
    await close_redis()
    logger.info("释放数据库连接池...")
    await dispose_engine()
    logger.info("应用已关闭")


def create_app() -> FastAPI:
    # 错误监控必须在 FastAPI 实例化之前初始化：Sentry 通过全局 patch
    # Starlette / FastAPI 类来挂载中间件，晚于实例化则当前 app 抓不到异常。
    init_monitoring()

    app = FastAPI(
        title="AI 猫学习与工具聚合平台",
        description="覆盖全领域的结构化教程学习平台：多章节课程、在线工具、知识百科、资源下载与扩展推荐",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.is_dev else None,
        redoc_url="/redoc" if settings.is_dev else None,
        openapi_url="/openapi.json" if settings.is_dev else None,
    )

    # 中间件注册顺序：后添加者在最外层。限流（内层）先注册、CORS（外层）后注册：
    # 预检 OPTIONS 由 CORS 直接消化不消耗限流配额；限流 429 响应也带 CORS 头
    app.add_middleware(RateLimitMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # 浏览器侧 Sentry 事件可携带本次请求的关联 ID，与后端日志 / nginx access log 串成一条线索
        expose_headers=[REQUEST_ID_HEADER],
    )

    # 安全响应头（nosniff / X-Frame-Options / Referrer-Policy）：生产由 nginx 下发，
    # 这里兜「直连后端」的场景（端口误暴露、本地扫描）；CSP 仍只由 nginx 负责。
    # ⚠️ 必须在 RequestIdMiddleware **之前**注册：RequestId 要保持最外层
    # （tests/test_request_id.py 钉住），否则 429 等响应带不上关联 ID。
    # 本中间件仍包住限流与 CORS，429 / 预检响应同样能带上安全头。
    app.add_middleware(SecurityHeadersMiddleware)

    # 最后注册 = 最外层：429 / 500 / 预检等所有响应都带得上 X-Request-Id
    app.add_middleware(RequestIdMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router)

    @app.get("/")
    async def root():
        return success_response({"name": "AI 猫平台 API", "version": "1.0.0", "docs": "/docs"})

    return app


app = create_app()
