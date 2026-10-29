"""全局异常处理 + 统一响应格式"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from loguru import logger

from app.config import settings


class AppException(Exception):
    """业务异常基类"""

    def __init__(self, code: int = 400, message: str = "请求错误", data=None):
        self.code = code
        self.message = message
        self.data = data


class AuthenticationError(AppException):
    def __init__(self, message: str = "认证失败，请先登录"):
        super().__init__(code=401, message=message)


class PermissionDeniedError(AppException):
    def __init__(self, message: str = "权限不足"):
        super().__init__(code=403, message=message)


class FreeTimeExhaustedError(AppException):
    def __init__(self, message: str = "免费体验时长已用完，请认证后继续使用"):
        super().__init__(code=403, message=message, data={"need_auth": True})


class NotFoundError(AppException):
    def __init__(self, message: str = "资源不存在"):
        super().__init__(code=404, message=message)


def success_response(data=None, message: str = "success", code: int = 0):
    return {"code": code, "message": message, "data": data}


def error_response(message: str, code: int = 400, data=None):
    return {"code": code, "message": message, "data": data}


def _safe_validation_errors(errors: list) -> list:
    """把 Pydantic 校验错误裁剪为可安全落盘的形式：exc.errors() 带 input（用户提交的原始值，
    会把密码/验证码明文沉淀到落盘日志），只留 loc/msg/type 对排查无损。"""
    return [
        {
            "loc": list(e.get("loc", [])),
            "msg": e.get("msg"),
            "type": e.get("type"),
        }
        for e in errors
    ]


def register_exception_handlers(app: FastAPI):
    """注册全局异常处理器"""

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        # code 必须是合法 HTTP 状态码，业务长码（如 40001）回落 400；有效 5xx 保留原值便于告警分级
        status_code = exc.code if 100 <= exc.code <= 599 else 400
        # 5xx 必须留痕：系统性问题（第三方导入失败、AI 提供方故障）不记日志只能靠用户反馈发现；
        # 只记 5xx 不记 4xx（4xx 是用户输入问题，逐条记录会刷满日志），message 截断 200 字符
        if status_code >= 500:
            logger.bind(
                event="app_exception",
                path=request.url.path,
                method=request.method,
                status=status_code,
            ).error("业务异常(5xx): {}", str(exc.message)[:200])
        return JSONResponse(
            status_code=status_code,
            content=error_response(exc.message, exc.code, exc.data),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        # 透传 headers（如 401 的 WWW-Authenticate、429 的 Retry-After）
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(str(exc.detail), exc.status_code),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        # 必须走 _safe_validation_errors 裁剪：原始 exc.errors() 含 input（用户原始值）会泄密；
        # 用 loguru 占位符而非 f-string，避免不可序列化对象带来的问题
        errors = exc.errors()
        logger.bind(
            event="validation_error",
            path=request.url.path,
            method=request.method,
        ).warning("参数校验失败: {}", _safe_validation_errors(errors))
        # 消息带上第一个出错字段，管理后台可直接定位
        # 仅开发环境返回字段定位细节；生产环境统一模糊提示，避免泄露 schema 结构
        message = "参数校验失败"
        if settings.is_dev and errors:
            loc = ".".join(str(x) for x in errors[0].get("loc", []) if str(x) not in ("body",))
            msg = errors[0].get("msg", "")
            message = f"参数校验失败: {loc} {msg}".strip()
        # 生产环境不向客户端返回内部字段细节；开发环境返回精简后的可序列化字段
        # (errors() 的 ctx 可能含不可 JSON 序列化的对象, 直接返回会触发 500)
        if settings.is_dev:
            detail = [
                {
                    "loc": list(err.get("loc", [])),
                    "msg": err.get("msg"),
                    "type": err.get("type"),
                }
                for err in errors
            ]
        else:
            detail = None
        return JSONResponse(
            status_code=422,
            content=error_response(message, 422, detail),
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error("未处理异常: {}", str(exc), exc_info=True)
        return JSONResponse(
            status_code=500,
            content=error_response("服务器内部错误", 500),
        )
