"""请求关联 ID：入口读 nginx 注入的 X-Request-Id → contextvar → 响应头回显 + Sentry tag，
日志 filter 统一注入 extra.request_id；入站头只接受 [A-Za-z0-9._-]{1,64}，防日志注入。"""

import re
import uuid
from contextvars import ContextVar

from loguru import logger

# 与 nginx `$request_id` 同名同义；响应头回显也用它
REQUEST_ID_HEADER = "X-Request-Id"

# 入站头白名单：拒绝空白、换行、超长与任何富字符（防日志注入）
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
# 客户端 IP / User-Agent：供审计行归因。中间件统一提取一次，业务代码无需重复解析
_client_ip_var: ContextVar[str | None] = ContextVar("client_ip", default=None)
_user_agent_var: ContextVar[str | None] = ContextVar("user_agent", default=None)
# 当前登录用户 id（游客为 None）：供「埋点类」旁路读取，避免把 user 一路透传到 service 签名里。
# 由 dependencies.get_current_user / get_current_user_optional 在解析出用户后写入。
_user_id_var: ContextVar[int | None] = ContextVar("current_user_id", default=None)


def get_request_id() -> str | None:
    """当前请求的关联 ID（无请求上下文时返回 None，如脚本 / 调度器 / 启动阶段）"""
    return _request_id_var.get()


def get_client_ip() -> str | None:
    """当前请求的客户端 IP（可信代理解析口径与限流中间件完全一致）"""
    return _client_ip_var.get()


def get_user_agent() -> str | None:
    """当前请求的 User-Agent 原文（审计截断由写入方负责）"""
    return _user_agent_var.get()


def get_current_user_id() -> int | None:
    """当前请求的登录用户 id（游客 / 无请求上下文时返回 None）"""
    return _user_id_var.get()


def set_current_user_id(user_id: int | None) -> None:
    """由鉴权依赖在解析出用户后调用（不要在其他地方随意改）"""
    _user_id_var.set(user_id)


def new_request_id() -> str:
    """生成一枚新的关联 ID（32 位 hex，与 nginx $request_id 格式一致）"""
    return uuid.uuid4().hex


def sanitize_request_id(raw: str | None) -> str | None:
    """校验入站关联 ID；非法（含富字符/超长）返回 None 由调用方重新生成"""
    if raw and _SAFE_REQUEST_ID.match(raw):
        return raw
    return None


class RequestIdMiddleware:
    """纯 ASGI 实现（与 RateLimitMiddleware 同风格：避免 BaseHTTPMiddleware 每请求额外 task 与响应流包装的开销）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # 非 HTTP 流量（如 lifespan）直接放行
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = self._extract(scope)
        token = _request_id_var.set(request_id)
        ip_token = _client_ip_var.set(self._extract_client_ip(scope))
        ua_token = _user_agent_var.set(self._extract_user_agent(scope))
        self._tag_sentry(request_id)

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                # 兜底：同一响应里已有同名头（如未来某端点自己回显）则不重复添加
                if not any(k.lower() == b"x-request-id" for k, _ in headers):
                    headers.append((REQUEST_ID_HEADER.lower().encode("ascii"), request_id.encode("ascii")))
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            # 归还 contextvar，避免同一 worker 处理下个请求时读到上一个请求的 ID
            _user_agent_var.reset(ua_token)
            _client_ip_var.reset(ip_token)
            _request_id_var.reset(token)

    @staticmethod
    def _extract(scope) -> str:
        for key, value in scope.get("headers") or ():
            if key.lower() == b"x-request-id":
                try:
                    candidate = value.decode("ascii")
                except UnicodeDecodeError:
                    break
                valid = sanitize_request_id(candidate)
                if valid:
                    return valid
                break
        return new_request_id()

    @staticmethod
    def _extract_client_ip(scope) -> str | None:
        """复用限流中间件的可信代理解析（同一份口径）。
        懒 import 是刻意的：rate_limit → redis → logging → 本模块，模块级 import 会形成环。"""
        try:
            from fastapi import Request

            from app.core.rate_limit import get_client_ip

            return get_client_ip(Request(scope))
        except Exception as exc:  # noqa: BLE001 提取失败不能影响请求本身
            logger.debug(f"客户端 IP 提取失败（审计将记 NULL）: {exc}")
            return None

    @staticmethod
    def _extract_user_agent(scope) -> str | None:
        for key, value in scope.get("headers") or ():
            if key.lower() == b"user-agent":
                # HTTP 头按 latin-1 解码（RFC 7230）；UA 只进数据库不进日志，
                # 无需 ASCII 白名单，截断由写入方（审计 500 字符）负责
                return value.decode("latin-1", errors="replace")
        return None

    @staticmethod
    def _tag_sentry(request_id: str) -> None:
        """Sentry 事件带 request_id tag（未启用监控时零开销直接返回）"""
        try:
            from app.core.monitoring import is_monitoring_enabled

            if not is_monitoring_enabled():
                return
            import sentry_sdk

            sentry_sdk.set_tag("request_id", request_id)
        except Exception as exc:  # noqa: BLE001 监控缺失/异常绝不能影响请求本身
            logger.debug(f"Sentry 关联 ID 打标失败（不影响请求）: {exc}")
