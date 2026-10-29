"""安全响应头（纵深防御）。

生产链路里这些头由 `nginx.conf` 统一下发（server 级 + 每个显式 `add_header` 的 location
逐一重申）。但后端自己也该兜一层：容器端口被误暴露、绕过 nginx 直连 8000、或者本地起服务
做安全扫描时，缺头就是真的缺——而不是「nginx 会给」。

刻意**不下发 CSP**：本站的 CSP 需要按环境拼 `$csp_policy` 变量（主题脚本、Umami、Sentry、
iframe 白名单），在应用层再写一份必然与 nginx 那份漂移，两处不一致会让浏览器取交集、
把前端功能掐死。CSP 仍由 nginx 唯一负责。
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# 与 nginx.conf 的取值保持一致，避免"直连 8000 与走 nginx 行为不同"
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
)


class SecurityHeadersMiddleware:
    """纯 ASGI 实现（与 RateLimitMiddleware / RequestIdMiddleware 同风格，不加 task 与流包装）。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                existing = {name.lower() for name, _ in headers}
                # 已存在的头以路由/中间件自己写的为准（不覆盖，避免双重取值）
                headers.extend(
                    (name, value) for name, value in SECURITY_HEADERS if name not in existing
                )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
